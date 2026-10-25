"""
speech_to_text.py
Push-to-talk speech recognition powered primarily by AMD Ryzen AI XDNA2 NPU
(`whisper-v3:turbo` via FastFlowLM `/v1/audio/transcriptions` with `--asr 1`),
running with zero CPU/GPU contention.
Automatically falls back to `faster-whisper` on CPU if installed and NPU ASR
is unavailable.
"""

from __future__ import annotations

import io
import json
import os
import threading
import time
import urllib.error
import urllib.request
import uuid
import wave
from typing import Any

import numpy as np

try:
    import sounddevice as sd
except ImportError:
    sd = None  # type: ignore[assignment]

try:
    import soundfile as sf  # noqa: F401
except ImportError:
    sf = None  # type: ignore[assignment]

from capri.ai.xdna2_engine import (
    DEFAULT_ASR_MODEL,
    DEFAULT_FLM_HOST,
    DEFAULT_FLM_PORT,
    get_engine,
)

SAMPLE_RATE = 16_000
CHANNELS = 1
MODEL_SIZE = "base"  # CPU fallback size for faster-whisper
COMPUTE_TYPE = "int8"
DEVICE = "cpu"
MIN_SECONDS = 0.3
SILENCE_DB = -40

DEFAULT_FLM_URL = os.environ.get(
    "CAPRI_FLM_URL", f"http://{DEFAULT_FLM_HOST}:{DEFAULT_FLM_PORT}"
).rstrip("/")


def audio_to_wav_bytes(audio: np.ndarray, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Convert a float32 mono numpy array in [-1.0, 1.0] to 16-bit PCM WAV bytes."""
    clipped = np.clip(audio.flatten(), -1.0, 1.0)
    pcm16 = (clipped * 32767.0).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)  # 16-bit PCM
        wf.setframerate(sample_rate)
        wf.writeframes(pcm16.tobytes())
    return buf.getvalue()


class SpeechToText:
    """
    Push-to-talk Speech-to-Text engine.
    Primary backend: AMD XDNA2 NPU (`whisper-v3:turbo` via FastFlowLM `/v1/audio/transcriptions`).
    Fallback backend: `faster-whisper` on CPU.
    """

    def __init__(
        self,
        flm_url: str = DEFAULT_FLM_URL,
        asr_model: str = DEFAULT_ASR_MODEL,
        prefer_npu: bool = True,
    ) -> None:
        self._flm_url: str = flm_url.rstrip("/")
        self.asr_model: str = asr_model
        self.prefer_npu: bool = prefer_npu

        self._npu_ready: bool = False
        self._model: Any = None  # faster_whisper.WhisperModel instance if loaded
        self._backend: str = "initializing"
        self.last_latency_ms: float = 0.0

        self._frames: list[np.ndarray] = []
        self._recording: bool = False
        self._lock = threading.Lock()
        self._stream: Any = None

        threading.Thread(target=self._load_model, daemon=True).start()

    @property
    def backend(self) -> str:
        return self._backend

    def is_ready(self) -> bool:
        return sd is not None and (self._npu_ready or self._model is not None)

    def start_recording(self) -> None:
        if sd is None:
            print("[STT] sounddevice / PortAudio not available; cannot record microphone audio.")
            return
        with self._lock:
            if self._recording:
                return
            self._frames = []
            self._recording = True
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="float32",
            callback=self._audio_callback,
        )
        self._stream.start()

    def stop_and_transcribe(self) -> str:
        with self._lock:
            self._recording = False
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if not self._frames:
            return ""
        audio = np.concatenate(self._frames, axis=0).flatten()
        if len(audio) < SAMPLE_RATE * MIN_SECONDS:
            return ""
        rms_db = 20 * np.log10(np.sqrt(np.mean(audio**2)) + 1e-9)
        if rms_db < SILENCE_DB:
            print(f"[STT] Too quiet ({rms_db:.1f} dB), skipping.")
            return ""
        return self._transcribe(audio)

    def transcribe_audio(self, audio: np.ndarray) -> str:
        """Public helper to transcribe a pre-recorded 16kHz float32 mono numpy array."""
        return self._transcribe(audio)

    def _load_model(self) -> None:
        # 1. Primary: Check AMD XDNA2 NPU Whisper via FastFlowLM (`flm`)
        if self.prefer_npu:
            try:
                engine = get_engine(auto_start=False)
                if engine.is_asr_installed(self.asr_model) or engine.is_server_running():
                    if not engine.is_server_running():
                        engine.start_server(wait=True, timeout=18.0)
                    self._npu_ready = True
                    self._backend = "xdna2_npu"
                    print(
                        f"[STT] AMD XDNA2 NPU Whisper ({self.asr_model}) ready "
                        f"at {self._flm_url}/v1/audio/transcriptions."
                    )
                    return
            except Exception as exc:
                print(f"[STT] XDNA2 NPU ASR check failed ({exc}), checking CPU fallback...")

        # 2. Fallback: faster-whisper on CPU
        self._load_faster_whisper()

    def _load_faster_whisper(self) -> bool:
        if self._model is not None:
            return True
        try:
            from faster_whisper import WhisperModel

            print(f"[STT] Loading faster-whisper ({MODEL_SIZE}) on {DEVICE}...")
            self._model = WhisperModel(
                MODEL_SIZE, device=DEVICE, compute_type=COMPUTE_TYPE
            )
            self._backend = "faster_whisper"
            print("[STT] faster-whisper ready.")
            return True
        except ImportError:
            self._backend = "unavailable"
            print(
                "[STT] Neither XDNA2 NPU Whisper nor faster-whisper is available. "
                "Run `flm pull whisper-v3:turbo` or `pip install faster-whisper`."
            )
            return False
        except Exception as exc:
            self._backend = "unavailable"
            print(f"[STT] Failed to load faster-whisper: {exc}")
            return False

    def _audio_callback(self, indata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:
        with self._lock:
            if self._recording:
                self._frames.append(indata.copy())

    def _transcribe(self, audio: np.ndarray) -> str:
        t0 = time.perf_counter()
        if self._npu_ready:
            try:
                text = self._transcribe_npu(audio)
                self.last_latency_ms = round((time.perf_counter() - t0) * 1000.0, 1)
                if text:
                    print(
                        f"[STT][XDNA2 NPU] Heard ({self.last_latency_ms:.0f} ms): {text!r}"
                    )
                return text
            except Exception as exc:
                print(f"[STT] XDNA2 NPU transcription error ({exc}); trying CPU fallback.")
                if not self._load_faster_whisper():
                    return ""

        if self._model is None:
            return ""

        text = self._transcribe_faster_whisper(audio)
        self.last_latency_ms = round((time.perf_counter() - t0) * 1000.0, 1)
        if text:
            print(f"[STT][CPU] Heard ({self.last_latency_ms:.0f} ms): {text!r}")
        return text

    def _transcribe_npu(self, audio: np.ndarray) -> str:
        """
        Send 16kHz PCM WAV audio to FastFlowLM `/v1/audio/transcriptions`
        running `whisper-v3:turbo` on the AMD XDNA2 NPU.
        """
        engine = get_engine(auto_start=False)
        if not engine.is_server_running(timeout=1.0):
            engine.start_server(wait=True, timeout=15.0)

        wav_bytes = audio_to_wav_bytes(audio, sample_rate=SAMPLE_RATE)
        boundary = f"----CapriXDNA2Boundary{uuid.uuid4().hex}"

        parts: list[bytes] = [
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="file"; filename="speech.wav"\r\n'
                f"Content-Type: audio/wav\r\n\r\n"
            ).encode("utf-8"),
            wav_bytes,
            (
                f"\r\n--{boundary}\r\n"
                f'Content-Disposition: form-data; name="model"\r\n\r\n'
                f"{self.asr_model}\r\n"
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="language"\r\n\r\n'
                f"en\r\n"
                f"--{boundary}--\r\n"
            ).encode("utf-8"),
        ]
        body = b"".join(parts)

        req = urllib.request.Request(
            f"{self._flm_url}/v1/audio/transcriptions",
            data=body,
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Content-Length": str(len(body)),
            },
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=25) as resp:
            raw = resp.read().decode("utf-8", errors="replace").strip()

        try:
            obj = json.loads(raw)
            if isinstance(obj, dict):
                return str(obj.get("text", "")).strip()
        except json.JSONDecodeError:
            return raw.strip()

        return ""

    def _transcribe_faster_whisper(self, audio: np.ndarray) -> str:
        segments, _ = self._model.transcribe(
            audio,
            language="en",
            beam_size=5,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 300},
        )
        return " ".join(seg.text.strip() for seg in segments).strip()
