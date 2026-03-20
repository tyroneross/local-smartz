import SwiftUI

struct ResearchView: View {
    @EnvironmentObject var appState: AppState
    @StateObject private var backend = BackendManager()

    @State private var prompt = ""
    @State private var outputText = ""
    @State private var toolCalls: [ToolCallEntry] = []
    @State private var isStreaming = false
    @State private var threads: [ResearchThread] = []
    @State private var selectedThread: String?
    @State private var currentTitle = ""
    @State private var durationMs: Int?
    @State private var errorMessage: String?

    private let sseClient = SSEClient()

    var body: some View {
        NavigationSplitView {
            ThreadListView(
                threads: threads,
                selectedThread: $selectedThread,
                onNewThread: newThread
            )
            .frame(minWidth: 180)
            .navigationSplitViewColumnWidth(min: 180, ideal: 220, max: 300)
        } detail: {
            VStack(spacing: 0) {
                // L2: Toolbar
                toolbar

                Divider()

                // L3: Content
                if outputText.isEmpty && toolCalls.isEmpty && !isStreaming {
                    emptyState
                } else {
                    OutputView(
                        outputText: outputText,
                        toolCalls: toolCalls,
                        isStreaming: isStreaming
                    )
                }

                // Error
                if let error = errorMessage {
                    HStack {
                        Image(systemName: "exclamationmark.triangle")
                            .foregroundStyle(.red)
                        Text(error)
                            .font(.caption)
                            .foregroundStyle(.red)
                        Spacer()
                        Button("Dismiss") { errorMessage = nil }
                            .buttonStyle(.plain)
                            .font(.caption)
                    }
                    .padding(.horizontal)
                    .padding(.vertical, 6)
                    .background(.red.opacity(0.05))
                }

                Divider()

                // Input bar
                inputBar
            }
        }
        .task {
            appState.ollamaStatus = .loading
            await backend.start()
            if backend.isRunning {
                await refreshStatus()
                await fetchThreads()
                while !Task.isCancelled {
                    try? await Task.sleep(for: .seconds(30))
                    await refreshStatus()
                }
            } else {
                appState.ollamaStatus = .offline
                errorMessage = backend.errorMessage
            }
        }
    }

    // MARK: - Toolbar

    private var toolbar: some View {
        HStack {
            if !currentTitle.isEmpty {
                Text(currentTitle)
                    .font(.headline)
                    .lineLimit(1)
            } else {
                Text("Local Smartz")
                    .font(.headline)
            }

            Spacer()

            // Profile badge
            Text(appState.profile)
                .font(.caption)
                .padding(.horizontal, 8)
                .padding(.vertical, 2)
                .background(.secondary.opacity(0.1), in: Capsule())

            // Ollama status
            HStack(spacing: 4) {
                Circle()
                    .fill(ollamaColor)
                    .frame(width: 8, height: 8)
                Text(ollamaLabel)
                    .font(.caption)
                    .foregroundStyle(ollamaColor)
            }

            // Duration
            if let ms = durationMs {
                Text(formatDuration(ms))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(.horizontal)
        .padding(.vertical, 8)
    }

    // MARK: - Empty state

    private var emptyState: some View {
        VStack(spacing: 12) {
            Spacer()
            Image(systemName: "text.magnifyingglass")
                .font(.system(size: 32))
                .foregroundStyle(.secondary)
            Text(emptyStateMessage)
                .font(.subheadline)
                .foregroundStyle(.secondary)
            Spacer()
        }
        .frame(maxWidth: .infinity)
    }

    // MARK: - Input bar

    private var inputBar: some View {
        HStack(spacing: 8) {
            TextField("Ask a research question...", text: $prompt)
                .textFieldStyle(.plain)
                .font(.body)
                .onSubmit { runResearch() }

            Button(action: runResearch) {
                Image(systemName: "arrow.up.circle.fill")
                    .font(.title2)
            }
            .buttonStyle(.borderless)
            .disabled(!canRun)
            .opacity(canRun ? 1.0 : 0.3)
            .keyboardShortcut(.return, modifiers: .command)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
    }

    // MARK: - Logic

    private var canRun: Bool {
        !prompt.trimmingCharacters(in: .whitespaces).isEmpty
            && !isStreaming
            && backend.isRunning
            && appState.ollamaStatus == .ready
    }

    private var ollamaColor: Color {
        switch appState.ollamaStatus {
        case .ready: return .green
        case .offline: return .red
        case .loading: return .orange
        case .needsSetup: return .orange
        case .unknown: return .secondary
        }
    }

    private var ollamaLabel: String {
        switch appState.ollamaStatus {
        case .ready: return "Ready"
        case .offline: return "Offline"
        case .loading: return "Loading"
        case .needsSetup: return "Setup required"
        case .unknown: return "..."
        }
    }

    private var emptyStateMessage: String {
        switch appState.ollamaStatus {
        case .ready:
            return "Ask a research question to get started"
        case .offline:
            return "Start Ollama to begin researching"
        case .loading:
            return "Checking backend and model readiness..."
        case .needsSetup:
            return "Download the required model before researching"
        case .unknown:
            return "Preparing Local Smartz..."
        }
    }

    private func newThread() {
        selectedThread = nil
        outputText = ""
        toolCalls = []
        currentTitle = ""
        durationMs = nil
        errorMessage = nil
        prompt = ""
    }

    private func runResearch() {
        guard canRun else { return }

        let query = prompt.trimmingCharacters(in: .whitespaces)
        prompt = ""
        currentTitle = query
        outputText = ""
        toolCalls = []
        durationMs = nil
        errorMessage = nil
        isStreaming = true
        appState.isResearching = true

        guard let url = URL(string: "\(backend.baseURL)/api/research") else {
            isStreaming = false
            appState.isResearching = false
            errorMessage = "Could not build the research request."
            return
        }

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")

        var payload: [String: String] = ["prompt": query]
        if let threadId = selectedThread {
            payload["thread_id"] = threadId
        }

        do {
            request.httpBody = try JSONSerialization.data(withJSONObject: payload)
        } catch {
            isStreaming = false
            appState.isResearching = false
            errorMessage = "Could not encode the research request."
            return
        }

        Task {
            do {
                for try await event in await sseClient.stream(request: request) {
                    handleEvent(event)
                }
            } catch {
                if !Task.isCancelled {
                    errorMessage = error.localizedDescription
                }
            }
            isStreaming = false
            appState.isResearching = false
            await refreshStatus()
            await fetchThreads()
        }
    }

    private func handleEvent(_ event: SSEEvent) {
        switch event {
        case .text(let content):
            outputText += content
        case .tool(let name):
            toolCalls.append(ToolCallEntry(name: name, message: "", isError: false))
        case .toolError(let name, let message):
            toolCalls.append(ToolCallEntry(name: name, message: message, isError: true))
        case .done(let ms):
            durationMs = ms
        case .error(let message):
            errorMessage = message
        }
    }

    private func fetchThreads() async {
        guard backend.isRunning,
              let url = URL(string: "\(backend.baseURL)/api/threads") else { return }

        do {
            let (data, _) = try await URLSession.shared.data(from: url)
            let decoded = try JSONDecoder().decode([ResearchThread].self, from: data)
            threads = decoded
        } catch {
            // Thread fetch is best-effort
        }
    }

    private func refreshStatus() async {
        guard backend.isRunning,
              let url = URL(string: "\(backend.baseURL)/api/status") else {
            appState.ollamaStatus = .offline
            return
        }

        do {
            let (data, response) = try await URLSession.shared.data(from: url)
            guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
                appState.ollamaStatus = .loading
                return
            }

            let status = try JSONDecoder().decode(BackendStatusResponse.self, from: data)
            appState.profile = status.profile

            if !status.ollama.running {
                appState.ollamaStatus = .offline
            } else if status.ready {
                appState.ollamaStatus = .ready
            } else if !status.missingModels.isEmpty {
                appState.ollamaStatus = .needsSetup
            } else {
                appState.ollamaStatus = .loading
            }
        } catch {
            appState.ollamaStatus = .loading
        }
    }

    private func formatDuration(_ ms: Int) -> String {
        if ms < 1000 { return "\(ms)ms" }
        let seconds = Double(ms) / 1000.0
        if seconds < 60 { return String(format: "%.1fs", seconds) }
        let minutes = Int(seconds) / 60
        let secs = Int(seconds) % 60
        return "\(minutes)m \(secs)s"
    }
}

private struct BackendStatusResponse: Decodable {
    let profile: String
    let ready: Bool
    let missingModels: [String]
    let ollama: OllamaState

    struct OllamaState: Decodable {
        let running: Bool
    }

    enum CodingKeys: String, CodingKey {
        case profile
        case ready
        case missingModels = "missing_models"
        case ollama
    }
}
