import SwiftUI

// MARK: - Pattern tab (2026-04-23 phase-2 follow-up, Item 3)
//
// Dropdown listing the 4 patterns from GET /api/patterns. Save persists
// pattern + provider + per-role model_ref to .localsmartz/config.json via
// the existing POST /api/research flow's body fields, but the tab itself
// writes directly to config through a new small helper endpoint — we reuse
// /api/models/select (planning_model) for the per-role override path, and
// POST { provider, pattern } to a new handler below. For now, persistence
// is achieved by writing the values to a local file via /api/secrets
// keyed store is not appropriate (not a secret); so we use an explicit
// helper route not exposed to the user.
//
// Scope: this tab only sets pattern+provider. Per-role slot edits inherit
// from AgentsTab (user sets per-role models there; this tab doesn't
// duplicate).

private struct PatternRow: Decodable, Identifiable, Hashable {
    let name: String
    let description: String
    let requiredRoles: [String]
    var id: String { name }

    enum CodingKeys: String, CodingKey {
        case name
        case description
        case requiredRoles = "required_roles"
    }
}

private struct PatternListResponse: Decodable {
    let patterns: [PatternRow]
}

@MainActor
private final class PatternsVM: ObservableObject {
    @Published var patterns: [PatternRow] = []
    @Published var loading = false
    @Published var error: String?
    @Published var saving = false
    @Published var saveError: String?
    @Published var lastSavedMessage: String?

    /// Draft — the tab writes both together via one Save click.
    @Published var selectedPattern: String = "single"
    @Published var selectedProvider: String = "ollama"

    let providers = ["ollama", "anthropic", "openai", "groq"]

    func refresh() async {
        loading = true
        defer { loading = false }
        error = nil

        guard let base = await SettingsBackend.discover() else {
            error = "Backend not reachable."
            return
        }
        await loadPatterns(base: base)
        await loadCurrent(base: base)
    }

    private func loadPatterns(base: String) async {
        guard let url = URL(string: "\(base)/api/patterns") else { return }
        do {
            let (data, _) = try await URLSession.shared.data(from: url)
            let resp = try JSONDecoder().decode(PatternListResponse.self, from: data)
            self.patterns = resp.patterns
        } catch {
            self.error = "Could not load patterns: \(error.localizedDescription)"
        }
    }

    private func loadCurrent(base: String) async {
        // /api/status carries the active profile but not pattern/provider.
        // The backend's project config is the source of truth — we read it
        // by delegating to a small helper that returns the two fields.
        // If the field is absent, defaults stand.
        guard let url = URL(string: "\(base)/api/patterns/current") else { return }
        struct Resp: Decodable {
            let pattern: String?
            let provider: String?
        }
        if let (data, _) = try? await URLSession.shared.data(from: url),
           let decoded = try? JSONDecoder().decode(Resp.self, from: data) {
            if let p = decoded.pattern, !p.isEmpty { selectedPattern = p }
            if let p = decoded.provider, !p.isEmpty { selectedProvider = p }
        }
    }

    /// POST /api/patterns/active { pattern, provider }. Backend writes to
    /// .localsmartz/config.json via the shared save_config helper.
    func save() async {
        guard let base = await SettingsBackend.discover() else {
            saveError = "Backend not reachable."
            return
        }
        saving = true
        defer { saving = false }
        saveError = nil
        lastSavedMessage = nil

        let url = URL(string: "\(base)/api/patterns/active")!
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try? JSONSerialization.data(withJSONObject: [
            "pattern": selectedPattern,
            "provider": selectedProvider,
        ])
        do {
            let (_, resp) = try await URLSession.shared.data(for: req)
            if let http = resp as? HTTPURLResponse, http.statusCode != 200 {
                saveError = "Save failed (HTTP \(http.statusCode))"
                return
            }
            lastSavedMessage = "Saved. New threads will use this pattern."
        } catch {
            saveError = "Save failed: \(error.localizedDescription)"
        }
    }
}

struct PatternTab: View {
    @StateObject private var vm = PatternsVM()

    var body: some View {
        ScrollView {
            SettingsTabsForm {
                HStack {
                    Text("Coordination pattern")
                        .font(.system(size: 13, weight: .medium))
                    Spacer()
                    Button {
                        Task { await vm.refresh() }
                    } label: {
                        Image(systemName: "arrow.clockwise")
                    }
                    .controlSize(.small)
                    .disabled(vm.loading)
                }

                if let err = vm.error {
                    Text(err)
                        .font(.system(size: 12))
                        .foregroundStyle(.red)
                }

                Text(
                    "Pattern controls how agents collaborate on a research query. "
                    + "Switching pattern mid-thread is blocked — it starts a new thread."
                )
                .font(.system(size: 11))
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)

                Divider().padding(.vertical, 2)

                SettingsTabsRow("Pattern") {
                    Picker("Pattern", selection: $vm.selectedPattern) {
                        if vm.patterns.isEmpty {
                            Text("single").tag("single")
                        } else {
                            ForEach(vm.patterns) { p in
                                Text(p.name).tag(p.name)
                            }
                        }
                    }
                    .pickerStyle(.menu)
                    .controlSize(.small)
                    .frame(maxWidth: 240, alignment: .leading)
                }

                // Show the description + required roles for the pick.
                if let p = vm.patterns.first(where: { $0.name == vm.selectedPattern }) {
                    VStack(alignment: .leading, spacing: 3) {
                        Text(p.description)
                            .font(.system(size: 11))
                            .foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                        if !p.requiredRoles.isEmpty {
                            Text("Required roles: \(p.requiredRoles.joined(separator: ", "))")
                                .font(.system(size: 11, design: .monospaced))
                                .foregroundStyle(.tertiary)
                        }
                    }
                }

                Divider().padding(.vertical, 2)

                SettingsTabsRow("Provider") {
                    Picker("Provider", selection: $vm.selectedProvider) {
                        ForEach(vm.providers, id: \.self) { prov in
                            Text(prov).tag(prov)
                        }
                    }
                    .pickerStyle(.segmented)
                }

                if vm.selectedProvider != "ollama" {
                    Text(
                        "Cloud provider: runs will prompt for cost confirmation "
                        + "before each query (estimate shown in USD)."
                    )
                    .font(.system(size: 11))
                    .foregroundStyle(.orange)
                    .fixedSize(horizontal: false, vertical: true)
                }

                if let err = vm.saveError {
                    Text(err)
                        .font(.system(size: 11))
                        .foregroundStyle(.red)
                }
                if let msg = vm.lastSavedMessage {
                    Text(msg)
                        .font(.system(size: 11))
                        .foregroundStyle(.green)
                }

                HStack {
                    Spacer()
                    Button("Save") { Task { await vm.save() } }
                        .controlSize(.small)
                        .keyboardShortcut(.defaultAction)
                        .disabled(vm.saving)
                    if vm.saving { ProgressView().controlSize(.small) }
                }
            }
        }
        .task {
            if vm.patterns.isEmpty { await vm.refresh() }
        }
    }
}
