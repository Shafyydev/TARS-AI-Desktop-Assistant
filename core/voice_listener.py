"""High-accuracy, locale-aware voice capture and speech recognition listener for TARS."""
import io
import os
import time
import re
import locale
import threading
import struct
import math
import wave
from typing import Optional, Tuple, Callable, Any

from core.wake_word import WakeWordDetector


_WHISPER_HALLUCINATIONS = frozenset({
    "thank you",
    "thank you.",
    "thanks for watching",
    "thanks for watching.",
    "you",
    "you.",
    "bye",
    "bye.",
    "...",
    "mbc",
    "amara.org",
})


def _audio_speech_stats(audio: Any) -> Tuple[float, float]:
    """Computes (duration_sec, peak_window_rms) on 16kHz mono PCM in <1ms."""
    try:
        raw = audio.get_raw_data(convert_rate=16000, convert_width=2)
    except Exception:
        return 1.0, 9999.0
    if not isinstance(raw, (bytes, bytearray)) or len(raw) < 4:
        # Mock objects in unit tests may return non-byte sentinels.
        return (1.0, 9999.0) if not isinstance(raw, (bytes, bytearray)) else (0.0, 0.0)
    duration = len(raw) / float(16000 * 2)
    # 80ms windows = 1280 samples = 2560 bytes
    step = 2560
    peak_rms = 0.0
    for off in range(0, len(raw) - 1, step):
        chunk = raw[off:off + step]
        cnt = len(chunk) // 2
        if cnt <= 0:
            continue
        shorts = struct.unpack(f"<{cnt}h", chunk[:cnt * 2])
        rms = math.sqrt(sum(s * s for s in shorts) / cnt)
        if rms > peak_rms:
            peak_rms = rms
    return duration, peak_rms


def _is_whisper_hallucination(text: str, peak_rms: float, threshold: float, duration: float) -> bool:
    """True when Whisper returns a stock silence hallucination on marginal audio."""
    clean = (text or "").strip().casefold()
    if clean not in _WHISPER_HALLUCINATIONS:
        return False
    return duration < 1.3 and peak_rms < max(350.0, threshold * 1.4)


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
        pause_threshold: float = 0.35,
        language: Optional[str] = None,
        mic_index: Optional[int] = None,
        stt_engine: Optional[str] = None,
    ):
        self.energy_threshold = int(os.getenv("TARS_STT_ENERGY", str(energy_threshold)))
        self.pause_threshold = float(os.getenv("TARS_STT_PAUSE", str(pause_threshold)))
        self._min_energy_floor = float(max(120, self.energy_threshold))
        self.language = language or os.getenv("TARS_STT_LANGUAGE") or detect_system_stt_language()
        
        env_mic = os.getenv("TARS_MIC_INDEX")
        self.mic_index = mic_index if mic_index is not None else (int(env_mic) if env_mic else None)

        self._recognizer = None
        self._microphone = None
        self._stream_open = False
        self._whisper_model = None
        self._fw_model = None
        self._whisper_failed = False
        self._http_session = None
        self._groq_cooldown_until = 0.0
        self.stt_engine = (stt_engine or os.getenv("TARS_STT_ENGINE") or "auto").strip().lower()
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
            self._recognizer.non_speaking_duration = min(0.15, max(0.05, self.pause_threshold * 0.5))
            self._recognizer.phrase_threshold = min(0.12, max(0.05, self.pause_threshold * 0.4))
            # Prevent network timeouts on longer spoken sentences
            self._recognizer.operation_timeout = 3.0
            # Smooth dynamic adaptation to ambient room sound
            self._recognizer.dynamic_energy_threshold = True
            self._recognizer.dynamic_energy_adjustment_damping = 0.2
            self._recognizer.dynamic_energy_ratio = 1.6

            self._microphone = sr.Microphone(device_index=self.mic_index)
        except Exception as e:
            print(f"[Listener] Warning: Microphone init failed: {e}")
            self._recognizer = None
            self._microphone = None

    def _is_real_mic(self) -> bool:
        """True when self._microphone is a real SpeechRecognition Microphone instance."""
        return (
            self._microphone is not None
            and getattr(type(self._microphone), "__module__", "").startswith("speech_recognition")
        )

    def _open_stream(self) -> Any:
        """Lazily opens the underlying PyAudio stream once and reuses it across idle waits."""
        if not self._is_real_mic():
            return self._microphone
        if not self._stream_open or getattr(self._microphone, "stream", None) is None:
            self._microphone.__enter__()
            self._stream_open = True
        return self._microphone

    def close_stream(self) -> None:
        """Closes any persistent PyAudio microphone stream cleanly."""
        if self._stream_open and self._microphone is not None:
            try:
                self._microphone.__exit__(None, None, None)
            except Exception:
                try:
                    setattr(self._microphone, "stream", None)
                except Exception:
                    pass
            finally:
                self._stream_open = False

    def __del__(self) -> None:
        try:
            self.close_stream()
        except Exception:
            pass

    def calibrate(self, duration: float = 0.45) -> float:
        """Calibrates microphone for ambient acoustics while skipping initial stream pops."""
        if self._recognizer and self._microphone:
            self.close_stream()
            try:
                with self._microphone as source:
                    # Drain ~150ms of hardware-open DC transient before measuring floor.
                    chunk = getattr(source, "CHUNK", None)
                    rate = getattr(source, "SAMPLE_RATE", None)
                    if isinstance(chunk, int) and isinstance(rate, int) and chunk > 0 and rate > 0:
                        drain_reads = max(0, min(5, int((rate / chunk) * 0.15)))
                        for _ in range(drain_reads):
                            try:
                                source.stream.read(chunk)
                            except Exception:
                                break
                    self._recognizer.adjust_for_ambient_noise(source, duration=max(0.25, min(float(duration), 0.5)))
                floor = max(180.0, min(1800.0, float(self._recognizer.energy_threshold) * 1.1))
                self._min_energy_floor = floor
                self.energy_threshold = int(floor)
                self._recognizer.energy_threshold = floor
                self._recognizer.dynamic_energy_threshold = True
                return floor
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


    def _get_http_session(self) -> Any:
        if self._http_session is None:
            import requests
            self._http_session = requests.Session()
        return self._http_session

    def _transcribe_groq_whisper(self, audio: Any) -> Tuple[Optional[str], bool]:
        """Transcribes 16kHz WAV audio via Groq Whisper (~250ms warm).

        Returns (text, reachable) where ``reachable`` is True on HTTP 200 so a
        clean empty/noise response does not cascade into multi-second fallbacks.
        """
        if time.time() < self._groq_cooldown_until:
            return None, False
        api_key = (os.getenv("GROQ_API_KEY") or "").strip()
        if not api_key or api_key.startswith("your_"):
            return None, False
        try:
            raw16 = audio.get_raw_data(convert_rate=16000, convert_width=2)
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(raw16)
            model = os.getenv("TARS_GROQ_STT_MODEL", "whisper-large-v3-turbo").strip()
            sess = self._get_http_session()
            resp = sess.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {api_key}"},
                files={"file": ("cmd.wav", buf.getvalue(), "audio/wav")},
                data={
                    "model": model,
                    "language": (self.language or "en").split("-")[0],
                    "temperature": "0",
                    "prompt": "TARS, WhatsApp, Amma, Reshma, Reshmaa, Shafyy, Chrome, Brave, YouTube, Gmail.",
                    "response_format": "json",
                },
                timeout=3.5,
            )
            if resp.status_code == 200:
                payload = resp.json() if hasattr(resp, "json") else {}
                text = (payload.get("text") or "").strip()
                return (text or None), True
            if resp.status_code in (401, 403, 429):
                self._groq_cooldown_until = time.time() + 60.0
            return None, False
        except Exception:
            self._groq_cooldown_until = time.time() + 15.0
            return None, False

    def _transcribe_whisper(self, audio) -> Optional[str]:
        """
        Transcribes a speech_recognition.AudioData object locally.

        Uses faster-whisper with greedy decoding (beam_size=1) and VAD filtering
        for low CPU latency, falling back to openai-whisper.
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
                        self.whisper_model_name,
                        device="cpu",
                        compute_type="int8",
                        cpu_threads=min(4, os.cpu_count() or 2),
                    )
                segments, _ = self._fw_model.transcribe(
                    samples,
                    beam_size=1,
                    vad_filter=True,
                    language="en",
                    condition_on_previous_text=False,
                )
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

    def _transcribe_google(self, audio: Any, duration: float, peak_rms: float) -> Optional[str]:
        """Runs Google Web Speech on 16kHz downsampled audio without blind dual-lang noise stalls."""
        import speech_recognition as sr

        target_audio = audio
        try:
            if getattr(audio, "sample_rate", 16000) != 16000:
                raw16 = audio.get_raw_data(convert_rate=16000, convert_width=2)
                if isinstance(raw16, (bytes, bytearray)):
                    target_audio = sr.AudioData(raw16, 16000, 2)
        except Exception:
            target_audio = audio

        try:
            return self._recognizer.recognize_google(target_audio, language=self.language)
        except sr.UnknownValueError:
            retry_dual = (
                os.getenv("TARS_STT_DUAL_LANG") == "1"
                or (duration >= 1.0 and peak_rms >= max(350.0, self._min_energy_floor * 1.8))
            )
            if retry_dual and self.language and self.language != "en-US":
                try:
                    return self._recognizer.recognize_google(target_audio, language="en-US")
                except Exception:
                    return None
            return None

    def listen_once(
        self,
        timeout: int = 8,
        phrase_time_limit: Optional[int] = 10,
        on_listening_start: Optional[Callable[[], None]] = None
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Listens to the microphone with persistent stream reuse, acoustic gating,
        and low-latency STT backend selection (Groq Whisper -> faster-whisper -> Google).
        """
        if not self._recognizer or not self._microphone:
            return None, "Microphone not initialized"

        import speech_recognition as sr

        self._stt_timings = {
            "wait_and_record": 0.0,
            "transcription": 0.0,
            "end_reason": "unknown",
            "engine": self.stt_engine,
        }
        try:
            if on_listening_start:
                on_listening_start()

            # Prevent dynamic_energy_threshold from drifting down to near-zero in quiet moments.
            try:
                cur_thresh = float(self._recognizer.energy_threshold)
                clamped_thresh = max(self._min_energy_floor, min(1800.0, cur_thresh))
                self._recognizer.energy_threshold = clamped_thresh
                self.energy_threshold = int(clamped_thresh)
            except Exception:
                clamped_thresh = float(self.energy_threshold)

            _t0 = time.time()
            if self._is_real_mic():
                source = self._open_stream()
                audio = self._recognizer.listen(
                    source,
                    timeout=timeout,
                    phrase_time_limit=phrase_time_limit,
                )
                # Close once a phrase was captured so TTS / tool execution never fills a stale ring buffer.
                self.close_stream()
            else:
                with self._microphone as source:
                    audio = self._recognizer.listen(
                        source,
                        timeout=timeout,
                        phrase_time_limit=phrase_time_limit,
                    )
            _t1 = time.time()
            self._stt_timings["wait_and_record"] = _t1 - _t0
            duration, peak_rms = _audio_speech_stats(audio)
            if phrase_time_limit and duration >= phrase_time_limit - 0.5:
                self._stt_timings["end_reason"] = "phrase_time_limit"
            elif timeout and (_t1 - _t0) >= timeout - 0.1 and duration < 0.25:
                self._stt_timings["end_reason"] = "timeout"
            else:
                self._stt_timings["end_reason"] = "silence"

            if duration < 0.22 or peak_rms < max(110.0, clamped_thresh * 0.72):
                self._stt_timings["end_reason"] = "noise_gated"
                return None, "timeout"

            text = None
            used_engine = self.stt_engine
            _t2 = time.time()
            if self.stt_engine in ("auto", "groq"):
                text, groq_ok = self._transcribe_groq_whisper(audio)
                if groq_ok:
                    used_engine = "groq"
                    if text and _is_whisper_hallucination(text, peak_rms, clamped_thresh, duration):
                        text = None
                else:
                    if self.stt_engine == "auto":
                        text = self._transcribe_whisper(audio)
                        if text:
                            used_engine = "whisper"
                        if text and _is_whisper_hallucination(text, peak_rms, clamped_thresh, duration):
                            text = None
                    if not text:
                        text = self._transcribe_google(audio, duration, peak_rms)
                        if text:
                            used_engine = "google"
            elif self.stt_engine == "whisper":
                text = self._transcribe_whisper(audio)
                if text and _is_whisper_hallucination(text, peak_rms, clamped_thresh, duration):
                    text = None
                if text:
                    used_engine = "whisper"
                else:
                    text = self._transcribe_google(audio, duration, peak_rms)
                    if text:
                        used_engine = "google"
            else:
                text = self._transcribe_google(audio, duration, peak_rms)
                used_engine = "google"

            _t3 = time.time()
            self._stt_timings["transcription"] = _t3 - _t2
            self._stt_timings["engine"] = used_engine
            if text and text.strip():
                normalized = normalize_voice_command(text.strip())
                return normalized, "ok"
            return None, "unrecognized"

        except sr.WaitTimeoutError:
            return None, "timeout"
        except sr.UnknownValueError:
            self.close_stream()
            return None, "unrecognized"
        except Exception as e:
            self.close_stream()
            return None, f"error: {str(e)}"

    def create_barge_in_monitor(
        self,
        ref_audio: Any = None,
        pos_fn: Optional[Callable[[], float]] = None,
    ) -> Optional["VoiceBargeInMonitor"]:
        """Creates an echo-aware barge-in monitor calibrated to current room acoustics."""
        if not self._recognizer or not self._microphone:
            return None
        self.close_stream()
        # Baseline floor above quiet room noise; echo subtraction + Silero VAD handle active speaker bleed.
        thresh = max(260, int(self._recognizer.energy_threshold * 1.35))
        return VoiceBargeInMonitor(
            self._recognizer,
            self._microphone,
            energy_threshold=thresh,
            grace_period=0.35,
            required_frames=3,
            ref_audio=ref_audio,
            pos_fn=pos_fn,
        )


class VoiceBargeInMonitor:
    """
    Echo-aware voice barge-in monitor that detects user speech over TARS's own
    speaker playback using playback-reference envelope gating and Silero VAD.
    """

    def __init__(
        self,
        recognizer: Any,
        microphone: Any,
        energy_threshold: int = 350,
        grace_period: float = 0.35,
        required_frames: int = 3,
        ref_audio: Any = None,
        pos_fn: Optional[Callable[[], float]] = None,
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
        self._ref_lock = threading.Lock()
        self._ref_audio = ref_audio
        self._pos_fn = pos_fn
        self._echo_gain = 0.35
        self._vad_model = None
        self._vad_checked = False
        self._recent_hits: list = []

    def set_reference_audio(self, ref_audio: Any, pos_fn: Optional[Callable[[], float]] = None) -> None:
        """Updates the active 16kHz playback reference waveform and playback clock."""
        with self._ref_lock:
            self._ref_audio = ref_audio
            self._pos_fn = pos_fn
            self._recent_hits.clear()

    def _get_vad(self) -> Any:
        if not self._vad_checked:
            self._vad_checked = True
            try:
                from faster_whisper import vad
                self._vad_model = vad.get_vad_model()
            except Exception:
                self._vad_model = None
        return self._vad_model

    def start(self) -> None:
        """Starts background monitoring."""
        if not self.microphone or not self.recognizer:
            return
        self._stop_event.clear()
        self._triggered.clear()
        self._recent_hits.clear()
        self._started_at = time.time()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stops background monitoring."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.2)

    def is_triggered(self) -> bool:
        """Returns True if sustained user speech was detected."""
        return self._triggered.is_set()

    def _ref_window_rms(self, pos_ms: Optional[float]) -> Optional[float]:
        """Returns local speaker reference RMS around ``pos_ms`` (covering 180ms echo window)."""
        with self._ref_lock:
            ref = self._ref_audio
        if ref is None or pos_ms is None or pos_ms < 0:
            return None
        try:
            import numpy as np
            center = int(pos_ms * 16.0)
            lo = max(0, center - 2880)  # -180ms speaker->mic latency + room reverb
            hi = min(len(ref), center + 640)
            if hi <= lo:
                return 0.0
            win = ref[lo:hi]
            # Peak 32ms block RMS inside echo window handles transient syllables accurately.
            if len(win) <= 512:
                return float(np.sqrt(np.mean(win * win)))
            trimmed = win[: (len(win) // 512) * 512].reshape(-1, 512)
            return float(np.max(np.sqrt(np.mean(trimmed * trimmed, axis=1))))
        except Exception:
            return None

    def _evaluate_frame(self, mic_16k: Any, pos_ms: Optional[float] = None, use_vad: bool = True) -> bool:
        """Evaluates one 512-sample 16kHz frame; returns True if overlapping user speech occurred."""
        import numpy as np

        mic_rms_f = float(np.sqrt(np.mean(mic_16k * mic_16k)))
        mic_rms_int16 = mic_rms_f * 32768.0
        min_floor = float(max(240, self.energy_threshold))

        ref_rms = self._ref_window_rms(pos_ms)
        vad_prob = None
        if use_vad and mic_rms_int16 >= min_floor * 0.8:
            vad_model = self._get_vad()
            if vad_model is not None:
                try:
                    vad_prob = float(vad_model(mic_16k.astype(np.float32))[0])
                except Exception:
                    vad_prob = None

        if ref_rms is not None:
            if ref_rms > 0.015 and (vad_prob is None or vad_prob < 0.45):
                sample_gain = min(0.65, max(0.18, mic_rms_f / ref_rms))
                self._echo_gain = 0.85 * self._echo_gain + 0.15 * sample_gain
            echo_ceiling = max(min_floor / 32768.0, ref_rms * max(self._echo_gain * 1.25, 0.40))
            if mic_rms_f <= echo_ceiling:
                return False
            if vad_prob is not None and vad_prob < 0.50:
                return False
            return mic_rms_int16 >= min_floor

        # Fallback without reference audio (SAPI / offline): require higher threshold + VAD
        fallback_floor = max(900.0, float(self.energy_threshold) * 2.5)
        if mic_rms_int16 < fallback_floor:
            return False
        if vad_prob is not None and vad_prob < 0.52:
            return False
        return True

    def _run(self) -> None:
        try:
            import numpy as np

            with self.microphone as source:
                rate = getattr(source, "SAMPLE_RATE", 16000)
                chunk = getattr(source, "CHUNK", 1024)
                if not isinstance(rate, int) or rate <= 0:
                    rate = 16000
                if not isinstance(chunk, int) or chunk <= 0:
                    chunk = 1024
                buf16 = np.array([], dtype=np.float32)
                use_echo_model = not isinstance(source, object) or hasattr(source, "SAMPLE_RATE")
                while not self._stop_event.is_set():
                    try:
                        raw = source.stream.read(chunk)
                        if not raw:
                            continue
                        if (time.time() - self._started_at) < self.grace_period:
                            self._recent_hits.clear()
                            continue

                        with self._ref_lock:
                            ref_audio = self._ref_audio
                            pos_fn = self._pos_fn

                        if ref_audio is None or rate == 16000:
                            cnt = len(raw) // 2
                            if cnt <= 0:
                                continue
                            shorts = np.frombuffer(raw[:cnt * 2], dtype=np.int16).astype(np.float32) / 32768.0
                            x16 = shorts
                        else:
                            x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
                            out_len = max(1, int(round(len(x) * 16000.0 / rate)))
                            idx = np.linspace(0, max(0, len(x) - 1), out_len)
                            x16 = np.interp(idx, np.arange(len(x)), x).astype(np.float32)

                        if ref_audio is None and not hasattr(self._microphone, "SAMPLE_RATE"):
                            rms = int(math.sqrt(float(np.mean(x16 * x16))) * 32768.0)
                            hit = rms >= self.energy_threshold
                            self._recent_hits.append(1 if hit else 0)
                            if len(self._recent_hits) > max(4, self.required_frames + 1):
                                self._recent_hits.pop(0)
                            if sum(self._recent_hits) >= self.required_frames:
                                self._triggered.set()
                                break
                            continue

                        buf16 = np.concatenate([buf16, x16])
                        while len(buf16) >= 512 and not self._stop_event.is_set():
                            frame = buf16[:512]
                            buf16 = buf16[512:]
                            pos_ms = None
                            if pos_fn is not None:
                                try:
                                    pos_ms = float(pos_fn())
                                except Exception:
                                    pos_ms = None
                            hit = self._evaluate_frame(frame, pos_ms=pos_ms, use_vad=use_echo_model)
                            self._recent_hits.append(1 if hit else 0)
                            win_len = max(4, self.required_frames + 1)
                            if len(self._recent_hits) > win_len:
                                self._recent_hits.pop(0)
                            if sum(self._recent_hits) >= self.required_frames:
                                self._triggered.set()
                                return
                    except Exception:
                        break
        except Exception:
            pass
