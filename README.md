# Local Smartz

Local-first multi-agent research system powered by [Ollama](https://ollama.com) and [DeepAgents](https://github.com/langchain-ai/deepagents). All LLM inference runs on your hardware — no cloud API keys needed.

## Install

### One-line install (recommended)

```bash
curl -fsSL https://raw.githubusercontent.com/tyroneross/local-smartz/main/install.sh | bash
```

Flags:
- `--no-ollama` — skip Ollama install / start (use if you already manage Ollama yourself)

The script:
1. Checks Python 3.12+ is installed (exits with a pointer to the installer if not)
2. Installs Ollama if missing (macOS: Homebrew or direct download; Linux: official installer)
3. Starts Ollama if it's not running
4. Installs `localsmartz` CLI — prefers `uv tool install -e .`, falls back to `pipx`, then `pip --user`
5. Runs `localsmartz --setup` only in an interactive shell (downloads default models, ~5–40 GB)

### Manual install

1. Install **Python 3.12+** — [python.org/downloads/macos](https://www.python.org/downloads/macos/) or `brew install python@3.12`
2. Install **Ollama** — [ollama.com/download](https://ollama.com/download) or `brew install ollama`
3. Clone and install:

   ```bash
   git clone https://github.com/tyroneross/local-smartz.git
   cd local-smartz

   # Pick one
   uv tool install -e .     # uv (recommended, fastest)
   pipx install -e .        # pipx
   pip install -e .         # pip
   ```
4. Run first-run setup:

   ```bash
   localsmartz --setup
   localsmartz --check      # verify
   ```

### Cross-Mac install

Installing `localsmartz` on a fresh Mac:

- The native macOS app (`.app` bundle in `app/`) requires **Python 3.12+ on `PATH`** at launch time. Bundled-Python work is planned; until it lands, install Python first.
- **Plugins** live at `$HOME/.localsmartz/plugins/` (per-user, not in the repo). Copying the repo between machines does not copy your plugins.
- **Settings** live at `$HOME/.localsmartz/global.json`. Project-scoped state stays in `.localsmartz/` inside the project directory.

### Requirements

- **Python 3.12+** — check with `python3 --version`
- **Ollama** — installed automatically by the script, or [download manually](https://ollama.com/download)
- **8GB+ RAM** for lite profile (~5 GB disk for models)
- **64GB+ RAM** for full profile (~40 GB disk for models)

### Don't have Ollama?

The install script handles it automatically. If you prefer to install manually:

| Platform | Install Command |
|----------|----------------|
| macOS (Homebrew) | `brew install ollama` |
| macOS (direct) | Download from [ollama.com/download](https://ollama.com/download) |
| Linux | `curl -fsSL https://ollama.ai/install.sh \| sh` |

After installing, start it with `ollama serve` (Linux) or open the Ollama app (macOS).

## Plugins & Skills

Local-smartz can load plugins, skills, and MCP servers from external directories (default search path includes RossLabs-AI-Toolkit). Add one with:

```bash
localsmartz plugins install <path>
```

See `localsmartz plugins --help` for the full CLI.

> This feature is being wired up in the current build — the exact command surface (list, remove, validate) lands with this release. If `localsmartz plugins` is not yet available on your install, pull latest and re-run the installer.

## Quick Start

```bash
# Single query
localsmartz "What are the top AI trends in 2026?"

# Interactive REPL
localsmartz

# Web UI
localsmartz --serve
# Open http://localhost:11435
```

## Usage

### CLI

```bash
# Single query
localsmartz "Compare React vs Vue for enterprise apps"

# Interactive REPL (slash commands: /help, /model, /thread, /exit)
localsmartz

# With thread for multi-session research
localsmartz --thread market-research "What is the current state of the EV market?"
localsmartz --thread market-research "Now compare Tesla vs BYD specifically"

# Force lite profile or specific model
localsmartz --profile lite "Summarize this document"
localsmartz --model qwen3:8b-q4_K_M "Quick question"

# Check Ollama status
localsmartz --check

# List research threads
localsmartz --list-threads
```

### Web UI

```bash
localsmartz --serve                  # Start at localhost:11435
localsmartz --serve --port 8080      # Custom port
```

The web UI provides:
- **Model selector** — switch between available Ollama models
- **Folder management** — configure which directories the agent can access
- **Thread sidebar** — view and resume past research sessions
- **SSE streaming** — real-time output as the agent works

### macOS App

A native SwiftUI desktop app is available in `app/`. It wraps the web API with a menu bar interface.

```bash
cd app
xcodegen generate        # Generate Xcode project from project.yml
xcodebuild -scheme LocalSmartz build
open ~/Library/Developer/Xcode/DerivedData/LocalSmartz-*/Build/Products/Debug/Local\ Smartz.app
```

Or install via the pre-built DMG in `app/LocalSmartz-Installer.dmg`.

## Hardware Profiles

| | Full (64GB+ RAM) | Lite (<64GB RAM) |
|---|---|---|
| **Planning model** | Llama 3.1 70B | Qwen 3 8B |
| **Execution model** | Qwen 2.5-Coder 32B | Qwen 3 8B |
| **Max turns** | 20 | 10 |
| **Quality review** | Yes | No |
| **Subagent delegation** | Yes | No |

Profile is auto-detected from system RAM, or set manually with `--profile lite`.

The first-run model picker lets you choose any model available in Ollama. Your selection is saved to `.localsmartz/config.json` per project.

## Tools

| Tool | Purpose |
|------|---------|
| `web_search` | DuckDuckGo search (no API key) |
| `scrape_url` | Fetch and extract web page content |
| `parse_pdf` | Extract text from PDF files |
| `read_spreadsheet` | Read Excel files |
| `read_text_file` | Read local text files |
| `python_exec` | Sandboxed Python for calculations |
| `create_report` | Generate markdown/HTML/DOCX reports |
| `create_spreadsheet` | Create Excel files |

Plus built-in DeepAgents tools: `write_todos` (planning), `task` (subagent delegation), `read_file`/`write_file` (context management).

## API

When running with `--serve`, the following endpoints are available:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/` | GET | Web UI |
| `/api/health` | GET | Liveness check |
| `/api/status` | GET | Profile, models, Ollama state |
| `/api/models` | GET | List available models |
| `/api/models/select` | POST | Switch active model |
| `/api/folders` | GET | List research folders |
| `/api/folders` | POST | Add a research folder |
| `/api/folders` | DELETE | Remove a research folder |
| `/api/threads` | GET | List research threads |
| `/api/research` | POST | Run research (SSE stream) |
| `/api/setup` | POST | Setup Ollama + models (SSE stream) |

## Architecture

Single DeepAgent with all tools — DeepAgents handles orchestration:
- **Planning**: Built-in `write_todos` decomposes questions into steps
- **Research**: `web_search` -> `scrape_url` -> extract findings
- **Analysis**: `python_exec` for all calculations (local models hallucinate math)
- **Output**: `create_report` saves markdown/HTML/DOCX reports
- **Delegation**: Built-in `task` tool spawns subagents for context isolation

## Project Structure

```
src/localsmartz/
├── __main__.py           # CLI entry (argparse, REPL, --serve)
├── agent.py              # Single DeepAgent + system prompt
├── serve.py              # HTTP server + embedded web UI
├── config.py             # Persistent model + folder config
├── profiles.py           # Full/Lite hardware detection
├── ollama.py             # Ollama health check + setup
├── threads.py            # Research continuity across sessions
├── artifacts.py          # Output tracking
├── validation.py         # Tool call validation + loop detection
├── tools/                # 8 custom tools
├── agents/prompts/       # Subagent prompt templates
└── domains/core/         # Core domain pack

app/                      # macOS SwiftUI desktop app
```

Storage: `.localsmartz/` per project (threads, artifacts, config, scripts, reports)
