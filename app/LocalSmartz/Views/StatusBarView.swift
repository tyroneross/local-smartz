import SwiftUI

struct StatusBarView: View {
    @EnvironmentObject var appState: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("Local Smartz")
                    .font(.headline)
                Spacer()
                statusIndicator
            }

            Divider()

            HStack {
                Text("Profile")
                    .foregroundStyle(.secondary)
                Spacer()
                Text(appState.profile)
            }
            .font(.subheadline)

            HStack {
                Text("Status")
                    .foregroundStyle(.secondary)
                Spacer()
                Text(appState.isResearching ? "Researching" : "Idle")
            }
            .font(.subheadline)

            Divider()

            Button("Open Window") {
                NSApplication.shared.activate(ignoringOtherApps: true)
                if let window = NSApplication.shared.windows.first(where: { $0.canBecomeMain }) {
                    window.makeKeyAndOrderFront(nil)
                }
            }

            Button("Quit") {
                NSApplication.shared.terminate(nil)
            }
        }
        .padding(12)
        .frame(width: 200)
    }

    @ViewBuilder
    private var statusIndicator: some View {
        switch appState.ollamaStatus {
        case .ready:
            Text("Ready")
                .font(.caption)
                .foregroundStyle(.green)
        case .offline:
            Text("Offline")
                .font(.caption)
                .foregroundStyle(.red)
        case .loading:
            Text("Loading")
                .font(.caption)
                .foregroundStyle(.orange)
        case .unknown:
            Text("...")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }
}
