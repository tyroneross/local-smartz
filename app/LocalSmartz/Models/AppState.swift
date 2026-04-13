import Foundation

enum AppMode: String, CaseIterable, Identifiable {
    case research
    case author
    var id: String { rawValue }
    var label: String {
        switch self {
        case .research: return "Research"
        case .author:   return "Author"
        }
    }
    var systemImage: String {
        switch self {
        case .research: return "magnifyingglass"
        case .author:   return "square.and.pencil"
        }
    }
}

@MainActor
class AppState: ObservableObject {
    @Published var isConfigured: Bool
    @Published var profile: String = "lite"
    @Published var ollamaStatus: OllamaStatus = .unknown
    @Published var isResearching = false
    @Published var mode: AppMode = .research

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

    /// Wipe saved setup state so the next launch returns to the setup wizard.
    /// Use when the saved Python path no longer works (e.g. user reinstalled).
    func resetSetup() {
        let defaults = UserDefaults.standard
        defaults.removeObject(forKey: "pythonPath")
        defaults.removeObject(forKey: "projectDirectory")
        isConfigured = false
    }

    func updateFromHealth(_ data: [String: Any]) {
        if let profileName = data["profile"] as? String {
            profile = profileName
        }
    }
}
