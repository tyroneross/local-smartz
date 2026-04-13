import Foundation
import AppKit

@MainActor
class BackendManager: ObservableObject {
    @Published var isRunning = false
    @Published var port = 11435
    @Published var errorMessage: String?

    private var process: Process?

    var baseURL: String {
        "http://localhost:\(port)"
    }

    init() {
        NotificationCenter.default.addObserver(
            self,
            selector: #selector(applicationWillTerminate),
            name: NSApplication.willTerminateNotification,
            object: nil
        )
    }

    deinit {
        NotificationCenter.default.removeObserver(self)
    }

    @objc private func applicationWillTerminate() {
        stop()
    }

    func start() async {
        guard !isRunning else { return }
        errorMessage = nil

        // Try to find an available port starting from 11435
        var testPort = 11435
        var foundPort = false

        for attempt in 0..<10 {
            testPort = 11435 + attempt

            // Check if backend is already running on this port
            if await healthCheck(port: testPort) {
                self.port = testPort
                self.isRunning = true
                return
            }

            if isPortAvailable(testPort) {
                foundPort = true
                break
            }
        }

        guard foundPort else {
            errorMessage = "Could not find available port in range 11435-11445"
            return
        }

        self.port = testPort

        // Write-through: ensure UserDefaults mirrors the latest GlobalSettings
        // so any downstream reader (including resolveBackendSpawn fallbacks)
        // sees the user's current Settings tab values.
        syncGlobalSettingsToUserDefaults()

        guard let spawn = resolveBackendSpawn() else {
            errorMessage = """
                Could not find `localsmartz` on disk. Install it with:
                  uv tool install /path/to/local-smartz
                or
                  pip install -e /path/to/local-smartz
                Then restart the app.
                """
            return
        }
        let projectDir = effectiveWorkspaceDirectory()

        try? FileManager.default.createDirectory(
            atPath: projectDir,
            withIntermediateDirectories: true,
            attributes: nil
        )

        let process = Process()
        process.executableURL = URL(fileURLWithPath: spawn.executable)
        process.arguments = spawn.arguments + ["--serve", "--port", "\(testPort)"]
        process.currentDirectoryURL = URL(fileURLWithPath: projectDir)
        // Ensure the spawned process sees a sensible PATH even outside a login shell.
        var env = ProcessInfo.processInfo.environment
        let addPaths = [
            "\(NSHomeDirectory())/.local/bin",
            "/opt/homebrew/bin",
            "/usr/local/bin",
            "/usr/bin",
            "/bin",
        ]
        let existingPath = env["PATH"] ?? ""
        let pathParts = existingPath.split(separator: ":").map(String.init)
        let merged = addPaths + pathParts.filter { !addPaths.contains($0) }
        env["PATH"] = merged.joined(separator: ":")

        // Telemetry: propagate the user's Settings > Telemetry toggle into the
        // child process env. Toggle off must actively strip any inherited
        // value (users commonly set LOCALSMARTZ_OBSERVE=1 in their shell rc).
        if UserDefaults.standard.bool(forKey: "LOCALSMARTZ_OBSERVE") {
            env["LOCALSMARTZ_OBSERVE"] = "1"
        } else {
            env.removeValue(forKey: "LOCALSMARTZ_OBSERVE")
        }

        process.environment = env

        let errorPipe = Pipe()
        process.standardError = errorPipe

        do {
            try process.run()
            self.process = process

            // Monitor stderr
            Task.detached { [weak self] in
                let handle = errorPipe.fileHandleForReading
                while true {
                    let data = handle.availableData
                    guard !data.isEmpty else { break }
                    if let errorStr = String(data: data, encoding: .utf8) {
                        await MainActor.run { [weak self] in
                            if let self, !self.isRunning {
                                self.errorMessage = errorStr.trimmingCharacters(
                                    in: .whitespacesAndNewlines
                                )
                            }
                        }
                    }
                }
            }

            // Wait for backend readiness (up to 15s)
            for _ in 0..<30 {
                try? await Task.sleep(for: .milliseconds(500))
                if await healthCheck(port: testPort) {
                    self.isRunning = true
                    return
                }
            }

            process.terminate()
            self.process = nil
            errorMessage = "Backend failed to start within 15 seconds"

        } catch {
            errorMessage = "Failed to launch backend: \(error.localizedDescription)"
        }
    }

    func stop() {
        guard let process = process else { return }
        if process.isRunning {
            process.terminate()
            process.waitUntilExit()
        }
        self.process = nil
        self.isRunning = false
    }

    private func healthCheck(port: Int) async -> Bool {
        guard let url = URL(string: "http://localhost:\(port)/api/health") else {
            return false
        }
        var request = URLRequest(url: url)
        request.timeoutInterval = 2
        do {
            let (_, response) = try await URLSession.shared.data(for: request)
            if let http = response as? HTTPURLResponse {
                return http.statusCode == 200
            }
            return false
        } catch {
            return false
        }
    }

    private func isPortAvailable(_ port: Int) -> Bool {
        let socketFD = socket(AF_INET, SOCK_STREAM, 0)
        guard socketFD != -1 else { return false }

        var addr = sockaddr_in()
        addr.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = in_port_t(port).bigEndian
        addr.sin_addr.s_addr = inet_addr("127.0.0.1")

        let result = withUnsafePointer(to: &addr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                bind(socketFD, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        close(socketFD)
        return result == 0
    }

    /// What to invoke to start the backend.
    struct BackendSpawn {
        let executable: String       // absolute path, ready for Process.executableURL
        let arguments: [String]      // args that go BEFORE --serve (e.g. ["-m","localsmartz"] for raw Python)
    }

    /// Resolve how to spawn the backend.
    ///
    /// Priority:
    ///   1. Bundled Python + localsmartz inside the .app
    ///      (produced by `app/scripts/embed-python.sh` + `build-dmg.sh`).
    ///   2. `localsmartz` executable shim on disk — preferred because it carries
    ///      its own venv Python in its shebang, so we don't need to guess.
    ///   3. A user-configured `pythonPath` (set during Setup) IFF that Python
    ///      can import localsmartz.
    ///
    /// Returns nil when nothing viable is found — caller surfaces a clear error.
    private func resolveBackendSpawn() -> BackendSpawn? {
        let fm = FileManager.default

        // 1. Bundled python + package inside the .app
        let bundledPython = Bundle.main.bundleURL
            .appendingPathComponent("Contents/Resources/python/bin/python3")
            .path
        if fm.isExecutableFile(atPath: bundledPython),
           pythonHasLocalsmartz(bundledPython) {
            return BackendSpawn(executable: bundledPython, arguments: ["-m", "localsmartz"])
        }

        // 2. Localsmartz shim anywhere common.
        let home = NSHomeDirectory()
        let candidateShims = [
            "\(home)/.local/bin/localsmartz",
            "/opt/homebrew/bin/localsmartz",
            "/usr/local/bin/localsmartz",
        ]
        for path in candidateShims where fm.isExecutableFile(atPath: path) {
            return BackendSpawn(executable: path, arguments: [])
        }

        // 3. User-configured Python that actually imports localsmartz.
        //    Prefer the GlobalSettings value (edited in Settings > Python) over
        //    the legacy UserDefaults key — they can diverge during the
        //    transition. Fall back to UserDefaults for backward compat.
        let globalPython = GlobalSettings.load().pythonPath
        if !globalPython.isEmpty,
           fm.isExecutableFile(atPath: globalPython),
           pythonHasLocalsmartz(globalPython) {
            return BackendSpawn(executable: globalPython, arguments: ["-m", "localsmartz"])
        }
        if let configured = UserDefaults.standard.string(forKey: "pythonPath"),
           fm.isExecutableFile(atPath: configured),
           pythonHasLocalsmartz(configured) {
            return BackendSpawn(executable: configured, arguments: ["-m", "localsmartz"])
        }

        // 4. Try the uv tool venv directly as a last resort.
        let uvToolPython = "\(home)/.local/share/uv/tools/localsmartz/bin/python3"
        if fm.isExecutableFile(atPath: uvToolPython),
           pythonHasLocalsmartz(uvToolPython) {
            return BackendSpawn(executable: uvToolPython, arguments: ["-m", "localsmartz"])
        }

        return nil
    }

    /// Synchronous check: does `python` import the `localsmartz` module?
    /// Cheap because it's only run at startup and when the user reconfigures.
    private func pythonHasLocalsmartz(_ python: String) -> Bool {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: python)
        p.arguments = ["-c", "import localsmartz"]
        p.standardOutput = Pipe()
        p.standardError = Pipe()
        do {
            try p.run()
            p.waitUntilExit()
            return p.terminationStatus == 0
        } catch {
            return false
        }
    }

    private func defaultWorkspaceDirectory() -> String {
        let baseURL = FileManager.default.urls(
            for: .applicationSupportDirectory,
            in: .userDomainMask
        ).first ?? URL(fileURLWithPath: NSHomeDirectory())
        return baseURL.appendingPathComponent("LocalSmartz").path
    }

    /// Resolve the effective workspace: prefer GlobalSettings (~/.localsmartz/global.json)
    /// when the path is non-empty and exists, then fall back to the legacy
    /// UserDefaults key, then the bundled default. The Settings tab writes to
    /// GlobalSettings; this ensures those edits reach the spawned backend.
    private func effectiveWorkspaceDirectory() -> String {
        let fm = FileManager.default
        let ws = GlobalSettings.load().workspace
        if !ws.isEmpty {
            var isDir: ObjCBool = false
            if fm.fileExists(atPath: ws, isDirectory: &isDir), isDir.boolValue {
                return ws
            }
            // Non-existent paths are still acceptable — we create the dir above.
            // Only skip purely empty values.
            return ws
        }
        if let legacy = UserDefaults.standard.string(forKey: "projectDirectory"),
           !legacy.isEmpty {
            return legacy
        }
        return defaultWorkspaceDirectory()
    }

    /// Write GlobalSettings values through to the legacy UserDefaults keys so
    /// any code still reading UserDefaults (AppState, Setup, etc.) sees a
    /// consistent view after the user saves in Settings. Only overwrites when
    /// GlobalSettings has a non-empty value — never clobbers legacy data with
    /// empties.
    private func syncGlobalSettingsToUserDefaults() {
        let g = GlobalSettings.load()
        let defaults = UserDefaults.standard
        if !g.workspace.isEmpty {
            defaults.set(g.workspace, forKey: "projectDirectory")
        }
        if !g.pythonPath.isEmpty {
            defaults.set(g.pythonPath, forKey: "pythonPath")
        }
    }
}
