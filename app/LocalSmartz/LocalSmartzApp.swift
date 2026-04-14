import SwiftUI

@main
struct LocalSmartzApp: App {
    @StateObject private var appState = AppState()
    @StateObject private var projectIndex = ProjectIndex()

    var body: some Scene {
        WindowGroup {
            Group {
                if appState.isConfigured {
                    MainView()
                } else {
                    SetupView()
                }
            }
            .environmentObject(appState)
            .environmentObject(projectIndex)
            .frame(minWidth: 640, minHeight: 480)
        }
        .windowResizability(.contentMinSize)
        .defaultSize(width: 800, height: 600)
        .commands {
            CommandGroup(replacing: .newItem) {}
        }

        MenuBarExtra("Local Smartz", systemImage: "magnifyingglass.circle.fill") {
            StatusBarView()
                .environmentObject(appState)
        }

        Settings {
            SettingsView()
                .environmentObject(appState)
        }
    }
}
