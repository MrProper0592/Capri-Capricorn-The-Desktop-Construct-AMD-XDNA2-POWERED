"""
xdna2_engine.py
AMD Ryzen AI XDNA2 NPU (aie2p) Hardware Detection, Modelfile Parser,
FastFlowLM (`flm serve`) Lifecycle Manager, and Real-Time NPU Telemetry.
"""

from __future__ import annotations

import atexit
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Default FastFlowLM XDNA2 server configuration
DEFAULT_FLM_HOST = os.environ.get("CAPRI_FLM_HOST", "127.0.0.1")
DEFAULT_FLM_PORT = int(os.environ.get("CAPRI_FLM_PORT", "52625"))
DEFAULT_PMODE = os.environ.get("CAPRI_NPU_PMODE", "performance")
DEFAULT_ASR_MODEL = os.environ.get("CAPRI_ASR_MODEL", "whisper-v3:turbo")

# Minimum recommended memlock limit for XDNA2 NPU (1 GiB)
MIN_MEMLOCK_BYTES = 1024 * 1024 * 1024

# Preferred fallback order for NPU2 chat models if primary model isn't installed
FALLBACK_LLM_MODELS = (
    "gemma3:4b",
    "gemma3:1b",
    "qwen3-it:4b",
    "qwen3:4b",
    "llama3.2:3b",
    "llama3.2:1b",
    "qwen3:1.7b",
    "qwen3:0.6b",
)


@dataclass
class ModelfileConfig:
    """Parsed configuration and persona from the repository root Modelfile."""

    base_model: str = "gemma3:4b"
    temperature: float = 0.85
    num_ctx: int = 4096
    repeat_penalty: float = 1.12
    system_prompt: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    modelfile_path: str = ""

    def to_options_dict(self) -> dict[str, Any]:
        """Return Ollama / FastFlowLM compatible generation options."""
        opts: dict[str, Any] = {
            "temperature": self.temperature,
            "num_ctx": self.num_ctx,
            "repeat_penalty": self.repeat_penalty,
        }
        opts.update(self.parameters)
        return opts

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_model": self.base_model,
            "temperature": self.temperature,
            "num_ctx": self.num_ctx,
            "repeat_penalty": self.repeat_penalty,
            "system_prompt": self.system_prompt,
            "parameters": dict(self.parameters),
            "modelfile_path": self.modelfile_path,
        }


def find_modelfile(custom_path: str | os.PathLike | None = None) -> Path:
    """Locate the repository root Modelfile."""
    if custom_path:
        p = Path(custom_path).expanduser().resolve()
        if p.is_file():
            return p

    env_path = os.environ.get("CAPRI_MODELFILE")
    if env_path:
        p = Path(env_path).expanduser().resolve()
        if p.is_file():
            return p

    # Relative to capri/ai/xdna2_engine.py -> repo root
    repo_root = Path(__file__).resolve().parents[2]
    candidate = repo_root / "Modelfile"
    if candidate.is_file():
        return candidate

    cwd_candidate = Path.cwd() / "Modelfile"
    if cwd_candidate.is_file():
        return cwd_candidate

    return candidate


def parse_modelfile(modelfile_path: str | os.PathLike | None = None) -> ModelfileConfig:
    """
    Parse the Ollama-style Modelfile to extract:
      - FROM <model> (default: gemma3:4b)
      - PARAMETER temperature (0.85)
      - PARAMETER num_ctx (4096)
      - PARAMETER repeat_penalty (1.12)
      - SYSTEM \"\"\"...\"\"\" (Capri's full cyberpunk construct persona)
    """
    path = find_modelfile(modelfile_path)
    cfg = ModelfileConfig(modelfile_path=str(path))

    if not path.is_file():
        return cfg

    try:
        raw = path.read_text(encoding="utf-8")
    except Exception as exc:
        print(f"[XDNA2] Warning: Failed to read Modelfile at {path}: {exc}")
        return cfg

    # Extract FROM <model>
    from_match = re.search(r"^\s*FROM\s+(\S+)", raw, flags=re.MULTILINE | re.IGNORECASE)
    if from_match:
        cfg.base_model = from_match.group(1).strip()

    # Extract PARAMETER <key> <value>
    for param_match in re.finditer(
        r"^\s*PARAMETER\s+(\S+)\s+([^\r\n#]+)", raw, flags=re.MULTILINE | re.IGNORECASE
    ):
        key = param_match.group(1).strip().lower()
        val_str = param_match.group(2).strip()
        val: int | float | str
        try:
            if "." in val_str:
                val = float(val_str)
            else:
                val = int(val_str)
        except ValueError:
            val = val_str

        cfg.parameters[key] = val
        if key == "temperature" and isinstance(val, (int, float)):
            cfg.temperature = float(val)
        elif key == "num_ctx" and isinstance(val, (int, float)):
            cfg.num_ctx = int(val)
        elif key == "repeat_penalty" and isinstance(val, (int, float)):
            cfg.repeat_penalty = float(val)

    # Extract SYSTEM """...""" or SYSTEM "..."
    sys_triple = re.search(
        r'SYSTEM\s+"""(.*?)"""', raw, flags=re.DOTALL | re.IGNORECASE
    )
    if sys_triple:
        cfg.system_prompt = sys_triple.group(1).strip()
    else:
        sys_single = re.search(
            r'^\s*SYSTEM\s+"([^"]+)"', raw, flags=re.MULTILINE | re.IGNORECASE
        )
        if sys_single:
            cfg.system_prompt = sys_single.group(1).strip()

    return cfg


def check_memlock_limit(raise_if_low: bool = True) -> dict[str, Any]:
    """
    Check RLIMIT_MEMLOCK on Arch Linux / CachyOS (required by amdxdna / XDNA2 NPU pinned buffers).
    Attempts to raise soft limit to hard limit if below 1 GiB, and warns clearly
    if the effective limit remains below 1 GiB.
    """
    try:
        import resource

        soft, hard = resource.getrlimit(resource.RLIMIT_MEMLOCK)
        rlim_inf = getattr(resource, "RLIM_INFINITY", -1)

        def _is_sufficient(limit_val: int) -> bool:
            return limit_val == rlim_inf or limit_val < 0 or limit_val >= MIN_MEMLOCK_BYTES

        if not _is_sufficient(soft) and raise_if_low:
            # Attempt to raise soft limit to hard limit
            target = hard if (hard == rlim_inf or hard < 0 or hard > soft) else soft
            if target != soft:
                try:
                    resource.setrlimit(resource.RLIMIT_MEMLOCK, (target, hard))
                    soft, hard = resource.getrlimit(resource.RLIMIT_MEMLOCK)
                except (ValueError, OSError):
                    pass

        ok = _is_sufficient(soft)
        if soft == rlim_inf or soft < 0:
            human = "infinity"
        else:
            human = f"{soft / (1024 * 1024):.1f} MiB"

        warning = None
        if not ok:
            warning = (
                f"[XDNA2] WARNING: RLIMIT_MEMLOCK is {human} (< 1 GiB). "
                "AMD XDNA2 NPU requires at least 1 GB locked memory (or 'unlimited'). "
                "Run `ulimit -l unlimited` or add `* hard memlock unlimited` to /etc/security/limits.conf."
            )
            print(warning)

        return {
            "ok": ok,
            "soft": soft,
            "hard": hard,
            "human": human,
            "warning": warning,
        }
    except Exception as exc:
        return {
            "ok": True,
            "soft": -1,
            "hard": -1,
            "human": "unknown",
            "warning": f"Could not inspect RLIMIT_MEMLOCK: {exc}",
        }


class XDNA2Engine:
    """
    Manages the AMD Ryzen AI XDNA2 NPU stack (FastFlowLM `flm serve` + XRT/amdxdna),
    Modelfile parsing, model discovery, subprocess lifecycle, and live telemetry.
    """

    def __init__(
        self,
        model: str | None = None,
        port: int = DEFAULT_FLM_PORT,
        host: str = DEFAULT_FLM_HOST,
        pmode: str = DEFAULT_PMODE,
        ctx_len: int | None = None,
        enable_asr: bool | None = None,
        auto_start: bool = False,
        auto_pull: bool = False,
        modelfile_path: str | os.PathLike | None = None,
    ) -> None:
        self.modelfile: ModelfileConfig = parse_modelfile(modelfile_path)
        self.host: str = host
        self.port: int = int(port)
        self.pmode: str = pmode
        self.ctx_len: int = int(ctx_len if ctx_len is not None else self.modelfile.num_ctx)
        self.requested_model: str = (
            model or os.environ.get("CAPRI_MODEL") or self.modelfile.base_model or "gemma3:4b"
        )
        self.auto_pull: bool = auto_pull
        self._enable_asr_pref: bool | None = enable_asr

        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._external_server: bool = False
        self._active_model: str = self.requested_model
        self._asr_enabled: bool = False
        self._hw_cache: dict[str, Any] | None = None
        self._installed_cache: tuple[float, list[dict[str, Any]]] | None = None

        # Check memlock on initialization
        self._memlock_info = check_memlock_limit(raise_if_low=True)

        atexit.register(self.stop_server)

        if auto_start:
            self.start_server(wait=True)

    # ── Properties ────────────────────────────────────────────────────

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def system_prompt(self) -> str:
        return self.modelfile.system_prompt

    @property
    def parameters(self) -> dict[str, Any]:
        return self.modelfile.to_options_dict()

    @property
    def active_model(self) -> str:
        return self._active_model

    @property
    def asr_enabled(self) -> bool:
        return self._asr_enabled

    # ── Hardware & Stack Validation ───────────────────────────────────

    def detect_hardware(self, force_refresh: bool = False) -> dict[str, Any]:
        """
        Inspect `/dev/accel/accel0`, `xrt-smi examine`, `flm validate --json`,
        and `flm version --json` to build a complete XDNA2 hardware profile.
        """
        if self._hw_cache is not None and not force_refresh:
            return dict(self._hw_cache)

        info: dict[str, Any] = {
            "accel_devices": [],
            "device_path": "/dev/accel/accel0",
            "device_present": False,
            "device_name": "AMD Ryzen AI XDNA2 NPU",
            "processor": "",
            "bdf": "",
            "platform": "aie2p",
            "columns": 8,
            "firmware_version": "",
            "driver_version": "",
            "xrt_version": "",
            "flm_version": "",
            "flm_path": shutil.which("flm") or "",
            "xrt_smi_path": shutil.which("xrt-smi") or "",
            "memlock_limit": self._memlock_info.get("human", "unknown"),
            "memlock_ok": self._memlock_info.get("ok", True),
            "ready": False,
        }

        # 1. Check /dev/accel/accel* on Arch Linux / CachyOS
        accel_devs = sorted(glob.glob("/dev/accel/accel*"))
        info["accel_devices"] = accel_devs
        if accel_devs:
            info["device_path"] = accel_devs[0]
            info["device_present"] = True

        # 2. Query `flm validate --json`
        flm_bin = info["flm_path"]
        if flm_bin:
            try:
                proc = subprocess.run(
                    [flm_bin, "validate", "--json"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    val = json.loads(proc.stdout.strip())
                    info["platform"] = val.get("npu_platform") or info["platform"]
                    info["driver_version"] = val.get("drm_version") or info["driver_version"]
                    info["memlock_limit"] = val.get("memlock_limit") or info["memlock_limit"]
                    info["memlock_ok"] = bool(val.get("memlock_ok", info["memlock_ok"]))
                    info["ready"] = bool(val.get("ready", False))
                    info["device_present"] = bool(
                        val.get("amd_device_found", info["device_present"])
                    )
                    devs = val.get("devices") or []
                    if devs and isinstance(devs[0], dict):
                        d0 = devs[0]
                        info["device_path"] = d0.get("device") or info["device_path"]
                        info["columns"] = int(d0.get("cols") or info["columns"])
                        fw_parts = [
                            d0.get("fw_major"),
                            d0.get("fw_minor"),
                            d0.get("fw_patch"),
                            d0.get("fw_build"),
                        ]
                        if all(x is not None for x in fw_parts):
                            info["firmware_version"] = ".".join(str(x) for x in fw_parts)
            except Exception:
                # Fallback to text parsing of `flm validate`
                try:
                    proc = subprocess.run(
                        [flm_bin, "validate"],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    out = proc.stdout + "\n" + proc.stderr
                    m_cols = re.search(r"(/dev/accel/\S+)\s+with\s+(\d+)\s+columns", out)
                    if m_cols:
                        info["device_path"] = m_cols.group(1)
                        info["columns"] = int(m_cols.group(2))
                        info["device_present"] = True
                    m_fw = re.search(r"NPU FW Version:\s*(\S+)", out)
                    if m_fw:
                        info["firmware_version"] = m_fw.group(1)
                    m_plat = re.search(r"NPU platform:\s*(\S+)", out)
                    if m_plat:
                        info["platform"] = m_plat.group(1)
                    info["ready"] = info["device_present"]
                except Exception:
                    pass

            # Query `flm version --json`
            try:
                vproc = subprocess.run(
                    [flm_bin, "version", "--json"],
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                if vproc.returncode == 0 and vproc.stdout.strip():
                    vdata = json.loads(vproc.stdout.strip())
                    info["flm_version"] = str(vdata.get("version", ""))
            except Exception:
                pass

        # 3. Query `xrt-smi examine` for human-readable NPU name, BDF, processor
        xrt_bin = info["xrt_smi_path"]
        if xrt_bin:
            try:
                xproc = subprocess.run(
                    [xrt_bin, "examine"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                xout = xproc.stdout
                m_cpu = re.search(r"Processor\s*:\s*(.+)", xout)
                if m_cpu:
                    info["processor"] = m_cpu.group(1).strip()
                m_xrt = re.search(r"XRT\s*\n\s*Version\s*:\s*(\S+)", xout)
                if m_xrt:
                    info["xrt_version"] = m_xrt.group(1).strip()
                m_fw = re.search(r"NPU Firmware Version\s*:\s*(\S+)", xout)
                if m_fw and not info["firmware_version"]:
                    info["firmware_version"] = m_fw.group(1).strip()
                # Match table row: |[0000:67:00.1]  |NPU Krackan 1  |
                m_dev = re.search(r"\|\s*\[([0-9a-fA-F:.]+)\]\s*\|\s*([^|]+?)\s*\|", xout)
                if m_dev:
                    info["bdf"] = m_dev.group(1).strip()
                    info["device_name"] = m_dev.group(2).strip()
                    info["device_present"] = True
            except Exception:
                pass

        self._hw_cache = dict(info)
        return info

    def validate_npu_stack(self) -> dict[str, Any]:
        """Validate that the XDNA2 NPU hardware and FastFlowLM stack are ready."""
        hw = self.detect_hardware(force_refresh=True)
        return {
            "valid": bool(hw.get("ready") or hw.get("device_present")),
            "hardware": hw,
            "memlock": self._memlock_info,
        }

    # ── Installed Models Discovery ────────────────────────────────────

    def get_installed_model_entries(self, force_refresh: bool = False) -> list[dict[str, Any]]:
        """Return detailed model entries from `flm list --filter installed --json`."""
        now = time.monotonic()
        if (
            not force_refresh
            and self._installed_cache is not None
            and (now - self._installed_cache[0]) < 15.0
        ):
            return list(self._installed_cache[1])

        flm_bin = shutil.which("flm")
        if not flm_bin:
            return []

        entries: list[dict[str, Any]] = []
        try:
            proc = subprocess.run(
                [flm_bin, "list", "--filter", "installed", "--json"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                data = json.loads(proc.stdout.strip())
                raw_models = data.get("models") or []
                if isinstance(raw_models, list):
                    entries = [m for m in raw_models if isinstance(m, dict)]
        except Exception:
            pass

        if not entries:
            # Fallback to text parsing of `flm list --filter installed`
            try:
                proc = subprocess.run(
                    [flm_bin, "list", "--filter", "installed"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                for line in proc.stdout.splitlines():
                    m = re.search(r"-\s*([a-zA-Z0-9_.:-]+)", line)
                    if m:
                        tag = m.group(1).strip()
                        entries.append(
                            {
                                "name": tag,
                                "model": tag,
                                "installed": True,
                                "asr": "whisper" in tag.lower(),
                            }
                        )
            except Exception:
                pass

        self._installed_cache = (now, entries)
        return list(entries)

    def get_installed_models(self, include_asr: bool = True) -> list[str]:
        """Return list of installed NPU2 model tags (e.g. ['gemma3:4b', 'qwen3:0.6b', 'whisper-v3:turbo'])."""
        entries = self.get_installed_model_entries()
        names: list[str] = []
        for entry in entries:
            name = entry.get("model") or entry.get("name")
            if not name:
                continue
            is_asr = bool(entry.get("asr")) or "whisper" in str(name).lower()
            if not include_asr and is_asr:
                continue
            names.append(str(name))
        return names

    def is_asr_installed(self, asr_model: str = DEFAULT_ASR_MODEL) -> bool:
        """Check whether `whisper-v3:turbo` (or another ASR model) is installed in `flm`."""
        installed = self.get_installed_models(include_asr=True)
        if asr_model in installed:
            return True
        return any("whisper" in m.lower() for m in installed)

    def pull_model(self, model_tag: str, timeout: float = 600.0) -> bool:
        """Download an NPU2 model via `flm pull <model_tag>`."""
        flm_bin = shutil.which("flm")
        if not flm_bin:
            return False
        try:
            print(f"[XDNA2] Pulling NPU2 model {model_tag!r} via FastFlowLM...")
            proc = subprocess.run(
                [flm_bin, "pull", model_tag],
                capture_output=False,
                timeout=timeout,
            )
            self._installed_cache = None
            return proc.returncode == 0
        except Exception as exc:
            print(f"[XDNA2] Error pulling model {model_tag!r}: {exc}")
            return False

    def resolve_model(
        self, requested_model: str | None = None, auto_pull: bool | None = None
    ) -> str:
        """
        Resolve which NPU2 LLM model to serve/use:
        1. If requested_model (default `gemma3:4b` from Modelfile) is installed, return it.
        2. If `auto_pull` is True, attempt `flm pull <requested_model>`.
        3. Otherwise fall back to any installed NPU2 chat model (`qwen3:0.6b`, `llama3.2:1b`, etc.).
        """
        target = requested_model or self.requested_model or "gemma3:4b"
        # Map custom Ollama alias `capri:latest` to Modelfile's base model `gemma3:4b`
        if target.startswith("capri"):
            target = self.modelfile.base_model or "gemma3:4b"

        installed_llms = self.get_installed_models(include_asr=False)
        if target in installed_llms:
            return target

        do_pull = self.auto_pull if auto_pull is None else auto_pull
        if do_pull and self.pull_model(target):
            return target

        for candidate in FALLBACK_LLM_MODELS:
            if candidate in installed_llms:
                print(
                    f"[XDNA2] Requested model {target!r} not installed; "
                    f"falling back to installed NPU2 model {candidate!r}."
                )
                return candidate

        if installed_llms:
            chosen = installed_llms[0]
            print(
                f"[XDNA2] Requested model {target!r} not installed; "
                f"using installed NPU2 model {chosen!r}."
            )
            return chosen

        return target

    # ── FastFlowLM (`flm serve`) Server Lifecycle ─────────────────────

    def get_live_npu_status(self, timeout: float = 1.5) -> dict[str, Any]:
        """Query `http://127.0.0.1:52625/api/npu/status` if `flm serve` is running."""
        url = f"{self.base_url}/api/npu/status"
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    if isinstance(data, dict):
                        return {
                            "running": True,
                            "npu_available": bool(data.get("npu_available", True)),
                            "active_requests": int(data.get("active_requests", 0)),
                            "message": str(data.get("message", "NPU is available")),
                        }
        except Exception:
            pass

        # Secondary check on /api/tags
        try:
            with urllib.request.urlopen(f"{self.base_url}/api/tags", timeout=timeout) as resp:
                if resp.status == 200:
                    return {
                        "running": True,
                        "npu_available": True,
                        "active_requests": 0,
                        "message": "Server active",
                    }
        except Exception:
            pass

        return {
            "running": False,
            "npu_available": False,
            "active_requests": 0,
            "message": "Server offline",
        }

    def is_server_running(self, timeout: float = 1.5) -> bool:
        """Return True if port 52625 is already serving `/api/npu/status` or `/api/tags`."""
        return bool(self.get_live_npu_status(timeout=timeout).get("running"))

    def start_server(
        self,
        model: str | None = None,
        wait: bool = True,
        timeout: float = 20.0,
    ) -> bool:
        """
        Start `flm serve <model> --port <port> --pmode <pmode> --ctx-len <ctx_len> [--asr 1]`
        as a managed background daemon process if a server is not already running on `port`.
        """
        with self._lock:
            resolved_model = self.resolve_model(model or self.requested_model)
            self._active_model = resolved_model

            if self._enable_asr_pref is None:
                self._asr_enabled = self.is_asr_installed()
            else:
                self._asr_enabled = bool(self._enable_asr_pref and self.is_asr_installed())

            # Check if an external or previously started server is already listening
            if self.is_server_running(timeout=1.0):
                if self._proc is None or self._proc.poll() is not None:
                    self._external_server = True
                return True

            # Only spawn a new process if we don't already have a live child starting up
            if self._proc is None or self._proc.poll() is not None:
                flm_bin = shutil.which("flm")
                if not flm_bin:
                    print("[XDNA2] `flm` (FastFlowLM) binary not found in PATH.")
                    return False

                cmd = [
                    flm_bin,
                    "serve",
                    resolved_model,
                    "--host",
                    self.host,
                    "--port",
                    str(self.port),
                    "--pmode",
                    self.pmode,
                    "--ctx-len",
                    str(self.ctx_len),
                ]
                if self._asr_enabled:
                    cmd.extend(["--asr", "1"])

                print(f"[XDNA2] Starting NPU server: {' '.join(cmd)}")
                try:
                    popen_kwargs: dict[str, Any] = {
                        "stdout": subprocess.DEVNULL,
                        "stderr": subprocess.DEVNULL,
                        "stdin": subprocess.DEVNULL,
                        "start_new_session": True,
                    }

                    self._proc = subprocess.Popen(cmd, **popen_kwargs)
                    self._external_server = False
                except Exception as exc:
                    print(f"[XDNA2] Failed to start `flm serve`: {exc}")
                    self._proc = None
                    return False

            if not wait:
                return True

            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if self._proc is not None and self._proc.poll() is not None:
                    print(
                        f"[XDNA2] `flm serve` exited prematurely with code {self._proc.returncode}."
                    )
                    self._proc = None
                    return False
                if self.is_server_running(timeout=1.0):
                    print(
                        f"[XDNA2] FastFlowLM NPU server ready at {self.base_url} "
                        f"(model={self._active_model}, pmode={self.pmode}, asr={self._asr_enabled})"
                    )
                    return True
                time.sleep(0.25)

            print(f"[XDNA2] Timed out waiting for `flm serve` on {self.base_url}.")
            return self.is_server_running(timeout=1.0)

    # Convenience aliases
    start = start_server
    ensure_running = start_server
    ensure_server_running = start_server

    def stop_server(self) -> None:
        """Cleanly terminate the managed `flm serve` subprocess on exit."""
        with self._lock:
            proc = self._proc
            self._proc = None
            if proc is None or self._external_server:
                return
            if proc.poll() is not None:
                return
            try:
                proc.terminate()
                proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                    proc.wait(timeout=2.0)
                except Exception:
                    pass
            except Exception:
                pass

    stop = stop_server

    # ── Telemetry ─────────────────────────────────────────────────────

    def get_npu_telemetry(self) -> dict[str, Any]:
        """
        Return comprehensive AMD XDNA2 NPU telemetry:
        device name (`NPU Krackan 1` / `AMD Ryzen AI XDNA2 NPU`), platform (`aie2p`),
        columns (`8`), firmware version, active model, power mode, and live `/api/npu/status`.
        """
        hw = self.detect_hardware(force_refresh=False)
        live = self.get_live_npu_status(timeout=1.2)
        installed = self.get_installed_models(include_asr=True)

        return {
            "npu_available": bool(live.get("npu_available") or hw.get("device_present")),
            "server_running": bool(live.get("running")),
            "server_url": self.base_url,
            "device_name": hw.get("device_name") or "NPU Krackan 1",
            "processor": hw.get("processor") or "AMD Ryzen AI (XDNA2)",
            "platform": hw.get("platform") or "aie2p",
            "columns": int(hw.get("columns") or 8),
            "device_path": hw.get("device_path") or "/dev/accel/accel0",
            "bdf": hw.get("bdf") or "",
            "firmware_version": hw.get("firmware_version") or "unknown",
            "driver_version": hw.get("driver_version") or "",
            "xrt_version": hw.get("xrt_version") or "",
            "flm_version": hw.get("flm_version") or "",
            "memlock_limit": hw.get("memlock_limit") or "unknown",
            "memlock_ok": bool(hw.get("memlock_ok", True)),
            "active_model": self._active_model,
            "power_mode": self.pmode,
            "ctx_len": self.ctx_len,
            "asr_enabled": self._asr_enabled or self.is_asr_installed(),
            "asr_model": DEFAULT_ASR_MODEL if self.is_asr_installed() else "",
            "active_requests": int(live.get("active_requests", 0)),
            "status_message": str(live.get("message", "")),
            "installed_models": installed,
            "npu_status": live,
        }

    get_telemetry = get_npu_telemetry
    status = get_npu_telemetry

    def get_status_report(self) -> str:
        """Return a formatted cyberpunk terminal telemetry report for the AMD XDNA2 NPU."""
        t = self.get_npu_telemetry()
        models_str = ", ".join(t.get("installed_models") or []) or "none"
        srv_state = "ONLINE" if t.get("server_running") else ("STANDBY (auto-start)" if t.get("npu_available") else "OFFLINE")
        asr_str = f"ENABLED ({t.get('asr_model')})" if t.get("asr_enabled") else "DISABLED"
        lines = [
            "============================================================",
            "  CAPRI // AMD XDNA2 NPU TELEMETRY & SELF-TEST",
            "============================================================",
            f"  Processor  : {t.get('processor')}",
            f"  NPU Device : {t.get('device_name')} [{t.get('bdf') or t.get('device_path')}]",
            f"  Platform   : {t.get('platform')} ({t.get('columns')} AIE2P Columns)",
            f"  Firmware   : {t.get('firmware_version')} (XRT {t.get('xrt_version') or '2.21'} / FLM v{t.get('flm_version') or '1.0'})",
            f"  Memlock    : {t.get('memlock_limit')} ({'OK' if t.get('memlock_ok') else 'LOW - SEE README'})",
            f"  Server     : {srv_state} @ {t.get('server_url')}",
            f"  LLM Model  : {t.get('active_model')} (ctx={t.get('ctx_len')}, pmode={t.get('power_mode')})",
            f"  NPU ASR    : {asr_str}",
            f"  Installed  : {models_str}",
            "============================================================",
        ]
        return "\n".join(lines)



# ── Module-Level Singleton & Helpers ──────────────────────────────────

_ENGINE_SINGLETON: XDNA2Engine | None = None
_ENGINE_LOCK = threading.Lock()


def get_engine(auto_start: bool = False, **kwargs: Any) -> XDNA2Engine:
    """Get or create the shared XDNA2Engine singleton."""
    global _ENGINE_SINGLETON
    with _ENGINE_LOCK:
        if _ENGINE_SINGLETON is None:
            _ENGINE_SINGLETON = XDNA2Engine(auto_start=auto_start, **kwargs)
        elif auto_start and not _ENGINE_SINGLETON.is_server_running():
            _ENGINE_SINGLETON.start_server(wait=True)
        return _ENGINE_SINGLETON


def get_npu_telemetry() -> dict[str, Any]:
    """Convenience function returning live NPU telemetry from the shared engine."""
    return get_engine(auto_start=False).get_npu_telemetry()
