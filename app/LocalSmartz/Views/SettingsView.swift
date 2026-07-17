import SwiftUI
import AppKit

// MARK: - View Model

@MainActor
final class SettingsViewModel: ObservableObject {
    @Published var settings: GlobalSettings {
        didSet { hasChanges = settings != original }
    }
    @Published var hasChanges: Bool = false
    @Published var errorMessage: String?

    private var original: GlobalSettings

    init() {
        let loaded = GlobalSettings.load()
        self.settings = loaded
        self.original = loaded
    }

    func reload() {
        let loaded = GlobalSettings.load()
        settings = loaded
        original = loaded
        hasChanges = false
    }

    func apply() {
        do {
            try settings.save()
            // Belt-and-suspenders: mirror critical paths into UserDefaults so
            // legacy consumers (AppState.isConfigured, BackendManager legacy
            // fallbacks, Setup wizard) see the change without a relaunch.
            let defaults = UserDefaults.standard
            if !settings.workspace.isEmpty {
                defaults.set(settings.workspace, forKey: "projectDirectory")
            }
            if !settings.pythonPath.isEmpty {
                defaults.set(settings.pythonPath, forKey: "pythonPath")
            }
            original = settings
            hasChanges = false
            errorMessage = nil
        } catch {
            errorMessage = "Could not save settings: \(error.localizedDescription)"
        }
    }

    func revert() {
        settings = original
        hasChanges = false
    }
}

// MARK: - Root Settings View

struct SettingsView: View {
    @StateObject private var vm = SettingsViewModel()

    var body: some View {
        VStack(spacing: 0) {
            TabView {
                GeneralTab(vm: vm)
                    .tabItem { Label("General", systemImage: "gear") }
                ModelsTab()
                    .tabItem { Label("Models", systemImage: "cpu") }
                AgentRoutingTab()
                    .tabItem { Label("Agent Routing", systemImage: "arrow.triangle.branch") }
                AgentsTab()
                    .tabItem { Label("Agents", systemImage: "person.3") }
                PatternTab()
                    .tabItem { Label("Pattern", systemImage: "rectangle.connected.to.line.below") }
                EvalTab()
                    .tabItem { Label("Eval", systemImage: "checkmark.seal") }
                ApiKeysTab()
                    .tabItem { Label("API Keys", systemImage: "key") }
                TelemetryTab()
                    .tabItem { Label("Telemetry", systemImage: "waveform.path.ecg") }
                DebugTab()
                    .tabItem { Label("Debug", systemImage: "ladybug") }
                PythonTab(vm: vm)
                    .tabItem { Label("Python", systemImage: "terminal") }
                PluginsTab(vm: vm)
                    .tabItem { Label("Plugins", systemImage: "puzzlepiece.extension") }
                AboutTab()
                    .tabItem { Label("About", systemImage: "info.circle") }
            }
            .padding(.top, 12)

            Divider()

            footer
        }
        .frame(width: 640, height: 520)
        .onAppear { vm.reload() }
        .alert(
            "Save failed",
            isPresented: Binding(
                get: { vm.errorMessage != nil },
                set: { if !$0 { vm.errorMessage = nil } }
            ),
            presenting: vm.errorMessage
        ) { _ in
            Button("OK", role: .cancel) { vm.errorMessage = nil }
        } message: { msg in
            Text(msg)
        }
    }

    private var footer: some View {
        HStack {
            if vm.hasChanges {
                Text("Unsaved changes")
                    .font(.system(size: 14))
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Button("Revert") { vm.revert() }
                .disabled(!vm.hasChanges)
            Button("Apply") { vm.apply() }
                .keyboardShortcut(.defaultAction)
                .disabled(!vm.hasChanges)
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 12)
    }
}

// MARK: - Backend-owned settings (local_only, active_model, disabled_agents)
//
// These three keys are frozen-contract fields owned by the backend's
// GET/POST /api/settings endpoint (~/.localsmartz/global.json, validated
// server-side). Swift must not become a second, validation-bypassing writer
// of these keys — GeneralTab talks to /api/settings directly and never
// round-trips them through GlobalSettings.save().

/// GET /api/models — existing endpoint, unchanged shape.
private struct BackendModelOption: Decodable, Identifiable, Hashable {
    let name: String
    let sizeGB: Double

    var id: String { name }

    enum CodingKeys: String, CodingKey {
        case name
        case sizeGB = "size_gb"
    }
}

private struct ModelsListResponse: Decodable {
    let models: [BackendModelOption]
    let current: String
    let profile: String?
}

/// GET/POST /api/settings envelope.
private struct BackendSettings: Decodable {
    let localOnly: Bool
    let activeModel: String
    let disabledAgents: [String]
    let profile: String

    enum CodingKeys: String, CodingKey {
        case localOnly = "local_only"
        case activeModel = "active_model"
        case disabledAgents = "disabled_agents"
        case profile
    }
}

/// Sentinel tag for "no global override — each agent uses its own model".
private let perAgentDefaultSentinel = ""

@MainActor
private final class BackendSettingsVM: ObservableObject {
    @Published var reachable = true
    @Published var loading = false
    @Published var models: [BackendModelOption] = []
    @Published var localOnly: Bool = false
    @Published var activeModel: String = ""
    @Published var saveError: String?
    @Published var saving = false

    func refresh() async {
        loading = true
        defer { loading = false }
        guard let base = await SettingsBackend.discover() else {
            reachable = false
            return
        }
        reachable = true
        await loadModels(base: base)
        await loadSettings(base: base)
    }

    private func loadModels(base: String) async {
        guard let url = URL(string: "\(base)/api/models") else { return }
        do {
            let (data, _) = try await URLSession.shared.data(from: url)
            let decoded = try JSONDecoder().decode(ModelsListResponse.self, from: data)
            models = decoded.models
        } catch {
            // Non-fatal — picker just shows the sentinel option.
        }
    }

    private func loadSettings(base: String) async {
        guard let url = URL(string: "\(base)/api/settings") else { return }
        do {
            let (data, _) = try await URLSession.shared.data(from: url)
            let decoded = try JSONDecoder().decode(BackendSettings.self, from: data)
            localOnly = decoded.localOnly
            activeModel = decoded.activeModel
        } catch {
            // Non-fatal — controls fall back to their published defaults.
        }
    }

    /// POST /api/settings { active_model }. Only this one key — never
    /// round-trips disabled_agents or local_only, so it can't clobber
    /// concurrent edits from the Agents tab.
    func setActiveModel(_ model: String) async {
        await post(["active_model": model])
    }

    /// POST /api/settings { local_only }.
    func setLocalOnly(_ value: Bool) async {
        await post(["local_only": value])
    }

    private func post(_ body: [String: Any]) async {
        guard let base = await SettingsBackend.discover() else {
            reachable = false
            return
        }
        saving = true
        defer { saving = false }
        saveError = nil

        let url = URL(string: "\(base)/api/settings")!
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try? JSONSerialization.data(withJSONObject: body)
        do {
            let (data, resp) = try await URLSession.shared.data(for: req)
            if let http = resp as? HTTPURLResponse, http.statusCode != 200 {
                if let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                   let msg = obj["error"] as? String {
                    saveError = msg
                } else {
                    saveError = "Save failed (HTTP \(http.statusCode))"
                }
                await loadSettings(base: base)  // resync — reject reverts UI
                return
            }
            let decoded = try JSONDecoder().decode(BackendSettings.self, from: data)
            localOnly = decoded.localOnly
            activeModel = decoded.activeModel
        } catch {
            saveError = "Save failed: \(error.localizedDescription)"
        }
    }
}

// MARK: - General Tab

private struct GeneralTab: View {
    @ObservedObject var vm: SettingsViewModel
    @StateObject private var backendVM = BackendSettingsVM()

    var body: some View {
        SettingsForm {
            LabeledRow("Workspace folder") {
                PathPickerField(
                    path: $vm.settings.workspace,
                    placeholder: "Choose workspace folder",
                    chooseDirectories: true
                )
            }
            Divider().padding(.vertical, 2)
            LabeledRow("Model for all agents") {
                VStack(alignment: .leading, spacing: 4) {
                    Picker("", selection: Binding(
                        get: { backendVM.activeModel },
                        set: { newValue in
                            backendVM.activeModel = newValue
                            Task { await backendVM.setActiveModel(newValue) }
                        }
                    )) {
                        Text("Per-agent (default)").tag(perAgentDefaultSentinel)
                        ForEach(backendVM.models) { model in
                            Text(model.name).tag(model.name)
                        }
                    }
                    .pickerStyle(.menu)
                    .labelsHidden()
                    .disabled(!backendVM.reachable || backendVM.saving)
                    Text("Use one model for all agents instead of each agent's own assignment.")
                        .font(.system(size: 13))
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                    if !backendVM.reachable {
                        Text("Backend offline — start the main window to change this.")
                            .font(.system(size: 13))
                            .foregroundStyle(.orange)
                    } else if let err = backendVM.saveError {
                        Text(err)
                            .font(.system(size: 13))
                            .foregroundStyle(.red)
                    }
                }
            }
            Divider().padding(.vertical, 2)
            LabeledRow("Local Only") {
                VStack(alignment: .leading, spacing: 4) {
                    Toggle(
                        "Local Only",
                        isOn: Binding(
                            get: { backendVM.localOnly },
                            set: { newValue in
                                backendVM.localOnly = newValue
                                Task { await backendVM.setLocalOnly(newValue) }
                            }
                        )
                    )
                    .labelsHidden()
                    .disabled(!backendVM.reachable || backendVM.saving)
                    Text("Blocks cloud providers — all inference stays on this Mac.")
                        .font(.system(size: 13))
                        .foregroundStyle(.secondary)
                    if !backendVM.reachable {
                        Text("Backend offline — start the main window to change this.")
                            .font(.system(size: 13))
                            .foregroundStyle(.orange)
                    }
                }
            }
            Divider().padding(.vertical, 2)
            LabeledRow("Safety") {
                VStack(alignment: .leading, spacing: 4) {
                    Toggle(
                        "Warn before running large models",
                        isOn: $vm.settings.warnBeforeLargeModels
                    )
                    Text("Show a confirmation when the selected model's size exceeds detected system RAM.")
                        .font(.system(size: 13))
                        .foregroundStyle(.secondary)
                }
            }
            Divider().padding(.vertical, 2)
            LabeledRow("Research pipeline") {
                VStack(alignment: .leading, spacing: 4) {
                    Picker("", selection: $vm.settings.pipelineBackend) {
                        Text("Deterministic graph (default)").tag("graph")
                        Text("Prompt-driven orchestrator").tag("orchestrator")
                    }
                    .pickerStyle(.menu)
                    .labelsHidden()
                    Text("Graph mode is more reliable on small models (qwen3:8b), enforcing a fact-check loop structurally. Orchestrator mode is simpler and slightly faster on trivial queries.")
                        .font(.system(size: 13))
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
        .task {
            await backendVM.refresh()
        }
    }
}

// MARK: - Python Tab

private struct PythonTab: View {
    @ObservedObject var vm: SettingsViewModel

    var body: some View {
        SettingsForm {
            LabeledRow("Interpreter path") {
                VStack(alignment: .leading, spacing: 6) {
                    PathPickerField(
                        path: $vm.settings.pythonPath,
                        placeholder: "Choose python3 binary",
                        chooseDirectories: false
                    )
                    HStack {
                        Button("Detect…") { detectPython() }
                            .controlSize(.small)
                        Text("Runs `/usr/bin/env which python3`")
                            .font(.system(size: 13))
                            .foregroundStyle(.secondary)
                    }
                }
            }
        }
    }

    private func detectPython() {
        let process = Process()
        process.launchPath = "/usr/bin/env"
        process.arguments = ["which", "python3"]
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = Pipe()
        do {
            try process.run()
            process.waitUntilExit()
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            if let output = String(data: data, encoding: .utf8) {
                let trimmed = output.trimmingCharacters(in: .whitespacesAndNewlines)
                if !trimmed.isEmpty {
                    vm.settings.pythonPath = trimmed
                }
            }
        } catch {
            // Silently fail — user can still enter path manually.
        }
    }
}

// MARK: - Plugins Tab

private struct PluginsTab: View {
    @ObservedObject var vm: SettingsViewModel
    @State private var selection: String?

    var body: some View {
        SettingsForm {
            LabeledRow("Plugin source paths") {
                VStack(alignment: .leading, spacing: 8) {
                    List(selection: $selection) {
                        ForEach(vm.settings.pluginPaths, id: \.self) { path in
                            Text(path)
                                .font(.system(size: 14, design: .monospaced))
                                .lineLimit(1)
                                .truncationMode(.middle)
                                .tag(path)
                        }
                    }
                    .frame(height: 120)
                    .overlay(
                        RoundedRectangle(cornerRadius: 4)
                            .stroke(Color.secondary.opacity(0.3), lineWidth: 1)
                    )

                    HStack(spacing: 6) {
                        Button {
                            addPluginPath()
                        } label: {
                            Image(systemName: "plus")
                        }
                        .controlSize(.small)

                        Button {
                            removeSelected()
                        } label: {
                            Image(systemName: "minus")
                        }
                        .controlSize(.small)
                        .disabled(selection == nil)
                    }
                }
            }

            Divider().padding(.vertical, 2)

            LabeledRow("Active skills") {
                if vm.settings.activeSkills.isEmpty {
                    Text("None")
                        .font(.system(size: 14))
                        .foregroundStyle(.secondary)
                } else {
                    Text(vm.settings.activeSkills.joined(separator: ", "))
                        .font(.system(size: 14))
                        .foregroundStyle(.primary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    private func addPluginPath() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        if panel.runModal() == .OK, let url = panel.url {
            let path = url.path
            if !vm.settings.pluginPaths.contains(path) {
                vm.settings.pluginPaths.append(path)
            }
        }
    }

    private func removeSelected() {
        guard let sel = selection else { return }
        vm.settings.pluginPaths.removeAll { $0 == sel }
        selection = nil
    }
}

// MARK: - About Tab

private struct AboutTab: View {
    @EnvironmentObject var appState: AppState
    @State private var confirmReset = false

    private var version: String {
        let bundle = Bundle.main
        let short = bundle.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "—"
        let build = bundle.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "—"
        return "\(short) (\(build))"
    }

    var body: some View {
        SettingsForm {
            LabeledRow("Version") {
                Text(version)
                    .font(.system(size: 14))
            }
            Divider().padding(.vertical, 2)
            LabeledRow("Repository") {
                Link(
                    "github.com/tyroneross/local-smartz",
                    destination: URL(string: "https://github.com/tyroneross/local-smartz")!
                )
                .font(.system(size: 14))
            }
            Divider().padding(.vertical, 2)
            LabeledRow("License") {
                Text("MIT")
                    .font(.system(size: 14))
            }
            Divider().padding(.vertical, 2)
            LabeledRow("Setup") {
                Button("Reset setup wizard…") {
                    confirmReset = true
                }
                .font(.system(size: 14))
                .confirmationDialog(
                    "Reset setup?",
                    isPresented: $confirmReset,
                    titleVisibility: .visible
                ) {
                    Button("Reset", role: .destructive) {
                        appState.resetSetup()
                    }
                    Button("Cancel", role: .cancel) {}
                } message: {
                    Text("Clears the saved Python path and workspace. You'll go through the setup wizard on next launch.")
                }
            }
        }
    }
}

// MARK: - Layout primitives

/// Grouped form container — single border around all rows, no per-row chrome.
private struct SettingsForm<Content: View>: View {
    @ViewBuilder let content: () -> Content

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            content()
        }
        .padding(16)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(
            RoundedRectangle(cornerRadius: 6)
                .stroke(Color.secondary.opacity(0.2), lineWidth: 1)
        )
        .padding(20)
    }
}

/// Label above field, Calm Precision: 13pt secondary label, regular field.
private struct LabeledRow<Content: View>: View {
    let label: String
    @ViewBuilder let content: () -> Content

    init(_ label: String, @ViewBuilder content: @escaping () -> Content) {
        self.label = label
        self.content = content
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label)
                .font(.system(size: 15))
                .foregroundStyle(.secondary)
            content()
        }
    }
}

/// Text field + "Choose…" button using NSOpenPanel.
private struct PathPickerField: View {
    @Binding var path: String
    let placeholder: String
    let chooseDirectories: Bool

    var body: some View {
        HStack(spacing: 6) {
            TextField(placeholder, text: $path)
                .textFieldStyle(.roundedBorder)
                .font(.system(size: 14, design: .monospaced))
            Button("Choose…") { openPicker() }
                .controlSize(.small)
        }
    }

    private func openPicker() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = chooseDirectories
        panel.canChooseFiles = !chooseDirectories
        panel.allowsMultipleSelection = false
        panel.showsHiddenFiles = true
        if !path.isEmpty {
            let expanded = (path as NSString).expandingTildeInPath
            panel.directoryURL = URL(fileURLWithPath: expanded)
                .deletingLastPathComponent()
        }
        if panel.runModal() == .OK, let url = panel.url {
            path = url.path
        }
    }
}
