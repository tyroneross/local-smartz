import SwiftUI

// MARK: - Models

struct AgentEntry: Decodable, Identifiable {
    let name: String
    let title: String
    let summary: String
    let model: String?
    let defaultModel: String?
    let modelOverride: String?

    var id: String { name }
    var effectiveModel: String { model ?? defaultModel ?? "" }
    var hasOverride: Bool { !(modelOverride ?? "").isEmpty }

    enum CodingKeys: String, CodingKey {
        case name
        case title
        case summary
        case model
        case defaultModel = "default_model"
        case modelOverride = "model_override"
    }
}

private struct InstalledModel: Decodable {
    let name: String
    let size_gb: Double?
}

private struct AgentsResponse: Decodable {
    let profile: String?
    let agents: [AgentEntry]
}

private struct AgentModelsResponse: Decodable {
    let profile: String?
    let models: [String: String]
}

private struct InstalledModelsResponse: Decodable {
    let models: [InstalledModel]
}

// MARK: - View model

@MainActor
final class AgentRoutingVM: ObservableObject {
    @Published var agents: [AgentEntry] = []
    @Published var installedModels: [String] = []
    /// Backend's effective per-agent mapping (name -> model). Used to detect
    /// whether a given agent has an override ("") vs an inherited default.
    @Published var effective: [String: String] = [:]
    @Published var loading = false
    @Published var error: String?

    static let profileDefaultSentinel = "__profile_default__"

    /// Returns the picker selection tag for a given agent — either a concrete
    /// installed model name or the profile-default sentinel.
    func selection(for agent: AgentEntry) -> String {
        if let override = agent.modelOverride, !override.isEmpty {
            return override
        }
        return Self.profileDefaultSentinel
    }

    /// Union of "Profile default" + installed models + each agent's current
    /// backend value (so stale/unavailable assignments stay visible).
    func pickerOptions(for agent: AgentEntry) -> [String] {
        var seen = Set<String>()
        var result: [String] = [Self.profileDefaultSentinel]
        for m in installedModels where !m.isEmpty && seen.insert(m).inserted {
            result.append(m)
        }
        if let defaultModel = agent.defaultModel,
           !defaultModel.isEmpty,
           seen.insert(defaultModel).inserted {
            result.append(defaultModel)
        }
        let current = agent.effectiveModel
        if !current.isEmpty, seen.insert(current).inserted {
            result.append(current)
        }
        return result
    }

    func refresh() async {
        loading = true
        defer { loading = false }
        error = nil

        guard let base = await SettingsBackend.discover() else {
            error = "Could not load agents"
            return
        }

        // Load agents
        if let url = URL(string: "\(base)/api/agents") {
            do {
                let (data, resp) = try await URLSession.shared.data(from: url)
                if let http = resp as? HTTPURLResponse, (200..<300).contains(http.statusCode) {
                    let decoded = try JSONDecoder().decode(AgentsResponse.self, from: data)
                    agents = decoded.agents
                } else {
                    error = "Could not load agents"
                    return
                }
            } catch {
                self.error = "Could not load agents"
                return
            }
        }

        // Load effective mapping (best-effort, non-fatal)
        if let url = URL(string: "\(base)/api/agents/models") {
            if let (data, _) = try? await URLSession.shared.data(from: url),
               let decoded = try? JSONDecoder().decode(AgentModelsResponse.self, from: data) {
                effective = decoded.models
            }
        }

        // Load installed models (best-effort, non-fatal)
        if let url = URL(string: "\(base)/api/models") {
            if let (data, _) = try? await URLSession.shared.data(from: url),
               let decoded = try? JSONDecoder().decode(InstalledModelsResponse.self, from: data) {
                installedModels = decoded.models.map(\.name)
            }
        }
    }

    /// Assigns a model for the given agent. Pass `nil` (or the sentinel) to
    /// clear the override and inherit the profile default.
    func assign(agent: String, model: String?) async {
        guard let base = await SettingsBackend.discover(),
              let url = URL(string: "\(base)/api/agents/\(agent)/model") else { return }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let payload: [String: String] = ["model": model ?? ""]
        req.httpBody = try? JSONSerialization.data(withJSONObject: payload)
        _ = try? await URLSession.shared.data(for: req)

        // Optimistic local update, then re-sync from backend to stay authoritative.
        if let idx = agents.firstIndex(where: { $0.name == agent }) {
            let existing = agents[idx]
            agents[idx] = AgentEntry(
                name: existing.name,
                title: existing.title,
                summary: existing.summary,
                model: (model?.isEmpty ?? true) ? existing.defaultModel : model,
                defaultModel: existing.defaultModel,
                modelOverride: (model?.isEmpty ?? true) ? "" : model
            )
        }
        await refresh()
    }

    func resetAll() async {
        for agent in agents {
            await assign(agent: agent.name, model: nil)
        }
    }
}

// MARK: - Tab view

struct AgentRoutingTab: View {
    @StateObject private var vm = AgentRoutingVM()
    @State private var confirmReset = false

    var body: some View {
        ScrollView {
            SettingsTabsForm {
                HStack(alignment: .firstTextBaseline) {
                    Text("Agent Routing")
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

                Text("Configure which Ollama model each agent uses. Leave as \u{201C}Profile default\u{201D} to inherit from the active profile.")
                    .font(.system(size: 12))
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)

                Divider().padding(.vertical, 2)

                if let err = vm.error {
                    Text(err)
                        .font(.system(size: 12))
                        .foregroundStyle(.secondary)
                } else if vm.agents.isEmpty {
                    if vm.loading {
                        HStack {
                            ProgressView().controlSize(.small)
                            Text("Loading agents\u{2026}")
                                .font(.system(size: 12))
                                .foregroundStyle(.secondary)
                        }
                    } else {
                        Text("No agents reported by backend.")
                            .font(.system(size: 12))
                            .foregroundStyle(.secondary)
                    }
                } else {
                    agentTable
                    Divider().padding(.vertical, 2)
                    HStack {
                        Spacer()
                        Button("Reset to profile defaults") {
                            confirmReset = true
                        }
                        .buttonStyle(.borderless)
                        .foregroundStyle(.secondary)
                        .font(.system(size: 12))
                        .disabled(vm.agents.allSatisfy { !$0.hasOverride })
                    }
                }
            }
        }
        .task { await vm.refresh() }
        .confirmationDialog(
            "Reset all agent overrides?",
            isPresented: $confirmReset,
            titleVisibility: .visible
        ) {
            Button("Reset", role: .destructive) {
                Task { await vm.resetAll() }
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("Every agent will inherit its model from the active profile. You can reassign any time.")
        }
    }

    // MARK: Table

    @ViewBuilder
    private var agentTable: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            Divider()
            ForEach(Array(vm.agents.enumerated()), id: \.element.id) { idx, agent in
                if idx > 0 { Divider() }
                row(agent: agent)
            }
        }
        .overlay(
            RoundedRectangle(cornerRadius: 4)
                .stroke(Color.secondary.opacity(0.2), lineWidth: 1)
        )
    }

    private var header: some View {
        HStack(alignment: .center, spacing: 12) {
            Text("AGENT")
                .font(.system(size: 11, weight: .medium))
                .foregroundStyle(.secondary)
                .frame(width: 96, alignment: .leading)
            Text("ROLE")
                .font(.system(size: 11, weight: .medium))
                .foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, alignment: .leading)
            Text("MODEL")
                .font(.system(size: 11, weight: .medium))
                .foregroundStyle(.secondary)
                .frame(width: 180, alignment: .trailing)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
    }

    @ViewBuilder
    private func row(agent: AgentEntry) -> some View {
        let selection = Binding<String>(
            get: { vm.selection(for: agent) },
            set: { newValue in
                let toSend: String? = (newValue == AgentRoutingVM.profileDefaultSentinel) ? nil : newValue
                Task { await vm.assign(agent: agent.name, model: toSend) }
            }
        )

        HStack(alignment: .center, spacing: 12) {
            Text(agent.title)
                .font(.system(size: 13, weight: .medium))
                .foregroundStyle(.primary)
                .frame(width: 96, alignment: .leading)
                .lineLimit(1)
                .truncationMode(.tail)

            Text(agent.summary)
                .font(.system(size: 11))
                .foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, alignment: .leading)
                .lineLimit(1)
                .truncationMode(.tail)

            Picker("", selection: selection) {
                ForEach(vm.pickerOptions(for: agent), id: \.self) { option in
                    if option == AgentRoutingVM.profileDefaultSentinel {
                        Text("Profile default (\(Self.compactModelName(agent.effectiveModel)))")
                            .italic()
                            .tag(option)
                    } else {
                        Text(Self.compactModelName(option)).tag(option)
                    }
                }
            }
            .pickerStyle(.menu)
            .labelsHidden()
            .controlSize(.small)
            .frame(width: 180, alignment: .trailing)
            .help(agent.hasOverride
                  ? "Override: \(agent.modelOverride ?? "")"
                  : "Profile default: \(agent.effectiveModel)")
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
    }

    private static func compactModelName(_ model: String) -> String {
        var value = model
        for suffix in [
            "-instruct-q5_K_M",
            "-instruct-q4_K_M",
            "-q8_0",
            "-q6_K",
            "-q5_K_M",
            "-q4_K_M",
            "-latest",
        ] {
            if value.hasSuffix(suffix) {
                value.removeLast(suffix.count)
                break
            }
        }
        return value.isEmpty ? "not set" : value
    }
}
