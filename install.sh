#!/usr/bin/env bash
# Local Smartz installer — one command to set up everything
# Usage: curl -fsSL https://raw.githubusercontent.com/tyroneross/local-smartz/main/install.sh | bash
set -euo pipefail

echo "Local Smartz Installer"
echo "======================"
echo ""

# ── Check Python ──
if ! command -v python3 &>/dev/null; then
    echo "Error: Python 3 is required but not found."
    echo "  Install: https://www.python.org/downloads/"
    echo "  Or: brew install python@3.12"
    exit 1
fi

PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)

if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 12 ]; }; then
    echo "Error: Python 3.12+ required, found $PY_VERSION"
    echo "  Install: brew install python@3.12"
    exit 1
fi
echo "Python $PY_VERSION: OK"

# ── Check/install Ollama ──
if ! command -v ollama &>/dev/null; then
    echo ""
    echo "Ollama not found. Installing..."
    if command -v brew &>/dev/null; then
        brew install ollama
    else
        echo "Install Ollama manually: https://ollama.com/download"
        echo "Then re-run this script."
        exit 1
    fi
fi
echo "Ollama: installed"

# ── Install Local Smartz ──
echo ""
echo "Installing Local Smartz..."

if command -v pipx &>/dev/null; then
    pipx install "git+https://github.com/tyroneross/local-smartz.git" --force
elif command -v uv &>/dev/null; then
    uv tool install "git+https://github.com/tyroneross/local-smartz.git" --force
else
    # Fallback: pip install into user site
    python3 -m pip install --user "git+https://github.com/tyroneross/local-smartz.git" --quiet
fi

# ── Verify install ──
if ! command -v localsmartz &>/dev/null; then
    echo ""
    echo "Warning: 'localsmartz' not found on PATH."
    echo "You may need to add ~/.local/bin to your PATH:"
    echo '  export PATH="$HOME/.local/bin:$PATH"'
    echo ""
fi

# ── Setup Ollama + models ──
echo ""
echo "Setting up Ollama and downloading models..."
localsmartz --setup || true

echo ""
echo "Done! Run: localsmartz \"your research question\""
