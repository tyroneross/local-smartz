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
    @State private var availableModels: [String] = []
    @State private var currentModel: String = ""
    @State private var isSwitchingModel = false
    @State private var researchTask: Task<Void, Never>?
    @State private var agents: [AgentInfo] = []
    @State private var focusAgent: String?
    @State private var showInstallSheet = false

    private let sseClient = SSEClient()

    var body: some View {
        NavigationSplitView {
            ThreadListView(
                threads: threads,
                selectedThread: $selectedThread,
                onNewThread: newThread,
                agents: agents,
                focusAgent: $focusAgent
            )
            .frame(minWidth: 220)
            .navigationSplitViewColumnWidth(min: 220, ideal: 260, max: 320)
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
            // Loading overlay — blocks input until the planning model is
            // resident in Ollama VRAM. Without this, the first query on
            // a cold model silently waits 10–60s with only "Thinking…".
            .overlay {
                if appState.modelWarmup == .loading {
                    warmupOverlay
                }
            }
            .sheet(isPresented: $showInstallSheet) {
                InstallModelSheet(
                    backendBaseURL: backend.baseURL,
                    onInstalled: { name in
                        Task {
                            await fetchModels()
                            // Optionally auto-switch to the freshly installed model.
                            await switchModel(to: name)
                        }
                    }
                )
            }
        }
        .task {
            appState.ollamaStatus = .loading
            await backend.start()
            if backend.isRunning {
                await refreshStatus()
                await fetchModels()
                await fetchAgents()
                await fetchThreads()
                while !Task.isCancelled {
                    try? await Task.sleep(for: .seconds(30))
                    await refreshStatus()
                }
            } else {
                appState.ollamaStatus = .offline
                errorMessage = backend.errorMessage ?? "Backend failed to start."
            }
        }
    }

    private func fetchAgents() async {
        guard backend.isRunning,
              let url = URL(string: "\(backend.baseURL)/api/agents") else { return }
        do {
            let (data, _) = try await URLSession.shared.data(from: url)
            struct Resp: Decodable { let agents: [AgentInfo] }
            agents = try JSONDecoder().decode(Resp.self, from: data).agents
        } catch {
            agents = []
        }
    }

    // MARK: - Toolbar

    private var toolbar: some View {
        HStack(spacing: 12) {
            // Title (left). Empty if no thread title — system window title bar
            // already says "Local Smartz", no need to repeat it.
            if !currentTitle.isEmpty {
                Text(currentTitle)
                    .font(.system(size: 14, weight: .semibold))
                    .lineLimit(1)
                    .truncationMode(.tail)
            }

            Spacer()

            // Right cluster: model · profile · status · duration
            // All status uses text + color, no individual badges/borders per Calm Precision.
            HStack(spacing: 14) {
                modelPicker

                // Calm Precision Rule 9: status/profile is text only, no badge.
                Text(appState.profile.uppercased())
                    .font(.system(size: 10, weight: .medium))
                    .tracking(0.5)
                    .foregroundStyle(.secondary)

                HStack(spacing: 5) {
                    Circle()
                        .fill(ollamaColor)
                        .frame(width: 7, height: 7)
                    Text(ollamaLabel)
                        .font(.system(size: 11, weight: .medium))
                        .foregroundStyle(ollamaColor)
                }

                if let ms = durationMs {
                    Text(formatDuration(ms))
                        .font(.system(size: 11))
                        .foregroundStyle(.secondary)
                        .monospacedDigit()
                }
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
    }

    private var modelPicker: some View {
        Menu {
            if availableModels.isEmpty {
                Text("No models loaded")
            } else {
                Section("Switch active model") {
                    ForEach(availableModels, id: \.self) { name in
                        Button {
                            Task { await switchModel(to: name) }
                        } label: {
                            HStack {
                                Image(systemName: name == currentModel ? "checkmark.circle.fill" : "circle")
                                Text(name)
                            }
                        }
                        .disabled(isStreaming || isSwitchingModel)
                    }
                }
                Divider()
            }
            Divider()
            Button {
                showInstallSheet = true
            } label: {
                Label("Install new model…", systemImage: "plus.circle")
            }
            Button("Refresh list") {
                Task { await fetchModels() }
            }
        } label: {
            HStack(spacing: 6) {
                Image(systemName: "cpu")
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(.secondary)
                Text("Model:")
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
                Text(modelPickerLabel)
                    .font(.system(size: 11, weight: .semibold))
                    .lineLimit(1)
                Image(systemName: "chevron.down")
                    .font(.system(size: 9, weight: .semibold))
                    .foregroundStyle(.secondary)
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 5)
            .overlay(
                RoundedRectangle(cornerRadius: 6)
                    .stroke(Color.secondary.opacity(0.25), lineWidth: 1)
            )
            .foregroundStyle(.primary)
            .contentShape(Rectangle())
        }
        .menuStyle(.borderlessButton)
        .menuIndicator(.hidden)
        .fixedSize()
        .disabled(isStreaming || isSwitchingModel)
        .help(currentModel.isEmpty ? "Pick a model" : "Current: \(currentModel) — click to switch")
    }

    private var modelPickerLabel: String {
        // Prefer the active model when it's in the loaded list. If the
        // configured model isn't pulled in Ollama, `currentModel` can point
        // to something not in `availableModels` — show "Pick a model" so
        // the toolbar never renders a blank label.
        if !currentModel.isEmpty, availableModels.contains(currentModel) {
            return currentModel
        }
        if !currentModel.isEmpty, availableModels.isEmpty {
            // Haven't loaded the list yet but backend reports a current
            // model. Show it rather than "Loading…" so the label isn't
            // empty once fetchModels() returns with an empty list.
            return currentModel
        }
        if availableModels.isEmpty { return "Loading…" }
        return "Pick a model"
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
            TextField(inputPlaceholder, text: $prompt)
                .textFieldStyle(.plain)
                .font(.body)
                .onSubmit { if canRun { runResearch() } }
                .disabled(false)  // typeable while streaming so user can queue thoughts

            if isStreaming {
                Button(action: cancelResearch) {
                    HStack(spacing: 4) {
                        Image(systemName: "stop.circle.fill")
                            .font(.title3)
                        Text("Stop")
                            .font(.system(size: 12, weight: .medium))
                    }
                    .foregroundStyle(.red)
                }
                .buttonStyle(.borderless)
                .keyboardShortcut(".", modifiers: .command)
                .help("Stop the current research (⌘.)")
            } else {
                Button(action: runResearch) {
                    Image(systemName: "arrow.up.circle.fill")
                        .font(.title2)
                }
                .buttonStyle(.borderless)
                .disabled(!canRun)
                .opacity(canRun ? 1.0 : 0.3)
                .keyboardShortcut(.return, modifiers: .command)
                .help("Send (⌘↩)")
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
    }

    private var inputPlaceholder: String {
        if appState.modelWarmup == .loading {
            return "Loading model — please wait…"
        }
        if isStreaming { return "Type your next question — send when ready…" }
        return "Ask a research question…"
    }

    // MARK: - Logic

    private var canRun: Bool {
        !prompt.trimmingCharacters(in: .whitespaces).isEmpty
            && !isStreaming
            && backend.isRunning
            && appState.ollamaStatus == .ready
            && appState.modelWarmup != .loading
    }

    // MARK: - Warmup overlay

    private var warmupOverlay: some View {
        // Full-view block with centered label. Tap capture via a clear
        // background so clicks can't reach the input beneath.
        ZStack {
            Color.black.opacity(0.35)
                .ignoresSafeArea()
            VStack(spacing: 12) {
                ProgressView()
                    .controlSize(.large)
                VStack(spacing: 4) {
                    Text("Loading model")
                        .font(.system(size: 13, weight: .medium))
                        .foregroundStyle(.primary)
                    Text(appState.warmupModelName.isEmpty ? " " : appState.warmupModelName)
                        .font(.system(size: 12, design: .monospaced))
                        .foregroundStyle(.secondary)
                    Text("Ollama is loading the model into memory. This only happens on first use.")
                        .font(.system(size: 11))
                        .foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)
                        .frame(maxWidth: 320)
                }
            }
            .padding(24)
            .background(
                RoundedRectangle(cornerRadius: 12)
                    .fill(.regularMaterial)
            )
        }
        .allowsHitTesting(true)
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
        if let agent = focusAgent {
            payload["agent"] = agent
        }

        do {
            request.httpBody = try JSONSerialization.data(withJSONObject: payload)
        } catch {
            isStreaming = false
            appState.isResearching = false
            errorMessage = "Could not encode the research request."
            return
        }

        researchTask = Task {
            do {
                for try await event in await sseClient.stream(request: request) {
                    if Task.isCancelled { break }
                    handleEvent(event)
                }
            } catch {
                if !Task.isCancelled {
                    errorMessage = error.localizedDescription
                }
            }
            isStreaming = false
            appState.isResearching = false
            researchTask = nil
            await refreshStatus()
            await fetchThreads()
        }
    }

    private func cancelResearch() {
        researchTask?.cancel()
        researchTask = nil
        isStreaming = false
        appState.isResearching = false
        toolCalls.append(ToolCallEntry(name: "cancelled", message: "Stopped by user", isError: false))
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
            // Stop the spinner immediately on done — don't wait for the SSE
            // stream to close. Some servers keep the connection alive briefly
            // after the final event, which leaves a stale "Thinking…".
            isStreaming = false
            appState.isResearching = false
            researchTask?.cancel()
            researchTask = nil
        case .error(let message):
            errorMessage = message
            isStreaming = false
            appState.isResearching = false
            researchTask?.cancel()
            researchTask = nil
        case .status(let stage, let model, _):
            // Backend mid-stream lifecycle. We don't need to flip the main
            // warmup overlay here — startup polling already handles that —
            // but keep warmupModelName fresh so the overlay copy is right
            // if the next query triggers a reload.
            if let model, !model.isEmpty {
                appState.warmupModelName = model
            }
            if stage == "loading_model" {
                appState.modelWarmup = .loading
            } else if stage == "ready" {
                appState.modelWarmup = .ready
            }
        case .heartbeat:
            // Idle keep-alive — no UI state change. The URLSession idle
            // timer resets on any byte received, which is what we want.
            break
        }
    }

    private func fetchModels() async {
        guard backend.isRunning,
              let url = URL(string: "\(backend.baseURL)/api/models") else { return }
        do {
            let (data, _) = try await URLSession.shared.data(from: url)
            let decoded = try JSONDecoder().decode(ModelsResponse.self, from: data)
            availableModels = decoded.models.map(\.name)
            // Guard against a stale/missing current model. If the backend
            // reports a model that isn't actually installed, fall back to
            // the largest available one and persist the switch so the
            // toolbar never shows a blank label.
            if !decoded.current.isEmpty, availableModels.contains(decoded.current) {
                currentModel = decoded.current
            } else if let fallback = pickFallback(from: decoded.models) {
                currentModel = ""
                await switchModel(to: fallback)
            } else {
                currentModel = ""
            }
            // Kick off warmup for whatever ended up as the active model,
            // so the first query doesn't pay the cold-load cost silently.
            if !currentModel.isEmpty, backend.isRunning {
                ModelWarmup.shared.start(
                    baseURL: backend.baseURL,
                    model: currentModel,
                    appState: appState
                )
            }
        } catch {
            // Non-fatal — picker shows "Loading models…" / "No models loaded"
            availableModels = []
        }
    }

    /// Pick the largest installed model as a safe fallback when the backend's
    /// reported current model isn't actually in `availableModels`.
    private func pickFallback(from models: [ModelsResponse.ModelInfo]) -> String? {
        let sorted = models.sorted { (a, b) in
            (a.sizeGB ?? 0) > (b.sizeGB ?? 0)
        }
        return sorted.first?.name
    }

    private func switchModel(to name: String) async {
        guard !name.isEmpty, name != currentModel, backend.isRunning,
              let url = URL(string: "\(backend.baseURL)/api/models/select") else { return }
        isSwitchingModel = true
        defer { isSwitchingModel = false }

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try? JSONSerialization.data(withJSONObject: ["model": name])

        do {
            let (_, response) = try await URLSession.shared.data(for: request)
            if let http = response as? HTTPURLResponse, http.statusCode == 200 {
                currentModel = name
                // New active model — warm it so the next query is fast.
                ModelWarmup.shared.start(
                    baseURL: backend.baseURL,
                    model: name,
                    appState: appState
                )
                await refreshStatus()
            } else {
                errorMessage = "Could not switch to \(name)."
            }
        } catch {
            errorMessage = "Model switch failed: \(error.localizedDescription)"
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

private struct ModelsResponse: Decodable {
    let models: [ModelInfo]
    let current: String
    let profile: String?

    struct ModelInfo: Decodable {
        let name: String
        let sizeGB: Double?

        enum CodingKeys: String, CodingKey {
            case name
            case sizeGB = "size_gb"
        }
    }
}
