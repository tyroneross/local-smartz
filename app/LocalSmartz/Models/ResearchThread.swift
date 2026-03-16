import Foundation

struct ResearchThread: Identifiable, Codable, Equatable {
    let id: String
    var title: String
    var entryCount: Int
    var lastUpdated: String

    enum CodingKeys: String, CodingKey {
        case id, title
        case entryCount = "entry_count"
        case lastUpdated = "last_updated"
    }

    var relativeTime: String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        guard let date = formatter.date(from: lastUpdated)
                ?? ISO8601DateFormatter().date(from: lastUpdated) else {
            return lastUpdated
        }
        let interval = Date().timeIntervalSince(date)
        if interval < 60 { return "Just now" }
        if interval < 3600 { return "\(Int(interval / 60))m ago" }
        if interval < 86400 { return "\(Int(interval / 3600))h ago" }
        if interval < 172800 { return "Yesterday" }
        return "\(Int(interval / 86400))d ago"
    }
}
