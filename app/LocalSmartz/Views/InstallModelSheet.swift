import SwiftUI

/// Sheet launched from the toolbar Model picker so the user can install a
/// new Ollama model without leaving the Research view.
///
/// Sections:
/// 1. **Popular on Ollama** — live top-10 from `ollama.com/search`, fetched
///    via `/api/models/library`. Deduped by family so newer releases
///    (gemma4, gemma3n) replace their predecessors. Refresh button forces
///    a live fetch bypassing the 24h cache.
/// 2. **Recommended for Local Smartz** — the 4 load-bearing models
///    (profile planning + execution defaults + heavy-tier alternatives).
///    Fetched from `/api/models/catalog`.
/// 3. **Pull any model by name** — free-text for the long tail.
struct InstallModelSheet: View {
    let backendBaseURL: String
    /// Called after a successful install so the caller can refresh its
    /// available-models list and optionally switch to the new model.
    var onInstalled: (String) -> Void

    @Environment(\.dismiss) private var dismiss
    @StateObject private var vm = InstallModelViewModel()
    @State private var customName: String = ""
    @State private var filter: String = ""

    /// Detected system RAM (GB) via /api/status — drives the fit chip colors.
    /// Zero means "unknown" and hides the chip.
    @State private var ramGB: Int = 0

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            Divider()

            if let err = vm.error {
                Text(err)
                    .font(.system(size: 12))
                    .foregroundStyle(.red)
                    .padding(.horizontal, 20)
                    .padding(.top, 10)
            }

            List {
                // ── Popular on Ollama ────────────────────────────────
                Section {
                    if vm.popular.isEmpty && vm.libraryLoading {
                        HStack(spacing: 8) {
                            ProgressView().controlSize(.small)
                            Text("Fetching from ollama.com…")
                                .font(.system(size: 12))
                                .foregroundStyle(.secondary)
                        }
                    } else if vm.popular.isEmpty {
                        Text("Couldn't reach ollama.com — use the curated list below or type a name.")
                            .font(.system(size: 12))
                            .foregroundStyle(.secondary)
                    } else {
                        ForEach(filteredPopular) { model in
                            libraryRow(model)
                        }
                    }
                } header: {
                    popularHeader
                }

                // ── Recommended for Local Smartz ────────────────────
                if !vm.curated.isEmpty {
                    Section {
                        ForEach(filteredCurated) { model in
                            curatedRow(model)
                        }
                    } header: {
                        Text("Recommended for Local Smartz profiles")
                            .font(.system(size: 11).smallCaps())
                            .foregroundStyle(.secondary)
                    }
                }

                // ── Pull any model by name ─────────────────────────
                Section {
                    customPullRow
                } header: {
                    Text("Pull any model by name")
                        .font(.system(size: 11).smallCaps())
                        .foregroundStyle(.secondary)
                }
            }
            .listStyle(.inset)

            Divider()
            footer
        }
        .frame(width: 680, height: 620)
        .task {
            await vm.refresh(base: backendBaseURL)
            await loadRAM()
        }
    }

    // MARK: - Header

    private var header: some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            VStack(alignment: .leading, spacing: 2) {
                Text("Install a model")
                    .font(.system(size: 15, weight: .semibold))
                Text("Pulls via Ollama. Safe to run while the app is open.")
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
            }
            Spacer()
            TextField("Filter…", text: $filter)
                .textFieldStyle(.roundedBorder)
                .frame(width: 180)
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 14)
    }

    private var popularHeader: some View {
        HStack(spacing: 8) {
            Text("Popular on Ollama")
                .font(.system(size: 11).smallCaps())
                .foregroundStyle(.secondary)
            if let fetched = vm.libraryFetchedAt {
                Text("·")
                    .font(.system(size: 10))
                    .foregroundStyle(.secondary)
                Text(formatFetched(fetched, source: vm.librarySource))
                    .font(.system(size: 10))
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Button {
                Task { await vm.refreshLibrary(base: backendBaseURL, forceFresh: true) }
            } label: {
                Label("Refresh", systemImage: "arrow.clockwise")
                    .font(.system(size: 11))
            }
            .buttonStyle(.borderless)
            .disabled(vm.libraryLoading)
        }
    }

    // MARK: - Footer

    private var footer: some View {
        HStack(spacing: 10) {
            Link("Browse ollama.com/library",
                 destination: URL(string: "https://ollama.com/library")!)
                .font(.system(size: 11))
            if ramGB > 0 {
                Text("·")
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
                Text("Detected RAM: \(ramGB) GB")
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Button("Done") {
                dismiss()
            }
            .keyboardShortcut(.defaultAction)
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 12)
    }

    // MARK: - Custom pull

    @ViewBuilder
    private var customPullRow: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                TextField("e.g. gemma4:e4b, qwen3.5:14b, mistral:7b", text: $customName)
                    .textFieldStyle(.roundedBorder)
                    .font(.system(size: 12, design: .monospaced))
                    .disableAutocorrection(true)
                    .onSubmit { submitCustom() }
                Button("Install") { submitCustom() }
                    .disabled(customTrimmed.isEmpty || vm.busyModel == customTrimmed)
            }
            Text("Any Ollama model name works. Size tags default to :latest if omitted.")
                .font(.system(size: 11))
                .foregroundStyle(.secondary)
            if let progress = vm.pullProgress[customTrimmed],
               vm.busyModel == customTrimmed || progress == "Downloaded ✓" {
                Text(progress)
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundStyle(progress.hasPrefix("Error") ? .red : .secondary)
                    .lineLimit(1)
                    .truncationMode(.tail)
            }
        }
        .padding(.vertical, 4)
    }

    private var customTrimmed: String {
        customName.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private func submitCustom() {
        let name = customTrimmed
        guard !name.isEmpty else { return }
        Task {
            await vm.pull(base: backendBaseURL, model: name)
            if vm.pullProgress[name] == "Downloaded ✓" {
                onInstalled(name)
            }
        }
    }

    // MARK: - Filter

    private var filteredPopular: [LibraryModel] {
        let q = filter.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        if q.isEmpty { return vm.popular }
        return vm.popular.filter { m in
            m.name.lowercased().contains(q)
                || m.capabilities.contains(where: { $0.lowercased().contains(q) })
                || m.sizes.contains(where: { $0.lowercased().contains(q) })
        }
    }

    private var filteredCurated: [InstallCatalogModel] {
        let q = filter.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        if q.isEmpty { return vm.curated }
        return vm.curated.filter { m in
            m.name.lowercased().contains(q)
                || (m.note ?? "").lowercased().contains(q)
                || (m.ramClass ?? "").lowercased().contains(q)
        }
    }

    // MARK: - Popular row

    @ViewBuilder
    private func libraryRow(_ model: LibraryModel) -> some View {
        let targetName = model.name  // pulls default tag
        let isInstalling = vm.busyModel == targetName || vm.pullProgress[targetName] != nil
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 8) {
                Text(model.name)
                    .font(.system(size: 13, weight: .medium, design: .monospaced))
                // Newer-than-30-days gets a subtle NEW badge.
                if (model.updatedDays ?? 999) <= 30 {
                    Text("NEW")
                        .font(.system(size: 9, weight: .bold))
                        .foregroundStyle(.green)
                }
                Spacer()
                if !model.pullsRaw.isEmpty {
                    Text(model.pullsRaw + " pulls")
                        .font(.system(size: 10, weight: .medium))
                        .foregroundStyle(.secondary)
                }
                if isInstalling {
                    ProgressView().controlSize(.small)
                } else {
                    Button("Install") {
                        Task {
                            await vm.pull(base: backendBaseURL, model: targetName)
                            if vm.pullProgress[targetName] == "Downloaded ✓" {
                                onInstalled(targetName)
                            }
                        }
                    }
                    .buttonStyle(.borderless)
                }
            }

            // Capabilities + sizes — text-only per Calm Precision rule 9
            // (no badge pills, color encodes meaning).
            HStack(spacing: 6) {
                ForEach(model.capabilities, id: \.self) { cap in
                    Text(cap)
                        .font(.system(size: 10, weight: .medium))
                        .foregroundStyle(capabilityColor(cap))
                }
                if !model.capabilities.isEmpty && !model.sizes.isEmpty {
                    Text("·")
                        .font(.system(size: 10))
                        .foregroundStyle(.secondary)
                }
                if !model.sizes.isEmpty {
                    Text(model.sizes.prefix(5).joined(separator: ", "))
                        .font(.system(size: 10, design: .monospaced))
                        .foregroundStyle(.secondary)
                }
                Spacer()
                if !model.updated.isEmpty {
                    Text(model.updated)
                        .font(.system(size: 10))
                        .foregroundStyle(.secondary)
                }
            }

            if let progress = vm.pullProgress[targetName],
               vm.busyModel == targetName {
                Text(progress)
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.tail)
            }
        }
        .padding(.vertical, 4)
    }

    private func capabilityColor(_ cap: String) -> Color {
        switch cap.lowercased() {
        case "tools": return .indigo
        case "vision": return .purple
        case "thinking": return .blue
        case "audio": return .teal
        case "embedding": return .gray
        default: return .secondary
        }
    }

    // MARK: - Curated row

    @ViewBuilder
    private func curatedRow(_ model: InstallCatalogModel) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 8) {
                Text(model.name)
                    .font(.system(size: 13, weight: .medium, design: .monospaced))
                if model.installed {
                    Text("Installed")
                        .font(.system(size: 9, weight: .semibold))
                        .foregroundStyle(.green)
                }
                if ramGB > 0 {
                    fitChip(for: model)
                }
                Spacer()
                if vm.busyModel == model.name {
                    ProgressView().controlSize(.small)
                } else if model.installed {
                    Text("Already installed")
                        .font(.system(size: 11))
                        .foregroundStyle(.secondary)
                } else {
                    Button("Install") {
                        Task {
                            await vm.pull(base: backendBaseURL, model: model.name)
                            if vm.pullProgress[model.name] == "Downloaded ✓" {
                                onInstalled(model.name)
                            }
                        }
                    }
                    .buttonStyle(.borderless)
                }
            }
            HStack(spacing: 8) {
                Text("~\(String(format: "%.1f", model.sizeGBEstimate)) GB")
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
                if let cls = model.ramClass {
                    Text("·")
                        .font(.system(size: 11))
                        .foregroundStyle(.secondary)
                    Text(cls)
                        .font(.system(size: 11))
                        .foregroundStyle(.secondary)
                }
                if let note = model.note {
                    Text("·")
                        .font(.system(size: 11))
                        .foregroundStyle(.secondary)
                    Text(note)
                        .font(.system(size: 11))
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                        .truncationMode(.tail)
                }
            }
            if let progress = vm.pullProgress[model.name],
               vm.busyModel == model.name {
                Text(progress)
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.tail)
            }
        }
        .padding(.vertical, 4)
    }

    private func fitChip(for model: InstallCatalogModel) -> some View {
        let ratio = model.sizeGBEstimate / Double(ramGB)
        let (label, color): (String, Color)
        if ratio <= 0.5 { label = "✓ Fits"; color = .green }
        else if ratio <= 1.0 { label = "⚠ Tight"; color = .orange }
        else { label = "✗ Too large"; color = .secondary }
        return Text(label)
            .font(.system(size: 10, weight: .medium))
            .foregroundStyle(color)
    }

    // MARK: - "Fetched X ago" label

    private func formatFetched(_ unix: Double, source: String) -> String {
        let age = Date().timeIntervalSince(Date(timeIntervalSince1970: unix))
        let phrase: String
        if age < 60 { phrase = "just now" }
        else if age < 3600 { phrase = "\(Int(age / 60))m ago" }
        else if age < 86400 { phrase = "\(Int(age / 3600))h ago" }
        else { phrase = "\(Int(age / 86400))d ago" }
        if source == "stale-fallback" {
            return "cached (offline) \(phrase)"
        }
        return phrase
    }

    // MARK: - RAM

    private func loadRAM() async {
        struct StatusResp: Decodable {
            let ramGB: Int?
            enum CodingKeys: String, CodingKey { case ramGB = "ram_gb" }
        }
        guard let url = URL(string: "\(backendBaseURL)/api/status") else { return }
        do {
            let (data, _) = try await URLSession.shared.data(from: url)
            let decoded = try JSONDecoder().decode(StatusResp.self, from: data)
            if let gb = decoded.ramGB, gb > 0 {
                ramGB = gb
            }
        } catch {
            // non-fatal — chips just hide
        }
    }
}

// MARK: - Models

/// Live popular-on-Ollama entry from ``/api/models/library``. Dedup'd by
/// family on the server, so a single row represents the most-recent
/// gemma4 / qwen3.5 / llama3.3 family head rather than multiple versions.
struct LibraryModel: Decodable, Identifiable {
    let name: String
    let family: String?
    let pulls: Int?
    let pullsRaw: String
    let sizes: [String]
    let capabilities: [String]
    let updated: String
    let updatedDays: Int?
    let quantizationHint: String?

    var id: String { name }

    enum CodingKeys: String, CodingKey {
        case name, family, pulls, sizes, capabilities, updated
        case pullsRaw = "pulls_raw"
        case updatedDays = "updated_days"
        case quantizationHint = "quantization_hint"
    }
}

/// Curated fallback entry from ``/api/models/catalog``. Four load-bearing
/// models (the actual profile defaults) — not a hand-maintained Top-N.
struct InstallCatalogModel: Decodable, Identifiable {
    let name: String
    let sizeGBEstimate: Double
    let ramClass: String?
    let note: String?
    let installed: Bool

    var id: String { name }

    enum CodingKeys: String, CodingKey {
        case name
        case sizeGBEstimate = "size_gb_estimate"
        case ramClass = "ram_class"
        case note
        case installed
    }
}

// MARK: - View Model

@MainActor
final class InstallModelViewModel: ObservableObject {
    @Published var popular: [LibraryModel] = []
    @Published var librarySource: String = ""
    @Published var libraryFetchedAt: Double?
    @Published var libraryLoading = false

    @Published var curated: [InstallCatalogModel] = []
    @Published var loading = false
    @Published var error: String?
    @Published var busyModel: String?
    @Published var pullProgress: [String: String] = [:]

    /// Both lists in parallel so the sheet renders at once.
    func refresh(base: String) async {
        async let live: Void = refreshLibrary(base: base, forceFresh: false)
        async let curatedLoad: Void = refreshCurated(base: base)
        _ = await (live, curatedLoad)
    }

    func refreshLibrary(base: String, forceFresh: Bool) async {
        libraryLoading = true
        defer { libraryLoading = false }

        var comps = URLComponents(string: "\(base)/api/models/library")
        var items: [URLQueryItem] = [
            URLQueryItem(name: "limit", value: "10"),
            URLQueryItem(name: "capability", value: "tools"),
        ]
        if forceFresh { items.append(URLQueryItem(name: "refresh", value: "1")) }
        comps?.queryItems = items
        guard let url = comps?.url else { return }

        struct Resp: Decodable {
            let source: String
            let fetched_at: Double?
            let entries: [LibraryModel]
        }
        do {
            let (data, _) = try await URLSession.shared.data(from: url)
            let decoded = try JSONDecoder().decode(Resp.self, from: data)
            popular = decoded.entries
            librarySource = decoded.source
            libraryFetchedAt = decoded.fetched_at
        } catch {
            // Non-fatal — the curated fallback section will still render.
            popular = []
        }
    }

    private func refreshCurated(base: String) async {
        loading = true
        defer { loading = false }
        guard let url = URL(string: "\(base)/api/models/catalog") else {
            error = "Bad backend URL"
            return
        }
        struct Resp: Decodable { let catalog: [InstallCatalogModel] }
        do {
            let (data, _) = try await URLSession.shared.data(from: url)
            let decoded = try JSONDecoder().decode(Resp.self, from: data)
            curated = decoded.catalog
        } catch {
            self.error = "Could not load catalog: \(error.localizedDescription)"
        }
    }

    /// Streams Ollama pull progress via /api/models/pull (SSE). The backend
    /// now emits structured `{status, digest, total, completed, percent}`
    /// chunks — use percent when present, else the status string.
    func pull(base: String, model: String) async {
        guard let url = URL(string: "\(base)/api/models/pull") else { return }
        busyModel = model
        pullProgress[model] = "Starting…"
        defer { busyModel = nil }

        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try? JSONSerialization.data(withJSONObject: ["model": model])

        do {
            let (stream, _) = try await URLSession.shared.bytes(for: req)
            for try await line in stream.lines {
                guard line.hasPrefix("data: ") else { continue }
                let payload = String(line.dropFirst(6))
                guard let data = payload.data(using: .utf8),
                      let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                    continue
                }
                if let kind = obj["type"] as? String {
                    switch kind {
                    case "progress":
                        let status = obj["status"] as? String ?? ""
                        if let percent = obj["percent"] as? Double {
                            pullProgress[model] = "\(status) \(String(format: "%.0f", percent))%"
                        } else {
                            pullProgress[model] = status
                        }
                    case "done":
                        pullProgress[model] = "Downloaded ✓"
                    case "error":
                        pullProgress[model] = "Error: \(obj["message"] as? String ?? "")"
                    default:
                        break
                    }
                }
            }
            await refreshCurated(base: base)
        } catch {
            pullProgress[model] = "Error: \(error.localizedDescription)"
        }
    }
}
