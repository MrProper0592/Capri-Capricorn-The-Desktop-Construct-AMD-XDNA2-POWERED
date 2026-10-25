"""
main.py — Entry point for Capri (AMD XDNA2 NPU-Powered Desktop Construct).
Arch Linux / CachyOS native desktop companion with automated dependency safety-net,
CLI flags for NPU backend, model, power mode, port, auto-serve, and headless
NPU telemetry verification (--check-npu).
"""

from __future__ import annotations

import argparse
import importlib.util
import inspect
import os
import shutil
import subprocess
import sys

REQUIRED_PACKAGES: tuple[tuple[str, str], ...] = (
    ("PySide6", "PySide6>=6.6.0"),
    ("numpy", "numpy>=1.24.0"),
    ("requests", "requests>=2.31.0"),
    ("sounddevice", "sounddevice>=0.4.6"),
    ("soundfile", "soundfile>=0.12.1"),
    ("pynput", "pynput>=1.7.6"),
)


def ensure_dependencies(check_only_core: bool = False) -> bool:
    """
    Python-level dependency safety-net executed before importing GUI or voice modules.
    If required packages (`PySide6`, `numpy`, `requests`, `sounddevice`, `soundfile`, `pynput`)
    are missing when running `python -m capri.main` directly, automatically attempts to
    bootstrap them from `requirements.txt` via `pip` or `uv pip`, or prints a clean
    actionable Arch Linux / CachyOS diagnostic instead of a raw ModuleNotFoundError traceback.
    """
    core_modules = {"PySide6", "numpy", "requests"}
    missing_mods: list[str] = []
    missing_specs: list[str] = []

    for mod_name, pkg_spec in REQUIRED_PACKAGES:
        if check_only_core and mod_name not in core_modules:
            continue
        if importlib.util.find_spec(mod_name) is None:
            missing_mods.append(mod_name)
            missing_specs.append(pkg_spec)

    if not missing_mods:
        return True

    here = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(here)
    req_file = os.path.join(repo_root, "requirements.txt")

    print(
        f"[Safety-Net] Missing Python dependencies detected: {', '.join(missing_mods)}"
    )
    print("[Safety-Net] Attempting automatic dependency bootstrap...")

    install_cmds: list[list[str]] = []
    if os.path.isfile(req_file):
        install_cmds.append([sys.executable, "-m", "pip", "install", "-r", req_file])
    else:
        install_cmds.append([sys.executable, "-m", "pip", "install", *missing_specs])

    uv_bin = shutil.which("uv")
    if uv_bin:
        if os.path.isfile(req_file):
            install_cmds.append(
                [uv_bin, "pip", "install", "--python", sys.executable, "-r", req_file]
            )
        else:
            install_cmds.append(
                [uv_bin, "pip", "install", "--python", sys.executable, *missing_specs]
            )

    installed = False
    for cmd in install_cmds:
        try:
            proc = subprocess.run(cmd, check=False)
            if proc.returncode == 0:
                installed = True
                break
        except Exception:
            continue

    still_missing_core = [
        mod
        for mod in missing_mods
        if mod in core_modules and importlib.util.find_spec(mod) is None
    ]
    if still_missing_core:
        print(
            "\n============================================================\n"
            "  CAPRI // ARCH LINUX & CACHYOS DEPENDENCY SAFETY-NET\n"
            "============================================================\n"
            f"  Missing core Python modules: {', '.join(still_missing_core)}\n"
            "  Automatic pip install could not modify this Python environment\n"
            "  (PEP 668 externally-managed system Python).\n\n"
            "  Recommended fix — launch via the self-healing Arch/CachyOS script:\n"
            "    ./run_xdna2.sh\n\n"
            "  Or install inside the project virtual environment:\n"
            "    python3 -m venv .venv && .venv/bin/pip install -r requirements.txt\n"
            "    .venv/bin/python -m capri.main\n"
            "============================================================",
            file=sys.stderr,
        )
        return False

    return installed or True


from capri.ai import model_client as _mc


def get_assets_dir() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    return os.path.join(root, "assets", "character")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="capri",
        description="Capri — AMD XDNA2 NPU-Powered Desktop Construct",
    )
    parser.add_argument(
        "--backend",
        choices=["auto", "xdna2", "ollama", "openai"],
        default=os.environ.get("CAPRI_BACKEND", "xdna2"),
        help="Inference backend (default: xdna2 / auto)",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("CAPRI_MODEL", "gemma3:4b"),
        help="Model tag to run on FastFlowLM / Ollama (default: gemma3:4b)",
    )
    parser.add_argument(
        "--pmode",
        choices=["powersaver", "balanced", "performance", "turbo"],
        default=os.environ.get("CAPRI_PMODE", "performance"),
        help="AMD XDNA2 NPU power mode for FastFlowLM (default: performance)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("CAPRI_PORT", "52625")),
        help="Local server port for FastFlowLM flm serve (default: 52625)",
    )
    parser.add_argument(
        "--no-auto-serve",
        action="store_true",
        help="Do not auto-spawn 'flm serve' if the server is not already running",
    )
    parser.add_argument(
        "--check-npu",
        action="store_true",
        help="Run AMD XDNA2 NPU hardware & runtime self-test, print telemetry, and exit 0",
    )
    args, qt_args = parser.parse_known_args(argv)
    return args, qt_args


def create_ai_client(args):
    """Instantiate XDNA2Client / OllamaClient safely using inspect.signature."""
    client_cls = getattr(_mc, "XDNA2Client", None) or getattr(_mc, "OllamaClient")
    sig = inspect.signature(client_cls.__init__)
    params = sig.parameters
    accepts_var_kw = any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
    )

    candidate_kwargs = {
        "backend": args.backend,
        "model": args.model,
        "model_name": args.model,
        "pmode": args.pmode,
        "power_mode": args.pmode,
        "port": args.port,
        "auto_serve": not args.no_auto_serve,
    }

    if accepts_var_kw:
        filtered = {
            k: v
            for k, v in candidate_kwargs.items()
            if k in ("backend", "model", "pmode", "port", "auto_serve")
        }
    else:
        filtered = {k: v for k, v in candidate_kwargs.items() if k in params}

    client = client_cls(**filtered)

    # Ensure standard attributes are accessible for the UI even on fallback clients
    if not hasattr(client, "backend"):
        client.backend = args.backend
    if not hasattr(client, "model"):
        client.model = args.model
    if not hasattr(client, "pmode"):
        client.pmode = args.pmode
    if not hasattr(client, "port"):
        client.port = args.port

    return client


def run_npu_check(args) -> int:
    """Run AMD XDNA2 NPU hardware & model self-test and print telemetry."""
    client = create_ai_client(args)
    if hasattr(client, "get_status_report") and callable(client.get_status_report):
        print(client.get_status_report())
    else:
        try:
            from capri.ai import xdna2_engine
            if hasattr(xdna2_engine, "format_status_report"):
                print(xdna2_engine.format_status_report())
            elif hasattr(xdna2_engine, "XDNA2Engine"):
                eng = xdna2_engine.XDNA2Engine(
                    model=args.model,
                    pmode=args.pmode,
                    port=args.port,
                    auto_serve=not args.no_auto_serve,
                )
                if hasattr(eng, "get_status_report"):
                    print(eng.get_status_report())
        except Exception:
            ok = client.is_connected() if hasattr(client, "is_connected") else False
            print("============================================================")
            print("  CAPRI // AMD XDNA2 NPU TELEMETRY & SELF-TEST")
            print("============================================================")
            print(f"  Backend    : {args.backend}")
            print(f"  Model      : {args.model}")
            print(f"  Power Mode : {args.pmode}")
            print(f"  FLM Port   : {args.port}")
            print(f"  Server     : {'ONLINE' if ok else 'OFFLINE'}")
            print("============================================================")
    return 0


def load_voice_components():
    """
    Try to load STT / TTS / PTT.
    Supports XDNA2 NPU Whisper STT + push-to-talk even when Piper TTS is not installed.
    """
    try:
        from capri.voice.speech_to_text import SpeechToText
        from capri.voice.text_to_speech import TextToSpeech
        from capri.voice.ptt            import PushToTalk

        stt = SpeechToText()
        tts = TextToSpeech()
        if not tts.is_available():
            print("[Voice] Piper TTS binary/model not found — voice replies disabled.")
            tts = None

        if stt.is_ready():
            ptt = PushToTalk(on_press=lambda: None, on_release=lambda: None)
            print(f"[Voice] STT ({stt.backend}) + PTT ready. Hold LEFT CTRL to speak.")
            return stt, tts, ptt

        return None, tts, None

    except ImportError as e:
        print(f"[Voice] Missing package: {e}")
        print("        pip install sounddevice soundfile pynput")
        print("[Voice] Running text-only.")
        return None, None, None
    except Exception as e:
        print(f"[Voice] Voice initialization skipped: {e}")
        return None, None, None


def main() -> None:
    args, qt_args = parse_args()

    if args.check_npu:
        ensure_dependencies(check_only_core=True)
        sys.exit(run_npu_check(args))

    if not ensure_dependencies(check_only_core=False):
        sys.exit(1)

    from PySide6.QtWidgets import QApplication
    from capri.character.controller import CharacterController
    from capri.desktop.window       import CapriWindow
    from capri.desktop.chat_window  import ChatWindow

    app = QApplication([sys.argv[0]] + qt_args)
    app.setApplicationName("Capri")
    app.setQuitOnLastWindowClosed(False)

    assets_dir = get_assets_dir()
    controller = CharacterController(assets_dir)
    ai_client  = create_ai_client(args)

    stt, tts, ptt = load_voice_components()

    capri_win = CapriWindow(controller)
    chat_win  = ChatWindow(
        ai_client, controller, capri_win,
        stt=stt, tts=tts, ptt=ptt,
    )

    capri_win.set_chat_window(chat_win)
    capri_win.show()

    print(
        f"Capri running [{args.backend.upper()} | model={args.model} | pmode={args.pmode}]. "
        "Click her to open chat. Right-click for NPU status / quit."
    )
    if ptt:
        print("Hold LEFT CTRL anywhere to speak.")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
