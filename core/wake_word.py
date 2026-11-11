"""Dedicated wake-word detection engine for TARS using Picovoice Porcupine and local acoustic streaming."""
import os
import time
import struct
import logging
from typing import Optional, List, Callable

logger = logging.getLogger("TARS.WakeWord")


class WakeWordDetector:
    """
    Dedicated local wake-word engine.
    Streams raw PCM frames from PyAudio to Picovoice Porcupine without sending
    audio to cloud speech-to-text APIs.
    """

    def __init__(
        self,
        access_key: Optional[str] = None,
        keywords: Optional[List[str]] = None,
        keyword_paths: Optional[List[str]] = None,
        sensitivities: Optional[List[float]] = None,
        mic_index: Optional[int] = None
    ):
        self.access_key = access_key or os.getenv("PICOVOICE_ACCESS_KEY")
        self.keywords = keywords or [os.getenv("TARS_WAKE_KEYWORD", "computer")]
        self.keyword_paths = keyword_paths
        self.sensitivities = sensitivities
        self.mic_index = mic_index or (int(os.getenv("TARS_MIC_INDEX")) if os.getenv("TARS_MIC_INDEX") else None)

        self.porcupine = None
        self.pa = None
        self.audio_stream = None
        self.engine_name = "none"

        self._init_engine()

    def _init_engine(self) -> None:
        """Initializes Porcupine wake word engine if access key is present, else local acoustic fallback."""
        if self.access_key and self.access_key != "your_picovoice_access_key_here":
            try:
                import pvporcupine
                # Verify requested keywords against built-in or keyword paths
                valid_keywords = []
                for kw in self.keywords:
                    if kw.lower() in pvporcupine.KEYWORDS:
                        valid_keywords.append(kw.lower())
                if not valid_keywords and not self.keyword_paths:
                    valid_keywords = ["computer"]

                kwargs = {"access_key": self.access_key}
                if self.keyword_paths:
                    kwargs["keyword_paths"] = self.keyword_paths
                else:
                    kwargs["keywords"] = valid_keywords

                if self.sensitivities:
                    kwargs["sensitivities"] = self.sensitivities

                self.porcupine = pvporcupine.create(**kwargs)
                self.engine_name = "porcupine"
                logger.info(f"Porcupine wake-word engine initialized with keywords: {valid_keywords or self.keyword_paths}")
                return
            except Exception as e:
                logger.warning(f"Porcupine initialization failed: {e}. Falling back to acoustic VAD.")

        self.porcupine = None
        self.engine_name = "acoustic_vad"

    def is_porcupine_active(self) -> bool:
        """Returns True if the dedicated Porcupine engine is active."""
        return self.porcupine is not None

    def start_stream(self) -> bool:
        """Opens PyAudio input stream matching engine sample rate and frame size."""
        if self.audio_stream is not None:
            return True

        try:
            import pyaudio
            self.pa = pyaudio.PyAudio()

            sample_rate = self.porcupine.sample_rate if self.porcupine else 16000
            frame_length = self.porcupine.frame_length if self.porcupine else 512

            stream_kwargs = {
                "rate": sample_rate,
                "channels": 1,
                "format": pyaudio.paInt16,
                "input": True,
                "frames_per_buffer": frame_length
            }
            if self.mic_index is not None:
                stream_kwargs["input_device_index"] = self.mic_index

            self.audio_stream = self.pa.open(**stream_kwargs)
            return True
        except Exception as e:
            logger.error(f"Failed to open PyAudio input stream: {e}")
            self.stop_stream()
            return False

    def stop_stream(self) -> None:
        """Closes PyAudio stream."""
        if self.audio_stream is not None:
            try:
                self.audio_stream.stop_stream()
                self.audio_stream.close()
            except Exception:
                pass
            self.audio_stream = None

        if self.pa is not None:
            try:
                self.pa.terminate()
            except Exception:
                pass
            self.pa = None

    def wait_for_wake_word(self, timeout: Optional[float] = None) -> bool:
        """
        Blocks and processes real-time audio frames until a wake word is detected
        or timeout is reached. Returns True if detected, False on timeout or error.
        """
        if not self.start_stream():
            return False

        start_time = time.time()
        frame_length = self.porcupine.frame_length if self.porcupine else 512

        try:
            while True:
                if timeout is not None and (time.time() - start_time) >= timeout:
                    return False

                pcm_bytes = self.audio_stream.read(frame_length, exception_on_overflow=False)
                if not pcm_bytes:
                    continue

                pcm = struct.unpack_from(f"{frame_length}h", pcm_bytes)

                if self.porcupine:
                    result = self.porcupine.process(pcm)
                    if result >= 0:
                        logger.info("Wake word detected by Porcupine engine.")
                        return True
                else:
                    # Acoustic energy threshold detection fallback
                    energy = sum(abs(x) for x in pcm) / len(pcm)
                    if energy > 1200:
                        time.sleep(0.05)
                        return True

        except Exception as e:
            logger.error(f"Error during wake word processing: {e}")
            return False

    def close(self) -> None:
        """Releases audio stream and Porcupine resources."""
        self.stop_stream()
        if self.porcupine is not None:
            try:
                self.porcupine.delete()
            except Exception:
                pass
            self.porcupine = None

