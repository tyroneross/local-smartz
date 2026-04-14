import Foundation

/// Talks to ``/api/models/warmup`` so the UI can preload a model into
/// Ollama VRAM and block query input until the model is resident.
///
/// Usage: ``ModelWarmup.shared.start(baseURL:model:appState:)`` fires a
/// background warmup POST, then polls the GET endpoint every 1s until the
/// backend reports stage ``ready`` or ``error``. Updates are published on
/// ``AppState.modelWarmup`` so views can react via @EnvironmentObject.
@MainActor
final class ModelWarmup {
    static let shared = ModelWarmup()
    private init() {}

    /// Inner JSON shape of the status endpoint. Fields are optional because
    /// the backend returns only ``stage`` when idle.
    struct Status: Decodable {
        let stage: String
        let model: String?
        let error: String?
        let durationMs: Int?

        enum CodingKeys: String, CodingKey {
            case stage, model, error
            case durationMs = "duration_ms"
        }
    }

    private var pollTask: Task<Void, Never>?

    /// Kick off a warmup and start polling. Safe to call repeatedly — a new
    /// call cancels any previous poll task and starts fresh.
    func start(baseURL: String, model: String, appState: AppState) {
        pollTask?.cancel()
        appState.modelWarmup = .loading
        appState.warmupModelName = model

        pollTask = Task { [weak appState] in
            await self.fire(baseURL: baseURL, model: model)
            await self.poll(baseURL: baseURL, model: model, appState: appState)
        }
    }

    /// Cancel any in-flight poll. Does not revert state — callers can set
    /// ``appState.modelWarmup = .idle`` if they want.
    func cancel() {
        pollTask?.cancel()
        pollTask = nil
    }

    // MARK: - Internal

    /// POST /api/models/warmup — idempotent. Fire-and-forget; the state
    /// is read via the polling loop.
    private func fire(baseURL: String, model: String) async {
        guard let url = URL(string: "\(baseURL)/api/models/warmup") else { return }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try? JSONSerialization.data(
            withJSONObject: ["model": model, "keep_alive": "30m"]
        )
        req.timeoutInterval = 5
        _ = try? await URLSession.shared.data(for: req)
    }

    /// GET /api/models/warmup?model=... at 1 Hz. Stops when stage == ready
    /// or error, or when the task is cancelled.
    private func poll(baseURL: String, model: String, appState: AppState?) async {
        var delayNs: UInt64 = 500_000_000  // 0.5s initial
        while !Task.isCancelled {
            let status = await fetchStatus(baseURL: baseURL, model: model)
            if let status {
                await MainActor.run {
                    guard let appState else { return }
                    switch status.stage {
                    case "ready":
                        appState.modelWarmup = .ready
                    case "error":
                        appState.modelWarmup = .error
                    case "loading":
                        appState.modelWarmup = .loading
                    default:
                        // idle — the server hasn't started warmup yet,
                        // the POST may still be reaching it.
                        break
                    }
                }
                if status.stage == "ready" || status.stage == "error" {
                    return
                }
            }
            // Small backoff caps — we're polling at 1 Hz max.
            try? await Task.sleep(nanoseconds: delayNs)
            if delayNs < 1_000_000_000 {
                delayNs = min(1_000_000_000, delayNs * 2)
            }
        }
    }

    private func fetchStatus(baseURL: String, model: String) async -> Status? {
        guard var comps = URLComponents(string: "\(baseURL)/api/models/warmup") else {
            return nil
        }
        comps.queryItems = [URLQueryItem(name: "model", value: model)]
        guard let url = comps.url else { return nil }
        var req = URLRequest(url: url)
        req.timeoutInterval = 5
        do {
            let (data, _) = try await URLSession.shared.data(for: req)
            return try JSONDecoder().decode(Status.self, from: data)
        } catch {
            return nil
        }
    }
}
