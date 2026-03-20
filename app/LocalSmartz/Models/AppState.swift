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
        case needsSetup
    }

    init() {
        let defaults = UserDefaults.standard
        if let pythonPath = defaults.string(forKey: "pythonPath"),
           FileManager.default.fileExists(atPath: pythonPath),
           let projectDirectory = defaults.string(forKey: "projectDirectory"),
           FileManager.default.fileExists(atPath: projectDirectory) {
            self.isConfigured = true
        } else {
            defaults.removeObject(forKey: "pythonPath")
            defaults.removeObject(forKey: "projectDirectory")
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
