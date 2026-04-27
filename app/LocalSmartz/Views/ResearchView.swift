import SwiftUI

struct ResearchView: View {
    @EnvironmentObject var appState: AppState
    @EnvironmentObject var projectIndex: ProjectIndex
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

    /// Banner shown when the main view is rendering a saved query in
    /// read-only mode. Non-nil disables the input bar; cleared by
    /// starting a new thread or creating a new project.
    @State private var savedQueryBanner: String? = nil

    /// Pending deletion confirmation — set by the sidebar context menu.
    @State private var projectPendingDeletion: Project? = nil

    // MARK: - Cost-confirm modal (Item 4, 2026-04-23)
    /// Cached provider read from /api/patterns/current. Refreshed on
    /// appear and after a Save on the PatternTab. Defaults to "ollama"
    /// which bypasses the cost modal.
    @State private var currentProvider: String = "ollama"
    @State private var currentPattern: String = "single"
    /// When non-nil, surface the cost-confirm modal. Cleared by
    /// Continue (which fires the real run) or Cancel.
    @State private var pendingCloudQuery: String?
    @State private var pendingCostUSD: Double? = nil
    @State private var pendingCostRateKnown: Bool = true
    @State private var pendingCostRateDate: String = ""

    // MARK: - Thread pattern conflict modal (Item 7, F15)
    @State private var patternConflictQuery: String?
    @State private var patternConflictMessage: String = ""

    /// Prompts typed while a run is in progress. The first entry is
    /// auto-dispatched when `isStreaming` flips to false.
    @State private var queuedPrompts: [String] = []
    /// Controls the queue popover triggered from the count badge.
    @State private var showQueuePopover = false

    /// Composer height controlled by drag handle; persisted across launches.
    @State private var composerHeight: CGFloat = {
        let saved = UserDefaults.standard.double(forKey: "composerHeight")
        return saved > 0 ? CGFloat(saved) : 80
    }()

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
                focusAgent: $focusAgent,
                onSelectSavedQuery: { project, saved in
                    loadSavedQuery(project: project, saved: saved)
                },
                onDeleteProject: { project in
                    projectPendingDeletion = project
                }
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

                // Read-only saved-query banner. Appears only when the
                // sidebar loaded a past query into the main view.
                if let banner = savedQueryBanner {
                    HStack(spacing: 8) {
                        Image(systemName: "clock.arrow.circlepath")
                            .font(.system(size: 11))
                            .foregroundStyle(.secondary)
                        Text(banner)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        Spacer()
                        Button("Close") {
                            newThread()
                            savedQueryBanner = nil
                        }
                        .buttonStyle(.plain)
                        .font(.caption)
                        .foregroundStyle(Color.accentColor)
                    }
                    .padding(.horizontal, 16)
                    .padding(.vertical, 6)
                    .background(Color.secondary.opacity(0.06))
                }

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

                // Input bar — drag handle on top allows user to resize
                VStack(spacing: 0) {
                    // Drag handle: invisible fat hit area + thin visible line
                    Rectangle()
                        .fill(Color.secondary.opacity(0.001))
                        .frame(height: 6)
                        .overlay(
                            Rectangle()
                                .fill(Color.secondary.opacity(0.3))
                                .frame(height: 1),
                            alignment: .center
                        )
                        .gesture(
                            DragGesture()
                                .onChanged { gesture in
                                    let newHeight = composerHeight - gesture.translation.height
                                    composerHeight = max(48, min(400, newHeight))
                                    UserDefaults.standard.set(Double(composerHeight), forKey: "composerHeight")
                                }
                        )
                        .onHover { hovering in
                            if hovering { NSCursor.resizeUpDown.push() } else { NSCursor.pop() }
                        }
                    // Queue indicator — visible only when prompts are waiting.
                    if !queuedPrompts.isEmpty {
                        HStack(spacing: 6) {
                            Button {
                                showQueuePopover = true
                            } label: {
                                Text("\(queuedPrompts.count) queued")
                                    .font(.system(size: 11))
                                    .foregroundStyle(.secondary)
                            }
                            .buttonStyle(.plain)
                            .popover(isPresented: $showQueuePopover, arrowEdge: .bottom) {
                                VStack(alignment: .leading, spacing: 6) {
                                    Text("Queued prompts")
                                        .font(.system(size: 11, weight: .semibold))
                                        .foregroundStyle(.secondary)
                                    ForEach(Array(queuedPrompts.enumerated()), id: \.offset) { idx, q in
                                        HStack(spacing: 6) {
                                            Text(q)
                                                .font(.system(size: 11))
                                                .lineLimit(2)
                                            Spacer()
                                            Button {
                                                queuedPrompts.remove(at: idx)
                                            } label: {
                                                Image(systemName: "xmark.circle.fill")
                                                    .font(.system(size: 11))
                                                    .foregroundStyle(.secondary)
                                            }
                                            .buttonStyle(.plain)
                                        }
                                    }
                                }
                                .padding(12)
                                .frame(minWidth: 240, maxWidth: 320)
                            }
                            Spacer()
                        }
                        .padding(.horizontal, 16)
                        .padding(.top, 4)
                    }
                    // Fix 4: Prospective model hint — shown while composing,
                    // hidden during streaming or when input is empty.
                    if !prompt.trimmingCharacters(in: .whitespaces).isEmpty && !isStreaming {
                        HStack(spacing: 0) {
                            Text("→ next: ")
                                .font(.system(size: 11))
                                .foregroundStyle(.secondary)
                            Text(prospectiveModel)
                                .font(.system(size: 11, design: .monospaced))
                                .foregroundStyle(.secondary)
                            Spacer()
                        }
                        .padding(.horizontal, 16)
                        .padding(.top, 2)
                    }
                    inputBar
                }
                .frame(height: composerHeight)
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
                    projectIndex.add(name: url.lastPathComponent, path: url)
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
            .alert(
                "Delete \u{0022}\(projectPendingDeletion?.name ?? "")\u{0022}?",
                isPresented: Binding(
                    get: { projectPendingDeletion != nil },
                    set: { if !$0 { projectPendingDeletion = nil } }
                ),
                presenting: projectPendingDeletion
            ) { project in
                Button("Delete", role: .destructive) {
                    confirmDeleteProject(project)
                    projectPendingDeletion = nil
                }
                Button("Cancel", role: .cancel) {
                    projectPendingDeletion = nil
                }
            } message: { _ in
                Text("This removes the folder from Desktop \u{2014} saved queries will be lost.")
            }
            .sheet(
                isPresented: Binding(
                    get: { pendingCloudQuery != nil },
                    set: { if !$0 { pendingCloudQuery = nil } }
                )
            ) {
                costConfirmSheet
            }
            .alert(
                "Pattern change requires a new thread",
                isPresented: Binding(
                    get: { patternConflictQuery != nil },
                    set: { if !$0 { patternConflictQuery = nil } }
                )
            ) {
                Button("Start new thread") {
                    if let q = patternConflictQuery {
                        patternConflictQuery = nil
                        newThread()
                        // Re-queue the query now that selectedThread is nil.
                        prompt = q
                        runResearch(query: q, bypassRAMCheck: true, bypassCostCheck: true)
                    }
                }
                Button("Cancel", role: .cancel) {
                    patternConflictQuery = nil
                }
            } message: {
                Text(patternConflictMessage)
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
                await fetchPatternConfig()
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

    // MARK: - Cost-confirm sheet + fetchers (Item 4)

    @ViewBuilder
    private var costConfirmSheet: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Cloud run — confirm cost")
                .font(.system(size: 14, weight: .semibold))
            Text(
                "This request will run against the \(currentProvider) API, not a "
                + "local model. Estimated cost for this query:"
            )
            .font(.system(size: 12))
            .foregroundStyle(.secondary)
            .fixedSize(horizontal: false, vertical: true)

            HStack(spacing: 8) {
                if let usd = pendingCostUSD {
                    Text(String(format: "$%.4f", usd))
                        .font(.system(size: 20, weight: .semibold, design: .monospaced))
                } else {
                    ProgressView().controlSize(.small)
                    Text("Estimating…")
                        .font(.system(size: 12))
                        .foregroundStyle(.secondary)
                }
                Spacer()
            }
            if !pendingCostRateKnown {
                Text("Rate not found in table — cost may be off. (\(pendingCostRateDate))")
                    .font(.system(size: 11))
                    .foregroundStyle(.orange)
            } else if !pendingCostRateDate.isEmpty {
                Text("Rates dated \(pendingCostRateDate).")
                    .font(.system(size: 11))
                    .foregroundStyle(.tertiary)
            }
            HStack {
                Spacer()
                Button("Cancel") {
                    pendingCloudQuery = nil
                    pendingCostUSD = nil
                }
                Button("Continue") {
                    if let q = pendingCloudQuery {
                        pendingCloudQuery = nil
                        pendingCostUSD = nil
                        runResearch(query: q, bypassRAMCheck: true, bypassCostCheck: true)
                    }
                }
                .keyboardShortcut(.defaultAction)
                .disabled(pendingCostUSD == nil)
            }
        }
        .padding(20)
        .frame(minWidth: 360)
    }

    /// Fetch the active pattern + provider from /api/patterns/current.
    /// Called on view startup and after the Research flow (so switching
    /// the pattern in Settings reflects without an app relaunch).
    private func fetchPatternConfig() async {
        guard let url = URL(string: "\(backend.baseURL)/api/patterns/current") else { return }
        struct Resp: Decodable { let pattern: String?; let provider: String? }
        if let (data, _) = try? await URLSession.shared.data(from: url),
           let decoded = try? JSONDecoder().decode(Resp.self, from: data) {
            if let p = decoded.pattern, !p.isEmpty { currentPattern = p }
            if let p = decoded.provider, !p.isEmpty { currentProvider = p }
        }
    }

    /// POST /api/cloud/estimate with the current model + pattern to get a
    /// token-based USD estimate. Populates pendingCostUSD + rate flags.
    private func fetchCostEstimate(for query: String) async {
        guard let url = URL(string: "\(backend.baseURL)/api/cloud/estimate") else {
            pendingCostUSD = 0
            pendingCostRateKnown = false
            pendingCostRateDate = ""
            return
        }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try? JSONSerialization.data(withJSONObject: [
            "model": currentModel,
            "prompt": query,
            "pattern": currentPattern,
        ])
        struct Resp: Decodable {
            let estimatedUsd: Double
            let rateKnown: Bool
            let lastUpdated: String?
            enum CodingKeys: String, CodingKey {
                case estimatedUsd = "estimated_usd"
                case rateKnown = "rate_known"
                case lastUpdated = "last_updated"
            }
        }
        do {
            let (data, _) = try await URLSession.shared.data(for: req)
            let decoded = try JSONDecoder().decode(Resp.self, from: data)
            pendingCostUSD = decoded.estimatedUsd
            pendingCostRateKnown = decoded.rateKnown
            pendingCostRateDate = decoded.lastUpdated ?? ""
        } catch {
            // Surface a pessimistic "unknown" state rather than silently
            // blocking — user can still proceed.
            pendingCostUSD = 0
            pendingCostRateKnown = false
            pendingCostRateDate = ""
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
        if availableModels.isEmpty {
            // Both empty — avoid indefinite "Loading…" by falling back to
            // the profile chip so the toolbar always shows something useful.
            return appState.profile.uppercased()
        }
        return "Pick a model"
    }

    /// The model that will fire for the next query. Prefers a focused agent's
    /// per-agent model override over the global currentModel.
    private var prospectiveModel: String {
        if let agentName = focusAgent,
           let agentModel = agents.first(where: { $0.name == agentName })?.model,
           !agentModel.isEmpty {
            return agentModel
        }
        return currentModel.isEmpty ? modelPickerLabel : currentModel
    }

    // MARK: - Empty state

    private var emptyState: some View {
        VStack(spacing: 12) {
            Spacer()
            Image(systemName: emptyStateIcon)
                .font(.system(size: 32))
                .foregroundStyle(.secondary)
            Text(emptyStateMessage)
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 380)
            // Actionable affordance when Ollama isn't ready — beats a static
            // "Start Ollama" message with no way to act on it.
            if let action = emptyStateAction {
                Button(action: action.handler) {
                    Label(action.title, systemImage: action.icon)
                        .font(.system(size: 13, weight: .medium))
                        .padding(.horizontal, 8)
                }
                .controlSize(.regular)
                .buttonStyle(.borderedProminent)
                .padding(.top, 4)
                if let hint = action.hint {
                    Text(hint)
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                        .multilineTextAlignment(.center)
                        .frame(maxWidth: 380)
                }
            }
            Spacer()
        }
        .frame(maxWidth: .infinity)
    }

    private var emptyStateIcon: String {
        switch appState.ollamaStatus {
        case .ready: return "text.magnifyingglass"
        case .offline: return "exclamationmark.triangle"
        case .needsSetup: return "arrow.down.circle"
        case .loading, .unknown: return "hourglass"
        }
    }

    private struct EmptyStateAction {
        let title: String
        let icon: String
        let hint: String?
        let handler: () -> Void
    }

    private var emptyStateAction: EmptyStateAction? {
        switch appState.ollamaStatus {
        case .offline:
            return EmptyStateAction(
                title: "Open Ollama install instructions",
                icon: "arrow.up.right.square",
                hint: "Install Ollama from ollama.com, then run `ollama serve` (or open the Ollama app).",
                handler: {
                    if let url = URL(string: "https://ollama.com/download") {
                        NSWorkspace.shared.open(url)
                    }
                }
            )
        case .needsSetup:
            return EmptyStateAction(
                title: "Install a model",
                icon: "arrow.down.circle",
                hint: "Pull a model so the agents can run. The default is qwen3:8b-q4_K_M.",
                handler: { showInstallSheet = true }
            )
        case .loading, .unknown, .ready:
            return nil
        }
    }

    // MARK: - Input bar

    private var inputBar: some View {
        HStack(alignment: .bottom, spacing: 8) {
            // Multiline field — grows up to 10 lines naturally, scrollable
            // inside the resized composer container when composerHeight > 120.
            Group {
                if composerHeight > 120 {
                    ScrollView(.vertical, showsIndicators: false) {
                        TextField(inputPlaceholder, text: $prompt, axis: .vertical)
                            .textFieldStyle(.plain)
                            .font(.body)
                            .lineLimit(1...10)
                            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                            .onSubmit { if canRun { runResearch() } }
                            .disabled(false)  // typeable while streaming so user can queue thoughts
                    }
                } else {
                    TextField(inputPlaceholder, text: $prompt, axis: .vertical)
                        .textFieldStyle(.plain)
                        .font(.body)
                        .lineLimit(1...10)
                        .frame(maxWidth: .infinity, alignment: .topLeading)
                        .onSubmit { if canRun { runResearch() } }
                        .disabled(false)  // typeable while streaming so user can queue thoughts
                }
            }
            .padding(.vertical, 4)
            .padding(.horizontal, 8)

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
                .padding(.bottom, 8)
            } else {
                Button(action: runResearch) {
                    Image(systemName: "arrow.up.circle.fill")
                        .font(.title2)
                        .frame(minWidth: 24, minHeight: 24)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.borderless)
                .disabled(!canRun)
                .opacity(canRun ? 1.0 : 0.3)
                .keyboardShortcut(.return, modifiers: .command)
                .help("Send (⌘↩)")
                .accessibilityLabel("Send research query")
                .padding(.bottom, 8)
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 6)
        .background(
            RoundedRectangle(cornerRadius: 8)
                .fill(.regularMaterial)
                .strokeBorder(Color.secondary.opacity(0.2), lineWidth: 1)
        )
        .padding(.horizontal, 12)
        .padding(.bottom, 8)
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
            && savedQueryBanner == nil
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
        savedQueryBanner = nil
    }

    // MARK: - Saved query + project delete

    /// Load a past query's preview into the main view in read-only mode.
    /// Cancels any active stream so UI state is consistent.
    private func loadSavedQuery(project: Project, saved: SavedQuery) {
        researchTask?.cancel()
        researchTask = nil
        isStreaming = false
        appState.isResearching = false

        currentTitle = saved.query
        outputText = saved.answerPreview
        toolCalls = []
        durationMs = nil
        errorMessage = nil
        prompt = ""
        currentPhase = nil

        let df = DateFormatter()
        df.dateStyle = .short
        df.timeStyle = .short
        let when = df.string(from: saved.timestamp)
        savedQueryBanner = "Viewing saved query from \(project.name) \u{00B7} \(when)"
    }

    /// Confirmed delete of a project: remove the Desktop folder, remove
    /// the index entry, and clear active project state if needed.
    private func confirmDeleteProject(_ project: Project) {
        let url = URL(fileURLWithPath: project.path)
        try? FileManager.default.removeItem(at: url)
        projectIndex.remove(project)
        if projectDir?.path == project.path {
            projectDir = nil
            savedQueryBanner = nil
            newThread()
        }
    }

    private func runResearch() {
        let trimmed = prompt.trimmingCharacters(in: .whitespaces)
        guard !trimmed.isEmpty else { return }
        // If a run is in flight, queue rather than no-op.
        if isStreaming {
            queuedPrompts.append(trimmed)
            prompt = ""
            return
        }
        guard canRun else { return }
        runResearch(query: trimmed, bypassRAMCheck: false)
    }

    /// Core research dispatch. The public zero-arg ``runResearch()`` calls
    /// this with ``bypassRAMCheck: false`` so a too-large-model dialog can
    /// gate the request; the "Continue anyway" branch of that dialog calls
    /// back in with ``bypassRAMCheck: true`` and the same query.
    ///
    /// Cloud runs (provider != ollama) additionally pass through a
    /// cost-confirm modal (Item 4). ``bypassCostCheck`` is set by the
    /// modal's Continue button so we don't loop.
    private func runResearch(query: String, bypassRAMCheck: Bool, bypassCostCheck: Bool = false) {
        if !bypassRAMCheck, let warning = ramWarningForCurrentModel() {
            // Stash state and surface the dialog. Preserve the prompt in
            // the text field so the user can edit/retry after switching
            // models — only clear once they actually commit.
            pendingLargeModelQuery = query
            pendingLargeModelSize = warning
            return
        }

        // Cost-confirm gate — cloud runs only, always shown (no threshold).
        if !bypassCostCheck && currentProvider != "ollama" {
            pendingCloudQuery = query
            pendingCostUSD = nil  // show "Estimating…" until fetch completes
            Task { await fetchCostEstimate(for: query) }
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
        // Thread pattern-pinning (F15) needs pattern + provider on the
        // request so the backend can 409 on cross-pattern re-use of an
        // existing thread, and pin both on first creation.
        payload["pattern"] = currentPattern
        payload["provider"] = currentProvider

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
                    // Detect the pattern-mismatch 409 (F15) and surface a
                    // friendlier modal instead of a raw error line. The
                    // SSEClient packs the HTTP status + response body
                    // into SSEError.badResponse.
                    if case SSEError.badResponse(let status, let message) = error,
                       status == 409 {
                        patternConflictMessage = message ?? "Switching pattern will start a new thread."
                        patternConflictQuery = query
                    } else {
                        errorMessage = error.localizedDescription
                    }
                }
            }
            isStreaming = false
            appState.isResearching = false
            researchTask = nil
            await refreshStatus()
            await fetchThreads()
            // Drain the queue — pop and fire the next prompt, if any.
            if !queuedPrompts.isEmpty {
                let next = queuedPrompts.removeFirst()
                // Small async hop so the UI settles between runs.
                Task { @MainActor in
                    try? await Task.sleep(nanoseconds: 100_000_000) // 0.1s
                    runResearch(query: next, bypassRAMCheck: false)
                }
            }
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
                // Populate toolbar model name from the first status event
                // that carries a model. Gate behind !isSwitchingModel so we
                // don't stomp a picker selection the user just made.
                if currentModel.isEmpty && !isSwitchingModel {
                    currentModel = model
                }
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
        projectIndex.add(name: sanitized, path: dir)
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
