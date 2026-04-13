#!/usr/bin/env bash
# Local Smartz installer — works both from cloned repo and remote curl
# Local:  cd local-smartz && bash install.sh
# Remote: curl -fsSL https://raw.githubusercontent.com/tyroneross/local-smartz/main/install.sh | bash
#
# Flags:
#   --no-ollama   Skip Ollama auto-install / start (useful if you manage it yourself)
set -euo pipefail

SKIP_OLLAMA=0
for arg in "$@"; do
    case "$arg" in
        --no-ollama) SKIP_OLLAMA=1 ;;
        -h|--help)
            echo "Usage: install.sh [--no-ollama]"
            echo "  --no-ollama   Skip Ollama install/start (user manages it)"
            exit 0
            ;;
        *) echo "Unknown flag: $arg" >&2; exit 2 ;;
    esac
done

echo "Local Smartz Installer"
echo "======================"
echo ""

step_ok()   { echo "  ✓ $1"; }
step_fail() { echo "  ✗ $1" >&2; }

# ── Check Python ──
echo "[1/5] Checking Python..."
if ! command -v python3 &>/dev/null; then
    step_fail "Python 3 not found"
    echo "    Install: https://www.python.org/downloads/macos/"
    echo "    Or: brew install python@3.12"
    exit 1
fi

PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)

if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 12 ]; }; then
    step_fail "Python 3.12+ required, found $PY_VERSION"
    echo "    Install: brew install python@3.12"
    exit 1
fi
step_ok "Python $PY_VERSION"

# ── Ollama install ──
echo "[2/5] Checking Ollama..."
if [ "$SKIP_OLLAMA" -eq 1 ]; then
    step_ok "Skipped (--no-ollama)"
else
    if ! command -v ollama &>/dev/null; then
        echo "    Ollama not found. Installing..."
        case "$(uname -s)" in
            Darwin)
                if command -v brew &>/dev/null; then
                    brew install ollama
                else
                    echo "    Downloading Ollama for macOS..."
                    curl -fsSL https://ollama.com/download/Ollama-darwin.zip -o /tmp/Ollama.zip
                    unzip -oq /tmp/Ollama.zip -d /Applications
                    rm /tmp/Ollama.zip
                    open /Applications/Ollama.app
                    sleep 5
                fi
                ;;
            Linux)
                curl -fsSL https://ollama.ai/install.sh | sh
                ;;
            *)
                step_fail "Unsupported OS. Install Ollama manually: https://ollama.com/download"
                exit 1
                ;;
        esac
    fi
    step_ok "Ollama installed"
fi

# ── Ensure Ollama is running ──
echo "[3/5] Ensuring Ollama is running..."
if [ "$SKIP_OLLAMA" -eq 1 ]; then
    step_ok "Skipped (--no-ollama)"
else
    if ! curl -sf http://localhost:11434/api/tags &>/dev/null; then
        case "$(uname -s)" in
            Darwin)
                open /Applications/Ollama.app 2>/dev/null || ollama serve &>/dev/null &
                ;;
            Linux)
                ollama serve &>/dev/null &
                ;;
        esac
        for i in $(seq 1 15); do
            if curl -sf http://localhost:11434/api/tags &>/dev/null; then
                break
            fi
            sleep 1
        done
    fi
    if curl -sf http://localhost:11434/api/tags &>/dev/null; then
        step_ok "Ollama running at http://localhost:11434"
    else
        step_fail "Ollama installed but not responding"
        echo "    Start it manually: ollama serve"
        echo "    Then re-run: localsmartz --setup"
    fi
fi

# ── Install localsmartz ──
echo "[4/5] Installing localsmartz..."
INSTALL_MODE="remote"
if [ -f "pyproject.toml" ] && grep -q "localsmartz" pyproject.toml 2>/dev/null; then
    INSTALL_MODE="local"
fi

REPO_URL="git+https://github.com/tyroneross/local-smartz.git"

if [ "$INSTALL_MODE" = "local" ]; then
    if command -v uv &>/dev/null; then
        uv tool install -e . --force
    elif command -v pipx &>/dev/null; then
        pipx install -e . --force
    else
        python3 -m pip install --user -e . --quiet
    fi
    step_ok "Installed (local, editable)"
else
    if command -v uv &>/dev/null; then
        uv tool install "$REPO_URL" --force
    elif command -v pipx &>/dev/null; then
        pipx install "$REPO_URL" --force
    else
        python3 -m pip install --user "$REPO_URL" --quiet
    fi
    step_ok "Installed (from GitHub)"
fi

# ── Verify install ──
if ! command -v localsmartz &>/dev/null; then
    step_fail "'localsmartz' not found on PATH"
    echo ""
    echo "  Add one of these to your shell profile (\$HOME/.zshrc or \$HOME/.bashrc):"
    if command -v uv &>/dev/null; then
        echo '    export PATH="$HOME/.local/bin:$PATH"   # uv tool'
    elif command -v pipx &>/dev/null; then
        echo '    eval "$(pipx ensurepath)"'
    else
        echo '    export PATH="$HOME/.local/bin:$PATH"'
    fi
    echo ""
    echo "  Then restart your terminal or: source \$HOME/.zshrc"
    exit 1
fi

# ── Setup Ollama + models ──
echo "[5/5] First-run setup..."
if [ -t 0 ] && [ -t 1 ]; then
    localsmartz --setup || step_fail "Setup reported errors (see above)"
    step_ok "Setup complete"
else
    step_ok "Non-interactive shell detected — skipping --setup"
    echo "    Run 'localsmartz --setup' manually to pick your profile and download models."
fi

echo ""
echo "Done. Run 'localsmartz' to pick your model and start researching."
