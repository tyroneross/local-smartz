import SwiftUI

struct SetupView: View {
    @EnvironmentObject var appState: AppState

    @State private var pythonPath = ""
    @State private var projectDirectory = ""
    @State private var localsmartzReady = false
    @State private var ollamaReady = false
    @State private var statusText = ""
    @State private var errorText = ""

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
                    title: "Python",
                    detail: pythonPath.isEmpty ? "Detecting..." : pythonPath,
                    done: !pythonPath.isEmpty
                )

                stepRow(
                    title: "Local Smartz",
                    detail: localsmartzReady ? "Installed in selected Python" : "Not installed yet",
                    done: localsmartzReady
                )

                stepRow(
                    title: "Workspace",
                    detail: projectDirectory.isEmpty ? defaultWorkspaceDirectory() : projectDirectory,
                    done: !projectDirectory.isEmpty
                )

                stepRow(
                    title: "Ollama",
                    detail: ollamaReady ? "Running" : "Not running",
                    done: ollamaReady
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

                Button("Choose Workspace...") {
                    chooseWorkspace()
                }
                .buttonStyle(.bordered)

                Button("Get Started") {
                    completeSetup()
                }
                .buttonStyle(.borderedProminent)
                .disabled(!canCompleteSetup)
            }

            Spacer().frame(height: 16)
        }
        .padding(32)
        .task {
            await detectPython()
        }
    }

    private var canCompleteSetup: Bool {
        !pythonPath.isEmpty && localsmartzReady && !projectDirectory.isEmpty
    }

    private func stepRow(title: String, detail: String, done: Bool) -> some View {
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
        statusText = "Looking for Python..."
        errorText = ""
        localsmartzReady = false
        ollamaReady = false
        projectDirectory = UserDefaults.standard.string(forKey: "projectDirectory")
            ?? defaultWorkspaceDirectory()

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

        await validateEnvironment()
    }

    private func validateEnvironment() async {
        statusText = "Checking Local Smartz..."
        await checkLocalSmartz()
        guard localsmartzReady else { return }
        await checkOllama()
    }

    private func checkLocalSmartz() async {
        let versionOutput = await runCommand(
            pythonPath,
            arguments: ["-m", "localsmartz", "--version"]
        )
        localsmartzReady = versionOutput.contains("localsmartz")

        if !localsmartzReady {
            errorText = "Local Smartz is not installed in this Python environment. Install it there, then try again."
            statusText = ""
            return
        }

        errorText = ""
    }

    private func checkOllama() async {
        statusText = "Checking Ollama..."

        guard let url = URL(string: "http://localhost:11434/api/version") else {
            ollamaReady = false
            return
        }

        do {
            var request = URLRequest(url: url)
            request.timeoutInterval = 3
            let (_, response) = try await URLSession.shared.data(for: request)
            if let http = response as? HTTPURLResponse, http.statusCode == 200 {
                ollamaReady = true
                statusText = "Local Smartz is ready."
                return
            }
        } catch {
            // Fall through to offline state below.
        }

        ollamaReady = false
        statusText = "Local Smartz is installed. Start Ollama before researching."
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
            Task { await validateEnvironment() }
        }
    }

    private func chooseWorkspace() {
        let panel = NSOpenPanel()
        panel.title = "Select workspace directory"
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.canCreateDirectories = true

        if panel.runModal() == .OK, let url = panel.url {
            projectDirectory = url.path
        }
    }

    private func completeSetup() {
        try? FileManager.default.createDirectory(
            atPath: projectDirectory,
            withIntermediateDirectories: true,
            attributes: nil
        )
        UserDefaults.standard.set(pythonPath, forKey: "pythonPath")
        UserDefaults.standard.set(projectDirectory, forKey: "projectDirectory")
        appState.markConfigured()
    }

    private func runCommand(_ command: String, arguments: [String]) async -> String {
        await withCheckedContinuation { continuation in
            DispatchQueue.global(qos: .userInitiated).async {
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

    private func defaultWorkspaceDirectory() -> String {
        let baseURL = FileManager.default.urls(
            for: .applicationSupportDirectory,
            in: .userDomainMask
        ).first ?? URL(fileURLWithPath: NSHomeDirectory())
        return baseURL.appendingPathComponent("LocalSmartz").path
    }
}
