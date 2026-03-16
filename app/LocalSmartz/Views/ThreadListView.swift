import SwiftUI

struct ThreadListView: View {
    let threads: [ResearchThread]
    @Binding var selectedThread: String?
    let onNewThread: () -> Void

    var body: some View {
        List(selection: $selectedThread) {
            Section {
                Button(action: onNewThread) {
                    Label("New Research", systemImage: "plus.circle")
                }
                .buttonStyle(.plain)
                .foregroundStyle(Color.accentColor)
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
