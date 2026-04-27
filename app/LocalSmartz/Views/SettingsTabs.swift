import SwiftUI
import AppKit

// MARK: - Shared backend discovery

enum SettingsBackend {
    static let candidatePorts = 11435...11444

    static func discover() async -> String? {
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
}

// MARK: - Layout primitives (local to settings tabs)

/// Grouped form container — single border around rows, matches SettingsView style.
struct SettingsTabsForm<Content: View>: View {
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

struct SettingsTabsRow<Content: View>: View {
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

// MARK: - Telemetry tab

struct ObservabilityInfo: Decodable {
    let enabled: Bool
    let endpoint: String
    let serviceName: String
    let envOverride: Bool
    let phoenixInstallHint: String

    enum CodingKeys: String, CodingKey {
        case enabled
        case endpoint
        case serviceName = "service_name"
        case envOverride = "env_override"
        case phoenixInstallHint = "phoenix_install_hint"
    }
}

@MainActor
final class TelemetryVM: ObservableObject {
    @Published var info: ObservabilityInfo?
    @Published var loading = false
    @Published var error: String?

    @Published var observeEnabled: Bool = UserDefaults.standard.bool(forKey: "LOCALSMARTZ_OBSERVE") {
        didSet {
            UserDefaults.standard.set(observeEnabled, forKey: "LOCALSMARTZ_OBSERVE")
        }
    }

    func refresh() async {
        loading = true
        defer { loading = false }
        error = nil

        guard let base = await SettingsBackend.discover() else {
            error = "Backend not reachable. Is the main window open?"
            return
        }
        guard let url = URL(string: "\(base)/api/observability/info") else { return }
        do {
            let (data, _) = try await URLSession.shared.data(from: url)
            info = try JSONDecoder().decode(ObservabilityInfo.self, from: data)
        } catch {
            self.error = "Could not load telemetry info: \(error.localizedDescription)"
        }
    }
}

struct TelemetryTab: View {
    @StateObject private var vm = TelemetryVM()

    var body: some View {
        ScrollView {
            SettingsTabsForm {
                HStack {
                    Text("Observability")
                        .font(.system(size: 15, weight: .medium))
                    Spacer()
                    Button {
                        Task { await vm.refresh() }
                    } label: {
                        Image(systemName: "arrow.clockwise")
                    }
                    .controlSize(.small)
                    .disabled(vm.loading)
                }

                Divider().padding(.vertical, 2)

                if let err = vm.error {
                    Text(err)
                        .font(.system(size: 14))
                        .foregroundStyle(.secondary)
                } else if let info = vm.info {
                    statusRow(info: info)
                    Divider().padding(.vertical, 2)
                    SettingsTabsRow("Endpoint") {
                        Text(info.endpoint)
                            .font(.system(size: 14, design: .monospaced))
                            .textSelection(.enabled)
                    }
                    Divider().padding(.vertical, 2)
                    SettingsTabsRow("Service name") {
                        Text(info.serviceName)
                            .font(.system(size: 14, design: .monospaced))
                            .textSelection(.enabled)
                    }
                    Divider().padding(.vertical, 2)
                    SettingsTabsRow("Enable observability") {
                        VStack(alignment: .leading, spacing: 4) {
                            Toggle("Send traces to Phoenix", isOn: $vm.observeEnabled)
                                .toggleStyle(.switch)
                                .controlSize(.small)
                            if vm.observeEnabled != (vm.info?.enabled ?? false) {
                                Text("Restart the app for changes to take effect")
                                    .font(.system(size: 13))
                                    .foregroundStyle(.secondary)
                            }
                            if info.envOverride {
                                Text("Currently overridden by environment variable")
                                    .font(.system(size: 13))
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                    Divider().padding(.vertical, 2)
                    SettingsTabsRow("Phoenix UI") {
                        HStack {
                            Button("Open Phoenix") {
                                if let url = URL(string: "http://localhost:6006") {
                                    NSWorkspace.shared.open(url)
                                }
                            }
                            .controlSize(.small)
                            Text("http://localhost:6006")
                                .font(.system(size: 13, design: .monospaced))
                                .foregroundStyle(.secondary)
                        }
                    }
                    Divider().padding(.vertical, 2)
                    SettingsTabsRow("Install Phoenix") {
                        VStack(alignment: .leading, spacing: 6) {
                            HStack {
                                Text(info.phoenixInstallHint)
                                    .font(.system(size: 14, design: .monospaced))
                                    .textSelection(.enabled)
                                    .fixedSize(horizontal: false, vertical: true)
                                Spacer()
                                Button {
                                    let pb = NSPasteboard.general
                                    pb.clearContents()
                                    pb.setString(info.phoenixInstallHint, forType: .string)
                                } label: {
                                    Image(systemName: "doc.on.doc")
                                }
                                .buttonStyle(.borderless)
                                .help("Copy")
                            }
                        }
                    }
                } else if vm.loading {
                    ProgressView()
                        .controlSize(.small)
                } else {
                    Text("Loading…")
                        .font(.system(size: 14))
                        .foregroundStyle(.secondary)
                }
            }
        }
        .task { await vm.refresh() }
    }

    @ViewBuilder
    private func statusRow(info: ObservabilityInfo) -> some View {
        SettingsTabsRow("Status") {
            HStack(spacing: 8) {
                Circle()
                    .fill(info.enabled ? Color.green : Color.secondary.opacity(0.4))
                    .frame(width: 8, height: 8)
                if info.enabled {
                    Text("Tracing enabled (sending to \(info.endpoint))")
                        .font(.system(size: 14))
                } else {
                    Text("Tracing disabled")
                        .font(.system(size: 14))
                        .foregroundStyle(.secondary)
                }
            }
        }
    }
}

// MARK: - API Keys tab

struct SecretEntry: Decodable, Identifiable {
    let provider: String
    let envVar: String?
    let set: Bool
    let lastFour: String?
    let source: String?
    let preset: Bool

    var id: String { provider }

    enum CodingKeys: String, CodingKey {
        case provider
        case envVar = "env_var"
        case set
        case lastFour = "last_four"
        case source
        case preset
    }
}

@MainActor
final class SecretsVM: ObservableObject {
    @Published var entries: [SecretEntry] = []
    @Published var loading = false
    @Published var error: String?

    var presets: [SecretEntry] { entries.filter { $0.preset } }
    var custom: [SecretEntry] { entries.filter { !$0.preset } }

    func refresh() async {
        loading = true
        defer { loading = false }
        error = nil

        guard let base = await SettingsBackend.discover() else {
            error = "Backend not reachable."
            return
        }
        guard let url = URL(string: "\(base)/api/secrets") else { return }
        do {
            let (data, _) = try await URLSession.shared.data(from: url)
            entries = try JSONDecoder().decode([SecretEntry].self, from: data)
        } catch {
            self.error = "Could not load API keys: \(error.localizedDescription)"
        }
    }

    func save(provider: String, value: String) async -> Bool {
        guard let base = await SettingsBackend.discover(),
              let url = URL(string: "\(base)/api/secrets") else { return false }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let body: [String: String] = ["provider": provider, "value": value]
        req.httpBody = try? JSONSerialization.data(withJSONObject: body)
        do {
            let (_, resp) = try await URLSession.shared.data(for: req)
            if let http = resp as? HTTPURLResponse, (200..<300).contains(http.statusCode) {
                await refresh()
                return true
            }
        } catch {
            self.error = "Save failed: \(error.localizedDescription)"
        }
        return false
    }

    func remove(provider: String) async {
        guard let base = await SettingsBackend.discover() else { return }
        var comps = URLComponents(string: "\(base)/api/secrets")
        comps?.queryItems = [URLQueryItem(name: "provider", value: provider)]
        guard let url = comps?.url else { return }
        var req = URLRequest(url: url)
        req.httpMethod = "DELETE"
        _ = try? await URLSession.shared.data(for: req)
        await refresh()
    }
}

struct ApiKeysTab: View {
    @StateObject private var vm = SecretsVM()
    @State private var sheet: SecretSheetKind?
    @State private var confirmRemove: String?

    enum SecretSheetKind: Identifiable {
        case existing(String)
        case custom

        var id: String {
            switch self {
            case .existing(let p): return "set:\(p)"
            case .custom: return "custom"
            }
        }
    }

    var body: some View {
        ScrollView {
            SettingsTabsForm {
                HStack {
                    Text("API keys")
                        .font(.system(size: 15, weight: .medium))
                    Spacer()
                    Button {
                        Task { await vm.refresh() }
                    } label: {
                        Image(systemName: "arrow.clockwise")
                    }
                    .controlSize(.small)
                    .disabled(vm.loading)
                }

                Divider().padding(.vertical, 2)

                if let err = vm.error {
                    Text(err)
                        .font(.system(size: 14))
                        .foregroundStyle(.secondary)
                }

                if !vm.presets.isEmpty {
                    Text("Built-in providers")
                        .font(.system(size: 13))
                        .foregroundStyle(.secondary)
                        .padding(.top, 2)
                    ForEach(Array(vm.presets.enumerated()), id: \.element.id) { idx, entry in
                        if idx > 0 { Divider() }
                        row(entry: entry)
                    }
                }

                if !vm.custom.isEmpty {
                    Divider().padding(.vertical, 2)
                    Text("Custom")
                        .font(.system(size: 13))
                        .foregroundStyle(.secondary)
                    ForEach(Array(vm.custom.enumerated()), id: \.element.id) { idx, entry in
                        if idx > 0 { Divider() }
                        row(entry: entry)
                    }
                }

                Divider().padding(.vertical, 2)

                HStack {
                    Spacer()
                    Button("Add custom key…") {
                        sheet = .custom
                    }
                    .controlSize(.small)
                }
            }
        }
        .task { await vm.refresh() }
        .sheet(item: $sheet) { kind in
            switch kind {
            case .existing(let provider):
                SecretEditorSheet(
                    title: "Set key for \(provider)",
                    providerLocked: true,
                    initialProvider: provider
                ) { _, value in
                    Task { _ = await vm.save(provider: provider, value: value) }
                }
            case .custom:
                SecretEditorSheet(
                    title: "Add custom API key",
                    providerLocked: false,
                    initialProvider: ""
                ) { provider, value in
                    guard !provider.isEmpty else { return }
                    Task { _ = await vm.save(provider: provider, value: value) }
                }
            }
        }
        .confirmationDialog(
            "Remove API key?",
            isPresented: Binding(
                get: { confirmRemove != nil },
                set: { if !$0 { confirmRemove = nil } }
            ),
            titleVisibility: .visible,
            presenting: confirmRemove
        ) { provider in
            Button("Remove", role: .destructive) {
                Task { await vm.remove(provider: provider) }
                confirmRemove = nil
            }
            Button("Cancel", role: .cancel) { confirmRemove = nil }
        } message: { provider in
            Text("Removes the stored key for \(provider). You can set it again at any time.")
        }
    }

    @ViewBuilder
    private func row(entry: SecretEntry) -> some View {
        HStack(alignment: .center, spacing: 8) {
            VStack(alignment: .leading, spacing: 2) {
                Text(entry.provider)
                    .font(.system(size: 15, weight: .medium))
                if entry.preset, let envVar = entry.envVar {
                    Text("env: \(envVar)")
                        .font(.system(size: 13))
                        .foregroundStyle(.secondary)
                }
            }
            Spacer()
            if entry.set {
                VStack(alignment: .trailing, spacing: 2) {
                    Text("••••\(entry.lastFour ?? "")")
                        .font(.system(size: 14, design: .monospaced))
                    if let source = entry.source {
                        Text("Source: \(source)")
                            .font(.system(size: 13))
                            .foregroundStyle(.secondary)
                    }
                }
                Button("Remove") {
                    confirmRemove = entry.provider
                }
                .buttonStyle(.borderless)
                .foregroundStyle(.secondary)
                .font(.system(size: 14))
            } else {
                Button("Set…") {
                    sheet = .existing(entry.provider)
                }
                .controlSize(.small)
            }
        }
        .padding(.vertical, 4)
    }
}

private struct SecretEditorSheet: View {
    let title: String
    let providerLocked: Bool
    let initialProvider: String
    let onSave: (String, String) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var provider: String
    @State private var value: String = ""

    init(title: String, providerLocked: Bool, initialProvider: String, onSave: @escaping (String, String) -> Void) {
        self.title = title
        self.providerLocked = providerLocked
        self.initialProvider = initialProvider
        self._provider = State(initialValue: initialProvider)
        self.onSave = onSave
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(title)
                .font(.system(size: 16, weight: .medium))

            if !providerLocked {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Provider name")
                        .font(.system(size: 14))
                        .foregroundStyle(.secondary)
                    TextField("e.g. openai", text: $provider)
                        .textFieldStyle(.roundedBorder)
                }
            }

            VStack(alignment: .leading, spacing: 4) {
                Text("API key")
                    .font(.system(size: 14))
                    .foregroundStyle(.secondary)
                SecureField("Paste key", text: $value)
                    .textFieldStyle(.roundedBorder)
                    .font(.system(size: 14, design: .monospaced))
            }

            HStack {
                Spacer()
                Button("Cancel") { dismiss() }
                Button("Save") {
                    onSave(provider.trimmingCharacters(in: .whitespacesAndNewlines), value)
                    dismiss()
                }
                .buttonStyle(.borderedProminent)
                .keyboardShortcut(.defaultAction)
                .disabled(value.isEmpty || (!providerLocked && provider.trimmingCharacters(in: .whitespaces).isEmpty))
            }
        }
        .padding(20)
        .frame(width: 360)
    }
}

// MARK: - Debug tab

struct LogEntry: Decodable, Identifiable {
    let seq: Int
    let ts: Double
    let level: String
    let source: String
    let message: String

    var id: Int { seq }
}

@MainActor
final class DebugVM: ObservableObject {
    @Published var entries: [LogEntry] = []
    @Published var error: String?
    @Published var sourceFilter: String = "All"
    @Published var levelFilter: LevelFilter = .all
    @Published var toast: String?

    enum LevelFilter: String, CaseIterable, Identifiable {
        case all = "All"
        case error = "Errors"
        case warn = "Warnings"
        case info = "Info"
        var id: String { rawValue }
    }

    private var lastSeq: Int = 0
    private var pollTask: Task<Void, Never>?
    private let maxVisible = 500

    var sources: [String] {
        var set = Set(entries.map(\.source))
        set.insert("All")
        return set.sorted()
    }

    var filtered: [LogEntry] {
        entries.filter { e in
            let levelOK: Bool
            switch levelFilter {
            case .all: levelOK = true
            case .error: levelOK = e.level == "error"
            case .warn: levelOK = e.level == "warn"
            case .info: levelOK = e.level == "info"
            }
            let sourceOK = sourceFilter == "All" || e.source == sourceFilter
            return levelOK && sourceOK
        }.suffix(maxVisible).map { $0 }
    }

    func start() {
        stop()
        pollTask = Task { [weak self] in
            while !(Task.isCancelled) {
                await self?.poll()
                try? await Task.sleep(nanoseconds: 2_000_000_000)
            }
        }
    }

    func stop() {
        pollTask?.cancel()
        pollTask = nil
    }

    func poll() async {
        guard let base = await SettingsBackend.discover() else { return }
        var comps = URLComponents(string: "\(base)/api/logs")
        comps?.queryItems = [URLQueryItem(name: "since", value: String(lastSeq))]
        guard let url = comps?.url else { return }
        do {
            let (data, _) = try await URLSession.shared.data(from: url)
            let new = try JSONDecoder().decode([LogEntry].self, from: data)
            if !new.isEmpty {
                entries.append(contentsOf: new)
                lastSeq = new.map(\.seq).max() ?? lastSeq
            }
        } catch {
            // soft-fail poll
        }
    }

    func clear() async {
        guard let base = await SettingsBackend.discover(),
              let url = URL(string: "\(base)/api/logs") else { return }
        var req = URLRequest(url: url)
        req.httpMethod = "DELETE"
        _ = try? await URLSession.shared.data(for: req)
        entries.removeAll()
        lastSeq = 0
    }

    func sendFeedback(title: String, description: String, includeLogs: Bool) async -> Bool {
        guard let base = await SettingsBackend.discover(),
              let url = URL(string: "\(base)/api/issues/report") else { return false }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let body: [String: Any] = [
            "title": title,
            "description": description,
            "include_logs": includeLogs
        ]
        req.httpBody = try? JSONSerialization.data(withJSONObject: body)
        do {
            let (_, resp) = try await URLSession.shared.data(for: req)
            if let http = resp as? HTTPURLResponse, (200..<300).contains(http.statusCode) {
                toast = "Feedback submitted"
                Task {
                    try? await Task.sleep(nanoseconds: 2_500_000_000)
                    await MainActor.run { self.toast = nil }
                }
                return true
            }
        } catch {
            toast = "Send failed: \(error.localizedDescription)"
        }
        return false
    }
}

struct DebugTab: View {
    @StateObject private var vm = DebugVM()
    @State private var showFeedback = false
    @State private var autoScroll = true

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            logList
            Divider()
            footer
        }
        .overlay(alignment: .top) {
            if let toast = vm.toast {
                Text(toast)
                    .font(.system(size: 14))
                    .padding(.horizontal, 12)
                    .padding(.vertical, 6)
                    .background(
                        RoundedRectangle(cornerRadius: 6)
                            .fill(Color.secondary.opacity(0.2))
                    )
                    .padding(.top, 8)
                    .transition(.opacity)
            }
        }
        .onAppear { vm.start() }
        .onDisappear { vm.stop() }
        .sheet(isPresented: $showFeedback) {
            FeedbackSheet { title, description, includeLogs in
                Task { _ = await vm.sendFeedback(title: title, description: description, includeLogs: includeLogs) }
            }
        }
    }

    private var header: some View {
        HStack(spacing: 8) {
            ForEach(DebugVM.LevelFilter.allCases) { f in
                Button {
                    vm.levelFilter = f
                } label: {
                    Text(f.rawValue)
                        .font(.system(size: 13, weight: vm.levelFilter == f ? .medium : .regular))
                        .foregroundStyle(vm.levelFilter == f ? .primary : .secondary)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 3)
                        .background(
                            RoundedRectangle(cornerRadius: 4)
                                .stroke(Color.secondary.opacity(vm.levelFilter == f ? 0.4 : 0.2), lineWidth: 1)
                        )
                }
                .buttonStyle(.plain)
            }

            Picker("", selection: $vm.sourceFilter) {
                ForEach(vm.sources, id: \.self) { src in
                    Text(src).tag(src)
                }
            }
            .labelsHidden()
            .controlSize(.small)
            .frame(width: 140)

            Spacer()

            Button("Clear") {
                Task { await vm.clear() }
            }
            .buttonStyle(.borderless)
            .foregroundStyle(.secondary)
            .controlSize(.small)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
    }

    private var logList: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 0) {
                    ForEach(vm.filtered) { entry in
                        LogRow(entry: entry)
                            .id(entry.seq)
                        Divider()
                    }
                }
                .padding(.horizontal, 12)
                .padding(.vertical, 6)
            }
            .onChange(of: vm.filtered.count) { _, _ in
                if autoScroll, let last = vm.filtered.last {
                    withAnimation(.linear(duration: 0.1)) {
                        proxy.scrollTo(last.seq, anchor: .bottom)
                    }
                }
            }
        }
    }

    private var footer: some View {
        HStack {
            Toggle("Auto-scroll", isOn: $autoScroll)
                .toggleStyle(.switch)
                .controlSize(.mini)
                .font(.system(size: 13))
            Spacer()
            Button("Send feedback…") {
                showFeedback = true
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.small)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
    }
}

private struct LogRow: View {
    let entry: LogEntry

    private static let tsFormatter: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "HH:mm:ss"
        return f
    }()

    private var tsText: String {
        Self.tsFormatter.string(from: Date(timeIntervalSince1970: entry.ts))
    }

    private var levelColor: Color {
        switch entry.level {
        case "error": return .red
        case "warn": return .orange
        default: return .secondary
        }
    }

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Text(tsText)
                .font(.system(size: 12, design: .monospaced))
                .foregroundStyle(.secondary)
                .frame(width: 62, alignment: .leading)
            Text(entry.level.uppercased())
                .font(.system(size: 12, weight: .medium, design: .monospaced))
                .foregroundStyle(levelColor)
                .frame(width: 44, alignment: .leading)
            Text(entry.source)
                .font(.system(size: 12, design: .monospaced))
                .foregroundStyle(.secondary)
                .frame(width: 80, alignment: .leading)
                .lineLimit(1)
                .truncationMode(.tail)
            Text(entry.message)
                .font(.system(size: 13, design: .monospaced))
                .foregroundStyle(.primary)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
        .padding(.vertical, 3)
    }
}

private struct FeedbackSheet: View {
    let onSend: (String, String, Bool) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var title: String = ""
    @State private var description: String = ""
    @State private var includeLogs: Bool = true

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Send feedback")
                .font(.system(size: 16, weight: .medium))

            VStack(alignment: .leading, spacing: 4) {
                Text("Title")
                    .font(.system(size: 14))
                    .foregroundStyle(.secondary)
                TextField("Short summary", text: $title)
                    .textFieldStyle(.roundedBorder)
            }

            VStack(alignment: .leading, spacing: 4) {
                Text("Description")
                    .font(.system(size: 14))
                    .foregroundStyle(.secondary)
                TextEditor(text: $description)
                    .font(.system(size: 14))
                    .frame(height: 120)
                    .overlay(
                        RoundedRectangle(cornerRadius: 4)
                            .stroke(Color.secondary.opacity(0.3), lineWidth: 1)
                    )
            }

            Toggle("Include recent logs", isOn: $includeLogs)
                .toggleStyle(.switch)
                .controlSize(.small)

            HStack {
                Spacer()
                Button("Cancel") { dismiss() }
                Button("Send") {
                    onSend(title, description, includeLogs)
                    dismiss()
                }
                .buttonStyle(.borderedProminent)
                .keyboardShortcut(.defaultAction)
                .disabled(title.trimmingCharacters(in: .whitespaces).isEmpty)
            }
        }
        .padding(20)
        .frame(width: 420)
    }
}
