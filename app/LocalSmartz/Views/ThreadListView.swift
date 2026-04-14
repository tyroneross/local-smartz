import SwiftUI

struct AgentInfo: Decodable, Identifiable {
    let name: String
    let title: String
    let summary: String
    /// Effective model for this agent (profile default + user override).
    /// Optional because older payloads may omit it; the Settings → Agents
    /// tab falls back to em-dash when nil.
    let model: String?
    /// Tool allow-list for this agent — surfaced in the sidebar so users
    /// can see what each focused agent will actually call. Backend populates
    /// it from ``AGENT_ROLES[role]["tools"]`` (see profiles.list_agents).
    /// Optional in the decoder because older servers pre-e7b1baa don't
    /// include the field.
    let tools: [String]?
    /// Full role system prompt from ``AGENT_ROLES[role]["system_focus"]``.
    /// Optional in the decoder because older servers (pre-D2) don't send it;
    /// the Settings → Agents tab hides the "System prompt" disclosure when
    /// absent or empty.
    let systemFocus: String?
    var id: String { name }

    enum CodingKeys: String, CodingKey {
        case name
        case title
        case summary
        case model
        case tools
        case systemFocus = "system_focus"
    }
}

struct ThreadListView: View {
    let threads: [ResearchThread]
    @Binding var selectedThread: String?
    let onNewThread: () -> Void
    let agents: [AgentInfo]
    @Binding var focusAgent: String?

    var body: some View {
        List(selection: $selectedThread) {
            Section {
                Button(action: onNewThread) {
                    Label("New Research", systemImage: "plus.circle")
                }
                .buttonStyle(.plain)
                .foregroundStyle(Color.accentColor)
            }

            if !agents.isEmpty {
                Section("Agents") {
                    AgentRow(
                        title: "All agents",
                        summary: "Default multi-step flow",
                        tools: nil,
                        isSelected: focusAgent == nil
                    ) {
                        focusAgent = nil
                    }

                    ForEach(agents) { agent in
                        AgentRow(
                            title: agent.title,
                            summary: agent.summary,
                            // Only render the tool list when this agent is
                            // the currently focused one — keeps the sidebar
                            // calm. Tool list is nil for "All agents" since
                            // its tool surface is the union of everything.
                            tools: focusAgent == agent.name ? agent.tools : nil,
                            isSelected: focusAgent == agent.name
                        ) {
                            focusAgent = (focusAgent == agent.name ? nil : agent.name)
                        }
                    }
                }
            }

            if threads.isEmpty {
                Text("No threads yet")
                    .font(.caption)
                    .foregroundStyle(.tertiary)
            } else {
                let grouped = groupedByDate
                ForEach(Array(grouped.keys.sorted().reversed()), id: \.self) { section in
                    Section(section) {
                        ForEach(grouped[section] ?? []) { thread in
                            VStack(alignment: .leading, spacing: 2) {
                                Text(thread.title)
                                    .font(.subheadline)
                                    .lineLimit(1)
                                HStack {
                                    Text("\(thread.entryCount) queries")
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                    Spacer()
                                    Text(thread.relativeTime)
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                }
                            }
                            .tag(thread.id)
                            .padding(.vertical, 2)
                        }
                    }
                }
            }
        }
        .listStyle(.sidebar)
    }

    /// Tap-only row for the Agents section. Avoids `Button` so the system
    /// hover/pressed background that List applies to in-row buttons doesn't
    /// make non-selected rows look selected.
    ///
    /// When ``tools`` is non-nil (focused agent), renders a compact, indented
    /// list of tool names below the summary. Calm Precision: monospaced,
    /// 11pt, secondary color, no pills/badges.
    private struct AgentRow: View {
        let title: String
        let summary: String
        let tools: [String]?
        let isSelected: Bool
        let onTap: () -> Void

        @State private var hovering = false

        var body: some View {
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 8) {
                    Image(systemName: isSelected ? "checkmark.circle.fill" : "circle")
                        .foregroundStyle(isSelected ? Color.accentColor : .secondary)
                    VStack(alignment: .leading, spacing: 1) {
                        Text(title)
                            .font(.system(size: 13, weight: .medium))
                        Text(summary)
                            .font(.system(size: 11))
                            .foregroundStyle(.secondary)
                            .lineLimit(2)
                    }
                    Spacer(minLength: 0)
                }
                // Per-agent tool list — only visible for the focused agent.
                // Indented under the checkmark column so the hierarchy is
                // obvious without needing a border or background.
                if let tools, !tools.isEmpty {
                    VStack(alignment: .leading, spacing: 2) {
                        ForEach(tools, id: \.self) { name in
                            Text(name)
                                .font(.system(size: 11, design: .monospaced))
                                .foregroundStyle(.secondary)
                        }
                    }
                    .padding(.leading, 24)  // align under the title column
                    .padding(.top, 2)
                }
            }
            .padding(.vertical, 4)
            .padding(.horizontal, 6)
            .background(
                RoundedRectangle(cornerRadius: 6)
                    .fill(isSelected
                          ? Color.accentColor.opacity(0.10)
                          : (hovering ? Color.secondary.opacity(0.06) : Color.clear))
            )
            .contentShape(Rectangle())
            .onHover { hovering = $0 }
            .onTapGesture(perform: onTap)
        }
    }

    private var groupedByDate: [String: [ResearchThread]] {
        var result: [String: [ResearchThread]] = [:]
        let calendar = Calendar.current

        for thread in threads {
            let formatter = ISO8601DateFormatter()
            formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            let date = formatter.date(from: thread.lastUpdated)
                ?? ISO8601DateFormatter().date(from: thread.lastUpdated)
                ?? Date()

            let section: String
            if calendar.isDateInToday(date) {
                section = "Today"
            } else if calendar.isDateInYesterday(date) {
                section = "Yesterday"
            } else {
                let df = DateFormatter()
                df.dateStyle = .medium
                section = df.string(from: date)
            }

            result[section, default: []].append(thread)
        }
        return result
    }
}
