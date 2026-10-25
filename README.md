# Capri — The Desktop Construct (AMD XDNA2 Edition)

[![AMD Ryzen AI](https://img.shields.io/badge/AMD_Ryzen_AI-XDNA2_aie2p-ED1C24?style=for-the-badge&logo=amd&logoColor=white)](https://www.amd.com/en/products/processors/consumer/ryzen-ai.html)
[![FastFlowLM NPU2](https://img.shields.io/badge/FastFlowLM-NPU2_.q4nx-FF6B00?style=for-the-badge)](https://github.com/ROCm/FastFlowLM)
[![Arch Linux](https://img.shields.io/badge/Arch_Linux-CachyOS-1793D1?style=for-the-badge&logo=archlinux&logoColor=white)](https://cachyos.org/)
[![PySide6](https://img.shields.io/badge/UI-PySide6_Qt6-41CD52?style=for-the-badge&logo=qt&logoColor=white)](https://wiki.qt.io/Qt_for_Python)

**Capri** is a lightweight, Cyberpunk 2077–inspired desktop pet and companion for **Arch Linux / CachyOS**, modified to run entirely on the **AMD Ryzen AI XDNA2 NPU (`aie2p`)**.

She isn't a coding assistant, productivity dashboard, or corporate chatbot—she's a casual Night City construct who hangs out on the corner of your screen while you game, listen to music, or chill, without touching a single megabyte of your GPU's VRAM or spinning up your CPU fans.

> **Upstream Credit:** Forked from [**Juliowiwiwiwi/Capri-Capricorn-The-Desktop-Construct**](https://github.com/Juliowiwiwiwi/Capri-Capricorn-The-Desktop-Construct).

---

## Preview

<p align="center">
  <img src="assets/ui/capri1.png" alt="Capri idle on desktop" width="45%" />
  <img src="assets/ui/capri2.png" alt="Capri sleeping on desktop" width="45%" />
</p>

<p align="center">
  <img src="assets/ui/caprichat.png" alt="Capri chat terminal" width="80%" />
</p>

---

## Why XDNA2 NPU?

Normally, running a local desktop companion via Ollama eats **3–5 GB of GPU VRAM** (or hammers your CPU cores), which hurts frame rates when gaming or drains your laptop battery.

On **AMD Ryzen AI 300 & Ryzen AI 400 Series** laptops (Strix Point / Krackan Point / Strix Halo / Gorgon Point — all sharing the same **XDNA2 `aie2p` NPU** architecture), the 50+ TOPS NPU usually sits completely idle on Linux. This fork moves Capri's entire brain and voice recognition onto the XDNA2 NPU via **FastFlowLM (`flm`)** and the **`amdxdna` / XRT** driver stack:

- **0% GPU VRAM Usage:** Your discrete GPU (NVIDIA/Radeon) and iGPU stay 100% free for games, rendering, or stay power-gated in `D3cold`.
- **Ultra-Low Wattage:** Runs `.q4nx` 4-bit NPU2 weights across 8 AIE2P columns (`~20 tok/s` on `gemma3:4b`).
- **NPU Push-to-Talk Voice (`whisper-v3:turbo`):** Hold `LEFT CTRL` to talk to Capri—speech-to-text runs directly on the XDNA2 NPU (`--asr 1`) instead of competing for CPU/GPU resources.
- **No Ollama Required:** Reads Capri's original [`Modelfile`](Modelfile) directly in Python and injects her persona and sampling parameters straight into `flm serve`.
- **Self-Healing Startup Safety-Net:** [`./run_xdna2.sh`](run_xdna2.sh) and [`capri/main.py`](capri/main.py) automatically check and fix missing Arch packages (`pacman`), XRT DMA `memlock` limits (`ulimit -l`), `.venv` Python packages, and NPU2 model downloads.

---

## Quick Start (Arch Linux / CachyOS)

### Prerequisites
Make sure the AMD XDNA2 NPU stack is installed on your Arch / CachyOS system:
- **Supported Silicon:** AMD **Ryzen AI 300 Series** & **Ryzen AI 400 Series** (fully compatible back-to-back as both use the same 8-column **XDNA2 `aie2p`** NPU)
- Linux kernel `6.14+` / `7.x` with `amdxdna` loaded (`/dev/accel/accel0`)
- `xrt` (`xrt-smi`) and `fastflowlm` (`flm`) installed from CachyOS / Arch repos

### Run Capri
```bash
git clone https://github.com/MrProper0592/Capri-Capricorn-The-Desktop-Construct-AMD-XDNA2-POWERED.git
cd Capri-Capricorn-The-Desktop-Construct-AMD-XDNA2-POWERED
./run_xdna2.sh
```

On first launch, `./run_xdna2.sh` will automatically:
1. Verify `python3`, `portaudio`, `libsndfile`, `/dev/accel/accel0`, `xrt-smi`, and `flm` (installing missing system libraries via `pkexec pacman` if needed).
2. Check your kernel DMA `memlock` limit (`ulimit -l`) and configure `unlimited` via `pkexec` if it's capped at 8 MB.
3. Pull `gemma3:4b` (`Gemma3-4B-NPU2`) and `whisper-v3:turbo` (`Whisper-V3-Turbo-NPU2`) via `flm pull` if not already installed.
4. Create `.venv`, install Python dependencies from [`requirements.txt`](requirements.txt), and spawn Capri on your desktop.

### Hardware Self-Test
To verify your NPU hardware, firmware, and installed models from the terminal without opening the GUI:
```bash
./run_xdna2.sh --check-npu
```
```text
============================================================
  CAPRI // AMD XDNA2 NPU TELEMETRY & SELF-TEST
============================================================
  Processor  : AMD Ryzen AI 7 350 w/ Radeon 860M
  NPU Device : NPU Krackan 1 [0000:67:00.1]
  Platform   : aie2p (8 AIE2P Columns)
  Firmware   : 1.1.2.64 (XRT 2.21.75 / FLM v1.0.9)
  Memlock    : infinity (OK)
  Server     : ONLINE @ http://127.0.0.1:52625
  LLM Model  : gemma3:4b (ctx=4096, pmode=performance)
  NPU ASR    : ENABLED (whisper-v3:turbo)
  Installed  : gemma3:4b, qwen3:0.6b, whisper-v3:turbo
============================================================
```

---

## Desktop Controls & Terminal Commands

### Mouse & Keyboard Controls
| Action | Effect |
| :--- | :--- |
| **Left-click** | Open / close the terminal chat window (or wake Capri if she's sleeping) |
| **Left-click + drag** | Move Capri anywhere on your screen |
| **Double left-click** | Poke Capri (happy reaction) |
| **Right-click** | Context menu (`⚡ Wake` / `💤 Sleep`, `⚡ XDNA2 NPU Status`, `🧹 Clear Chat Memory`, `✕ Quit`) |
| **Hold `LEFT CTRL`** | Push-to-Talk voice input (transcribed on the XDNA2 NPU via `whisper-v3:turbo`) |
| **`ESC`** | Interrupt spoken TTS audio |

### In-Chat Terminal Commands
Type these inside Capri's chat window:
- `/npu` or `/status` — Show live AMD XDNA2 NPU hardware info, firmware, active model, and last `tok/s` speed
- `/model <tag>` — Switch the active NPU2 model on the fly (e.g. `/model qwen3:0.6b` or `/model gemma3:4b`)
- `/clear` or `/reset` — Clear chat memory buffer
- `/help` — Show command list

---

## Supported NPU2 Models (`flm`)

You can pull any FastFlowLM `aie2p` model with `flm pull <tag>` and pass `--model <tag>` (or switch live with `/model <tag>`):

| Model Tag | NPU2 Package | Footprint | Notes |
| :--- | :--- | :---: | :--- |
| **`gemma3:4b`** *(Default)* | `Gemma3-4B-NPU2` | 4.5 GB | Original base model from Capri's `Modelfile` — best personality & banter |
| **`gemma3:1b`** | `Gemma3-1B-NPU2` | 1.2 GB | Lightweight Gemma 3 variant with faster response times |
| **`llama3.2:3b`** | `Llama-3.2-3B-NPU2` | 2.7 GB | Snappy casual chat with medium memory footprint |
| **`qwen3:1.7b`** | `Qwen3-1.7B-NPU2` | 1.6 GB | Fast conversational model (`<think>` tags are automatically stripped) |
| **`qwen3:0.6b`** | `Qwen3-0.6B-NPU2` | 0.66 GB | Ultra-lightweight, instant responses (`~80+ tok/s`) |
| **`whisper-v3:turbo`** | `Whisper-V3-Turbo-NPU2` | 0.62 GB | NPU Speech-to-Text engine loaded automatically when `--asr 1` is active |

---

## CLI Options

```bash
./run_xdna2.sh [OPTIONS]
# or: .venv/bin/python -m capri.main [OPTIONS]
```

| Flag | Default | Description |
| :--- | :---: | :--- |
| `--model <tag>` | `gemma3:4b` | Model tag to run on FastFlowLM (`gemma3:4b`, `qwen3:0.6b`, `llama3.2:3b`, etc.) |
| `--pmode <mode>` | `performance` | XDNA2 NPU power mode: `powersaver`, `balanced`, `performance`, `turbo` |
| `--port <port>` | `52625` | Local port for `flm serve` |
| `--backend <name>` | `xdna2` | Backend: `xdna2` (FastFlowLM NPU), `ollama` (`127.0.0.1:11434`), or `openai` |
| `--no-auto-serve` | `False` | Connect to an already-running `flm serve` instance without spawning one |
| `--check-npu` | `False` | Print XDNA2 NPU hardware & runtime telemetry and exit |

---

## Project Structure

```text
Capri-Capricorn-The-Desktop-Construct-AMD-XDNA2-POWERED/
├── Modelfile                  # Capri's Cyberpunk 2077 persona & generation parameters
├── run_xdna2.sh               # Self-healing Arch/CachyOS launcher & dependency safety-net
├── requirements.txt           # Python dependencies (PySide6, sounddevice, soundfile, pynput, etc.)
├── capri/
│   ├── main.py                # CLI entry point + startup dependency safety-net
│   ├── ai/
│   │   ├── xdna2_engine.py    # XDNA2 NPU detection, Modelfile parser & `flm serve` manager
│   │   └── model_client.py    # Streaming XDNA2 client, <think> filter & tok/s telemetry
│   ├── character/
│   │   └── controller.py      # Sprite animation state machine (idle, blink, happy, sleeping)
│   ├── desktop/
│   │   ├── window.py          # Frameless transparent desktop pet window
│   │   └── chat_window.py     # Cyberpunk terminal UI, NPU badge & slash commands
│   └── voice/
│       ├── speech_to_text.py  # XDNA2 NPU Whisper (`whisper-v3:turbo`) + faster-whisper fallback
│       ├── text_to_speech.py  # Local Piper TTS audio output
│       └── ptt.py             # Global Push-to-Talk key listener (LEFT CTRL)
└── assets/
    ├── character/             # Capri sprite frames
    └── ui/                    # README preview screenshots
```

---

## Troubleshooting

### `[xrt-smi] ERROR: mmap(...) failed (err=-11): Resource temporarily unavailable`
Linux restricts locked DMA memory (`ulimit -l`) to `8192 KB` by default, which prevents `amdxdna` from pinning NPU model buffers in RAM.
- Running `./run_xdna2.sh` automatically fixes this via `pkexec`.
- To fix it manually across reboots on Arch / CachyOS:
  ```bash
  echo "* - memlock unlimited" | sudo tee /etc/security/limits.d/99-xdna-npu.conf
  printf "[Manager]\nDefaultLimitMEMLOCK=infinity\n" | sudo tee /etc/systemd/user.conf.d/99-xdna-npu.conf
  ```

### Optional Local Voice Output (Piper TTS)
Push-to-talk speech recognition (`whisper-v3:turbo`) works out of the box on the XDNA2 NPU. If you also want Capri to speak replies out loud via `piper`, place the `piper` binary at `~/.local/share/piper/piper` (or install `piper-tts` from AUR) and place an ONNX voice model in `~/.local/share/piper/voices/`. If Piper isn't installed, Capri runs quietly in text-chat + NPU voice-input mode without errors.

---

## Credits

- [**Juliowiwiwiwi**](https://github.com/Juliowiwiwiwi/Capri-Capricorn-The-Desktop-Construct) — Original Capri concept, character sprites, PySide6 UI, and `Modelfile` writing.
- [**MrProper0592**](https://github.com/MrProper0592/Capri-Capricorn-The-Desktop-Construct-AMD-XDNA2-POWERED) — AMD Ryzen AI XDNA2 (`aie2p`) NPU port, FastFlowLM integration, NPU Whisper STT pipeline, and Arch Linux / CachyOS safety-net.
