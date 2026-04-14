import SwiftUI

// MARK: - Agents tab (read-only viewer)
//
// Track D2: surface every configured agent role in Settings so users can see
// title, effective model, tool allow-list, and the role's system_focus prompt
// without reading Python source. Purely informational — edit mode is a
// deferred follow-up and there are no write affordances here.

@MainActor
private final class AgentsVM: ObservableObject {
    @Published var agents: [AgentInfo] = []
    @Published var profile: String?
    @Published var loading = false
    @Published var error: String?

    func refresh() async {
        loading = true
        defer { loading = false }
        error = nil

        guard let base = await SettingsBackend.discover() else {
            error = "Backend not reachable. Is the main window open?"
            return
        }
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
                        .font(.system(size: 12))
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(Array(vm.agents.enumerated()), id: \.element.id) { idx, agent in
                        if idx > 0 { Divider().padding(.vertical, 4) }
                        AgentCard(agent: agent)
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
                    .font(.system(size: 13, weight: .medium))
                if let profile = vm.profile {
                    Text("Profile: \(profile)")
                        .font(.system(size: 11))
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
                .font(.system(size: 12))
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

// MARK: - Per-agent card

private struct AgentCard: View {
    let agent: AgentInfo
    @State private var promptExpanded = false

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            // Header row: title + model (mono, muted)
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(agent.title)
                    .font(.system(size: 13, weight: .medium))
                Spacer(minLength: 8)
                if let model = agent.model, !model.isEmpty {
                    Text(model)
                        .font(.system(size: 11, design: .monospaced))
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                } else {
                    Text("—")
                        .font(.system(size: 11, design: .monospaced))
                        .foregroundStyle(.tertiary)
                }
            }

            // Summary
            if !agent.summary.isEmpty {
                Text(agent.summary)
                    .font(.system(size: 12))
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            // Tool allow-list
            if let tools = agent.tools, !tools.isEmpty {
                ToolsList(tools: tools)
            } else {
                Text("Tools: (role inherits only DeepAgents built-ins)")
                    .font(.system(size: 11))
                    .foregroundStyle(.tertiary)
            }

            // System prompt disclosure — only if backend sent one
            if let focus = agent.systemFocus, !focus.isEmpty {
                DisclosureGroup(isExpanded: $promptExpanded) {
                    Text(focus)
                        .font(.system(size: 11, design: .monospaced))
                        .foregroundStyle(.primary)
                        .textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.top, 4)
                } label: {
                    Text("System prompt")
                        .font(.system(size: 11))
                        .foregroundStyle(.secondary)
                }
                .accessibilityLabel("Toggle system prompt for \(agent.title)")
            }
        }
        .padding(.vertical, 4)
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
                .font(.system(size: 11))
                .foregroundStyle(.secondary)
            WrappingHStack(items: tools) { tool in
                Text(tool)
                    .font(.system(size: 11, design: .monospaced))
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
