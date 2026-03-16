import SwiftUI

struct SetupView: View {
    @EnvironmentObject var appState: AppState
    @StateObject private var backend = BackendManager()

    @State private var pythonPath = ""
    @State private var projectDirectory = ""
    @State private var phase: SetupPhase = .detectPython
    @State private var statusText = ""
    @State private var errorText = ""

    enum SetupPhase {
        case detectPython
        case checkOllama
        case ready
        case failed
    }

    var body: some View {
        VStack(spacing: 24) {
            Spacer()

            // Header
            Image(systemName: "magnifyingglass.circle.fill")
                .font(.system(size: 48))
                .foregroundStyle(.secondary)

            Text("Local Smartz")
                .font(.title)
                .fontWeight(.semibold)

            Text("Local-first research powered by Ollama")
                .font(.subheadline)
                .foregroundStyle(.secondary)

            Spacer().frame(height: 8)

            // Setup steps
            VStack(alignment: .leading, spacing: 16) {
                stepRow(
                    number: 1,
                    title: "Python",
                    detail: pythonPath.isEmpty ? "Detecting..." : pythonPath,
                    done: !pythonPath.isEmpty
                )

                stepRow(
                    number: 2,
                    title: "Ollama",
                    detail: phase == .checkOllama ? "Checking..." : (phase == .ready ? "Ready" : "Waiting"),
                    done: phase == .ready
                )

                stepRow(
                    number: 3,
                    title: "Project directory",
                    detail: projectDirectory.isEmpty ? NSHomeDirectory() : projectDirectory,
                    done: phase == .ready
                )
            }
            .frame(maxWidth: 400)

            if !statusText.isEmpty {
                Text(statusText)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            if !errorText.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    Text(errorText)
                        .font(.caption)
                        .foregroundStyle(.red)
                }
                .frame(maxWidth: 400)
            }

            Spacer()

            // Actions
            HStack(spacing: 12) {
                Button("Choose Python...") {
                    choosePython()
                }
                .buttonStyle(.bordered)

                Button("Get Started") {
                    completeSetup()
                }
                .buttonStyle(.borderedProminent)
                .disabled(phase != .ready)
            }

            Spacer().frame(height: 16)
        }
        .padding(32)
        .task {
            await detectPython()
        }
    }

    private func stepRow(number: Int, title: String, detail: String, done: Bool) -> some View {
        HStack(spacing: 12) {
            Image(systemName: done ? "checkmark.circle.fill" : "circle")
                .foregroundStyle(done ? .green : .secondary)
                .font(.body)

            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.headline)
                Text(detail)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }
        }
    }

    private func detectPython() async {
        phase = .detectPython
        statusText = "Looking for Python..."

        // Try common locations
        let candidates = [
            "/usr/bin/python3",
            "/opt/homebrew/bin/python3",
            "/usr/local/bin/python3",
            "\(NSHomeDirectory())/.local/bin/python3",
        ]

        for path in candidates {
            if FileManager.default.fileExists(atPath: path) {
                // Verify it works
                let ok = await verifyPython(path)
                if ok {
                    pythonPath = path
                    break
                }
            }
        }

        if pythonPath.isEmpty {
            // Try which
            let result = await runCommand("/usr/bin/env", arguments: ["which", "python3"])
            let path = result.trimmingCharacters(in: .whitespacesAndNewlines)
            if !path.isEmpty && FileManager.default.fileExists(atPath: path) {
                pythonPath = path
            }
        }

        if pythonPath.isEmpty {
            statusText = "Python 3 not found. Use 'Choose Python...' to locate it."
            return
        }

        // Check if localsmartz is importable
        statusText = "Checking localsmartz package..."
        let importOk = await runCommand(pythonPath, arguments: ["-m", "localsmartz", "--version"])
        if !importOk.contains("localsmartz") && !importOk.contains("0.") {
            errorText = "localsmartz not installed. Run: pip install localsmartz"
        }

        projectDirectory = NSHomeDirectory()
        phase = .checkOllama
        await checkOllama()
    }

    private func checkOllama() async {
        statusText = "Checking Ollama..."

        let result = await runCommand(pythonPath, arguments: [
            "-c", "import httpx; r = httpx.get('http://localhost:11434/api/version', timeout=3); print(r.status_code)"
        ])

        if result.contains("200") {
            phase = .ready
            statusText = "Everything looks good."
            errorText = ""
        } else {
            errorText = "Ollama not running \u{2192} The local LLM server isn't active \u{2192} Start it with: ollama serve"
            phase = .ready // Allow proceeding — app will show status in toolbar
            statusText = ""
        }
    }

    private func verifyPython(_ path: String) async -> Bool {
        let result = await runCommand(path, arguments: ["--version"])
        return result.contains("Python 3")
    }

    private func choosePython() {
        let panel = NSOpenPanel()
        panel.title = "Select python3 binary"
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false

        if panel.runModal() == .OK, let url = panel.url {
            pythonPath = url.path
            Task { await checkOllama() }
        }
    }

    private func completeSetup() {
        UserDefaults.standard.set(pythonPath, forKey: "pythonPath")
        UserDefaults.standard.set(projectDirectory, forKey: "projectDirectory")
        appState.markConfigured()
    }

    private func runCommand(_ command: String, arguments: [String]) async -> String {
        await withCheckedContinuation { continuation in
            let process = Process()
            process.executableURL = URL(fileURLWithPath: command)
            process.arguments = arguments
            let pipe = Pipe()
            process.standardOutput = pipe
            process.standardError = pipe
            do {
                try process.run()
                process.waitUntilExit()
                let data = pipe.fileHandleForReading.readDataToEndOfFile()
                continuation.resume(returning: String(data: data, encoding: .utf8) ?? "")
            } catch {
                continuation.resume(returning: "")
            }
        }
    }
}
