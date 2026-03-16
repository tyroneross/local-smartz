import Foundation

@MainActor
class AppState: ObservableObject {
    @Published var isConfigured: Bool
    @Published var profile: String = "lite"
    @Published var ollamaStatus: OllamaStatus = .unknown
    @Published var isResearching = false

    enum OllamaStatus: Equatable {
        case unknown
        case ready
        case offline
        case loading
    }

    init() {
        if let path = UserDefaults.standard.string(forKey: "pythonPath"),
           FileManager.default.fileExists(atPath: path) {
            self.isConfigured = true
        } else {
            UserDefaults.standard.removeObject(forKey: "pythonPath")
            self.isConfigured = false
        }
    }

    func markConfigured() {
        isConfigured = true
    }

    func updateFromHealth(_ data: [String: Any]) {
        if let profileName = data["profile"] as? String {
            profile = profileName
        }
    }
}
