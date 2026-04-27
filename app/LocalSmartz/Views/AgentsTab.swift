import SwiftUI

// MARK: - Agents tab (editable — 2026-04-23 phase-2 follow-up)
//
// D2 shipped a read-only viewer; this pass promotes it to an editor with a
// per-agent model picker (pulls from /api/models/catalog?tier=<tier>) and a
// system_focus markdown textarea that PUTs to /api/agents/<role>/prompt.
// The agent/{role}/model override still uses the existing
// POST /api/agents/<name>/model endpoint. Cancel reverts to baseline.

// MARK: - Catalog model shape for the picker
// Reuses the backend /api/models/catalog envelope used by ModelsTab.

fileprivate struct AgentModelOption: Decodable, Identifiable, Hashable {
    let name: String
    let ramClass: String?
    let installed: Bool
    var id: String { name }

    enum CodingKeys: String, CodingKey {
        case name
        case ramClass = "ram_class"
        case installed
    }
}

fileprivate struct AgentCatalogResponse: Decodable {
    let catalog: [AgentModelOption]
    let current: String
}

@MainActor
fileprivate final class AgentsVM: ObservableObject {
    @Published var agents: [AgentInfo] = []
    @Published var profile: String?
    @Published var loading = false
    @Published var error: String?
    @Published var saving = false
    @Published var saveError: String?

    @Published var catalog: [AgentModelOption] = []

    /// Draft edits keyed by agent name. Populated from the loaded agent
    /// on enter-edit and cleared on Save or Cancel.
    @Published var draftModel: [String: String] = [:]
    @Published var draftPrompt: [String: String] = [:]

    /// Name of the agent currently in edit mode. Only one at a time to
    /// keep the surface small and the Save/Cancel affordance obvious.
    @Published var editingAgent: String?

    func refresh() async {
        loading = true
        defer { loading = false }
        error = nil

        guard let base = await SettingsBackend.discover() else {
            error = "Backend not reachable. Is the main window open?"
            return
        }
        await loadAgents(base: base)
        await loadCatalog(base: base)
    }

    private func loadAgents(base: String) async {
        guard let url = URL(string: "\(base)/api/agents") else {
            error = "Invalid agents URL."
            return
        }
        struct Resp: Decodable {
            let profile: String?
            let agents: [AgentInfo]
        }
        do {
            let (data, _) = try await URLSession.shared.data(from: url)
            let resp = try JSONDecoder().decode(Resp.self, from: data)
            self.profile = resp.profile
            self.agents = resp.agents
        } catch {
            self.error = "Could not load agents: \(error.localizedDescription)"
        }
    }

    private func loadCatalog(base: String) async {
        guard let url = URL(string: "\(base)/api/models/catalog") else { return }
        do {
            let (data, _) = try await URLSession.shared.data(from: url)
            let resp = try JSONDecoder().decode(AgentCatalogResponse.self, from: data)
            // Only installed models are valid picker choices — picking an
            // uninstalled model would fail at run time. ModelsTab handles
            // installs separately.
            self.catalog = resp.catalog.filter { $0.installed }
        } catch {
            // Non-fatal — picker just falls back to the single current model.
        }
    }

    func beginEdit(_ agent: AgentInfo) {
        editingAgent = agent.name
        draftModel[agent.name] = agent.model ?? ""
        draftPrompt[agent.name] = agent.systemFocus ?? ""
        saveError = nil
    }

    func cancelEdit() {
        if let name = editingAgent {
            draftModel.removeValue(forKey: name)
            draftPrompt.removeValue(forKey: name)
        }
        editingAgent = nil
        saveError = nil
    }

    /// PUT /api/agents/<role>/prompt + POST /api/agents/<name>/model.
    /// Two endpoints so a user can tweak one without the other.
    func save(_ agent: AgentInfo) async {
        guard let base = await SettingsBackend.discover() else {
            saveError = "Backend not reachable."
            return
        }
        saving = true
        defer { saving = false }
        saveError = nil

        let name = agent.name
        let newModel = (draftModel[name] ?? "").trimmingCharacters(in: .whitespaces)
        let newPrompt = draftPrompt[name] ?? ""

        // Model override: persist via POST /api/agents/<name>/model only
        // when the draft differs from the loaded value. Empty string means
        // "don't change".
        if !newModel.isEmpty && newModel != (agent.model ?? "") {
            let url = URL(string: "\(base)/api/agents/\(name)/model")!
            var req = URLRequest(url: url)
            req.httpMethod = "POST"
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = try? JSONSerialization.data(withJSONObject: ["model": newModel])
            do {
                let (_, resp) = try await URLSession.shared.data(for: req)
                if let http = resp as? HTTPURLResponse, http.statusCode != 200 {
                    saveError = "Model update failed (HTTP \(http.statusCode))"
                    return
                }
            } catch {
                saveError = "Model update failed: \(error.localizedDescription)"
                return
            }
        }

        // System prompt: PUT /api/agents/<role>/prompt when non-empty and
        // differs. The endpoint writes the markdown file and the next
        // /api/agents read surfaces it — no restart needed.
        if !newPrompt.isEmpty && newPrompt != (agent.systemFocus ?? "") {
            let url = URL(string: "\(base)/api/agents/\(name)/prompt")!
            var req = URLRequest(url: url)
            req.httpMethod = "PUT"
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = try? JSONSerialization.data(
                withJSONObject: ["system_focus": newPrompt]
            )
            do {
                let (_, resp) = try await URLSession.shared.data(for: req)
                if let http = resp as? HTTPURLResponse, http.statusCode != 200 {
                    saveError = "Prompt update failed (HTTP \(http.statusCode))"
                    return
                }
            } catch {
                saveError = "Prompt update failed: \(error.localizedDescription)"
                return
            }
        }

        // Clear draft state + refresh so the card shows the new values.
        draftModel.removeValue(forKey: name)
        draftPrompt.removeValue(forKey: name)
        editingAgent = nil
        await refresh()
    }
}

struct AgentsTab: View {
    @StateObject private var vm = AgentsVM()

    var body: some View {
        ScrollView {
            SettingsTabsForm {
                header

                Divider().padding(.vertical, 2)

                if let err = vm.error {
                    errorView(err)
                } else if vm.loading && vm.agents.isEmpty {
                    HStack {
                        Spacer()
                        ProgressView().controlSize(.small)
                        Spacer()
                    }
                    .padding(.vertical, 20)
                } else if vm.agents.isEmpty {
                    Text("No agents found for the active profile.")
                        .font(.system(size: 14))
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(Array(vm.agents.enumerated()), id: \.element.id) { idx, agent in
                        if idx > 0 { Divider().padding(.vertical, 4) }
                        AgentCard(
                            agent: agent,
                            catalog: vm.catalog,
                            isEditing: vm.editingAgent == agent.name,
                            draftModel: Binding(
                                get: { vm.draftModel[agent.name] ?? "" },
                                set: { vm.draftModel[agent.name] = $0 }
                            ),
                            draftPrompt: Binding(
                                get: { vm.draftPrompt[agent.name] ?? "" },
                                set: { vm.draftPrompt[agent.name] = $0 }
                            ),
                            saving: vm.saving,
                            onEdit: { vm.beginEdit(agent) },
                            onCancel: { vm.cancelEdit() },
                            onSave: { Task { await vm.save(agent) } }
                        )
                    }
                    if let err = vm.saveError {
                        Text(err)
                            .font(.system(size: 13))
                            .foregroundStyle(.red)
                    }
                }
            }
        }
        .task {
            if vm.agents.isEmpty { await vm.refresh() }
        }
    }

    private var header: some View {
        HStack {
            VStack(alignment: .leading, spacing: 1) {
                Text("Agents")
                    .font(.system(size: 15, weight: .medium))
                if let profile = vm.profile {
                    Text("Profile: \(profile)")
                        .font(.system(size: 13))
                        .foregroundStyle(.secondary)
                }
            }
            Spacer()
            Button {
                Task { await vm.refresh() }
            } label: {
                Image(systemName: "arrow.clockwise")
            }
            .controlSize(.small)
            .disabled(vm.loading)
            .accessibilityLabel("Refresh agents")
            .help("Refresh agents")
        }
    }

    @ViewBuilder
    private func errorView(_ message: String) -> some View {
        VStack(spacing: 8) {
            Text(message)
                .font(.system(size: 14))
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
            Button("Retry") {
                Task { await vm.refresh() }
            }
            .controlSize(.small)
            .accessibilityLabel("Retry loading agents")
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 12)
    }
}

// MARK: - Per-agent card (read + edit modes)

private struct AgentCard: View {
    let agent: AgentInfo
    let catalog: [AgentModelOption]
    let isEditing: Bool
    @Binding var draftModel: String
    @Binding var draftPrompt: String
    let saving: Bool
    let onEdit: () -> Void
    let onCancel: () -> Void
    let onSave: () -> Void

    @State private var promptExpanded = false

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            // Header row: title + model
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(agent.title)
                    .font(.system(size: 15, weight: .medium))
                Spacer(minLength: 8)
                if isEditing {
                    EmptyView()  // picker rendered in edit section
                } else if let model = agent.model, !model.isEmpty {
                    Text(model)
                        .font(.system(size: 13, design: .monospaced))
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                } else {
                    Text("—")
                        .font(.system(size: 13, design: .monospaced))
                        .foregroundStyle(.tertiary)
                }
                if !isEditing {
                    Button("Edit") { onEdit() }
                        .controlSize(.small)
                        .buttonStyle(.borderless)
                }
            }

            // Summary
            if !agent.summary.isEmpty {
                Text(agent.summary)
                    .font(.system(size: 14))
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            // Tool allow-list (read-only — editing tools is out of scope here)
            if let tools = agent.tools, !tools.isEmpty {
                ToolsList(tools: tools)
            } else {
                Text("Tools: (role inherits only DeepAgents built-ins)")
                    .font(.system(size: 13))
                    .foregroundStyle(.tertiary)
            }

            // Edit mode: model picker + system_focus editor + Save / Cancel
            if isEditing {
                editControls
            } else if let focus = agent.systemFocus, !focus.isEmpty {
                DisclosureGroup(isExpanded: $promptExpanded) {
                    Text(focus)
                        .font(.system(size: 13, design: .monospaced))
                        .foregroundStyle(.primary)
                        .textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.top, 4)
                } label: {
                    Text("System prompt")
                        .font(.system(size: 13))
                        .foregroundStyle(.secondary)
                }
                .accessibilityLabel("Toggle system prompt for \(agent.title)")
            }
        }
        .padding(.vertical, 4)
    }

    // Edit controls: compact form stacked below the card header.
    @ViewBuilder
    private var editControls: some View {
        VStack(alignment: .leading, spacing: 6) {
            // Model picker — shows "installed only" models. Falls back to
            // a plain text field when the catalog is empty.
            if catalog.isEmpty {
                TextField("Model", text: $draftModel)
                    .textFieldStyle(.roundedBorder)
                    .font(.system(size: 14, design: .monospaced))
            } else {
                Picker("Model", selection: $draftModel) {
                    // Ensure the currently-saved model is always pickable,
                    // even if no longer in the installed catalog.
                    if !catalog.contains(where: { $0.name == draftModel }),
                       !draftModel.isEmpty {
                        Text(draftModel).tag(draftModel)
                    }
                    ForEach(catalog) { opt in
                        Text(opt.name).tag(opt.name)
                    }
                }
                .pickerStyle(.menu)
                .controlSize(.small)
            }

            // System focus — markdown textarea. TextEditor fills vertical
            // space and is bordered to match SettingsTabsForm's visual
            // rhythm (single stroke, no pill).
            Text("System prompt (markdown)")
                .font(.system(size: 13))
                .foregroundStyle(.secondary)
            TextEditor(text: $draftPrompt)
                .font(.system(size: 13, design: .monospaced))
                .frame(minHeight: 120)
                .padding(6)
                .overlay(
                    RoundedRectangle(cornerRadius: 4)
                        .stroke(Color.secondary.opacity(0.25), lineWidth: 1)
                )

            HStack {
                Spacer()
                Button("Cancel") { onCancel() }
                    .controlSize(.small)
                    .disabled(saving)
                Button("Save") { onSave() }
                    .controlSize(.small)
                    .keyboardShortcut(.defaultAction)
                    .disabled(saving || draftPrompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
            if saving {
                HStack(spacing: 6) {
                    ProgressView().controlSize(.small)
                    Text("Saving…")
                        .font(.system(size: 13))
                        .foregroundStyle(.secondary)
                }
            }
        }
    }
}

// MARK: - Tool allow-list

/// Horizontal wrapping row of tool names. Calm Precision: no pills, no
/// backgrounds — monospaced secondary text separated by thin dividers.
private struct ToolsList: View {
    let tools: [String]

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text("Tools")
                .font(.system(size: 13))
                .foregroundStyle(.secondary)
            WrappingHStack(items: tools) { tool in
                Text(tool)
                    .font(.system(size: 13, design: .monospaced))
                    .foregroundStyle(.primary)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 2)
                    .overlay(
                        RoundedRectangle(cornerRadius: 3)
                            .stroke(Color.secondary.opacity(0.25), lineWidth: 1)
                    )
            }
        }
    }
}

/// Minimal horizontal-wrap layout for short chips. Uses SwiftUI's `Layout`
/// API — available on macOS 13+, target is macOS 14 so this is safe.
private struct WrappingHStack<Item: Hashable, Content: View>: View {
    let items: [Item]
    @ViewBuilder let content: (Item) -> Content

    init(items: [Item], @ViewBuilder content: @escaping (Item) -> Content) {
        self.items = items
        self.content = content
    }

    var body: some View {
        FlowLayout(spacing: 6) {
            ForEach(items, id: \.self) { item in
                content(item)
            }
        }
    }
}

/// Flow layout: left-to-right, wrap to next line when width is exceeded.
private struct FlowLayout: Layout {
    var spacing: CGFloat = 6

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let maxWidth = proposal.width ?? .infinity
        var x: CGFloat = 0
        var y: CGFloat = 0
        var rowHeight: CGFloat = 0
        var totalWidth: CGFloat = 0

        for sv in subviews {
            let size = sv.sizeThatFits(.unspecified)
            if x + size.width > maxWidth, x > 0 {
                y += rowHeight + spacing
                x = 0
                rowHeight = 0
            }
            x += size.width + spacing
            rowHeight = max(rowHeight, size.height)
            totalWidth = max(totalWidth, x)
        }
        return CGSize(width: min(maxWidth, totalWidth), height: y + rowHeight)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var x = bounds.minX
        var y = bounds.minY
        var rowHeight: CGFloat = 0

        for sv in subviews {
            let size = sv.sizeThatFits(.unspecified)
            if x + size.width > bounds.maxX, x > bounds.minX {
                y += rowHeight + spacing
                x = bounds.minX
                rowHeight = 0
            }
            sv.place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(size))
            x += size.width + spacing
            rowHeight = max(rowHeight, size.height)
        }
    }
}
