import Foundation
import SwiftUI

/// Lightweight registry of research project folders surfaced in the sidebar.
/// Persisted at `~/.localsmartz/projects.json`. Source of truth is this file;
/// folders that have been deleted on disk are silently dropped on load.
struct Project: Identifiable, Codable, Equatable {
    let name: String
    let path: String
    let createdAt: Date
    var id: String { path }
}

final class ProjectIndex: ObservableObject {
    @Published private(set) var projects: [Project] = []

    private let fileURL: URL

    init() {
        let home = FileManager.default.homeDirectoryForCurrentUser
        self.fileURL = home
            .appendingPathComponent(".localsmartz", isDirectory: true)
            .appendingPathComponent("projects.json")
        load()
    }

    func add(name: String, path: URL) {
        let entry = Project(name: name, path: path.path, createdAt: Date())
        // De-dupe by path so re-opening an existing folder doesn't double-list.
        projects.removeAll { $0.path == entry.path }
        projects.append(entry)
        write()
    }

    func remove(_ project: Project) {
        projects.removeAll { $0.path == project.path }
        write()
    }

    // MARK: - Private

    private struct Store: Codable {
        var projects: [Project]
    }

    private func load() {
        let fm = FileManager.default
        guard fm.fileExists(atPath: fileURL.path) else {
            projects = []
            return
        }
        do {
            let data = try Data(contentsOf: fileURL)
            let decoder = JSONDecoder()
            decoder.dateDecodingStrategy = .iso8601
            let store = try decoder.decode(Store.self, from: data)
            // Silently drop entries whose folder no longer exists on disk.
            let filtered = store.projects.filter { fm.fileExists(atPath: $0.path) }
            projects = filtered
            if filtered.count != store.projects.count {
                write()
            }
        } catch {
            logErr("ProjectIndex: load failed: \(error)")
            projects = []
        }
    }

    private func write() {
        let fm = FileManager.default
        do {
            try fm.createDirectory(
                at: fileURL.deletingLastPathComponent(),
                withIntermediateDirectories: true
            )
            let encoder = JSONEncoder()
            encoder.dateEncodingStrategy = .iso8601
            encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
            let data = try encoder.encode(Store(projects: projects))
            try data.write(to: fileURL, options: .atomic)
        } catch {
            logErr("ProjectIndex: write failed: \(error)")
        }
    }

    private func logErr(_ msg: String) {
        if let data = (msg + "\n").data(using: .utf8) {
            FileHandle.standardError.write(data)
        }
    }
}

/// One entry inside a project's `queries.json`. Matches the shape written by
/// `ResearchView.appendQueryRecord` (query, answer_preview, timestamp).
struct SavedQuery: Identifiable, Codable, Equatable {
    let query: String
    let answerPreview: String
    let timestamp: Date

    var id: String { "\(timestamp.timeIntervalSince1970)-\(query)" }

    enum CodingKeys: String, CodingKey {
        case query
        case answerPreview = "answer_preview"
        case timestamp
    }
}
