# Local Smartz

Local-first multi-agent research system powered by [Ollama](https://ollama.com) and [DeepAgents](https://github.com/langchain-ai/deepagents). All LLM inference runs on your hardware — no cloud API keys needed.

## Quick Start

```bash
# 1. Clone and install
git clone https://github.com/tyroneross/local-smartz.git
cd local-smartz
pip install -e .

# 2. Setup Ollama and download models
localsmartz --setup

# 3. Research
localsmartz "What are the top AI trends in 2026?"
```

## Requirements

- **Python 3.12+**
- **Ollama** — [download](https://ollama.com/download) or `brew install ollama`
- **8GB+ RAM** (lite profile) or **64GB+ RAM** (full profile)

## Hardware Profiles

| | Full (64GB+ RAM) | Lite (<64GB RAM) |
|---|---|---|
| **Planning model** | Llama 3.1 70B | Qwen 3 8B |
| **Execution model** | Qwen 2.5-Coder 32B | Qwen 3 8B |
| **Quality review** | Yes | No |

Profile is auto-detected from system RAM, or set manually with `--profile lite`.

## Usage

```bash
# Single query
localsmartz "Compare React vs Vue for enterprise apps"

# Interactive REPL
localsmartz

# With thread for multi-session research
localsmartz --thread market-research "What is the current state of the EV market?"
localsmartz --thread market-research "Now compare Tesla vs BYD specifically"

# Force lite profile
localsmartz --profile lite "Summarize this PDF" < report.pdf

# Check Ollama status
localsmartz --check

# List research threads
localsmartz --list-threads
```

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

## Architecture

Single DeepAgent with all tools — DeepAgents handles orchestration:
- **Planning**: Built-in `write_todos` decomposes questions into steps
- **Research**: `web_search` → `scrape_url` → extract findings
- **Analysis**: `python_exec` for all calculations (local models hallucinate math)
- **Output**: `create_report` saves markdown/HTML/DOCX reports
- **Delegation**: Built-in `task` tool spawns subagents for context isolation

## Project Structure

```
src/localsmartz/
├── agent.py              # Single DeepAgent + system prompt
├── profiles.py           # Full/Lite hardware detection
├── ollama.py             # Ollama health check + setup
├── threads.py            # Research continuity across sessions
├── artifacts.py          # Output tracking
├── tools/                # 8 custom tools
├── agents/prompts/       # Subagent prompt templates
└── domains/core/         # Core domain pack
```

Storage: `.localsmartz/` (threads, artifacts, scripts, reports)
