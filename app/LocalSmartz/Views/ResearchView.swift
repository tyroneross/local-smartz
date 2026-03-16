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
            await backend.start()
            if backend.isRunning {
                appState.ollamaStatus = .ready
                await fetchThreads()
            } else {
                appState.ollamaStatus = .offline
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
            Text("Ask a research question to get started")
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
    }

    private var ollamaColor: Color {
        switch appState.ollamaStatus {
        case .ready: return .green
        case .offline: return .red
        case .loading: return .orange
        case .unknown: return .secondary
        }
    }

    private var ollamaLabel: String {
        switch appState.ollamaStatus {
        case .ready: return "Ready"
        case .offline: return "Offline"
        case .loading: return "Loading"
        case .unknown: return "..."
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

        var components = URLComponents(string: "\(backend.baseURL)/api/research")!
        components.queryItems = [URLQueryItem(name: "prompt", value: query)]
        if let threadId = selectedThread {
            components.queryItems?.append(URLQueryItem(name: "thread_id", value: threadId))
        }

        guard let url = components.url else { return }

        Task {
            do {
                for try await event in await sseClient.stream(url: url) {
                    handleEvent(event)
                }
            } catch {
                if !Task.isCancelled {
                    errorMessage = error.localizedDescription
                }
            }
            isStreaming = false
            appState.isResearching = false
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

    private func formatDuration(_ ms: Int) -> String {
        if ms < 1000 { return "\(ms)ms" }
        let seconds = Double(ms) / 1000.0
        if seconds < 60 { return String(format: "%.1fs", seconds) }
        let minutes = Int(seconds) / 60
        let secs = Int(seconds) % 60
        return "\(minutes)m \(secs)s"
    }
}
