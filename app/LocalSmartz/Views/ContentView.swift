import SwiftUI

struct ContentView: View {
    @EnvironmentObject var appState: AppState

    var body: some View {
        Group {
            if appState.isConfigured {
                ResearchView()
            } else {
                SetupView()
            }
        }
    }
}
