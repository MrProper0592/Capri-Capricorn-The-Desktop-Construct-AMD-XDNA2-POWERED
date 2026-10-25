"""
text_to_speech.py
Speaks text using Piper TTS (local binary on Arch Linux / CachyOS, no cloud).
Native Linux path resolution:
  1. `PIPER_EXE` / `VOICE_MODEL` environment variables
  2. `shutil.which("piper")` or `shutil.which("piper-tts")`
  3. Standard Arch Linux / Linux (`~/.local/share/piper/`, `/usr/share/piper-voices/`, `/opt/piper/`) fallback locations.
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Callable

try:
    import sounddevice as sd
except ImportError:
    sd = None  # type: ignore[assignment]

try:
    import soundfile as sf
except ImportError:
    sf = None  # type: ignore[assignment]

DEFAULT_VOICE_NAME = "en_US-libritts-high.onnx"
LINUX_DEFAULT_PIPER = str(Path.home() / ".local" / "share" / "piper" / "piper")
LINUX_DEFAULT_MODEL = str(
    Path.home() / ".local" / "share" / "piper" / "voices" / DEFAULT_VOICE_NAME
)


def resolve_piper_exe() -> str:
    """Resolve the Piper TTS executable on Arch Linux / CachyOS."""
    for env_key in ("PIPER_EXE", "CAPRI_PIPER_EXE"):
        env_val = os.environ.get(env_key)
        if env_val:
            expanded = os.path.expanduser(env_val)
            if os.path.isfile(expanded):
                return expanded
            found = shutil.which(expanded)
            if found:
                return found

    for bin_name in ("piper", "piper-tts"):
        found = shutil.which(bin_name)
        if found:
            return found

    repo_root = Path(__file__).resolve().parents[2]
    candidates = [
        Path.home() / ".local" / "share" / "piper" / "piper",
        Path.home() / ".local" / "bin" / "piper",
        Path("/usr/bin/piper"),
        Path("/usr/bin/piper-tts"),
        Path("/opt/piper/piper"),
        repo_root / "assets" / "voices" / "piper",
        repo_root / "piper" / "piper",
    ]
    for cand in candidates:
        if cand.is_file():
            return str(cand)

    return LINUX_DEFAULT_PIPER


def resolve_voice_model() -> str:
    """Resolve the Piper ONNX voice model path on Arch Linux / CachyOS."""
    for env_key in ("VOICE_MODEL", "PIPER_VOICE_MODEL", "CAPRI_VOICE_MODEL"):
        env_val = os.environ.get(env_key)
        if env_val:
            expanded = os.path.expanduser(env_val)
            if os.path.isfile(expanded):
                return expanded

    repo_root = Path(__file__).resolve().parents[2]
    search_dirs = [
        Path.home() / ".local" / "share" / "piper" / "voices",
        Path.home() / ".local" / "share" / "piper",
        Path("/usr/share/piper-voices"),
        Path("/opt/piper/voices"),
        repo_root / "assets" / "voices",
    ]

    # 1. Look for the preferred voice model (`en_US-libritts-high.onnx`)
    for d in search_dirs:
        preferred = d / DEFAULT_VOICE_NAME
        if preferred.is_file():
            return str(preferred)

    # 2. Look for any *.onnx model in the voice directories
    for d in search_dirs:
        if d.is_dir():
            matches = sorted(glob.glob(str(d / "**" / "*.onnx"), recursive=True))
            if matches:
                return matches[0]

    return LINUX_DEFAULT_MODEL


PIPER_EXE: str = resolve_piper_exe()
VOICE_MODEL: str = resolve_voice_model()


class TextToSpeech:
    """Local neural Text-to-Speech using Piper TTS."""

    def __init__(
        self,
        piper_exe: str | None = None,
        voice_model: str | None = None,
    ) -> None:
        self.piper_exe: str = piper_exe or PIPER_EXE
        self.voice_model: str = voice_model or VOICE_MODEL
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

    def speak(self, text: str, on_done: Callable[[], None] | None = None) -> None:
        """Speak text in background. Interrupts anything currently playing."""
        self.stop()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, args=(text, on_done), daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Interrupt speech immediately."""
        self._stop.set()
        if sd is not None:
            try:
                sd.stop()
            except Exception:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def is_speaking(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def is_available(self) -> bool:
        if sd is None or sf is None:
            return False
        exe_ok = os.path.isfile(self.piper_exe) or bool(shutil.which(self.piper_exe))
        model_ok = os.path.isfile(self.voice_model)
        return exe_ok and model_ok

    def _run(self, text: str, on_done: Callable[[], None] | None) -> None:
        if sd is None or sf is None:
            return
        tmp = None
        try:
            tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            tmp.close()

            proc = subprocess.run(
                [self.piper_exe, "--model", self.voice_model, "--output_file", tmp.name],
                input=text.encode("utf-8"),
                capture_output=True,
                timeout=30,
            )

            if proc.returncode != 0:
                err_msg = proc.stderr.decode("utf-8", errors="replace")
                print(f"[TTS] Piper error: {err_msg}")
                return

            if self._stop.is_set():
                return

            data, sr = sf.read(tmp.name)
            sd.play(data, sr)

            # Poll until done or stopped
            while True:
                stream = sd.get_stream()
                if stream is None or not stream.active:
                    break
                if self._stop.is_set():
                    sd.stop()
                    return
                threading.Event().wait(0.05)

            sd.wait()

        except subprocess.TimeoutExpired:
            print("[TTS] Piper timed out.")
        except Exception as e:
            print(f"[TTS] Error: {e}")
        finally:
            if tmp and os.path.exists(tmp.name):
                try:
                    os.unlink(tmp.name)
                except Exception:
                    pass
            if on_done and not self._stop.is_set():
                on_done()
