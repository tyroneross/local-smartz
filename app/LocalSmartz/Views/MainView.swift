import SwiftUI

/// Top-level container that switches between Research and Author modes.
struct MainView: View {
    @EnvironmentObject var appState: AppState

    var body: some View {
        VStack(spacing: 0) {
            modeBar
            Divider()
            Group {
                switch appState.mode {
                case .research:
                    ResearchView()
                case .author:
                    AuthorView()
                }
            }
        }
    }

    private var modeBar: some View {
        // Calm Precision Rule 9 + 30: nav states use text color + weight +
        // 2px bottom border when selected — never a background pill.
        HStack(spacing: 20) {
            ForEach(AppMode.allCases) { mode in
                Button {
                    appState.mode = mode
                } label: {
                    HStack(spacing: 6) {
                        Image(systemName: mode.systemImage)
                            .font(.system(size: 11, weight: appState.mode == mode ? .semibold : .regular))
                        Text(mode.label)
                            .font(.system(size: 12, weight: appState.mode == mode ? .semibold : .regular))
                    }
                    .foregroundStyle(appState.mode == mode ? Color.primary : .secondary)
                    .padding(.vertical, 6)
                    .overlay(alignment: .bottom) {
                        Rectangle()
                            .fill(appState.mode == mode ? Color.accentColor : .clear)
                            .frame(height: 2)
                    }
                }
                .buttonStyle(.plain)
                .keyboardShortcut(mode == .research ? "1" : "2", modifiers: .command)
            }
            Spacer()
        }
        .padding(.horizontal, 16)
        .padding(.top, 8)
    }
}
