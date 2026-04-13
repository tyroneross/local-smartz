import SwiftUI

/// Per-row help content for the first-run setup wizard. Each entry explains
/// WHAT the component is, WHY it's needed, and HOW the user changes it.
/// Surfaced via a small "ⓘ" button next to the row title — one click opens a
/// popover, no inline clutter (Calm Precision: progressive disclosure).
struct SetupHelp {
    let title: String
    let what: String
    let why: String
    let whatChanges: String
    let howToChange: String

    static let python = SetupHelp(
        title: "Python",
        what: "Python is the runtime that runs the Local Smartz backend — the research agent, HTTP server, Ollama client, and plugin loader are all Python.",
        why: "The Swift app is a thin shell. It spawns a Python subprocess and talks to it over http://localhost:11435.",
        whatChanges: "The app ships its own Python inside the .app bundle — you do NOT need to install Python separately. It uses the bundled one by default. If you pick a different Python, it must have the `localsmartz` package importable from it (check with `python3 -c \"import localsmartz\"`).",
        howToChange: "Click Change… to pick a different python3 binary. Useful if you have custom libraries installed in a particular Python environment. Most users should leave this alone."
    )

    static let localSmartz = SetupHelp(
        title: "Local Smartz",
        what: "The Python package that implements the research agent and HTTP server.",
        why: "It contains the Ollama client, tool registry (web search, PDF parse, code exec), plugin system, and SSE streamer.",
        whatChanges: "This follows whichever Python you picked above — Local Smartz must be importable from that Python. The bundled Python always has it pre-installed.",
        howToChange: "To install it into a different Python, run: pip install -e <path-to-local-smartz-repo> in that Python environment."
    )

    static let workspace = SetupHelp(
        title: "Workspace",
        what: "The folder where research outputs are saved — reports, spreadsheets, Python scripts, thread history.",
        why: "The agent uses it as a working directory. create_report, create_spreadsheet, and python_exec all write here.",
        whatChanges: "Changing it only affects FUTURE outputs. Existing files stay where they are. A new .localsmartz/ subfolder gets created in whatever folder you pick.",
        howToChange: "Click Change… to pick a folder. Pick somewhere indexed by Spotlight/Finder if you want to find the outputs easily later."
    )

    static let ollama = SetupHelp(
        title: "Ollama",
        what: "A local LLM runtime — runs language models on your machine without any cloud API.",
        why: "Local Smartz sends its LLM calls to Ollama at http://localhost:11434. Ollama loads the model weights into RAM and runs inference on your GPU/CPU.",
        whatChanges: "Ollama is its own app. Local Smartz can't change it — we just check that it's running.",
        howToChange: "Download from ollama.com, run the Ollama app (menu bar icon appears), or `ollama serve` in a terminal. If it's already running but shown as Not running here, restart Ollama."
    )

    static let models = SetupHelp(
        title: "Models",
        what: "LLM weights that Ollama has downloaded — e.g. qwen3:8b, llama3.1:70b, gpt-oss:120b.",
        why: "The agent needs a model to think. Different models = different quality, speed, memory usage.",
        whatChanges: "Changing the active model changes what produces your research answers. Larger models = better but slower + more RAM. The Lite profile uses 8B models; Full uses 32B+; Heavy uses 70B+.",
        howToChange: "Settings → Models tab to install/remove. Toolbar model picker (top right) to switch the active one mid-session."
    )
}

/// Compact popover that renders one `SetupHelp` entry.
struct SetupHelpPopover: View {
    let help: SetupHelp

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(help.title)
                .font(.system(size: 14, weight: .semibold))

            section("What it is", help.what)
            section("Why it's needed", help.why)
            section("What changes if you change it", help.whatChanges)
            section("How to change it", help.howToChange)
        }
        .padding(16)
        .frame(width: 360)
    }

    private func section(_ label: String, _ body: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label)
                .font(.system(size: 11, weight: .medium))
                .foregroundStyle(.secondary)
            Text(body)
                .font(.system(size: 12))
                .foregroundStyle(.primary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

/// Setup checklist row with optional ⓘ help + Change… action.
struct StepRow: View {
    let title: String
    let detail: String
    let done: Bool
    let help: SetupHelp?
    let changeLabel: String?
    let onChange: (() -> Void)?

    @State private var showHelp = false

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: done ? "checkmark.circle.fill" : "circle")
                .foregroundStyle(done ? .green : .secondary)
                .font(.body)

            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(title)
                        .font(.system(size: 14, weight: .medium))
                    if help != nil {
                        Button {
                            showHelp.toggle()
                        } label: {
                            Image(systemName: "info.circle")
                                .font(.system(size: 12))
                                .foregroundStyle(.secondary)
                        }
                        .buttonStyle(.borderless)
                        .popover(isPresented: $showHelp, arrowEdge: .top) {
                            if let help = help {
                                SetupHelpPopover(help: help)
                            }
                        }
                    }
                }
                Text(detail)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }

            Spacer(minLength: 0)

            if let label = changeLabel, let onChange = onChange {
                Button(label, action: onChange)
                    .buttonStyle(.borderless)
                    .font(.system(size: 12))
                    .foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 10)
        .padding(.horizontal, 16)
    }
}
