import Foundation

actor SSEClient {
    private var activeTask: Task<Void, Never>?

    func stream(url: URL) -> AsyncThrowingStream<SSEEvent, Error> {
        AsyncThrowingStream { continuation in
            activeTask = Task {
                do {
                    var request = URLRequest(url: url)
                    request.timeoutInterval = 600
                    request.setValue("text/event-stream", forHTTPHeaderField: "Accept")

                    let (bytes, response) = try await URLSession.shared.bytes(for: request)

                    guard let http = response as? HTTPURLResponse,
                          http.statusCode == 200 else {
                        continuation.finish(throwing: SSEError.badResponse)
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

    func cancel() {
        activeTask?.cancel()
        activeTask = nil
    }
}

enum SSEError: Error, LocalizedError {
    case badResponse
    case connectionLost

    var errorDescription: String? {
        switch self {
        case .badResponse: return "Backend returned an error response"
        case .connectionLost: return "Connection to backend lost"
        }
    }
}
