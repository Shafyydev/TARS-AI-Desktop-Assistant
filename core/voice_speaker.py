"""Low-latency streaming neural TTS speaker for TARS with offline SAPI fallback and instant async acknowledgments."""
import asyncio
import os
import re
import random
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
        force_neural = (os.getenv("TARS_NEURAL_VOICE", "1") in ["1", "true", "True"]) or (not fast_voice)
        self.fast_voice = not force_neural
        self._is_speaking = False
        self._stop_requested = False
        self._sapi_speaker = None
        self._sapi_local = threading.local()
        self._pygame_initialized = False
        self._lock = threading.Lock()
        self._seq_lock = threading.Lock()
        self._utterance_seq = 0
        
        # Pre-warm audio subsystem at startup
        self._init_pygame()

    @property
    def is_speaking(self) -> bool:
        """Returns True if audio is currently playing."""
        return self._is_speaking

    def _init_pygame(self) -> bool:
        """Initializes pygame mixer once at startup for instant audio playback."""
        if not self._pygame_initialized:
            try:
                import pygame
                pygame.mixer.init()
                self._pygame_initialized = True
                return True
            except Exception as e:
                return False
        return True

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
        clean = re.sub(r"\s+", " ", clean).strip()
        return clean

    async def _synthesize_edge_tts(self, text: str, output_path: str) -> bool:
        """Synthesizes text into an MP3 file using edge-tts."""
        try:
            import edge_tts
            communicate = edge_tts.Communicate(
                text=text,
                voice=self.voice_name,
                rate=self.rate,
                pitch=self.pitch
            )
            await communicate.save(output_path)
            return True
        except Exception:
            return False

    def _play_audio_pygame(
        self,
        audio_file: str,
        interrupt_check: Optional[Callable[[], bool]] = None,
        utterance_id: Optional[int] = None,
    ) -> bool:
        """Plays the generated audio file using pygame with instant interruption support."""
        if not self._init_pygame():
            return False

        try:
            import pygame
            if self._stop_requested or (utterance_id is not None and utterance_id != self._utterance_seq):
                return False
            pygame.mixer.music.load(audio_file)
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy() and self._is_speaking and not self._stop_requested:
                if utterance_id is not None and utterance_id != self._utterance_seq:
                    pygame.mixer.music.stop()
                    return False
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
        except Exception:
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
                    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tf:
                        temp_audio = tf.name

                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    edge_success = loop.run_until_complete(self._synthesize_edge_tts(speech_text, temp_audio))
                    loop.close()

                    if self._stop_requested or utterance_id != self._utterance_seq:
                        return

                    if blocking and listener and hasattr(listener, "create_barge_in_monitor"):
                        monitor = listener.create_barge_in_monitor()
                        if monitor:
                            monitor.start()
                    check_fn = monitor.is_triggered if monitor else None

                    if edge_success and os.path.exists(temp_audio) and os.path.getsize(temp_audio) > 0:
                        played = self._play_audio_pygame(temp_audio, interrupt_check=check_fn, utterance_id=utterance_id)
                        if not played and not self._stop_requested and utterance_id == self._utterance_seq:
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
        for sp in {getattr(self._sapi_local, "speaker", None), self._sapi_speaker}:
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
