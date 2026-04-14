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
    /// Uninstalled catalog models surfaced directly in the toolbar Model menu.
    /// Fetched from `/api/models/catalog` alongside `availableModels` so the
    /// user can install from one click without opening the sheet first.
    @State private var installableCatalog: [InstallCatalogModel] = []
    /// Detected system RAM (GB) — used for the fit chip next to each
    /// uninstalled catalog entry. 0 means "unknown → hide the chip".
    @State private var detectedRamGB: Int = 0
    /// Set of model names currently mid-pull so we can disable the tap and
    /// show an inline spinner text without re-opening InstallModelSheet.
    @State private var pullingModels: Set<String> = []
    /// Estimated on-disk size (GB) keyed by model name. Populated from
    /// both `/api/models` (installed, authoritative size) and
    /// `/api/models/catalog` (pre-install estimate). Used for the
    /// pre-flight RAM-fit check in ``runResearch``.
    @State private var modelSizeGB: [String: Double] = [:]
    /// Pending RAM-warning confirmation state. When non-nil a modal is
    /// shown; the user can continue anyway or cancel and pick a
    /// smaller model.
    @State private var pendingLargeModelQuery: String?
    @State private var pendingLargeModelSize: Double = 0
    /// Most-recent pipeline phase label rendered by the slim
    /// ``StatusBanner`` under the toolbar. Sourced from SSE
    /// `stage`, `status(loading_model)`, and `tool` events;
    /// cleared on `.done`/`.error`. Additive to the existing
    /// ToolCallEntry breadcrumbs — OutputView is unchanged.
    @State private var currentPhase: String? = nil

    // MARK: - Project folder (New Research flow)
    /// Set when the user names a project via the "New Research" sheet.
    /// Sent as `cwd` in every /api/research POST so the backend writes
    /// threads/artifacts/reports inside the project folder.
    @State private var projectDir: URL? = nil
    @State private var showNewProjectSheet = false
    @State private var newProjectName: String = ""
    @State private var existingProjectURL: URL? = nil
    @State private var showExistingProjectAlert = false
    @State private var lastQuery: String = ""

    private let sseClient = SSEClient()

    var body: some View {
        NavigationSplitView {
            ThreadListView(
                threads: threads,
                selectedThread: $selectedThread,
                onNewThread: {
                    newProjectName = ""
                    showNewProjectSheet = true
                },
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

                // Non-blocking phase indicator. Collapses to zero-height
                // when nil so the VStack spacing is unchanged while idle.
                StatusBanner(
                    phase: currentPhase,
                    model: currentModel,
                    isStreaming: isStreaming
                )

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
            //
            // Gated on !isStreaming so mid-run specialist model swaps
            // (SSE status stage="loading_model") don't drop a full-screen
            // cover over OutputView and obliterate in-flight content.
            // Mid-stream loads surface inline via the loading_model
            // ToolCallEntry breadcrumb below.
            .overlay {
                if appState.modelWarmup == .loading && !isStreaming {
                    warmupOverlay
                }
            }
            // Pre-flight RAM-fit confirmation. Fires when the current
            // planning model's estimated size exceeds detected RAM and
            // the user hasn't disabled the warning in Settings. Keeps
            // users from silently swap-thrashing a 23 GB model on 16 GB.
            .confirmationDialog(
                ramWarningTitle,
                isPresented: Binding(
                    get: { pendingLargeModelQuery != nil },
                    set: { if !$0 { pendingLargeModelQuery = nil } }
                ),
                titleVisibility: .visible
            ) {
                Button("Continue anyway") {
                    if let q = pendingLargeModelQuery {
                        pendingLargeModelQuery = nil
                        runResearch(query: q, bypassRAMCheck: true)
                    }
                }
                Button("Switch model", role: .cancel) {
                    pendingLargeModelQuery = nil
                    // Leaves the prompt intact (restored below) so the
                    // user can pick a smaller model from the toolbar
                    // and re-submit without retyping.
                }
            } message: {
                Text(ramWarningMessage)
            }
            .sheet(isPresented: $showInstallSheet) {
                InstallModelSheet(
                    backendBaseURL: backend.baseURL,
                    onInstalled: { name in
                        Task {
                            await fetchModels()
                            await fetchCatalog()
                            // Optionally auto-switch to the freshly installed model.
                            await switchModel(to: name)
                        }
                    }
                )
            }
            .sheet(isPresented: $showNewProjectSheet) {
                newProjectSheet
            }
            .alert(
                "A folder named \u{0022}\(existingProjectURL?.lastPathComponent ?? "")\u{0022} already exists. Open it?",
                isPresented: $showExistingProjectAlert,
                presenting: existingProjectURL
            ) { url in
                Button("Open") {
                    projectDir = url
                    newThread()
                    showNewProjectSheet = false
                    existingProjectURL = nil
                }
                Button("Choose another", role: .cancel) {
                    existingProjectURL = nil
                    // Keep the sheet open so the user can rename.
                }
            } message: { _ in
                Text("Existing queries.json and artifacts will be reused.")
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
                await fetchCatalog()
                await fetchRam()
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

    // MARK: - Catalog menu helpers

    /// Render one uninstalled catalog row as plain text for a native Menu.
    /// Format:  "• gemma3:27b   16.3 GB  ✓ Fits"
    /// The prefix chip makes RAM fit visible without relying on SwiftUI
    /// custom-content support (macOS Menu strips most rich content).
    private func menuRowLabel(for model: InstallCatalogModel) -> String {
        let size = String(format: "%.1f GB", model.sizeGBEstimate)
        let fit: String
        if detectedRamGB > 0 {
            let ratio = model.sizeGBEstimate / Double(detectedRamGB)
            if ratio <= 0.5 { fit = "  ✓ Fits" }
            else if ratio <= 1.0 { fit = "  ⚠ Tight" }
            else { fit = "  ✗ Too large" }
        } else {
            fit = ""
        }
        let busy = pullingModels.contains(model.name) ? "  (installing…)" : ""
        return "\(model.name)   \(size)\(fit)\(busy)"
    }

    /// Fetch uninstalled rows from `/api/models/catalog`. Installed models
    /// are filtered out — they already appear in the "Active model" section.
    /// Also populates ``modelSizeGB`` with the catalog's size estimate so
    /// the pre-flight RAM check can still fire on freshly-pulled models
    /// before `/api/models` reports their authoritative on-disk size.
    private func fetchCatalog() async {
        guard backend.isRunning,
              let url = URL(string: "\(backend.baseURL)/api/models/catalog") else { return }
        struct Resp: Decodable { let catalog: [InstallCatalogModel] }
        do {
            let (data, _) = try await URLSession.shared.data(from: url)
            let decoded = try JSONDecoder().decode(Resp.self, from: data)
            installableCatalog = decoded.catalog.filter { !$0.installed }
            // Record estimated sizes for every catalog entry so the RAM
            // check has a number even before a model is installed.
            for entry in decoded.catalog where entry.sizeGBEstimate > 0 {
                // Prefer authoritative installed size (set in fetchModels)
                // over the catalog estimate.
                if modelSizeGB[entry.name] == nil {
                    modelSizeGB[entry.name] = entry.sizeGBEstimate
                }
            }
        } catch {
            installableCatalog = []
        }
    }

    /// Read detected RAM via /api/status so the menu fit chip matches
    /// what ModelsTab and InstallModelSheet show.
    private func fetchRam() async {
        struct StatusResp: Decodable {
            let ramGB: Int?
            enum CodingKeys: String, CodingKey { case ramGB = "ram_gb" }
        }
        guard backend.isRunning,
              let url = URL(string: "\(backend.baseURL)/api/status") else { return }
        do {
            let (data, _) = try await URLSession.shared.data(from: url)
            let decoded = try JSONDecoder().decode(StatusResp.self, from: data)
            if let gb = decoded.ramGB, gb > 0 {
                detectedRamGB = gb
            }
        } catch {
            // non-fatal — chips just hide
        }
    }

    /// Kick off a pull from the menu. Streams progress into
    /// `pullingModels` so the row shows "(installing…)" until the server
    /// emits `done`, then refreshes the installed + catalog lists and
    /// auto-switches to the new model so the user can use it immediately.
    private func installFromMenu(_ model: InstallCatalogModel) async {
        guard backend.isRunning,
              let url = URL(string: "\(backend.baseURL)/api/models/pull") else { return }
        pullingModels.insert(model.name)
        defer { pullingModels.remove(model.name) }

        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try? JSONSerialization.data(withJSONObject: ["model": model.name])

        do {
            let (stream, _) = try await URLSession.shared.bytes(for: req)
            var succeeded = false
            for try await line in stream.lines {
                guard line.hasPrefix("data: ") else { continue }
                let payload = String(line.dropFirst(6))
                if let data = payload.data(using: .utf8),
                   let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                   let kind = obj["type"] as? String {
                    if kind == "done" {
                        succeeded = true
                    } else if kind == "error", let msg = obj["message"] as? String {
                        errorMessage = "Install \(model.name) failed: \(msg)"
                    }
                }
            }
            if succeeded {
                await fetchModels()
                await fetchCatalog()
                await switchModel(to: model.name)
            }
        } catch {
            errorMessage = "Install \(model.name) failed: \(error.localizedDescription)"
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
                Section("Active model") {
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
            }
            if !installableCatalog.isEmpty {
                Section("Available to install") {
                    ForEach(installableCatalog) { model in
                        // Native macOS Menu items render plain text from
                        // Button labels (Image/HStack work but are clipped).
                        // Encode the fit chip and size into the label string
                        // itself so every row is one tappable unit.
                        Button {
                            Task { await installFromMenu(model) }
                        } label: {
                            Text(menuRowLabel(for: model))
                        }
                        .disabled(pullingModels.contains(model.name)
                                  || isStreaming
                                  || isSwitchingModel)
                    }
                }
            }
            Divider()
            Button {
                showInstallSheet = true
            } label: {
                Label("Install by name…", systemImage: "plus.circle")
            }
            Button("Refresh list") {
                Task {
                    await fetchModels()
                    await fetchCatalog()
                }
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
        runResearch(query: query, bypassRAMCheck: false)
    }

    /// Core research dispatch. The public zero-arg ``runResearch()`` calls
    /// this with ``bypassRAMCheck: false`` so a too-large-model dialog can
    /// gate the request; the "Continue anyway" branch of that dialog calls
    /// back in with ``bypassRAMCheck: true`` and the same query.
    private func runResearch(query: String, bypassRAMCheck: Bool) {
        if !bypassRAMCheck, let warning = ramWarningForCurrentModel() {
            // Stash state and surface the dialog. Preserve the prompt in
            // the text field so the user can edit/retry after switching
            // models — only clear once they actually commit.
            pendingLargeModelQuery = query
            pendingLargeModelSize = warning
            return
        }

        prompt = ""
        currentTitle = query
        lastQuery = query
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
        if let dir = projectDir {
            payload["cwd"] = dir.path
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

    // MARK: - RAM fit pre-flight

    /// Return the estimated model size (GB) if the current planning model
    /// exceeds detected RAM and the user has the warning toggle on. Nil
    /// means "proceed without confirmation".
    ///
    /// We treat an unknown size or unknown RAM as "proceed" — a warning
    /// dialog with no numbers is worse than silence.
    private func ramWarningForCurrentModel() -> Double? {
        let settings = GlobalSettings.load()
        guard settings.warnBeforeLargeModels else { return nil }
        guard !currentModel.isEmpty else { return nil }
        guard detectedRamGB > 0 else { return nil }
        guard let size = modelSizeGB[currentModel], size > 0 else { return nil }
        // Trigger when estimated model size exceeds detected RAM.
        // Half-full (ratio <= 1.0) is fine — Ollama can still load.
        return size > Double(detectedRamGB) ? size : nil
    }

    private var ramWarningTitle: String {
        if currentModel.isEmpty { return "Model may exceed available RAM" }
        return "\(currentModel) may exceed available RAM"
    }

    private var ramWarningMessage: String {
        let sizeStr = String(format: "%.1f GB", pendingLargeModelSize)
        let ramStr = "\(detectedRamGB) GB"
        return "This model is about \(sizeStr); your machine reports \(ramStr) of RAM. Ollama can still load it but may swap heavily, making responses slow. Continue anyway?"
    }

    private func handleEvent(_ event: SSEEvent) {
        switch event {
        case .text(let content):
            outputText += content
        case .tool(let name):
            toolCalls.append(ToolCallEntry(name: name, message: "", isError: false))
            // If no phase has been set yet (no stage event has arrived),
            // derive a provisional phase from the tool name so the banner
            // isn't empty during the prelude.
            if currentPhase == nil {
                currentPhase = phaseLabel(forTool: name)
            }
        case .toolError(let name, let message):
            toolCalls.append(ToolCallEntry(name: name, message: message, isError: true))
        case .done(let ms):
            durationMs = ms
            // Stop the spinner immediately on done — don't wait for the SSE
            // stream to close. Some servers keep the connection alive briefly
            // after the final event, which leaves a stale "Thinking…".
            isStreaming = false
            appState.isResearching = false
            currentPhase = nil
            researchTask?.cancel()
            researchTask = nil
            if let dir = projectDir {
                appendQueryRecord(dir: dir, query: lastQuery, answer: outputText)
            }
        case .error(let message):
            errorMessage = message
            isStreaming = false
            appState.isResearching = false
            currentPhase = nil
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
                let modelLabel = (model?.isEmpty == false) ? model! : "model"
                // Surface the swap in the StatusBanner so the user sees why
                // the stream paused without having to watch the tool list.
                currentPhase = "⏳ Loading \(modelLabel)"
                // Mid-stream the overlay is suppressed (would hide OutputView);
                // drop a breadcrumb into the tool list so the user can see
                // that the specialist swap is what the pause is about.
                if isStreaming {
                    toolCalls.append(
                        ToolCallEntry(
                            name: "loading model: \(modelLabel)",
                            message: "",
                            isError: false
                        )
                    )
                }
            } else if stage == "ready" {
                appState.modelWarmup = .ready
                // Intentionally do NOT clear currentPhase here — keep the
                // last non-loading phase visible so the banner doesn't
                // flicker to empty between specialist swaps.
            }
        case .heartbeat:
            // Idle keep-alive — no UI state change. The URLSession idle
            // timer resets on any byte received, which is what we want.
            break
        case .stage(let name):
            // Orchestrator pipeline transition. Surface it as a tool-call
            // entry so the existing OutputView tool-list UI renders the
            // pipeline breadcrumb without a new surface. Full breadcrumb
            // UI (Orchestrator → Researcher → …) is a follow-up.
            toolCalls.append(
                ToolCallEntry(
                    name: "stage: \(name)",
                    message: "",
                    isError: false
                )
            )
            currentPhase = phaseLabel(forStage: name)
        }
    }

    /// Map an orchestrator stage name to a short, human-readable phase
    /// label for the ``StatusBanner``. Unknown stages are capitalized
    /// rather than swallowed so new backend stages surface automatically.
    private func phaseLabel(forStage name: String) -> String {
        switch name {
        case "researcher": return "🔍 Searching"
        case "analyzer": return "🧠 Analyzing"
        case "fact_checker": return "✅ Fact-checking"
        case "writer": return "✍ Writing"
        case "planner": return "📋 Planning"
        default: return name.prefix(1).uppercased() + name.dropFirst()
        }
    }

    /// Provisional phase label derived from a tool name — used only when
    /// no `stage` event has arrived yet so the banner isn't empty during
    /// the prelude of a query.
    private func phaseLabel(forTool name: String) -> String {
        switch name {
        case "web_search", "scrape_url", "fetch_url":
            return "🔍 Searching"
        case "python_exec":
            return "🧠 Analyzing"
        case "create_report", "write_file":
            return "✍ Writing"
        default:
            return "⏳ Working"
        }
    }

    private func fetchModels() async {
        guard backend.isRunning,
              let url = URL(string: "\(backend.baseURL)/api/models") else { return }
        do {
            let (data, _) = try await URLSession.shared.data(from: url)
            let decoded = try JSONDecoder().decode(ModelsResponse.self, from: data)
            availableModels = decoded.models.map(\.name)
            // Record authoritative installed sizes for the RAM check.
            // Overrides any catalog estimate previously stored.
            for m in decoded.models {
                if let gb = m.sizeGB, gb > 0 {
                    modelSizeGB[m.name] = gb
                }
            }
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

    // MARK: - New Research project sheet

    private var newProjectSheet: some View {
        let sanitized = Self.sanitizeProjectName(newProjectName)
        let trimmed = newProjectName.trimmingCharacters(in: .whitespaces)
        let canCreate = !sanitized.isEmpty
        return VStack(alignment: .leading, spacing: 0) {
            Text("New Research")
                .font(.system(size: 15, weight: .semibold))
                .padding(.horizontal, 20)
                .padding(.top, 18)
                .padding(.bottom, 12)

            Form {
                TextField("Research topic…", text: $newProjectName)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit { if canCreate { commitNewProject(sanitized) } }

                if !trimmed.isEmpty {
                    Text("Folder: ~/Desktop/\(sanitized)/")
                        .font(.system(size: 11, design: .monospaced))
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
            }
            .padding(.horizontal, 20)

            HStack {
                Spacer()
                Button("Cancel", role: .cancel) {
                    showNewProjectSheet = false
                }
                .keyboardShortcut(.escape, modifiers: [])

                Button("Create") { commitNewProject(sanitized) }
                    .keyboardShortcut(.defaultAction)
                    .disabled(!canCreate)
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 14)
        }
        .frame(width: 440)
    }

    /// Sanitize a user-entered project name into a safe folder name.
    /// Strips characters outside [A-Za-z0-9._ -], replaces runs with a
    /// single dash, trims surrounding whitespace/dashes, caps at 80 chars.
    static func sanitizeProjectName(_ raw: String) -> String {
        let allowed: Set<Character> = Set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._ -")
        var out = ""
        var lastWasDash = false
        for ch in raw {
            if allowed.contains(ch) {
                out.append(ch)
                lastWasDash = (ch == "-")
            } else {
                if !lastWasDash { out.append("-") }
                lastWasDash = true
            }
        }
        out = out.trimmingCharacters(in: CharacterSet(charactersIn: " -"))
        if out.count > 80 { out = String(out.prefix(80)) }
        return out
    }

    private func commitNewProject(_ sanitized: String) {
        guard !sanitized.isEmpty else { return }
        let fm = FileManager.default
        guard let desktop = try? fm.url(
            for: .desktopDirectory,
            in: .userDomainMask,
            appropriateFor: nil,
            create: false
        ) else {
            errorMessage = "Could not resolve ~/Desktop."
            return
        }
        let dir = desktop.appendingPathComponent(sanitized, isDirectory: true)

        if fm.fileExists(atPath: dir.path) {
            existingProjectURL = dir
            showExistingProjectAlert = true
            return
        }

        do {
            try fm.createDirectory(at: dir, withIntermediateDirectories: true)
            try fm.createDirectory(
                at: dir.appendingPathComponent("artifacts", isDirectory: true),
                withIntermediateDirectories: true
            )
            let seed = Data("{\"queries\": []}\n".utf8)
            try seed.write(to: dir.appendingPathComponent("queries.json"))
        } catch {
            errorMessage = "Could not create \(dir.path): \(error.localizedDescription)"
            return
        }

        projectDir = dir
        newThread()
        showNewProjectSheet = false
    }

    /// Append {query, answer_preview, timestamp} to `<projectDir>/queries.json`.
    /// Best-effort: a failure here never blocks the UI.
    private func appendQueryRecord(dir: URL, query: String, answer: String) {
        let file = dir.appendingPathComponent("queries.json")
        let preview = String(answer.prefix(240))
        let ts = ISO8601DateFormatter().string(from: Date())
        let record: [String: Any] = [
            "query": query, "answer_preview": preview, "timestamp": ts,
        ]
        var list: [[String: Any]] = []
        if let data = try? Data(contentsOf: file),
           let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let arr = obj["queries"] as? [[String: Any]] {
            list = arr
        }
        list.append(record)
        if let data = try? JSONSerialization.data(
            withJSONObject: ["queries": list], options: [.prettyPrinted]
        ) {
            try? data.write(to: file)
        }
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
