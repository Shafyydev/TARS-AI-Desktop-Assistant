def _el_keys():
    keys = []
    raw = os.getenv("ELEVENLABS_API_KEYS", "")
    if raw.strip():
        keys.extend([k.strip() for k in raw.split(",") if k.strip()])
    single = os.getenv("ELEVENLABS_API_KEY", "").strip()
    if single and single not in keys:
        keys.append(single)
    return keys
"""Low-latency streaming neural TTS speaker for TARS with offline SAPI fallback and instant async acknowledgments."""
import asyncio
import hashlib
import json
import math
import os
import re
import random
import sys
import tempfile
import threading
import time
from typing import Optional, Callable, Any


os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"

DEFAULT_FEMALE_VOICE = "en-US-JennyNeural"
_MALE_VOICE_MARKERS = (
    "guyneural", "christopherneural", "ericneural", "ryanneural",
    "andrewneural", "brianneural", "davisneural", "tonyneural",
    "jasonneural", "steffanneural", "brandonneural", "jacobneural",
    "david", "mark", "george", "richard", "james", "daniel",
)
_FEMALE_SAPI_MARKERS = ("zira", "hazel", "susan", "heera", "aria", "jenny", "eva", "catherine", "linda", "female")


def _enforce_female_edge_voice(voice_name: Optional[str]) -> str:
    """Ensures Edge-TTS always uses a female voice, rejecting male presets."""
    clean = (voice_name or "").strip()
    low = clean.casefold()
    if not clean or any(m in low for m in _MALE_VOICE_MARKERS) or re.search(r"\bmale\b", low):
        return DEFAULT_FEMALE_VOICE
    return clean


def _pick_female_sapi_voice(sp_voice: Any) -> Any:
    """Selects a female Windows SAPI voice (e.g. Zira) and avoids male defaults like David."""
    fallback = None
    for v in sp_voice.GetVoices():
        desc = (v.GetDescription() or "").casefold()
        if any(marker in desc for marker in _FEMALE_SAPI_MARKERS):
            return v
        if fallback is None and "david" not in desc and not re.search(r"\bmale\b", desc):
            fallback = v
    return fallback


_EL_EXHAUSTED_KEYS = set()


class VoiceSpeaker:
    """
    Handles text-to-speech synthesis using female Microsoft Neural voices via edge-tts,
    with Windows female SAPI fallback and serialized non-overlapping playback.
    """

    ACKNOWLEDGMENTS = [
        "Affirmative.",
        "Right away.",
        "Executing now.",
        "Understood.",
        "On it.",
        "Acknowledged.",
        "Processing.",
    ]

    def __init__(self, voice_name: str = DEFAULT_FEMALE_VOICE, rate: str = "+0%", pitch: str = "+0Hz", fast_voice: bool = True):
        raw_voice = os.getenv("TARS_VOICE", voice_name)
        self.voice_name = _enforce_female_edge_voice(raw_voice)
        self.rate = os.getenv("TARS_VOICE_RATE", rate)
        self.pitch = os.getenv("TARS_VOICE_PITCH", pitch)
        # Default to fast native Windows SAPI for zero-latency instant responses unless cloud neural voice requested
        force_neural = (os.getenv("TARS_NEURAL_VOICE", "1") in ["1", "true", "True"]) or (not fast_voice) or bool((os.getenv("ELEVENLABS_API_KEY") or "").strip()) or bool((os.getenv("ELEVENLABS_BASE_URL") or "").strip()) or bool((os.getenv("VOICESTUDIO_URL") or "").strip())
        self.fast_voice = not force_neural
        self._is_speaking = False
        self._stop_requested = False
        self._sapi_speaker = None
        self._sapi_local = threading.local()
        self._pygame_initialized = False
        self._lock = threading.Lock()
        self._seq_lock = threading.Lock()
        self._utterance_seq = 0
        self._warm_phrases: dict[str, str] = {}
        self._speech_envelope: list[float] = []
        self._speech_fps: float = 30.0
        self._speech_start_perf: float = 0.0
        self._speech_backend: Optional[str] = None
        self._audio_active: bool = False
        self._elevenlabs_disabled: bool = False

        # Auto-launch VoiceStudio if configured and not already running.
        _base = (os.getenv("ELEVENLABS_BASE_URL") or "").strip()
        if _base and ("127.0.0.1" in _base or "localhost" in _base):
            try:
                _ensure_voicestudio_running(_base)
            except Exception:
                pass

        # Pre-warm audio subsystem at startup
        self._init_pygame()

    @property
    def is_speaking(self) -> bool:
        """Returns True if audio is currently playing."""
        return self._is_speaking

    @staticmethod
    def _extract_audio_envelope(audio_path: str, fps: float = 30.0) -> list[float]:
        """Decodes MP3 waveform RMS amplitude frames normalized to [0.0, 1.0]."""
        env_path = audio_path + ".env.json"
        if os.path.exists(env_path):
            try:
                with open(env_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list) and data:
                    return [float(x) for x in data]
            except Exception:
                pass
        try:
            import av
            import numpy as np

            container = av.open(audio_path)
            chunks = []
            sr = 24000
            for frame in container.decode(audio=0):
                sr = frame.sample_rate or sr
                arr = frame.to_ndarray()
                if arr.ndim > 1:
                    arr = arr.mean(axis=0)
                chunks.append(arr.astype(np.float32))
            container.close()
            if not chunks:
                return []
            samples = np.concatenate(chunks)
            step = max(1, int(sr / fps))
            usable = (len(samples) // step) * step
            if usable <= 0:
                return []
            blocks = samples[:usable].reshape(-1, step)
            rms = np.sqrt(np.mean(blocks * blocks, axis=1))
            peak = float(np.max(rms)) if len(rms) else 0.0
            if peak <= 1e-7:
                return [0.05] * len(rms)
            norm = np.clip(rms / peak, 0.0, 1.0) ** 0.65
            return [round(float(v), 3) for v in norm]
        except Exception:
            return []

    def current_voice_state(self) -> dict[str, Any]:
        """Returns real-time hardware playback status and normalized loudness [0.0..1.0]."""
        if not self._is_speaking or self._stop_requested or self._speech_backend is None:
            return {"speaking": False, "level": 0.0}

        if self._speech_backend == "sapi":
            sp = getattr(self._sapi_local, "speaker", None) or self._sapi_speaker
            if sp is not None:
                try:
                    running = (sp.Status.RunningState == 2)
                except Exception:
                    running = self._audio_active
                if running:
                    if not self._audio_active:
                        self._speech_start_perf = time.perf_counter()
                        self._audio_active = True
                    elap = max(0.0, time.perf_counter() - self._speech_start_perf)
                    cadence = 0.56 + 0.28 * abs(math.sin(elap * 11.5)) + 0.16 * abs(math.sin(elap * 23.0))
                    return {"speaking": True, "level": round(min(1.0, max(0.18, cadence)), 3)}
                elif self._audio_active:
                    self._is_speaking = False
                    self._audio_active = False
                    self._speech_backend = None
            return {"speaking": False, "level": 0.0}

        if not self._audio_active:
            return {"speaking": False, "level": 0.0}

        elap = max(0.0, time.perf_counter() - self._speech_start_perf) if self._speech_start_perf else 0.0
        env = self._speech_envelope
        if self._speech_backend == "pygame" and env:
            pos_ms = -1
            if self._pygame_initialized:
                try:
                    import pygame
                    pos_ms = pygame.mixer.music.get_pos()
                except Exception:
                    pos_ms = -1
            t_sec = (pos_ms / 1000.0) if pos_ms >= 0 else elap
            idx = min(len(env) - 1, max(0, int(t_sec * self._speech_fps)))
            return {"speaking": True, "level": round(float(env[idx]), 3)}

        cadence = 0.52 + 0.30 * abs(math.sin(elap * 12.0)) + 0.18 * abs(math.sin(elap * 24.5))
        return {"speaking": True, "level": round(min(1.0, max(0.18, cadence)), 3)}

    def _synthesize_voicestudio(self, text: str, output_path: str) -> bool:
        """Synthesizes text via VoiceStudio's OpenAI-compatible local TTS API."""
        base_url = (os.getenv("VOICESTUDIO_URL") or os.getenv("ELEVENLABS_BASE_URL") or "").strip().rstrip("/")
        if not base_url:
            return False
        # Normalize: strip /elevenlabs suffix if present (VoiceStudio uses /v1/audio/speech).
        if base_url.endswith("/elevenlabs"):
            base_url = base_url[:-len("/elevenlabs")]
        url = base_url + "/v1/audio/speech"
        voice = (os.getenv("VOICESTUDIO_VOICE") or os.getenv("ELEVENLABS_VOICE_ID") or "default").strip()
        model = os.getenv("VOICESTUDIO_MODEL", "omnivoice")
        payload = {"model": model, "input": text, "voice": voice}
        try:
            import requests
            resp = requests.post(url, json=payload, timeout=30.0)
            if resp.status_code == 200 and resp.content:
                with open(output_path, "wb") as f:
                    f.write(resp.content)
                if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                    return True
            else:
                print(f"[VoiceSpeaker] VoiceStudio failed: HTTP {resp.status_code} {resp.text[:120]}")
        except Exception as exc:
            print(f"[VoiceSpeaker] VoiceStudio failed ({type(exc).__name__}); falling back to Edge-TTS/SAPI.")
        return False

    def _synthesize_to_file(self, speech_text: str, output_path: str) -> bool:
        """Synthesizes speech_text to output_path using ElevenLabs or Edge-TTS."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        ok = False
        try:
            _base = (os.getenv("ELEVENLABS_BASE_URL") or "").strip()
            _vs_url = (os.getenv("VOICESTUDIO_URL") or "").strip()
            if _vs_url or ("127.0.0.1" in _base or "localhost" in _base):
                ok = self._synthesize_voicestudio(speech_text, output_path)
            if not ok and ((os.getenv("ELEVENLABS_API_KEY") or "").strip()):
                ok = self._synthesize_elevenlabs(speech_text, output_path)
            if not ok:
                ok = loop.run_until_complete(self._synthesize_edge_tts(speech_text, output_path))
        finally:
            try:
                loop.close()
            except Exception:
                pass
        return bool(ok and os.path.exists(output_path) and os.path.getsize(output_path) > 0)

    def warmup(self, phrase: Optional[str] = None) -> None:
        """Pre-warms SAPI COM or caches a static phrase MP3 ahead of synchronized playback."""
        if self.fast_voice:
            if sys.platform == "win32":
                try:
                    self._get_thread_sapi_speaker()
                except Exception:
                    pass
            return

        if not phrase:
            return
        speech_text = self._clean_text_for_speech(phrase)
        if not speech_text:
            return
        try:
            cache_dir = os.path.join(tempfile.gettempdir(), "tars_tts_cache")
            os.makedirs(cache_dir, exist_ok=True)
            el_key = (os.getenv("ELEVENLABS_API_KEY") or "").strip()
            el_voice = os.getenv("ELEVENLABS_VOICE_ID", "EXAVITQu4vr4xnSDxMaL") if el_key else ""
            mode = "elevenlabs" if el_key else "edge"
            sig = f"{mode}|{self.voice_name}|{self.rate}|{self.pitch}|{el_voice}|{speech_text}"
            digest = hashlib.sha256(sig.encode("utf-8")).hexdigest()[:20]
            cache_path = os.path.join(cache_dir, f"{digest}.mp3")
            if os.path.exists(cache_path) and os.path.getsize(cache_path) > 0:
                self._warm_phrases[speech_text] = cache_path
                env_path = cache_path + ".env.json"
                if not os.path.exists(env_path):
                    env = self._extract_audio_envelope(cache_path, fps=self._speech_fps)
                    if env:
                        try:
                            with open(env_path, "w", encoding="utf-8") as f:
                                json.dump(env, f)
                        except Exception:
                            pass
                return
            tmp_path = cache_path + f".{os.getpid()}.tmp"
            if self._synthesize_to_file(speech_text, tmp_path):
                os.replace(tmp_path, cache_path)
                self._warm_phrases[speech_text] = cache_path
                env = self._extract_audio_envelope(cache_path, fps=self._speech_fps)
                if env:
                    try:
                        with open(cache_path + ".env.json", "w", encoding="utf-8") as f:
                            json.dump(env, f)
                    except Exception:
                        pass
            elif os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
        except Exception:
            pass

    def _init_pygame(self) -> bool:
        """Initializes or recovers pygame mixer for reliable playback."""
        try:
            import pygame
            if not self._pygame_initialized or not pygame.mixer.get_init():
                try:
                    pygame.mixer.init(buffer=512)
                except Exception:
                    pygame.mixer.init()
                self._pygame_initialized = True
            return True
        except Exception:
            self._pygame_initialized = False
            return False

    def _clean_text_for_speech(self, text: str) -> str:
        """Strips markdown, emojis, codeblocks, and raw URLs for crisp speech delivery."""
        # Sanitize technical API errors so TARS never speaks raw JSON or stack traces aloud
        if any(err_kw in text for err_kw in ["{'error':", "RESOURCE_EXHAUSTED", "429", "Traceback (most recent"]):
            return "API rate limit reached on the free tier. Please pause for a few seconds or use direct system commands."

        clean = re.sub(r"```[\s\S]*?```", "", text)
        clean = re.sub(r"`([^`]+)`", r"\1", clean)
        clean = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", clean)
        clean = re.sub(r"https?://\S+", "a web link", clean)
        clean = re.sub(r"[*_~#>]", "", clean)
        # Drop non-BMP emojis / variation selectors so Edge-TTS won't read emoji names or choke on symbols
        clean = re.sub(r"[\U00010000-\U0010ffff\u2600-\u27bf\ufe0f\u200d]+", " ", clean)
        clean = re.sub(r"\s+", " ", clean).strip()
        clean = re.sub(r"\s+([.,!?;:])", r"\1", clean)
        if not re.search(r"[A-Za-z0-9]", clean):
            return ""
        return clean

    async def _synthesize_edge_tts(self, text: str, output_path: str) -> bool:
        """Synthesizes text into an MP3 file using edge-tts with 1 automatic transient retry."""
        import edge_tts

        last_err = None
        for attempt in range(2):
            try:
                communicate = edge_tts.Communicate(
                    text=text,
                    voice=self.voice_name,
                    rate=self.rate,
                    pitch=self.pitch
                )
                await asyncio.wait_for(communicate.save(output_path), timeout=8.0)
                if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                    return True
            except Exception as exc:
                last_err = exc
                if attempt == 0:
                    await asyncio.sleep(0.15)
        if last_err is not None:
            print(f"[VoiceSpeaker] Edge-TTS failed ({type(last_err).__name__}); falling back to Zira.")
        return False

    def _synthesize_elevenlabs(self, text: str, output_path: str) -> bool:
        """Synthesizes text via ElevenLabs REST API, rotating API keys on quota/auth failure."""
        if getattr(self, "_elevenlabs_disabled", False):
            return False
        base_url = (os.getenv("ELEVENLABS_BASE_URL") or "https://api.elevenlabs.io").rstrip("/")
        # VoiceStudio runs locally and needs no API key; allow empty key for local URLs.
        is_local = ("127.0.0.1" in base_url or "localhost" in base_url)
        voice_id = (os.getenv("ELEVENLABS_VOICE_ID") or "EXAVITQu4vr4xnSDxMaL").strip()
        url = f"{base_url}/v1/text-to-speech/{voice_id}"
        payload = {
            "text": text,
            "model_id": os.getenv("ELEVENLABS_MODEL", "eleven_turbo_v2_5"),
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
        }
        keys = _el_keys() if not is_local else [""]
        if not keys and not is_local:
            return False
        if not keys:
            keys = [""]
        for api_key in keys:
            headers = {"Content-Type": "application/json", "Accept": "audio/mpeg"}
            if api_key:
                headers["xi-api-key"] = api_key
            try:
                import requests
                resp = requests.post(url, json=payload, headers=headers, timeout=15.0)
                if resp.status_code == 200 and resp.content:
                    with open(output_path, "wb") as f:
                        f.write(resp.content)
                    if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                        return True
                else:
                    msg = (resp.text or "")[:120]
                    print(f"[VoiceSpeaker] ElevenLabs failed: HTTP {resp.status_code} {msg}")
                    if resp.status_code in (401, 402, 403, 429) or "quota" in (resp.text or "").lower():
                        if api_key:
                            _EL_EXHAUSTED_KEYS.add(api_key)
                        # If all keys exhausted, disable for session.
                        if all(k in _EL_EXHAUSTED_KEYS for k in keys if k):
                            self._elevenlabs_disabled = True
                            print("[VoiceSpeaker] All ElevenLabs keys exhausted; falling back to Edge-TTS/SAPI.")
                            return False
                        continue
            except Exception as exc:
                print(f"[VoiceSpeaker] ElevenLabs failed ({type(exc).__name__}); trying next key.")
                continue
        return False

    def _play_audio_pygame(
        self,
        audio_file: str,
        interrupt_check: Optional[Callable[[], bool]] = None,
        utterance_id: Optional[int] = None,
        check_keyboard: bool = False,
    ) -> bool:
        """Plays the generated audio file using pygame with mixer recovery & unload cleanup."""
        if not self._init_pygame():
            return False

        import pygame
        self._audio_active = False
        self._speech_envelope = self._extract_audio_envelope(audio_file, fps=self._speech_fps)
        self._speech_backend = "pygame"
        for attempt in range(2):
            try:
                if self._stop_requested or (utterance_id is not None and utterance_id != self._utterance_seq):
                    return False
                pygame.mixer.music.load(audio_file)
                pygame.mixer.music.play()
                # Wait briefly until SDL output cursor advances (>0ms) so UI syncs to real audio output
                wait_deadline = time.perf_counter() + 0.15
                while pygame.mixer.music.get_busy() and self._is_speaking and not self._stop_requested:
                    if pygame.mixer.music.get_pos() > 0:
                        break
                    if time.perf_counter() >= wait_deadline:
                        break
                    time.sleep(0.008)
                if not self._stop_requested and self._is_speaking:
                    self._speech_start_perf = time.perf_counter()
                    self._audio_active = True
                while pygame.mixer.music.get_busy() and self._is_speaking and not self._stop_requested:
                    if utterance_id is not None and utterance_id != self._utterance_seq:
                        pygame.mixer.music.stop()
                        return False
                    if check_keyboard:
                        try:
                            import msvcrt
                            if msvcrt.kbhit():
                                msvcrt.getch()
                                self.stop()
                                return False
                        except Exception:
                            pass
                    if interrupt_check:
                        try:
                            if interrupt_check():
                                self.stop()
                                return False
                        except Exception:
                            pass
                    pygame.time.Clock().tick(30)
                return True
            except Exception as exc:
                if attempt == 0:
                    try:
                        pygame.mixer.quit()
                    except Exception:
                        pass
                    self._pygame_initialized = False
                    if not self._init_pygame():
                        print(f"[VoiceSpeaker] pygame recovery failed ({type(exc).__name__}); falling back to Zira.")
                        return False
                else:
                    print(f"[VoiceSpeaker] pygame playback failed ({type(exc).__name__}); falling back to Zira.")
                    return False
            finally:
                self._audio_active = False
                self._speech_backend = None
                self._speech_envelope = []
                try:
                    pygame.mixer.music.unload()
                except Exception:
                    pass
        return False

    def _get_thread_sapi_speaker(self) -> Any:
        """Returns a thread-local COM SAPI SpVoice locked to a female voice."""
        import win32com.client
        import pythoncom

        pythoncom.CoInitialize()
        sp = getattr(self._sapi_local, "speaker", None)
        if sp is None:
            sp = win32com.client.Dispatch("SAPI.SpVoice")
            female_voice = _pick_female_sapi_voice(sp)
            if female_voice is not None:
                sp.Voice = female_voice
            sp.Rate = 1
            self._sapi_local.speaker = sp
        self._sapi_speaker = sp
        return sp

    def _speak_sapi(self, text: str, async_mode: bool = True) -> bool:
        """Zero-latency offline female speech using Windows native SAPI (Zira)."""
        try:
            sp = self._get_thread_sapi_speaker()
            # Flag 1: SVSFlagsAsync (non-blocking), Flag 2: SVSFPurgeBeforeSpeak
            flags = (1 | 2) if async_mode else 0
            self._audio_active = False
            self._speech_backend = "sapi"
            self._speech_start_perf = time.perf_counter()
            sp.Speak(text, flags)
            return True
        except Exception:
            try:
                import subprocess
                escaped = text.replace("'", "''").replace('"', '`"')
                cmd = (
                    "Add-Type -AssemblyName System.Speech; "
                    "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                    "$v = $s.GetInstalledVoices() | Where-Object { "
                    "$_.VoiceInfo.Gender -eq 'Female' -or $_.VoiceInfo.Name -match 'Zira|Hazel|Jenny|Aria' "
                    "} | Select-Object -First 1; "
                    "if ($v) { $s.SelectVoice($v.VoiceInfo.Name) } else { "
                    "$s.SelectVoiceByHints([System.Speech.Synthesis.VoiceGender]::Female) }; "
                    f"$s.Speak('{escaped}')"
                )
                subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, timeout=6)
                return True
            except Exception:
                return False

    def wait_while_speaking(
        self,
        interrupt_check: Optional[Callable[[], bool]] = None,
        poll_interval: float = 0.015,
        backend: Optional[str] = None,
    ) -> bool:
        """
        Monitors speech playback in real time (<15ms poll rate).
        Interruption triggers:
        1. Any key pressed in the console (msvcrt.kbhit).
        2. Voice barge-in callback returning True.
        3. self.stop() called.
        
        Returns True if interrupted, False if finished naturally.
        """
        use_sapi = (backend == "sapi") or (backend is None and self.fast_voice)
        active_sp = getattr(self._sapi_local, "speaker", None) or self._sapi_speaker
        # Wait briefly for SAPI async speech to enter RunningState == 2
        t0 = time.time()
        while use_sapi and active_sp:
            try:
                if active_sp.Status.RunningState == 2:
                    break
            except Exception:
                break
            if time.time() - t0 >= 0.12:
                break
            try:
                import msvcrt
                if msvcrt.kbhit():
                    msvcrt.getch()
                    self.stop()
                    return True
            except Exception:
                pass
            if interrupt_check:
                try:
                    if interrupt_check():
                        self.stop()
                        return True
                except Exception:
                    pass
            time.sleep(0.01)

        interrupted = False
        while self._is_speaking and not self._stop_requested:
            # 1. SAPI state check
            if use_sapi and active_sp:
                try:
                    if active_sp.Status.RunningState != 2:
                        break
                except Exception:
                    break

            # 2. Pygame state check
            if not use_sapi and self._pygame_initialized:
                try:
                    import pygame
                    if not pygame.mixer.music.get_busy():
                        break
                except Exception:
                    break

            # 3. Instant Keyboard Interruption Check (any key in console)
            try:
                import msvcrt
                if msvcrt.kbhit():
                    msvcrt.getch()
                    self.stop()
                    interrupted = True
                    break
            except Exception:
                pass

            # 4. Voice Barge-In Check
            if interrupt_check:
                try:
                    if interrupt_check():
                        self.stop()
                        interrupted = True
                        break
                except Exception:
                    pass

            time.sleep(poll_interval)

        self._is_speaking = False
        return interrupted

    def speak_ack(self) -> None:
        """Speaks a short female acknowledgment that automatically yields to real replies."""
        ack = random.choice(self.ACKNOWLEDGMENTS)
        if self.fast_voice:
            self.speak(ack, blocking=False)
        else:
            self.speak(ack, blocking=False)

    @staticmethod
    def _drain_keyboard() -> None:
        """
        Discards keystrokes typed before playback.

        The key that submitted a command (usually Enter) is still in the console
        buffer when TARS starts speaking, and the playback monitor treats any
        pending key as an interrupt, which produced a false "speech interrupted".
        """
        try:
            import msvcrt
            while msvcrt.kbhit():
                msvcrt.getch()
        except Exception:
            pass

    def _begin_utterance(self) -> int:
        """Stops any active/queued speech and issues a fresh monotonic utterance id."""
        with self._seq_lock:
            self._utterance_seq += 1
            uid = self._utterance_seq
        self.stop(reset_seq=False)
        return uid

    def speak(
        self,
        text: str,
        blocking: bool = False,
        listener: Optional[Any] = None
    ) -> bool:
        """
        Synthesizes and speaks text aloud in TARS's single female voice with
        serialized non-overlapping playback and instant interruption support.
        """
        if not text or not text.strip():
            return False

        speech_text = self._clean_text_for_speech(text)
        if not speech_text:
            return False

        utterance_id = self._begin_utterance()
        self._drain_keyboard()

        # Fast native Windows SAPI mode (female Zira only)
        if self.fast_voice:
            with self._lock:
                if utterance_id != self._utterance_seq:
                    return False
                self._stop_requested = False
                self._is_speaking = True
                self._speak_sapi(speech_text, async_mode=True)
                if blocking:
                    monitor = listener.create_barge_in_monitor() if listener and hasattr(listener, "create_barge_in_monitor") else None
                    if monitor:
                        monitor.start()
                    try:
                        check_fn = monitor.is_triggered if monitor else None
                        return self.wait_while_speaking(interrupt_check=check_fn, backend="sapi")
                    finally:
                        if monitor:
                            monitor.stop()
                return False

        # Neural edge-tts path (female JennyNeural, female Zira fallback)
        interrupted = False

        def _worker():
            nonlocal interrupted
            with self._lock:
                if utterance_id != self._utterance_seq:
                    return
                self._stop_requested = False
                self._is_speaking = True
                temp_audio = None
                monitor = None
                try:
                    warm_path = self._warm_phrases.get(speech_text)
                    edge_success = False
                    audio_path = None
                    if warm_path and os.path.exists(warm_path) and os.path.getsize(warm_path) > 0:
                        audio_path = warm_path
                        edge_success = True
                    else:
                        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tf:
                            temp_audio = tf.name
                        edge_success = self._synthesize_to_file(speech_text, temp_audio)
                        audio_path = temp_audio

                    if self._stop_requested or utterance_id != self._utterance_seq:
                        return

                    use_barge_in = (
                        blocking
                        and listener is not None
                        and hasattr(listener, "create_barge_in_monitor")
                        and os.getenv("TARS_ACOUSTIC_BARGE_IN") == "1"
                    )
                    if use_barge_in:
                        monitor = listener.create_barge_in_monitor()
                        if monitor:
                            monitor.start()
                    check_fn = monitor.is_triggered if monitor else None

                    if edge_success and audio_path and os.path.exists(audio_path) and os.path.getsize(audio_path) > 0:
                        played = self._play_audio_pygame(
                            audio_path,
                            interrupt_check=check_fn,
                            utterance_id=utterance_id,
                            check_keyboard=blocking,
                        )
                        if not played:
                            if self._stop_requested and utterance_id == self._utterance_seq:
                                interrupted = True
                            elif not self._stop_requested and utterance_id == self._utterance_seq:
                                self._speak_sapi(speech_text, async_mode=True)
                                interrupted = self.wait_while_speaking(interrupt_check=check_fn, backend="sapi")
                    elif not self._stop_requested and utterance_id == self._utterance_seq:
                        self._speak_sapi(speech_text, async_mode=True)
                        interrupted = self.wait_while_speaking(interrupt_check=check_fn, backend="sapi")

                except Exception:
                    if not self._stop_requested and utterance_id == self._utterance_seq:
                        self._speak_sapi(speech_text, async_mode=True)
                        interrupted = self.wait_while_speaking(backend="sapi")
                finally:
                    if monitor:
                        monitor.stop()
                    self._is_speaking = False
                    if temp_audio and os.path.exists(temp_audio):
                        try:
                            os.remove(temp_audio)
                        except Exception:
                            pass

        if blocking:
            _worker()
            return interrupted
        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        return False

    def stop(self, reset_seq: bool = True) -> None:
        """Stops currently playing audio immediately (<1ms) and cancels queued speech."""
        if reset_seq:
            with self._seq_lock:
                self._utterance_seq += 1
        self._is_speaking = False
        self._stop_requested = True
        self._audio_active = False
        self._speech_backend = None
        self._speech_envelope = []
        local_sp = getattr(self._sapi_local, "speaker", None)
        speakers = (local_sp,) if self._sapi_speaker is local_sp else (local_sp, self._sapi_speaker)
        for sp in speakers:
            if sp is not None:
                try:
                    # SVSFPurgeBeforeSpeak = 2 purges all speech instantly
                    sp.Speak("", 2)
                except Exception:
                    pass
        if self._pygame_initialized:
            try:
                import pygame
                pygame.mixer.music.stop()
            except Exception:
                pass


def _ensure_voicestudio_running(base_url: str) -> bool:
    """Best-effort auto-launch of VoiceStudio if its local server isn't up yet."""
    import socket
    import time
    # Extract host/port from base_url
    try:
        from urllib.parse import urlparse
        parsed = urlparse(base_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 3900
    except Exception:
        host, port = "127.0.0.1", 3900

    def _port_open():
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return True
        except Exception:
            return False

    if _port_open():
        return True

    # Try common install locations.
    import os
    candidates = [
        r"C:\Program Files\VoiceStudio\VoiceStudio.exe",
        r"C:\Program Files (x86)\VoiceStudio\VoiceStudio.exe",
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "VoiceStudio", "VoiceStudio.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "VoiceStudio", "VoiceStudio.exe"),
        os.path.join(os.environ.get("ProgramFiles", ""), "VoiceStudio", "VoiceStudio.exe"),
    ]
    for path in candidates:
        if path and os.path.exists(path):
            try:
                import subprocess
                subprocess.Popen([path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                break
            except Exception:
                continue
    else:
        return False

    # Wait up to 15s for the server to come up.
    for _ in range(30):
        if _port_open():
            return True
        time.sleep(0.5)
    return False
