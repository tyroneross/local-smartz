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
    let onSelectSavedQuery: (Project, SavedQuery) -> Void
    let onDeleteProject: (Project) -> Void

    @EnvironmentObject var projectIndex: ProjectIndex
    /// Per-project lazy cache of queries.json contents. Loaded once when a
    /// DisclosureGroup is first expanded; not live-reloaded as new queries
    /// are appended to the current project during this session.
    @State private var projectQueriesCache: [String: [SavedQuery]] = [:]
    @State private var expandedProjects: Set<String> = []

    var body: some View {
        List(selection: $selectedThread) {
            Section {
                Button(action: onNewThread) {
                    Label("New Research", systemImage: "plus.circle")
                        .frame(minHeight: 24)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .foregroundStyle(Color.accentColor)
            }

            Section("Projects") {
                if projectIndex.projects.isEmpty {
                    Text("No projects yet")
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                } else {
                    ForEach(projectIndex.projects) { project in
                        projectRow(project)
                    }
                }
            }

            if !agents.isEmpty {
                Section("Agents") {
                    AgentRow(
                        title: "All agents",
                        summary: "Default multi-step flow",
                        tools: nil,
                        modelName: nil,
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
                            modelName: agent.model,
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
        let modelName: String?
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
                        if let model = modelName, !model.isEmpty {
                            Text(model)
                                .font(.system(size: 11, design: .monospaced))
                                .foregroundStyle(.secondary)
                                .padding(.leading, 24)
                                .padding(.top, 1)
                        }
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

    // MARK: - Project rows

    @ViewBuilder
    private func projectRow(_ project: Project) -> some View {
        let binding = Binding<Bool>(
            get: { expandedProjects.contains(project.path) },
            set: { newValue in
                if newValue {
                    expandedProjects.insert(project.path)
                    if projectQueriesCache[project.path] == nil {
                        projectQueriesCache[project.path] = Self.loadQueries(from: project.path)
                    }
                } else {
                    expandedProjects.remove(project.path)
                }
            }
        )

        DisclosureGroup(isExpanded: binding) {
            let queries = projectQueriesCache[project.path] ?? []
            if queries.isEmpty {
                Text("No queries yet")
                    .font(.caption)
                    .foregroundStyle(.tertiary)
                    .padding(.leading, 4)
            } else {
                ForEach(queries.reversed()) { saved in
                    Button {
                        onSelectSavedQuery(project, saved)
                    } label: {
                        VStack(alignment: .leading, spacing: 1) {
                            Text(Self.truncate(saved.query, max: 80))
                                .font(.system(size: 12))
                                .lineLimit(1)
                                .foregroundStyle(.primary)
                            Text(saved.timestamp, style: .relative)
                                .font(.system(size: 10))
                                .foregroundStyle(.secondary)
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .padding(.vertical, 1)
                }
            }
        } label: {
            VStack(alignment: .leading, spacing: 1) {
                Text(project.name)
                    .font(.system(size: 13, weight: .medium))
                    .lineLimit(1)
                Text(project.createdAt, style: .relative)
                    .font(.system(size: 10))
                    .foregroundStyle(.secondary)
            }
            .contextMenu {
                Button("Open in Finder") {
                    NSWorkspace.shared.activateFileViewerSelecting(
                        [URL(fileURLWithPath: project.path)]
                    )
                }
                Divider()
                Button("Delete project", role: .destructive) {
                    onDeleteProject(project)
                }
            }
        }
    }

    private static func truncate(_ s: String, max: Int) -> String {
        if s.count <= max { return s }
        return String(s.prefix(max)) + "\u{2026}"
    }

    private static func loadQueries(from path: String) -> [SavedQuery] {
        let file = URL(fileURLWithPath: path).appendingPathComponent("queries.json")
        guard let data = try? Data(contentsOf: file) else { return [] }
        // queries.json uses ISO8601 timestamps written by ResearchView; the
        // top-level key is "queries".
        struct Wrapper: Decodable { let queries: [SavedQuery] }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        if let wrapped = try? decoder.decode(Wrapper.self, from: data) {
            return wrapped.queries
        }
        return []
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
