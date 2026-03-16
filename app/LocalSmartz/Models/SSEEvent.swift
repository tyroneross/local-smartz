import Foundation

enum SSEEvent {
    case text(String)
    case tool(name: String)
    case toolError(name: String, message: String)
    case done(durationMs: Int)
    case error(String)

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
        default:
            return nil
        }
    }
}
