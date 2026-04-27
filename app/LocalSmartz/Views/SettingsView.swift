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

// MARK: - General Tab

private struct GeneralTab: View {
    @ObservedObject var vm: SettingsViewModel

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
            LabeledRow("Active model") {
                TextField("e.g. qwen3:8b-q4_K_M", text: $vm.settings.activeModel)
                    .textFieldStyle(.roundedBorder)
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
