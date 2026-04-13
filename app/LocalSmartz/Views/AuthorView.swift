import SwiftUI
import AppKit

/// Plugin/skill authoring with the local LLM.
struct AuthorView: View {
    @EnvironmentObject var appState: AppState

    enum AuthorMode: String, CaseIterable, Identifiable {
        case refactor, fromScratch
        var id: String { rawValue }
        var label: String {
            switch self {
            case .refactor: return "Refactor existing skill"
            case .fromScratch: return "New skill from description"
            }
        }
    }

    @State private var mode: AuthorMode = .refactor
    @State private var installedSkills: [String] = []
    /// Maps skill name -> parent plugin name (empty string for standalone skills).
    /// Used to route refactor saves to the plugin's folder, not a new folder
    /// named after the skill.
    @State private var skillPluginByName: [String: String] = [:]
    @State private var selectedSkill: String = ""
    @State private var selectedSkillPluginName: String = ""
    @State private var newName: String = ""
    @State private var guidance: String = ""
    @State private var description: String = ""

    @State private var isGenerating = false
    @State private var generationError: String?
    @State private var proposedSkillMD: String = ""
    @State private var proposedPluginJSON: String = ""
    @State private var originalSkillMD: String = ""

    @State private var saveError: String?
    @State private var savedAt: String?

    private let candidatePorts = (11435...11444)
    @State private var baseURL: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                header
                modeSelector
                inputs
                generateButton
                if let err = generationError {
                    errorBanner(err)
                }
                if !proposedSkillMD.isEmpty {
                    preview
                    saveBar
                }
            }
            .padding(20)
            .frame(maxWidth: 760, alignment: .leading)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .task { await bootstrap() }
    }

    // MARK: - Sections

    private var header: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Author a plugin or skill")
                .font(.system(size: 15, weight: .semibold))
            Text("Uses the active local model to draft SKILL.md and plugin.json. You pick the target repo on save.")
                .font(.system(size: 12))
                .foregroundStyle(.secondary)
        }
    }

    private var modeSelector: some View {
        Picker("Mode", selection: $mode) {
            ForEach(AuthorMode.allCases) { m in
                Text(m.label).tag(m)
            }
        }
        .pickerStyle(.segmented)
        .labelsHidden()
    }

    @ViewBuilder
    private var inputs: some View {
        switch mode {
        case .refactor:
            VStack(alignment: .leading, spacing: 8) {
                FieldLabel("Installed skill")
                Picker("", selection: $selectedSkill) {
                    Text("Select a skill…").tag("")
                    ForEach(installedSkills, id: \.self) { Text($0).tag($0) }
                }
                .labelsHidden()
                .pickerStyle(.menu)
                .onChange(of: selectedSkill) { _, newValue in
                    selectedSkillPluginName = skillPluginByName[newValue] ?? ""
                }

                FieldLabel("Authoring guidance (paste AGENTS.md / CLAUDE.md / freeform notes)")
                TextEditor(text: $guidance)
                    .font(.system(size: 12, design: .monospaced))
                    .frame(minHeight: 140)
                    .overlay(RoundedRectangle(cornerRadius: 6).stroke(Color.secondary.opacity(0.2)))
            }
        case .fromScratch:
            VStack(alignment: .leading, spacing: 8) {
                FieldLabel("New skill name (kebab-case)")
                TextField("e.g. summarize-meetings", text: $newName)
                    .textFieldStyle(.roundedBorder)

                FieldLabel("Description / guidance")
                TextEditor(text: $description)
                    .font(.system(size: 12, design: .monospaced))
                    .frame(minHeight: 140)
                    .overlay(RoundedRectangle(cornerRadius: 6).stroke(Color.secondary.opacity(0.2)))
            }
        }
    }

    private var generateButton: some View {
        HStack {
            Button {
                Task { await generate() }
            } label: {
                HStack(spacing: 6) {
                    if isGenerating {
                        ProgressView().controlSize(.small)
                    } else {
                        Image(systemName: "wand.and.stars")
                    }
                    Text(isGenerating ? "Drafting with local model…" : "Draft with local model")
                }
                .padding(.horizontal, 4)
            }
            .buttonStyle(.borderedProminent)
            .disabled(!canGenerate)
            Spacer()
        }
    }

    private var canGenerate: Bool {
        if isGenerating { return false }
        switch mode {
        case .refactor:
            return !selectedSkill.isEmpty && !guidance.trimmingCharacters(in: .whitespaces).isEmpty
        case .fromScratch:
            return !newName.trimmingCharacters(in: .whitespaces).isEmpty
                && !description.trimmingCharacters(in: .whitespaces).isEmpty
        }
    }

    private var preview: some View {
        VStack(alignment: .leading, spacing: 10) {
            FieldLabel("Proposed SKILL.md")
            TextEditor(text: $proposedSkillMD)
                .font(.system(size: 12, design: .monospaced))
                .frame(minHeight: 220)
                .overlay(RoundedRectangle(cornerRadius: 6).stroke(Color.secondary.opacity(0.2)))

            if !proposedPluginJSON.isEmpty {
                FieldLabel("Proposed plugin.json")
                TextEditor(text: $proposedPluginJSON)
                    .font(.system(size: 12, design: .monospaced))
                    .frame(minHeight: 100)
                    .overlay(RoundedRectangle(cornerRadius: 6).stroke(Color.secondary.opacity(0.2)))
            }
        }
    }

    private var saveBar: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Button {
                    Task { await save() }
                } label: {
                    HStack(spacing: 6) {
                        Image(systemName: "tray.and.arrow.down.fill")
                        Text("Save to repository…")
                    }
                    .padding(.horizontal, 4)
                }
                .buttonStyle(.borderedProminent)
                Spacer()
            }
            if let err = saveError {
                Text(err).font(.system(size: 12)).foregroundStyle(.red)
            }
            if let at = savedAt {
                Text("✓ Saved to \(at)")
                    .font(.system(size: 12))
                    .foregroundStyle(.green)
            }
        }
    }

    private func errorBanner(_ msg: String) -> some View {
        Text(msg)
            .font(.system(size: 12))
            .foregroundStyle(.red)
            .padding(8)
            .frame(maxWidth: .infinity, alignment: .leading)
            .overlay(RoundedRectangle(cornerRadius: 6).stroke(Color.red.opacity(0.3)))
    }

    // MARK: - Actions

    private func bootstrap() async {
        baseURL = await discoverBackend()
        await fetchSkills()
    }

    private func discoverBackend() async -> String? {
        for port in candidatePorts {
            guard let url = URL(string: "http://localhost:\(port)/api/health") else { continue }
            var req = URLRequest(url: url)
            req.timeoutInterval = 0.8
            if let (_, resp) = try? await URLSession.shared.data(for: req),
               let http = resp as? HTTPURLResponse, http.statusCode == 200 {
                return "http://localhost:\(port)"
            }
        }
        return nil
    }

    private func fetchSkills() async {
        guard let base = baseURL,
              let url = URL(string: "\(base)/api/skills") else { return }
        struct Skill: Decodable {
            let name: String
            let plugin: String?
        }
        do {
            let (data, _) = try await URLSession.shared.data(from: url)
            let arr = try JSONDecoder().decode([Skill].self, from: data)
            installedSkills = arr.map(\.name)
            var map: [String: String] = [:]
            for s in arr {
                map[s.name] = s.plugin ?? ""
            }
            skillPluginByName = map
        } catch {
            installedSkills = []
            skillPluginByName = [:]
        }
    }

    private func generate() async {
        guard let base = baseURL else {
            generationError = "Backend not reachable. Open the Research tab first."
            return
        }
        isGenerating = true
        generationError = nil
        proposedSkillMD = ""
        proposedPluginJSON = ""
        originalSkillMD = ""
        savedAt = nil
        defer { isGenerating = false }

        do {
            switch mode {
            case .refactor:
                guard let url = URL(string: "\(base)/api/skills/refactor") else { return }
                var req = URLRequest(url: url)
                req.httpMethod = "POST"
                req.setValue("application/json", forHTTPHeaderField: "Content-Type")
                req.timeoutInterval = 180
                req.httpBody = try JSONSerialization.data(withJSONObject: [
                    "name": selectedSkill,
                    "guidance": guidance,
                ])
                let (data, resp) = try await URLSession.shared.data(for: req)
                guard let http = resp as? HTTPURLResponse, http.statusCode == 200 else {
                    let body = String(data: data, encoding: .utf8) ?? "(no body)"
                    generationError = "Refactor failed (\((resp as? HTTPURLResponse)?.statusCode ?? 0)): \(body)"
                    return
                }
                struct R: Decodable { let original: String; let proposed: String }
                let r = try JSONDecoder().decode(R.self, from: data)
                originalSkillMD = r.original
                proposedSkillMD = r.proposed

            case .fromScratch:
                guard let url = URL(string: "\(base)/api/skills/new") else { return }
                var req = URLRequest(url: url)
                req.httpMethod = "POST"
                req.setValue("application/json", forHTTPHeaderField: "Content-Type")
                req.timeoutInterval = 180
                req.httpBody = try JSONSerialization.data(withJSONObject: [
                    "name": newName,
                    "description": description,
                ])
                let (data, resp) = try await URLSession.shared.data(for: req)
                guard let http = resp as? HTTPURLResponse, http.statusCode == 200 else {
                    let body = String(data: data, encoding: .utf8) ?? "(no body)"
                    generationError = "Generation failed (\((resp as? HTTPURLResponse)?.statusCode ?? 0)): \(body)"
                    return
                }
                struct R: Decodable { let skill_md: String; let plugin_json: String }
                let r = try JSONDecoder().decode(R.self, from: data)
                proposedSkillMD = r.skill_md
                proposedPluginJSON = r.plugin_json
            }
        } catch {
            generationError = error.localizedDescription
        }
    }

    private func save() async {
        guard let base = baseURL else {
            saveError = "Backend not reachable."
            return
        }
        // Prompt for target directory
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.canCreateDirectories = true
        panel.allowsMultipleSelection = false
        panel.prompt = "Save Plugin Here"
        guard panel.runModal() == .OK, let target = panel.url else { return }

        // In refactor mode, write to the skill's parent plugin folder — not a
        // new folder named after the skill. Fall back to the skill name only
        // for standalone skills (plugin field empty).
        let pluginName: String
        switch mode {
        case .refactor:
            let parent = selectedSkillPluginName.trimmingCharacters(in: .whitespaces)
            if !parent.isEmpty {
                pluginName = parent
            } else {
                pluginName = selectedSkill.trimmingCharacters(in: .whitespaces)
            }
        case .fromScratch:
            pluginName = newName.trimmingCharacters(in: .whitespaces)
        }
        guard !pluginName.isEmpty else {
            saveError = "Plugin name is empty."
            return
        }

        var files: [String: String] = ["SKILL.md": proposedSkillMD]
        if !proposedPluginJSON.isEmpty {
            files["plugin.json"] = proposedPluginJSON
        }

        guard let url = URL(string: "\(base)/api/plugins/save") else { return }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.timeoutInterval = 30
        do {
            req.httpBody = try JSONSerialization.data(withJSONObject: [
                "target_path": target.path,
                "plugin_name": pluginName,
                "files": files,
            ])
            let (data, resp) = try await URLSession.shared.data(for: req)
            if let http = resp as? HTTPURLResponse, http.statusCode == 200 {
                struct R: Decodable { let plugin_dir: String }
                let r = try JSONDecoder().decode(R.self, from: data)
                savedAt = r.plugin_dir
                saveError = nil
            } else {
                let body = String(data: data, encoding: .utf8) ?? "(no body)"
                saveError = "Save failed (\((resp as? HTTPURLResponse)?.statusCode ?? 0)): \(body)"
            }
        } catch {
            saveError = error.localizedDescription
        }
    }
}

private struct FieldLabel: View {
    let text: String
    init(_ text: String) { self.text = text }
    var body: some View {
        Text(text)
            .font(.system(size: 11, weight: .medium))
            .foregroundStyle(.secondary)
    }
}
