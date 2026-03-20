import Foundation

actor SSEClient {
    private var activeTask: Task<Void, Never>?

    func stream(url: URL) -> AsyncThrowingStream<SSEEvent, Error> {
        let request = URLRequest(url: url)
        return stream(request: request)
    }

    func stream(request: URLRequest) -> AsyncThrowingStream<SSEEvent, Error> {
        AsyncThrowingStream { continuation in
            activeTask = Task {
                do {
                    var request = request
                    request.timeoutInterval = 600
                    if request.value(forHTTPHeaderField: "Accept") == nil {
                        request.setValue("text/event-stream", forHTTPHeaderField: "Accept")
                    }

                    let (bytes, response) = try await URLSession.shared.bytes(for: request)

                    guard let http = response as? HTTPURLResponse else {
                        continuation.finish(throwing: SSEError.badResponse(statusCode: nil, message: nil))
                        return
                    }

                    guard http.statusCode == 200 else {
                        var responseBody = ""
                        for try await byte in bytes {
                            responseBody.append(Character(UnicodeScalar(byte)))
                        }

                        let message = SSEClient.errorMessage(from: responseBody)
                        continuation.finish(
                            throwing: SSEError.badResponse(
                                statusCode: http.statusCode,
                                message: message
                            )
                        )
                        return
                    }

                    var buffer = ""
                    for try await byte in bytes {
                        let char = Character(UnicodeScalar(byte))
                        buffer.append(char)

                        if buffer.hasSuffix("\n\n") {
                            let lines = buffer.components(separatedBy: "\n")
                            for line in lines {
                                if line.hasPrefix("data: ") {
                                    let jsonStr = String(line.dropFirst(6))
                                    if let data = jsonStr.data(using: .utf8),
                                       let event = SSEEvent.parse(from: data) {
                                        continuation.yield(event)
                                    }
                                }
                            }
                            buffer = ""
                        }
                    }
                    continuation.finish()
                } catch {
                    if !Task.isCancelled {
                        continuation.finish(throwing: error)
                    } else {
                        continuation.finish()
                    }
                }
            }

            continuation.onTermination = { @Sendable _ in
                Task {
                    await self.activeTask?.cancel()
                }
            }
        }
    }

    private static func errorMessage(from responseBody: String) -> String? {
        guard !responseBody.isEmpty,
              let data = responseBody.data(using: .utf8) else {
            return nil
        }

        if let dict = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let error = dict["error"] as? String,
           !error.isEmpty {
            return error
        }

        let trimmed = responseBody.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    func cancel() {
        activeTask?.cancel()
        activeTask = nil
    }
}

enum SSEError: Error, LocalizedError {
    case badResponse(statusCode: Int?, message: String?)
    case connectionLost

    var errorDescription: String? {
        switch self {
        case .badResponse(let statusCode, let message):
            if let message, !message.isEmpty {
                return message
            }
            if let statusCode {
                return "Backend returned HTTP \(statusCode)"
            }
            return "Backend returned an error response"
        case .connectionLost: return "Connection to backend lost"
        }
    }
}
