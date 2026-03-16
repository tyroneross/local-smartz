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

        let pythonPath = UserDefaults.standard.string(forKey: "pythonPath") ?? "/usr/bin/python3"
        let projectDir = UserDefaults.standard.string(forKey: "projectDirectory") ?? NSHomeDirectory()

        let process = Process()
        process.executableURL = URL(fileURLWithPath: pythonPath)
        process.arguments = ["-m", "localsmartz", "--serve", "--port", "\(testPort)"]
        process.currentDirectoryURL = URL(fileURLWithPath: projectDir)

        let errorPipe = Pipe()
        process.standardError = errorPipe

        do {
            try process.run()
            self.process = process

            // Monitor stderr
            Task.detached { [weak self] in
                let handle = errorPipe.fileHandleForReading
                while let data = try? handle.availableData, !data.isEmpty {
                    if let errorStr = String(data: data, encoding: .utf8) {
                        await MainActor.run {
                            if let self = self, !self.isRunning {
                                self.errorMessage = errorStr.trimmingCharacters(in: .whitespacesAndNewlines)
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
}
