#!/usr/bin/env bash
# Local Smartz installer — works both from cloned repo and remote curl
# Local:  cd local-smartz && bash install.sh
# Remote: curl -fsSL https://raw.githubusercontent.com/tyroneross/local-smartz/main/install.sh | bash
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
    case "$(uname -s)" in
        Darwin)
            if command -v brew &>/dev/null; then
                brew install ollama
            else
                echo "  Downloading Ollama for macOS..."
                curl -fsSL https://ollama.com/download/Ollama-darwin.zip -o /tmp/Ollama.zip
                unzip -oq /tmp/Ollama.zip -d /Applications
                rm /tmp/Ollama.zip
                echo "  Ollama installed to /Applications. Opening it now..."
                open /Applications/Ollama.app
                echo "  Waiting for Ollama to start..."
                sleep 5
            fi
            ;;
        Linux)
            echo "  Running Ollama's Linux installer..."
            curl -fsSL https://ollama.ai/install.sh | sh
            ;;
        *)
            echo "  Unsupported OS. Install Ollama manually: https://ollama.com/download"
            echo "  Then re-run this script."
            exit 1
            ;;
    esac
fi
echo "Ollama: installed"

# ── Ensure Ollama is running ──
if ! curl -sf http://localhost:11434/api/tags &>/dev/null; then
    echo ""
    echo "Starting Ollama..."
    case "$(uname -s)" in
        Darwin)
            # macOS: Ollama runs as an app, not a daemon
            open /Applications/Ollama.app 2>/dev/null || ollama serve &>/dev/null &
            ;;
        Linux)
            # Linux: start as background process
            ollama serve &>/dev/null &
            ;;
    esac
    # Wait for it to come up
    for i in $(seq 1 15); do
        if curl -sf http://localhost:11434/api/tags &>/dev/null; then
            break
        fi
        sleep 1
    done
    if ! curl -sf http://localhost:11434/api/tags &>/dev/null; then
        echo "Warning: Ollama is installed but not responding."
        echo "  Start it manually: ollama serve"
        echo "  Then re-run: localsmartz --setup"
    else
        echo "Ollama: running"
    fi
else
    echo "Ollama: running"
fi

# ── Detect local repo vs remote install ──
echo ""
INSTALL_MODE="remote"
if [ -f "pyproject.toml" ] && grep -q "localsmartz" pyproject.toml 2>/dev/null; then
    INSTALL_MODE="local"
fi

REPO_URL="git+https://github.com/tyroneross/local-smartz.git"

if [ "$INSTALL_MODE" = "local" ]; then
    echo "Detected: running inside cloned repo"
    echo "Installing in editable mode..."
    echo ""

    if command -v pipx &>/dev/null; then
        pipx install -e . --force
    elif command -v uv &>/dev/null; then
        uv tool install -e . --force
    else
        python3 -m pip install --user -e . --quiet
    fi
else
    echo "Installing from GitHub..."
    echo ""

    if command -v pipx &>/dev/null; then
        pipx install "$REPO_URL" --force
    elif command -v uv &>/dev/null; then
        uv tool install "$REPO_URL" --force
    else
        python3 -m pip install --user "$REPO_URL" --quiet
    fi
fi

# ── Verify install ──
if ! command -v localsmartz &>/dev/null; then
    echo ""
    echo "Warning: 'localsmartz' not found on PATH."
    echo ""
    echo "Add one of these to your shell profile (~/.zshrc or ~/.bashrc):"
    echo ""
    if command -v pipx &>/dev/null; then
        echo '  # pipx should handle this, but if not:'
        echo '  eval "$(pipx ensurepath)"'
    else
        echo '  export PATH="$HOME/.local/bin:$PATH"'
    fi
    echo ""
    echo "Then restart your terminal or run: source ~/.zshrc"
    echo ""
fi

# ── Setup Ollama + models ──
echo ""
echo "Setting up Ollama and downloading models..."
localsmartz --setup || true

echo ""
echo "Done! Run 'localsmartz' to pick your model and start researching."
