import Foundation

/// Codable representation of `~/.localsmartz/global.json`.
///
/// All keys are optional on disk — missing keys decode to defaults. The Python
/// side (`localsmartz config` CLI) is the authoritative source of the schema;
/// this type mirrors it for two-way sync.
struct GlobalSettings: Codable, Equatable {
    var workspace: String = ""
    var pythonPath: String = ""
    var activeModel: String = ""
    var pluginPaths: [String] = []
    var activeSkills: [String] = []
    /// Show a confirmation dialog when the user launches a research run
    /// with a model whose estimated size exceeds detected system RAM.
    /// Default: true — prevents silent swap-thrashing on under-spec machines.
    var warnBeforeLargeModels: Bool = true
    /// Which research pipeline backend to run on ``/api/research``.
    ///
    /// - ``"graph"`` (default) — deterministic LangGraph supervisor. Each
    ///   specialist (researcher, analyzer, fact_checker, writer) is a real
    ///   ReAct executor with a scoped tool subset. Hard fact-check loop
    ///   bounded at 2 re-dispatch rounds. More reliable on small models
    ///   (qwen3:8b) because fan-out + re-dispatch are structural edges,
    ///   not LLM decisions.
    /// - ``"orchestrator"`` (opt-in) — legacy prompt-driven router inside
    ///   DeepAgents. Main agent gets the orchestrator ``system_focus`` and
    ///   delegates to specialists via ``task()``. Simpler and slightly
    ///   faster on trivial queries, but relies on the LLM to remember to
    ///   fan-out and re-dispatch.
    ///
    /// Wired through to the backend via the ``LOCALSMARTZ_PIPELINE`` env
    /// var when BackendManager spawns Python. "graph" or empty → do not
    /// set (default applies). "orchestrator" → set env var to opt out.
    var pipelineBackend: String = "graph"

    enum CodingKeys: String, CodingKey {
        case workspace
        case pythonPath = "python_path"
        case activeModel = "active_model"
        case pluginPaths = "plugin_paths"
        case activeSkills = "active_skills"
        case warnBeforeLargeModels = "warn_before_large_models"
        case pipelineBackend = "pipeline_backend"
    }

    init(
        workspace: String = "",
        pythonPath: String = "",
        activeModel: String = "",
        pluginPaths: [String] = [],
        activeSkills: [String] = [],
        warnBeforeLargeModels: Bool = true,
        pipelineBackend: String = "graph"
    ) {
        self.workspace = workspace
        self.pythonPath = pythonPath
        self.activeModel = activeModel
        self.pluginPaths = pluginPaths
        self.activeSkills = activeSkills
        self.warnBeforeLargeModels = warnBeforeLargeModels
        self.pipelineBackend = pipelineBackend
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.workspace = (try? c.decodeIfPresent(String.self, forKey: .workspace)) ?? ""
        self.pythonPath = (try? c.decodeIfPresent(String.self, forKey: .pythonPath)) ?? ""
        self.activeModel = (try? c.decodeIfPresent(String.self, forKey: .activeModel)) ?? ""
        self.pluginPaths = (try? c.decodeIfPresent([String].self, forKey: .pluginPaths)) ?? []
        self.activeSkills = (try? c.decodeIfPresent([String].self, forKey: .activeSkills)) ?? []
        self.warnBeforeLargeModels = (try? c.decodeIfPresent(Bool.self, forKey: .warnBeforeLargeModels)) ?? true
        self.pipelineBackend = (try? c.decodeIfPresent(String.self, forKey: .pipelineBackend)) ?? "graph"
    }

    // MARK: - Paths

    /// `~/.localsmartz/global.json`
    static var fileURL: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".localsmartz/global.json")
    }

    /// Directory that holds the global config file.
    static var directoryURL: URL {
        fileURL.deletingLastPathComponent()
    }

    // MARK: - Defaults

    /// Settings returned when no file exists on disk.
    static var defaults: GlobalSettings {
        let home = FileManager.default.homeDirectoryForCurrentUser
        return GlobalSettings(
            workspace: home.appendingPathComponent("Documents/LocalSmartz").path,
            pythonPath: "/usr/bin/env python3",
            activeModel: "",
            pluginPaths: [],
            activeSkills: [],
            warnBeforeLargeModels: true,
            pipelineBackend: "graph"
        )
    }

    // MARK: - Load / Save

    /// Reads the file on disk. On any error (missing file, decode failure, I/O
    /// error) returns `defaults` — the UI layer renders "Not set" placeholders
    /// when individual fields are still empty.
    static func load() -> GlobalSettings {
        let url = fileURL
        guard FileManager.default.fileExists(atPath: url.path) else {
            return defaults
        }
        do {
            let data = try Data(contentsOf: url)
            let decoded = try JSONDecoder().decode(GlobalSettings.self, from: data)
            return decoded.mergedOverDefaults()
        } catch {
            return defaults
        }
    }

    /// Atomically writes the config to `~/.localsmartz/global.json`.
    ///
    /// Writes to a sibling `.tmp` file with atomic write flag, then calls
    /// `FileManager.replaceItem` so a concurrent CLI writer can't observe a
    /// partial file. Creates the parent directory on first save.
    func save() throws {
        let dir = GlobalSettings.directoryURL
        if !FileManager.default.fileExists(atPath: dir.path) {
            try FileManager.default.createDirectory(
                at: dir,
                withIntermediateDirectories: true
            )
        }

        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        let data = try encoder.encode(self)

        let finalURL = GlobalSettings.fileURL
        let tmpURL = dir.appendingPathComponent("global.json.tmp")

        // Write to temp with atomic flag first.
        try data.write(to: tmpURL, options: .atomic)

        // Swap into place. If final doesn't yet exist, a plain move suffices.
        if FileManager.default.fileExists(atPath: finalURL.path) {
            _ = try FileManager.default.replaceItemAt(finalURL, withItemAt: tmpURL)
        } else {
            try FileManager.default.moveItem(at: tmpURL, to: finalURL)
        }
    }

    // MARK: - Helpers

    /// Fill empty fields from `defaults`. The on-disk file may legitimately
    /// omit keys; we preserve explicit empty arrays but backfill empty strings
    /// for critical paths so the UI shows something useful.
    ///
    /// Pipeline-backend migration (2026-04-13): default flipped from
    /// "orchestrator" to "graph". Existing users who explicitly stored
    /// "orchestrator" on disk keep that value through this merge — only
    /// empty or missing values get the new default. Users who never touched
    /// the setting (absent key → decoded default "graph") also get "graph".
    /// Net: no one is surprise-migrated off their chosen backend.
    private func mergedOverDefaults() -> GlobalSettings {
        let d = GlobalSettings.defaults
        return GlobalSettings(
            workspace: workspace.isEmpty ? d.workspace : workspace,
            pythonPath: pythonPath.isEmpty ? d.pythonPath : pythonPath,
            activeModel: activeModel,
            pluginPaths: pluginPaths,
            activeSkills: activeSkills,
            warnBeforeLargeModels: warnBeforeLargeModels,
            pipelineBackend: pipelineBackend.isEmpty ? d.pipelineBackend : pipelineBackend
        )
    }
}
