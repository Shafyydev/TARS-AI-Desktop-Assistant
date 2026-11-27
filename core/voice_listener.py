"""High-accuracy, locale-aware voice capture and speech recognition listener for TARS."""
import os
import time
import re
import locale
import threading
import struct
import math
from typing import Optional, Tuple, Callable, Any

from core.wake_word import WakeWordDetector


def detect_system_stt_language() -> str:
    """Detects system locale to choose the best Google STT acoustic model (e.g. en-IN, en-US, en-GB)."""
    lang_env = os.getenv("TARS_STT_LANGUAGE")
    if lang_env:
        return lang_env

    try:
        loc, _ = locale.getlocale()
        if loc:
            loc = loc.replace("_", "-")
            if loc.startswith("en-IN") or loc.startswith("en_IN"):
                return "en-IN"
            elif loc.startswith("en-GB") or loc.startswith("en_GB"):
                return "en-GB"
            elif loc.startswith("en-AU") or loc.startswith("en_AU"):
                return "en-AU"
            elif loc.startswith("en-CA") or loc.startswith("en_CA"):
                return "en-CA"
    except Exception:
        pass

    try:
        # Check timezone offset for India (+5:30 -> -19800)
        if time.timezone == -19800 or "India" in str(time.tzname):
            return "en-IN"
    except Exception:
        pass

    return "en-US"


def normalize_voice_command(text: str) -> str:
    """Corrects common speech-to-text phonetic confusions and acoustic artifacts."""
    if not text:
        return ""

    t = text.strip()

    # Browser tab confusions: "tap" / "top" / "table" -> "tab"
    t = re.sub(
        r"\b(new|close|next|previous|prev|switch|open|reopen|shut)\s+(?:this\s+)?(?:the\s+)?(?:tap|top|table)\b",
        r"\1 tab",
        t,
        flags=re.IGNORECASE
    )
    # Homophones: "clothes" -> "close"
    t = re.sub(r"\bclothes\s+(tab|window|app|this)\b", r"close \1", t, flags=re.IGNORECASE)
    # "write/ride click" -> "right click"
    t = re.sub(r"\b(?:write|ride|rite)\s+click\b", "right click", t, flags=re.IGNORECASE)
    # "double-click" -> "double click"
    t = re.sub(r"\bdouble-click\b", "double click", t, flags=re.IGNORECASE)
    # "call down / stroll down" -> "scroll down"
    t = re.sub(r"\b(?:call|stroll|score|crawled)\s+down\b", "scroll down", t, flags=re.IGNORECASE)
    # "stroll up / score up" -> "scroll up"
    t = re.sub(r"\b(?:call|stroll|score)\s+up\b", "scroll up", t, flags=re.IGNORECASE)
    # "screen shot" -> "screenshot"
    t = re.sub(r"\bscreen\s+shot\b", "screenshot", t, flags=re.IGNORECASE)
    # "mood / meet" -> "mute"
    if t.lower() in ["mood", "meet", "newt"]:
        t = "mute"
    # Single-word "ex", "ax it", "eggs it" -> "exit"
    if t.lower() in ["ex", "ax it", "eggs it", "exit out", "ex it"]:
        t = "exit"

    return t


class VoiceListener:
    """
    Captures microphone input with tuned VAD thresholds,
    locale-aware acoustic recognition, and phonetic command normalization.
    """

    WAKE_WORDS = [
        "tars", "hey tars", "yo tars", "ok tars", "okay tars", "robot",
        "tar", "stars", "cars", "taurus", "torres", "tas", "dars", "taz",
        "torch", "hello torch", "tarts", "target", "lotus"
    ]

    def __init__(
        self,
        energy_threshold: int = 250,
        pause_threshold: float = 0.3,
        language: Optional[str] = None,
        mic_index: Optional[int] = None
    ):
        self.energy_threshold = int(os.getenv("TARS_STT_ENERGY", str(energy_threshold)))
        self.pause_threshold = float(os.getenv("TARS_STT_PAUSE", str(pause_threshold)))
        self.language = language or os.getenv("TARS_STT_LANGUAGE") or detect_system_stt_language()
        
        env_mic = os.getenv("TARS_MIC_INDEX")
        self.mic_index = mic_index if mic_index is not None else (int(env_mic) if env_mic else None)

        self._recognizer = None
        self._microphone = None
        self._whisper_model = None
        self._fw_model = None
        self._whisper_failed = False
        self.stt_engine = os.getenv("TARS_STT_ENGINE", "google").strip().lower()
        self.whisper_model_name = os.getenv("TARS_STT_MODEL", "base").strip()
        self.last_active_time = 0.0
        self.active_dialogue_window_sec = 30.0
        self.wake_detector = WakeWordDetector(mic_index=self.mic_index)
        self._init_audio()

    def _init_audio(self) -> None:
        """Initializes speech_recognition with tuned low-latency acoustic VAD parameters."""
        try:
            import speech_recognition as sr
            self._recognizer = sr.Recognizer()
            self._recognizer.energy_threshold = self.energy_threshold
            # Keep enough silence for natural pauses between clauses and words
            self._recognizer.pause_threshold = self.pause_threshold
            # Short non-speaking duration so end-of-speech is detected quickly
            self._recognizer.non_speaking_duration = 0.2
            self._recognizer.phrase_threshold = 0.2
            # Prevent network timeouts on longer spoken sentences
            self._recognizer.operation_timeout = 3.0
            # Smooth dynamic adaptation to ambient room sound
            self._recognizer.dynamic_energy_threshold = True
            self._recognizer.dynamic_energy_adjustment_damping = 0.15
            self._recognizer.dynamic_energy_ratio = 1.5

            self._microphone = sr.Microphone(device_index=self.mic_index)
        except Exception as e:
            print(f"[Listener] Warning: Microphone init failed: {e}")
            self._recognizer = None
            self._microphone = None

    def calibrate(self, duration: float = 0.8) -> float:
        """Calibrates microphone for ambient acoustics with a safe noise floor."""
        if self._recognizer and self._microphone:
            try:
                with self._microphone as source:
                    self._recognizer.adjust_for_ambient_noise(source, duration=duration)
                # Keep energy floor safe: never clamp above 800 so end-of-sentence words aren't dropped
                self._recognizer.energy_threshold = max(150, min(800, self._recognizer.energy_threshold))
                # Keep dynamic adaptation enabled so background noise doesn't corrupt speech
                self._recognizer.dynamic_energy_threshold = True
                return self._recognizer.energy_threshold
            except Exception:
                pass
        return float(self.energy_threshold)

    def check_wake_word(self, text: str) -> Tuple[bool, str]:
        """
        Checks if text contains a wake word or phonetic approximation.
        Returns:
            (has_wake_word, cleaned_command)
        """
        if not text:
            return False, ""

        clean = text.strip()
        lower = clean.lower()
        now = time.time()

        for ww in self.WAKE_WORDS:
            pattern = rf"^{re.escape(ww)}[\s,:\-]*(.*)$"
            match = re.match(pattern, lower, re.IGNORECASE)
            if match:
                self.last_active_time = now
                remainder = clean[match.start(1):].strip() if match.group(1) else ""
                return True, remainder

            if lower.endswith(ww):
                self.last_active_time = now
                remainder = clean[:len(clean) - len(ww)].strip(" ,:-")
                return True, remainder

        # If already in an active conversational window, accept command
        if (now - self.last_active_time) <= self.active_dialogue_window_sec:
            self.last_active_time = now
            return True, clean

        return False, clean

    def listen_for_wake_word(self, timeout: Optional[float] = None) -> bool:
        """
        Uses dedicated wake-word detector (e.g. Porcupine) if available.
        Returns True if wake word is detected, False on timeout.
        """
        if self.wake_detector and self.wake_detector.is_porcupine_active():
            detected = self.wake_detector.wait_for_wake_word(timeout=timeout)
            if detected:
                self.last_active_time = time.time()
            return detected
        return False


    def _transcribe_whisper(self, audio) -> Optional[str]:
        """
        Transcribes a speech_recognition.AudioData object locally.

        Prefers faster-whisper (CTranslate2, 2-4x faster than openai-whisper,
        int8 quantized on CPU) and falls back to openai-whisper, then Google.
        """
        if self._whisper_failed:
            return None
        try:
            import numpy as np
            raw = audio.get_raw_data(convert_rate=16000, convert_width=2)
            samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

            # faster-whisper first (preferred: speed + accuracy)
            try:
                if getattr(self, "_fw_model", None) is None:
                    from faster_whisper import WhisperModel
                    self._fw_model = WhisperModel(
                        self.whisper_model_name, device="cpu", compute_type="int8"
                    )
                segments, _ = self._fw_model.transcribe(samples, beam_size=5)
                text = " ".join(s.text for s in segments).strip()
                if text:
                    return text
            except Exception:
                pass

            # openai-whisper fallback
            if self._whisper_model is None:
                import whisper
                self._whisper_model = whisper.load_model(self.whisper_model_name)
            result = self._whisper_model.transcribe(samples, fp16=False)
            text = (result.get("text") or "").strip()
            return text or None
        except Exception as e:
            print(f"[Listener] Whisper unavailable ({e}); falling back to Google STT.")
            self._whisper_failed = True
            return None

    def listen_once(
        self,
        timeout: int = 6,
        phrase_time_limit: Optional[int] = 15,
        on_listening_start: Optional[Callable[[], None]] = None
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Listens to the microphone with locale-aware STT and phonetic normalization.
        Supports full sentences without cutting off between natural word pauses.
        """
        if not self._recognizer or not self._microphone:
            return None, "Microphone not initialized"

        import speech_recognition as sr

        self._stt_timings = {"wait_and_record": 0.0, "transcription": 0.0, "end_reason": "unknown"}
        try:
            if on_listening_start:
                on_listening_start()

            _t0 = time.time()
            with self._microphone as source:
                audio = self._recognizer.listen(
                    source,
                    timeout=timeout,
                    phrase_time_limit=phrase_time_limit
                )
            _t1 = time.time()
            self._stt_timings["wait_and_record"] = _t1 - _t0
            # Estimate end reason: if audio duration close to phrase_time_limit, hit limit
            try:
                audio_dur = audio.duration if hasattr(audio, "duration") else 0
            except Exception:
                audio_dur = 0
            if timeout and (_t1 - _t0) >= timeout - 0.1:
                self._stt_timings["end_reason"] = "timeout"
            elif phrase_time_limit and audio_dur >= phrase_time_limit - 0.5:
                self._stt_timings["end_reason"] = "phrase_time_limit"
            else:
                self._stt_timings["end_reason"] = "silence"

            # Local Whisper first (no network); fall back to Google STT.
            text = None
            _t2 = time.time()
            if self.stt_engine == "whisper":
                text = self._transcribe_whisper(audio)
            if not text:
                try:
                    text = self._recognizer.recognize_google(audio, language=self.language)
                except sr.UnknownValueError:
                    if self.language and self.language != "en-US":
                        try:
                            text = self._recognizer.recognize_google(audio, language="en-US")
                        except Exception:
                            return None, "unrecognized"
                    else:
                        return None, "unrecognized"

            _t3 = time.time()
            self._stt_timings["transcription"] = _t3 - _t2
            if text and text.strip():
                normalized = normalize_voice_command(text.strip())
                return normalized, "ok"
            return None, "unrecognized"

        except sr.WaitTimeoutError:
            return None, "timeout"
        except sr.UnknownValueError:
            return None, "unrecognized"
        except Exception as e:
            return None, f"error: {str(e)}"

    def create_barge_in_monitor(self) -> Optional["VoiceBargeInMonitor"]:
        """Creates a barge-in audio energy monitor calibrated to current room acoustics."""
        if not self._recognizer or not self._microphone:
            return None
        # Safe threshold: well above the measured noise floor and above TARS's own
        # playback level, so the assistant does not interrupt itself.
        thresh = max(300, int(self._recognizer.energy_threshold * 1.5))
        return VoiceBargeInMonitor(
            self._recognizer,
            self._microphone,
            energy_threshold=thresh,
            grace_period=0.3,
        )


class VoiceBargeInMonitor:
    """
    Lightweight audio energy monitor that listens to the microphone
    during TTS speech playback to trigger instant voice barge-in.

    A grace period at the start of playback ignores TARS's own speaker output,
    and sustained speech is required before a barge-in is accepted, so the
    monitor does not fire on its own voice or on a single stray sample.
    """

    def __init__(
        self,
        recognizer: Any,
        microphone: Any,
        energy_threshold: int = 900,
        grace_period: float = 0.8,
        required_frames: int = 2,
    ):
        self.recognizer = recognizer
        self.microphone = microphone
        self.energy_threshold = energy_threshold
        self.grace_period = grace_period
        self.required_frames = required_frames
        self._stop_event = threading.Event()
        self._triggered = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._started_at = 0.0

    def start(self) -> None:
        """Starts background monitoring."""
        if not self.microphone or not self.recognizer:
            return
        self._stop_event.clear()
        self._triggered.clear()
        self._started_at = time.time()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stops background monitoring."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.15)

    def is_triggered(self) -> bool:
        """Returns True if user voice energy exceeded threshold."""
        return self._triggered.is_set()

    def _run(self) -> None:
        try:
            with self.microphone as source:
                consecutive = 0
                while not self._stop_event.is_set():
                    try:
                        raw = source.stream.read(source.CHUNK)
                        if not raw:
                            continue
                        # Ignore everything recorded while TARS's own voice is
                        # still ramping up at the start of playback.
                        if (time.time() - self._started_at) < self.grace_period:
                            consecutive = 0
                            continue
                        count = len(raw) // 2
                        shorts = struct.unpack(f"{count}h", raw)
                        sum_squares = sum(s * s for s in shorts)
                        rms = int(math.sqrt(sum_squares / max(1, count)))
                        if rms >= self.energy_threshold:
                            consecutive += 1
                            # Sustained speech only: a single loud frame is not a
                            # person talking.
                            if consecutive >= self.required_frames:
                                self._triggered.set()
                                break
                        else:
                            consecutive = max(0, consecutive - 1)
                    except Exception:
                        break
        except Exception:
            pass
