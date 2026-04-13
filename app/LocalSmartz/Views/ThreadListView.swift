import SwiftUI

struct AgentInfo: Decodable, Identifiable {
    let name: String
    let title: String
    let summary: String
    var id: String { name }
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
                        isSelected: focusAgent == nil
                    ) {
                        focusAgent = nil
                    }

                    ForEach(agents) { agent in
                        AgentRow(
                            title: agent.title,
                            summary: agent.summary,
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
    private struct AgentRow: View {
        let title: String
        let summary: String
        let isSelected: Bool
        let onTap: () -> Void

        @State private var hovering = false

        var body: some View {
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
