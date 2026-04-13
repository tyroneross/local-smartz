import Foundation

/// High-level events emitted by the Local Smartz setup SSE stream.
///
/// The Python backend (`serve.py :: _handle_setup`) emits the shared event
/// types used by the research stream (`text`, `tool_error`, `done`, `error`).
/// The setup flow does not currently emit discrete percent/MB progress — we
/// surface each `text` message as a step and promote any "Downloading X..."
/// messages into per-model progress markers so the UI can render them.
enum SetupEvent {
    /// Generic status line (e.g. "Ollama is running.", "Model X: ready").
    case step(String)
    /// A model pull has started / is in progress. The backend currently does
    /// not stream percent values, so `percent == nil` means indeterminate.
    /// Fields default to 0 when the backend doesn't supply them.
    case progress(model: String, percent: Int?, downloadedMB: Int, totalMB: Int)
    /// Setup completed successfully.
    case done
    /// Fatal setup error.
    case error(String)
}

/// SSE client targeting the Local Smartz `/api/setup` and `/api/status`
/// endpoints on a temporary backend process.
actor SetupSSEClient {
    struct Status: Decodable {
        let ready: Bool
        let missingModels: [String]
        let ramGb: Int?

        var missingModelsList: [String] { missingModels }
        var ramGB: Int? { ramGb }
    }

    private let baseURL: URL
    private var activeTask: Task<Void, Never>?

    init(baseURL: URL) {
        self.baseURL = baseURL
    }

    /// POST `/api/setup` and stream parsed events. The caller owns iteration;
    /// terminating the stream cancels the underlying request.
    func startSetup() -> AsyncThrowingStream<SetupEvent, Error> {
        AsyncThrowingStream { continuation in
            let url = baseURL.appendingPathComponent("api/setup")
            var request = URLRequest(url: url)
            request.httpMethod = "POST"
            request.setValue("text/event-stream", forHTTPHeaderField: "Accept")
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = "{}".data(using: .utf8)
            request.timeoutInterval = 600

            activeTask = Task {
                do {
                    let (bytes, response) = try await URLSession.shared.bytes(for: request)
                    guard let http = response as? HTTPURLResponse else {
                        continuation.finish(throwing: SetupSSEError.badResponse(statusCode: nil))
                        return
                    }
                    guard http.statusCode == 200 else {
                        continuation.finish(
                            throwing: SetupSSEError.badResponse(statusCode: http.statusCode)
                        )
                        return
                    }

                    var buffer = ""
                    for try await byte in bytes {
                        let char = Character(UnicodeScalar(byte))
                        buffer.append(char)
                        if buffer.hasSuffix("\n\n") {
                            for line in buffer.components(separatedBy: "\n") {
                                guard line.hasPrefix("data: ") else { continue }
                                let json = String(line.dropFirst(6))
                                if let data = json.data(using: .utf8),
                                   let event = Self.parse(jsonData: data) {
                                    continuation.yield(event)
                                    if case .done = event { continuation.finish(); return }
                                }
                            }
                            buffer = ""
                        }
                    }
                    continuation.finish()
                } catch {
                    if Task.isCancelled {
                        continuation.finish()
                    } else {
                        continuation.finish(throwing: error)
                    }
                }
            }

            continuation.onTermination = { @Sendable _ in
                Task { await self.cancel() }
            }
        }
    }

    /// GET `/api/status` — one-shot snapshot of readiness and missing models.
    func fetchStatus() async throws -> Status {
        let url = baseURL.appendingPathComponent("api/status")
        var request = URLRequest(url: url)
        request.timeoutInterval = 5
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            let code = (response as? HTTPURLResponse)?.statusCode
            throw SetupSSEError.badResponse(statusCode: code)
        }
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(Status.self, from: data)
    }

    func cancel() {
        activeTask?.cancel()
        activeTask = nil
    }

    // MARK: - Parsing

    /// Translate the raw backend event JSON into a `SetupEvent`. The server
    /// uses the shared event vocabulary (`text` / `tool_error` / `done` /
    /// `error`); we heuristically promote "Downloading <model>..." text
    /// events to `.progress` so the UI can render them per-model.
    private static func parse(jsonData: Data) -> SetupEvent? {
        guard let dict = try? JSONSerialization.jsonObject(with: jsonData) as? [String: Any],
              let type = dict["type"] as? String else {
            return nil
        }
        switch type {
        case "text", "step":
            let message = (dict["content"] as? String) ?? (dict["message"] as? String) ?? ""
            if let model = parseDownloadingModel(from: message) {
                return .progress(model: model, percent: nil, downloadedMB: 0, totalMB: 0)
            }
            return .step(message)
        case "progress":
            // Forward-compatible: if the backend ever emits discrete progress,
            // honour it here. Fields tolerate absence.
            let model = (dict["model"] as? String) ?? ""
            let percent = dict["percent"] as? Int
            let downloaded = dict["downloaded_mb"] as? Int ?? 0
            let total = dict["total_mb"] as? Int ?? 0
            return .progress(
                model: model,
                percent: percent,
                downloadedMB: downloaded,
                totalMB: total
            )
        case "done":
            return .done
        case "tool_error":
            let message = (dict["message"] as? String) ?? "Setup step failed"
            return .error(message)
        case "error":
            let message = (dict["message"] as? String) ?? "Unknown setup error"
            return .error(message)
        default:
            return nil
        }
    }

    /// "Downloading llama3.1:8b..." → "llama3.1:8b"
    private static func parseDownloadingModel(from message: String) -> String? {
        let prefix = "Downloading "
        guard message.hasPrefix(prefix) else { return nil }
        var rest = String(message.dropFirst(prefix.count))
        if rest.hasSuffix("...") { rest = String(rest.dropLast(3)) }
        let trimmed = rest.trimmingCharacters(in: .whitespaces)
        return trimmed.isEmpty ? nil : trimmed
    }
}

enum SetupSSEError: Error, LocalizedError {
    case badResponse(statusCode: Int?)

    var errorDescription: String? {
        switch self {
        case .badResponse(let code):
            if let code { return "Setup backend returned HTTP \(code)" }
            return "Setup backend returned an unexpected response"
        }
    }
}
