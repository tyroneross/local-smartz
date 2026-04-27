import SwiftUI
import AppKit

/// First-run wizard. Keeps the original detection checks (Python, Local
/// Smartz, Workspace, Ollama) and layers model readiness on top by spawning
/// a *temporary* backend on a free port, calling `/api/status`, and — if
/// any models are missing — streaming `/api/setup` progress to the user.
///
/// The temporary backend is terminated on disappear or when Get Started is
/// clicked; `BackendManager` then spawns its own long-running server on the
/// standard port for the main app session.
struct SetupView: View {
    @EnvironmentObject var appState: AppState

    // Detection state
    @State private var pythonPath = ""
    @State private var projectDirectory = ""
    @State private var localsmartzReady = false
    @State private var ollamaReady = false
    @State private var statusText = ""
    @State private var errorText = ""

    // Setup/model state
    @State private var statusChecked = false
    @State private var missingModels: [String] = []
    @State private var modelsReady = false
    @State private var ramGB: Int?

    @State private var isDownloading = false
    @State private var progresses: [ModelDownloadProgress] = []
    @State private var currentStep: String?
    @State private var downloadError: String?

    // Temporary backend
    @State private var tempBackend: TempBackend?
    @State private var showPythonChangeConfirm = false

    var body: some View {
        VStack(spacing: 14) {
            // Single compact header block (icon + title + subtitle together)
            // — avoids the "floating logo, gap, title, gap, subtitle" stack
            // that used to push Get Started off the window on shorter screens.
            VStack(spacing: 6) {
                Image(systemName: "magnifyingglass.circle.fill")
                    .font(.system(size: 34))
                    .foregroundStyle(.secondary)
                Text("Local Smartz")
                    .font(.system(size: 20, weight: .semibold))
                Text("Local-first research powered by Ollama")
                    .font(.system(size: 14))
                    .foregroundStyle(.secondary)
            }
            .padding(.top, 4)

            // Detection checklist — single border around the group.
            VStack(spacing: 0) {
                stepRow(
                    title: "Python",
                    detail: pythonPath.isEmpty ? "Detecting..." : pythonPath,
                    done: !pythonPath.isEmpty,
                    help: SetupHelp.python,
                    changeLabel: pythonPath.isEmpty ? nil : "Change…",
                    onChange: { showPythonChangeConfirm = true }
                )
                Divider()
                stepRow(
                    title: "Local Smartz",
                    detail: localsmartzReady ? "Installed in selected Python" : "Not installed yet",
                    done: localsmartzReady,
                    help: SetupHelp.localSmartz,
                    changeLabel: nil,
                    onChange: nil
                )
                Divider()
                stepRow(
                    title: "Workspace",
                    detail: projectDirectory.isEmpty ? defaultWorkspaceDirectory() : projectDirectory,
                    done: !projectDirectory.isEmpty,
                    help: SetupHelp.workspace,
                    changeLabel: "Change…",
                    onChange: { chooseWorkspace() }
                )
                Divider()
                stepRow(
                    title: "Ollama",
                    detail: ollamaReady ? "Running" : "Not running",
                    done: ollamaReady,
                    help: SetupHelp.ollama,
                    changeLabel: nil,
                    onChange: nil
                )
                Divider()
                stepRow(
                    title: "Models",
                    detail: modelsDetailText,
                    done: modelsReady,
                    help: SetupHelp.models,
                    changeLabel: nil,
                    onChange: nil
                )
            }
            .frame(maxWidth: 480)
            .overlay(
                RoundedRectangle(cornerRadius: 8)
                    .stroke(Color.secondary.opacity(0.25), lineWidth: 1)
            )

            // Missing models action / progress.
            if !missingModels.isEmpty && !isDownloading && !modelsReady {
                missingModelsPrompt
            }

            if isDownloading || !progresses.isEmpty {
                SetupProgressView(
                    progresses: progresses,
                    currentStep: currentStep,
                    isComplete: modelsReady,
                    errorMessage: downloadError
                )
            }

            if !statusText.isEmpty && !isDownloading {
                Text(statusText)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            if !errorText.isEmpty {
                Text(errorText)
                    .font(.caption)
                    .foregroundStyle(.red)
                    .frame(maxWidth: 480)
                    .fixedSize(horizontal: false, vertical: true)
            }

            // Flexible breathing room — capped so Get Started sits close to
            // the "ready" message on tall windows instead of pinning to the
            // bottom. Still compresses on short windows (min 8, max 32).
            Spacer(minLength: 8).frame(maxHeight: 32)

            Button {
                completeSetup()
            } label: {
                Text("Get Started")
                    .font(.system(size: 16, weight: .medium))
                    .frame(minWidth: 140)
                    .padding(.vertical, 4)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
            .disabled(!canCompleteSetup)
            .keyboardShortcut(.defaultAction)
            .accessibilityLabel("Get Started")
        }
        .padding(.horizontal, 24)
        .padding(.vertical, 16)
        .frame(minHeight: 560)
        .task { await runInitialDetection() }
        .onDisappear { shutdownTempBackend() }
        .sheet(isPresented: $showPythonChangeConfirm) {
            pythonChangeConfirmSheet
        }
    }

    // MARK: - Python-change confirmation sheet

    private var pythonChangeConfirmSheet: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(spacing: 10) {
                Image(systemName: "terminal")
                    .font(.system(size: 18))
                    .foregroundStyle(.secondary)
                Text("Change Python interpreter?")
                    .font(.system(size: 15, weight: .semibold))
            }

            Group {
                helpBlock(
                    "Current",
                    pythonPath.isEmpty ? "not set" : pythonPath,
                    mono: true
                )

                helpBlock(
                    "Recommended",
                    "The bundled Python inside the .app (default) — it has `localsmartz` pre-installed. Most users should leave this alone."
                )

                helpBlock(
                    "When to change it",
                    "• You manage your own Python environment (uv, pipx, conda) and want Local Smartz to use it.\n• You have custom libraries installed in a specific Python env that your plugins need.\n• You're developing Local Smartz itself and want to point at a `pip install -e` editable install."
                )

                helpBlock(
                    "What you need in the replacement Python",
                    "• Python 3.12 or later.\n• `localsmartz` package importable: verify with `python3 -c \"import localsmartz\"`.\n• If missing, run `pip install -e <path-to-local-smartz-repo>` in that Python first."
                )

                helpBlock(
                    "Impact",
                    "• The app will spawn the backend using the Python you pick for every launch.\n• If the replacement is missing `localsmartz`, setup fails and Get Started is disabled.\n• You can always come back here to revert."
                )
            }

            Spacer()

            HStack {
                Button("Cancel") {
                    showPythonChangeConfirm = false
                }
                .keyboardShortcut(.cancelAction)

                Spacer()

                Button("Open file picker…") {
                    showPythonChangeConfirm = false
                    choosePython()
                }
                .buttonStyle(.borderedProminent)
                .keyboardShortcut(.defaultAction)
                .accessibilityLabel("Open file picker to choose Python interpreter")
            }
        }
        .padding(24)
        .frame(width: 520, height: 500)
    }

    private func helpBlock(_ label: String, _ body: String, mono: Bool = false) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label)
                .font(.system(size: 13, weight: .medium))
                .foregroundStyle(.secondary)
            Text(body)
                .font(mono ? .system(size: 12, design: .monospaced) : .system(size: 12))
                .foregroundStyle(.primary)
                .fixedSize(horizontal: false, vertical: true)
                .textSelection(.enabled)
        }
    }

    // MARK: - Derived

    private var canCompleteSetup: Bool {
        !pythonPath.isEmpty
            && localsmartzReady
            && !projectDirectory.isEmpty
            && ollamaReady
            && modelsReady
            && !isDownloading
    }

    private var modelsDetailText: String {
        if !ollamaReady { return "Waiting for Ollama" }
        if !statusChecked { return "Checking..." }
        if modelsReady { return "All required models installed" }
        if missingModels.isEmpty { return "Checking..." }
        let list = missingModels.joined(separator: ", ")
        return "Missing: \(list)"
    }

    private var missingModelsPrompt: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Required models are not installed yet.")
                .font(.system(size: 16, weight: .medium))

            if let ram = ramGB {
                Text("Detected \(ram) GB RAM. Downloads may take several minutes.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            } else {
                Text("Downloads may take several minutes depending on connection.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Button("Download required models") {
                Task { await runSetupStream() }
            }
            .buttonStyle(.borderedProminent)
            .disabled(isDownloading)
        }
        .frame(maxWidth: 480, alignment: .leading)
    }

    // MARK: - Rows

    @ViewBuilder
    private func stepRow(
        title: String,
        detail: String,
        done: Bool,
        help: SetupHelp? = nil,
        changeLabel: String? = nil,
        onChange: (() -> Void)? = nil
    ) -> some View {
        StepRow(
            title: title,
            detail: detail,
            done: done,
            help: help,
            changeLabel: changeLabel,
            onChange: onChange
        )
    }

    // MARK: - Detection

    private func runInitialDetection() async {
        await detectPython()
        if ollamaReady && localsmartzReady {
            await refreshModelStatus()
        }
    }

    private func detectPython() async {
        statusText = "Looking for Python..."
        errorText = ""
        localsmartzReady = false
        ollamaReady = false
        modelsReady = false
        statusChecked = false
        missingModels = []

        projectDirectory = UserDefaults.standard.string(forKey: "projectDirectory")
            ?? defaultWorkspaceDirectory()

        // Prefer any Python that actually imports `localsmartz`. If the user
        // installed via `uv tool install .`, the real Python is inside a
        // private venv — try it first. This keeps `localsmartz --version`
        // useful during setup even when no system Python has the package.
        // Bundled Python (shipped in the .app) comes first so a DMG install
        // on a clean Mac works out of the box.
        let home = NSHomeDirectory()
        let bundledPython = Bundle.main.bundleURL
            .appendingPathComponent("Contents/Resources/python/bin/python3")
            .path
        let candidates = [
            bundledPython,
            "\(home)/.local/share/uv/tools/localsmartz/bin/python3",
            "/opt/homebrew/bin/python3",
            "/usr/local/bin/python3",
            "\(home)/.local/bin/python3",
            "/usr/bin/python3",
        ]

        for path in candidates {
            guard FileManager.default.fileExists(atPath: path),
                  await verifyPython(path) else { continue }
            // Prefer a Python where `import localsmartz` actually works.
            if await pythonImportsLocalsmartz(path) {
                pythonPath = path
                break
            }
            // Fall back to "first valid Python" if nothing we find has the module.
            if pythonPath.isEmpty { pythonPath = path }
        }

        if pythonPath.isEmpty {
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

    private func pythonImportsLocalsmartz(_ python: String) async -> Bool {
        let output = await runCommand(python, arguments: ["-c", "import localsmartz"])
        // A clean import produces no stdout/stderr; errors surface as text.
        return output.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    private func validateEnvironment() async {
        statusText = "Checking Local Smartz..."
        await checkLocalSmartz()
        guard localsmartzReady else { return }
        await checkOllama()
        if ollamaReady {
            await refreshModelStatus()
        }
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
                statusText = ""
                return
            }
        } catch {
            // fall through
        }
        ollamaReady = false
        statusText = "Local Smartz is installed. Start Ollama before researching."
    }

    // MARK: - Temporary backend + status

    private func refreshModelStatus() async {
        statusText = "Checking installed models..."
        do {
            let backend = try await ensureTempBackend()
            let client = SetupSSEClient(baseURL: backend.baseURL)
            let status = try await client.fetchStatus()
            missingModels = status.missingModels
            ramGB = status.ramGB
            modelsReady = status.ready
            statusChecked = true
            if status.ready {
                statusText = "Local Smartz is ready."
            } else if !status.missingModels.isEmpty {
                statusText = "Models need to be downloaded before researching."
            } else {
                statusText = ""
            }
        } catch {
            statusChecked = true
            statusText = ""
            // Phrase this as a model-status failure, not an Ollama failure.
            // ollamaReady may already be true (Ollama IS running); the failure
            // is the temp backend for model-readiness checks, not Ollama itself.
            errorText = "Could not check model status (the setup helper could not start): \(error.localizedDescription)"
        }
    }

    private func runSetupStream() async {
        guard !isDownloading else { return }
        do {
            let backend = try await ensureTempBackend()
            isDownloading = true
            downloadError = nil
            currentStep = "Starting download..."
            progresses = missingModels.map { ModelDownloadProgress(id: $0) }

            let client = SetupSSEClient(baseURL: backend.baseURL)
            let stream = await client.startSetup()

            for try await event in stream {
                handleSetupEvent(event)
            }

            await refreshModelStatus()
        } catch {
            downloadError = error.localizedDescription
        }
        isDownloading = false
    }

    private func handleSetupEvent(_ event: SetupEvent) {
        switch event {
        case .step(let message):
            currentStep = message
            // "Model X: ready" → mark that model complete.
            if message.hasPrefix("Model "), message.hasSuffix(": ready") {
                let trimmed = message.dropFirst("Model ".count).dropLast(": ready".count)
                let modelName = String(trimmed)
                updateProgress(model: modelName) { p in
                    p.isComplete = true
                    p.percent = 100
                }
            }
        case .progress(let model, let percent, let downloaded, let total):
            updateProgress(model: model) { p in
                if let percent { p.percent = percent }
                if downloaded > 0 { p.downloadedMB = downloaded }
                if total > 0 { p.totalMB = total }
            }
            currentStep = "Downloading \(model)..."
        case .done:
            currentStep = "Setup complete."
            for index in progresses.indices {
                progresses[index].isComplete = true
                progresses[index].percent = 100
            }
        case .error(let message):
            downloadError = message
        }
    }

    private func updateProgress(model: String, _ mutate: (inout ModelDownloadProgress) -> Void) {
        if let idx = progresses.firstIndex(where: { $0.id == model }) {
            mutate(&progresses[idx])
        } else {
            var entry = ModelDownloadProgress(id: model)
            mutate(&entry)
            progresses.append(entry)
        }
    }

    /// Spawn a throwaway backend if one isn't already running, and wait for
    /// `/api/health` to respond. Reuses any previous temp backend this view
    /// started.
    private func ensureTempBackend() async throws -> TempBackend {
        if let existing = tempBackend, existing.isAlive {
            return existing
        }

        let port = TempBackend.findFreePort(startingAt: 11450)
        let backend = TempBackend(
            pythonPath: pythonPath,
            workingDirectory: projectDirectory.isEmpty ? defaultWorkspaceDirectory() : projectDirectory,
            port: port
        )

        try FileManager.default.createDirectory(
            atPath: backend.workingDirectory,
            withIntermediateDirectories: true
        )

        try backend.launch()
        let ready = await backend.waitForHealth(timeoutSeconds: 15)
        if !ready {
            backend.terminate()
            throw SetupSSEError.badResponse(statusCode: nil)
        }
        tempBackend = backend
        return backend
    }

    private func shutdownTempBackend() {
        tempBackend?.terminate()
        tempBackend = nil
    }

    // MARK: - File panels

    private func choosePython() {
        let panel = NSOpenPanel()
        panel.title = "Select python3 binary"
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        if panel.runModal() == .OK, let url = panel.url {
            pythonPath = url.path
            shutdownTempBackend()  // python path changed; force fresh spawn
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

    // MARK: - Completion

    private func completeSetup() {
        try? FileManager.default.createDirectory(
            atPath: projectDirectory,
            withIntermediateDirectories: true,
            attributes: nil
        )
        UserDefaults.standard.set(pythonPath, forKey: "pythonPath")
        UserDefaults.standard.set(projectDirectory, forKey: "projectDirectory")
        shutdownTempBackend()
        appState.markConfigured()
    }

    // MARK: - Shell

    private func verifyPython(_ path: String) async -> Bool {
        let result = await runCommand(path, arguments: ["--version"])
        return result.contains("Python 3")
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

// MARK: - Temporary backend helper

/// A short-lived `localsmartz --serve` process used *only* during Setup to
/// query `/api/status` and stream `/api/setup`. Lives independently of the
/// long-running `BackendManager` so the main app can spawn its own on the
/// standard port after Get Started is clicked.
private final class TempBackend {
    let pythonPath: String
    let workingDirectory: String
    let port: Int
    private var process: Process?

    var baseURL: URL {
        URL(string: "http://localhost:\(port)")!
    }

    var isAlive: Bool {
        process?.isRunning == true
    }

    init(pythonPath: String, workingDirectory: String, port: Int) {
        self.pythonPath = pythonPath
        self.workingDirectory = workingDirectory
        self.port = port
    }

    func launch() throws {
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: pythonPath)
        proc.arguments = ["-m", "localsmartz", "--serve", "--port", "\(port)"]
        proc.currentDirectoryURL = URL(fileURLWithPath: workingDirectory)

        // Force unbuffered output so the log file fills in real time.
        var env = ProcessInfo.processInfo.environment
        env["PYTHONUNBUFFERED"] = "1"
        proc.environment = env

        // Route stdout+stderr to the shared backend log file (same path as
        // BackendManager). Using an O_APPEND file descriptor instead of
        // Pipe() eliminates the pipe-buffer-fill deadlock that can silently
        // kill the child on busy stderr output, and gives us a durable crash
        // trace. O_APPEND is required to avoid clobbering the main
        // BackendManager's writes if both live briefly.
        let logPath = BackendManager.defaultLogPath()
        let logDir = (logPath as NSString).deletingLastPathComponent
        try FileManager.default.createDirectory(
            atPath: logDir,
            withIntermediateDirectories: true,
            attributes: nil
        )
        let fd = open(logPath, O_WRONLY | O_CREAT | O_APPEND, 0o644)
        if fd == -1 {
            throw NSError(
                domain: NSPOSIXErrorDomain,
                code: Int(errno),
                userInfo: [NSLocalizedDescriptionKey: "open(\(logPath)) failed: errno=\(errno)"]
            )
        }
        let logHandle = FileHandle(fileDescriptor: fd, closeOnDealloc: true)
        let banner = "\n--- TempBackend spawn @ \(Date()) port=\(port) ---\n"
        if let data = banner.data(using: .utf8) {
            try? logHandle.write(contentsOf: data)
        }
        proc.standardOutput = logHandle
        proc.standardError = logHandle

        try proc.run()
        process = proc
    }

    func terminate() {
        guard let proc = process, proc.isRunning else {
            process = nil
            return
        }
        proc.terminate()
        proc.waitUntilExit()
        process = nil
    }

    func waitForHealth(timeoutSeconds: Int) async -> Bool {
        let url = baseURL.appendingPathComponent("api/health")
        let deadline = Date().addingTimeInterval(TimeInterval(timeoutSeconds))
        while Date() < deadline {
            var request = URLRequest(url: url)
            request.timeoutInterval = 1.5
            if let (_, response) = try? await URLSession.shared.data(for: request),
               let http = response as? HTTPURLResponse,
               http.statusCode == 200 {
                return true
            }
            try? await Task.sleep(for: .milliseconds(500))
        }
        return false
    }

    /// Probe ports starting at `startingAt` for one that accepts bind. If
    /// none found in range, returns the starting port (the launch will fail
    /// loudly rather than silently).
    static func findFreePort(startingAt start: Int) -> Int {
        for candidate in start..<(start + 20) {
            if isPortFree(candidate) {
                return candidate
            }
        }
        return start
    }

    private static func isPortFree(_ port: Int) -> Bool {
        let fd = socket(AF_INET, SOCK_STREAM, 0)
        guard fd != -1 else { return false }
        defer { close(fd) }

        var addr = sockaddr_in()
        addr.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = in_port_t(port).bigEndian
        addr.sin_addr.s_addr = inet_addr("127.0.0.1")

        let bound = withUnsafePointer(to: &addr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                bind(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        return bound == 0
    }
}
