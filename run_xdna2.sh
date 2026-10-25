#!/usr/bin/env bash
# ==============================================================================
# run_xdna2.sh — Arch Linux & CachyOS Launcher + Dependency Safety-Net for Capri
#                (AMD Ryzen AI XDNA2 NPU-Powered Desktop Construct)
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
PINK='\033[0;35m'
RED='\033[0;31m'
RESET='\033[0m'

CHECK_NPU_ONLY=0
for arg in "$@"; do
    if [[ "$arg" == "--check-npu" ]]; then
        CHECK_NPU_ONLY=1
    fi
done

echo -e "${CYAN}╔══════════════════════════════════════════════════════════════╗${RESET}"
echo -e "${CYAN}║   CAPRI // AMD XDNA2 NPU DESKTOP CONSTRUCT (ARCH / CACHYOS)  ║${RESET}"
echo -e "${CYAN}╚══════════════════════════════════════════════════════════════╝${RESET}"

# ------------------------------------------------------------------------------
# 1. Arch Linux / CachyOS System Binary & Shared Library Safety-Net
# ------------------------------------------------------------------------------
echo -e "${CYAN}[1/4] Checking Arch Linux / CachyOS system & XDNA2 NPU dependencies...${RESET}"

MISSING_PACMAN_PKGS=()

if ! command -v python3 >/dev/null 2>&1; then
    MISSING_PACMAN_PKGS+=("python")
fi

if ! ldconfig -p 2>/dev/null | grep -q "libportaudio\.so" && [[ ! -f /usr/lib/libportaudio.so ]]; then
    MISSING_PACMAN_PKGS+=("portaudio")
fi

if ! ldconfig -p 2>/dev/null | grep -q "libsndfile\.so" && [[ ! -f /usr/lib/libsndfile.so ]]; then
    MISSING_PACMAN_PKGS+=("libsndfile")
fi

if [[ ${#MISSING_PACMAN_PKGS[@]} -gt 0 ]]; then
    echo -e "${YELLOW}[WARN] Missing Arch/CachyOS system packages: ${MISSING_PACMAN_PKGS[*]}${RESET}"
    if command -v pacman >/dev/null 2>&1 && command -v pkexec >/dev/null 2>&1; then
        echo -e "${CYAN}[AUTO] Installing missing system packages via pkexec pacman -S --needed --noconfirm ...${RESET}"
        pkexec pacman -S --needed --noconfirm "${MISSING_PACMAN_PKGS[@]}" || \
            echo -e "${YELLOW}[WARN] Could not auto-install ${MISSING_PACMAN_PKGS[*]}. Run: sudo pacman -S --needed ${MISSING_PACMAN_PKGS[*]}${RESET}"
    else
        echo -e "${YELLOW}[HINT] Install them on Arch/CachyOS with: sudo pacman -S --needed ${MISSING_PACMAN_PKGS[*]}${RESET}"
    fi
else
    echo -e "${GREEN}[OK]   System audio & Python runtime libraries verified (python3, portaudio, libsndfile).${RESET}"
fi

# Check AMD XDNA2 NPU kernel device (/dev/accel/accel0)
if [[ -e /dev/accel/accel0 ]]; then
    echo -e "${GREEN}[OK]   AMD XDNA2 NPU device node detected: /dev/accel/accel0 (amdxdna)${RESET}"
else
    echo -e "${YELLOW}[WARN] /dev/accel/accel0 not found! Is the 'amdxdna' kernel module loaded?${RESET}"
    echo -e "${YELLOW}       On Arch Linux / CachyOS (Linux 6.14+): sudo modprobe amdxdna${RESET}"
fi

# Check XRT (xrt-smi)
if command -v xrt-smi >/dev/null 2>&1 || [[ -x /opt/xilinx/xrt/bin/xrt-smi ]]; then
    export PATH="/opt/xilinx/xrt/bin:$PATH"
    echo -e "${GREEN}[OK]   XRT runtime detected ($(command -v xrt-smi))${RESET}"
else
    echo -e "${YELLOW}[WARN] 'xrt-smi' (XRT NPU runtime) not found in PATH.${RESET}"
    echo -e "${YELLOW}       Install XRT + xrt-plugin-amdxdna from CachyOS / Arch AUR repositories.${RESET}"
fi

# Check FastFlowLM (flm)
if [[ -x /opt/fastflowlm/bin/flm ]] && ! command -v flm >/dev/null 2>&1; then
    export PATH="/opt/fastflowlm/bin:$PATH"
fi

if command -v flm >/dev/null 2>&1; then
    echo -e "${GREEN}[OK]   FastFlowLM NPU runtime detected ($(command -v flm))${RESET}"
    flm validate || true
else
    echo -e "${PINK}[WARN] 'flm' (FastFlowLM) binary not found in PATH.${RESET}"
    echo -e "${PINK}       Install 'fastflowlm' on Arch Linux / CachyOS (or fallback to Ollama/OpenAI backend).${RESET}"
fi

# ------------------------------------------------------------------------------
# 2. DMA Memory Lock (ulimit -l / RLIMIT_MEMLOCK) Safety-Net
# ------------------------------------------------------------------------------
echo -e "${CYAN}[2/4] Checking XRT DMA memory lock (ulimit -l) limit...${RESET}"
MEMLOCK_LIMIT="$(ulimit -l)"
if [[ "$MEMLOCK_LIMIT" != "unlimited" ]] && [[ "$MEMLOCK_LIMIT" -lt 16777216 ]]; then
    echo -e "${YELLOW}[WARN] Current memlock limit (ulimit -l) is ${MEMLOCK_LIMIT} KB (< 16 GB).${RESET}"
    echo -e "${YELLOW}       AMD XDNA2 NPU requires unlimited memlock to prevent 'mmap failed (err=-11)'.${RESET}"
    if command -v pkexec >/dev/null 2>&1 && [[ "$CHECK_NPU_ONLY" -eq 0 ]]; then
        echo -e "${CYAN}[AUTO] Applying /etc/security/limits.d/99-xdna-npu.conf & prlimit fix via pkexec...${RESET}"
        pkexec bash -c "printf '* soft memlock unlimited\n* hard memlock unlimited\n' > /etc/security/limits.d/99-xdna-npu.conf && prlimit --pid $$ --memlock=unlimited:unlimited" || \
            echo -e "${YELLOW}[WARN] Could not auto-elevate memlock. Run manually:${RESET}\n       printf '* soft memlock unlimited\n* hard memlock unlimited\n' | sudo tee /etc/security/limits.d/99-xdna-npu.conf"
        MEMLOCK_LIMIT="$(ulimit -l)"
    else
        echo -e "${YELLOW}       To fix permanently on Arch Linux / CachyOS:${RESET}"
        echo -e "         printf '* soft memlock unlimited\n* hard memlock unlimited\n' | pkexec tee /etc/security/limits.d/99-xdna-npu.conf"
        echo -e "         pkexec prlimit --pid \$\$ --memlock=unlimited:unlimited"
    fi
fi
echo -e "${GREEN}[OK]   Active memlock limit: $(ulimit -l)${RESET}"

# ------------------------------------------------------------------------------
# 3. FastFlowLM NPU2 Model Safety-Net (Auto-bootstrap gemma3:4b & whisper-v3:turbo)
# ------------------------------------------------------------------------------
echo -e "${CYAN}[3/4] Checking installed FastFlowLM NPU2 models...${RESET}"
if command -v flm >/dev/null 2>&1; then
    INSTALLED_MODELS="$(flm list --filter installed 2>/dev/null || true)"
    if [[ "$CHECK_NPU_ONLY" -eq 0 ]]; then
        if ! echo "$INSTALLED_MODELS" | grep -q "gemma3:4b"; then
            echo -e "${YELLOW}[AUTO] Default NPU2 LLM 'gemma3:4b' not found. Pulling via 'flm pull gemma3:4b'...${RESET}"
            flm pull gemma3:4b || echo -e "${YELLOW}[WARN] 'flm pull gemma3:4b' did not complete; will use fallback installed model.${RESET}"
        else
            echo -e "${GREEN}[OK]   Primary NPU2 LLM installed: gemma3:4b${RESET}"
        fi

        if ! echo "$INSTALLED_MODELS" | grep -qi "whisper-v3:turbo"; then
            echo -e "${YELLOW}[AUTO] NPU2 Whisper ASR model 'whisper-v3:turbo' not found. Pulling via 'flm pull whisper-v3:turbo'...${RESET}"
            flm pull whisper-v3:turbo || echo -e "${YELLOW}[WARN] 'flm pull whisper-v3:turbo' did not complete.${RESET}"
        else
            echo -e "${GREEN}[OK]   NPU2 ASR model installed: whisper-v3:turbo${RESET}"
        fi
    else
        echo -e "${GREEN}[OK]   Installed NPU2 models verified (check-only mode).${RESET}"
    fi
fi

# ------------------------------------------------------------------------------
# 4. Python Virtual Environment (.venv) & Package Safety-Net
# ------------------------------------------------------------------------------
echo -e "${CYAN}[4/4] Verifying Python virtual environment (.venv) & required packages...${RESET}"
if [[ ! -d ".venv" ]]; then
    echo -e "${CYAN}[AUTO] Creating Python virtual environment in .venv ...${RESET}"
    if command -v uv >/dev/null 2>&1; then
        uv venv .venv
    else
        python3 -m venv .venv
    fi
fi

# shellcheck disable=SC1091
source .venv/bin/activate

# Verify all required Python modules are importable inside .venv
if ! .venv/bin/python -c "import PySide6, numpy, sounddevice, soundfile, pynput, requests" >/dev/null 2>&1; then
    echo -e "${YELLOW}[AUTO] Missing Python packages in .venv — bootstrapping requirements.txt...${RESET}"
    if command -v uv >/dev/null 2>&1; then
        uv pip install --python .venv/bin/python -r requirements.txt
    else
        .venv/bin/pip install --upgrade pip
        .venv/bin/pip install -r requirements.txt
    fi
fi
echo -e "${GREEN}[OK]   All Python dependencies verified (PySide6, numpy, sounddevice, soundfile, pynput, requests).${RESET}"

# ------------------------------------------------------------------------------
# 5. Launch Capri
# ------------------------------------------------------------------------------
echo -e "${GREEN}[LAUNCH] Starting Capri with arguments: $*${RESET}"
exec .venv/bin/python -m capri.main "$@"
