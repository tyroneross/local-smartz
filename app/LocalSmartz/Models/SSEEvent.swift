import Foundation

enum SSEEvent {
    case text(String)
    case tool(name: String)
    case toolError(name: String, message: String)
    case done(durationMs: Int)
    case error(String)
    /// Lifecycle stages emitted by the backend around expensive work.
    /// ``stage`` is ``"loading_model"`` while the backend is preloading the
    /// model into Ollama VRAM, and ``"ready"`` once it's resident and the
    /// agent loop is about to begin.
    case status(stage: String, model: String?, warmupMs: Int?)
    /// Idle keep-alive emitted by the backend when the agent stream has
    /// been silent longer than ~15s. Used by the UI to keep the spinner
    /// alive without toggling visibility.
    case heartbeat(elapsedS: Int)
    /// Orchestrator pipeline stage transition. Emitted when the main
    /// agent delegates via DeepAgents' ``task`` tool — the ``name`` is
    /// the specialist role being entered ("researcher", "analyzer",
    /// "fact_checker", "writer"). The UI can render a breadcrumb
    /// (Orchestrator → Researcher → Fact-checker → Writer) to show the
    /// user what's happening during multi-step research.
    case stage(String)

    static func parse(from jsonData: Data) -> SSEEvent? {
        guard let dict = try? JSONSerialization.jsonObject(with: jsonData) as? [String: Any],
              let type = dict["type"] as? String else {
            return nil
        }
        switch type {
        case "text":
            return .text(dict["content"] as? String ?? "")
        case "tool":
            return .tool(name: dict["name"] as? String ?? "")
        case "tool_error":
            return .toolError(
                name: dict["name"] as? String ?? "",
                message: dict["message"] as? String ?? ""
            )
        case "done":
            return .done(durationMs: dict["duration_ms"] as? Int ?? 0)
        case "error":
            return .error(dict["message"] as? String ?? "Unknown error")
        case "status":
            return .status(
                stage: dict["stage"] as? String ?? "",
                model: dict["model"] as? String,
                warmupMs: dict["warmup_ms"] as? Int
            )
        case "heartbeat":
            return .heartbeat(elapsedS: dict["elapsed_s"] as? Int ?? 0)
        case "stage":
            return .stage(dict["stage"] as? String ?? "")
        default:
            return nil
        }
    }
}
