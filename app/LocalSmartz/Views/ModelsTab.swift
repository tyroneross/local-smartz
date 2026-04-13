import SwiftUI

// MARK: - Decodables

struct CatalogModel: Decodable, Identifiable {
    let name: String
    let ramClass: String?
    let sizeGBEstimate: Double
    let installedSizeGB: Double?
    let note: String?
    let installed: Bool
    let current: Bool

    var id: String { name }

    enum CodingKeys: String, CodingKey {
        case name
        case ramClass = "ram_class"
        case sizeGBEstimate = "size_gb_estimate"
        case installedSizeGB = "installed_size_gb"
        case note
        case installed
        case current
    }
}

struct CatalogResponse: Decodable {
    let catalog: [CatalogModel]
    let current: String
    let profile: String?
}

struct OllamaInfo: Decodable {
    let running: Bool
    let version: String?
    let modelsPath: String
    let pathExists: Bool
    let source: String
    let modelCount: Int
    let totalSizeBytes: Int

    enum CodingKeys: String, CodingKey {
        case running
        case version
        case modelsPath = "models_path"
        case pathExists = "path_exists"
        case source
        case modelCount = "model_count"
        case totalSizeBytes = "total_size_bytes"
    }
}

/// Minimal shape we need off `/api/status` — just `ram_gb`.
private struct StatusRAMResponse: Decodable {
    let ramGB: Int?

    enum CodingKeys: String, CodingKey {
        case ramGB = "ram_gb"
    }
}

// MARK: - View Model

@MainActor
final class ModelsViewModel: ObservableObject {
    @Published var catalog: [CatalogModel] = []
    @Published var info: OllamaInfo?
    @Published var currentModel: String = ""
    @Published var loading = false
    @Published var error: String?
    @Published var busyModel: String?           // model currently pulling/removing
    @Published var pullProgress: [String: String] = [:]  // last line per model
    @Published var detectedRAMGB: Int?

    /// Port range the Mac app's backend uses. We probe to find the running one.
    private let candidatePorts = (11435...11444)

    private var baseURL: String?

    func refresh() async {
        loading = true
        defer { loading = false }
        error = nil

        baseURL = await discoverBackend()
        guard let base = baseURL else {
            error = "Backend is not reachable. Is the main window open?"
            return
        }

        await loadCatalog(base: base)
        await loadInfo(base: base)
        await loadRAM(base: base)
    }

    private func discoverBackend() async -> String? {
        for port in candidatePorts {
            let url = URL(string: "http://localhost:\(port)/api/health")!
            var req = URLRequest(url: url)
            req.timeoutInterval = 0.8
            if let (_, resp) = try? await URLSession.shared.data(for: req),
               let http = resp as? HTTPURLResponse, http.statusCode == 200 {
                return "http://localhost:\(port)"
            }
        }
        return nil
    }

    private func loadCatalog(base: String) async {
        guard let url = URL(string: "\(base)/api/models/catalog") else { return }
        do {
            let (data, _) = try await URLSession.shared.data(from: url)
            let decoded = try JSONDecoder().decode(CatalogResponse.self, from: data)
            catalog = decoded.catalog
            currentModel = decoded.current
        } catch {
            self.error = "Could not load model catalog: \(error.localizedDescription)"
        }
    }

    private func loadInfo(base: String) async {
        guard let url = URL(string: "\(base)/api/ollama/info") else { return }
        do {
            let (data, _) = try await URLSession.shared.data(from: url)
            info = try JSONDecoder().decode(OllamaInfo.self, from: data)
        } catch {
            // non-fatal; ollama info section just hides
        }
    }

    private func loadRAM(base: String) async {
        guard let url = URL(string: "\(base)/api/status") else { return }
        do {
            let (data, _) = try await URLSession.shared.data(from: url)
            let decoded = try JSONDecoder().decode(StatusRAMResponse.self, from: data)
            if let gb = decoded.ramGB, gb > 0 {
                detectedRAMGB = gb
            }
        } catch {
            // non-fatal; recommendation chips simply hide
        }
    }

    func pull(_ model: String) async {
        guard let base = baseURL,
              let url = URL(string: "\(base)/api/models/pull") else { return }
        busyModel = model
        pullProgress[model] = "Starting..."
        defer { busyModel = nil }

        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try? JSONSerialization.data(withJSONObject: ["model": model])

        do {
            let (stream, _) = try await URLSession.shared.bytes(for: req)
            for try await line in stream.lines {
                // SSE lines: "data: {...}"
                guard line.hasPrefix("data: ") else { continue }
                let payload = String(line.dropFirst(6))
                if let data = payload.data(using: .utf8),
                   let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                    if let ln = obj["line"] as? String {
                        pullProgress[model] = ln
                    } else if let kind = obj["type"] as? String, kind == "done" {
                        pullProgress[model] = "Downloaded ✓"
                    } else if let kind = obj["type"] as? String, kind == "error",
                              let msg = obj["message"] as? String {
                        pullProgress[model] = "Error: \(msg)"
                    }
                }
            }
            await refresh()
        } catch {
            pullProgress[model] = "Error: \(error.localizedDescription)"
        }
    }

    func remove(_ model: String) async {
        guard let base = baseURL,
              var comps = URLComponents(string: "\(base)/api/models") else { return }
        comps.queryItems = [URLQueryItem(name: "name", value: model)]
        guard let url = comps.url else { return }
        busyModel = model
        defer { busyModel = nil }

        var req = URLRequest(url: url)
        req.httpMethod = "DELETE"
        do {
            let (_, resp) = try await URLSession.shared.data(for: req)
            if let http = resp as? HTTPURLResponse, http.statusCode != 200 {
                error = "Could not remove \(model) (status \(http.statusCode))"
            }
            await refresh()
        } catch {
            self.error = "Remove failed: \(error.localizedDescription)"
        }
    }
}

// MARK: - RAM fit classification

enum RAMFit {
    case comfortable   // model_size <= ram / 2
    case tight         // model_size <= ram
    case tooLarge      // model_size > ram

    static func classify(modelSizeGB: Double, ramGB: Int) -> RAMFit {
        let ram = Double(ramGB)
        if modelSizeGB <= ram / 2 { return .comfortable }
        if modelSizeGB <= ram { return .tight }
        return .tooLarge
    }

    var label: String {
        switch self {
        case .comfortable: return "✓ Fits"
        case .tight:       return "⚠ Tight"
        case .tooLarge:    return "✗ Too large"
        }
    }

    var color: Color {
        switch self {
        case .comfortable: return .green
        case .tight:       return .orange
        case .tooLarge:    return .secondary
        }
    }
}

// MARK: - View

struct ModelsTab: View {
    @StateObject private var vm = ModelsViewModel()

    /// User-provided RAM override, in GB. Zero/absent means "use detected".
    @AppStorage("ramGBOverride") private var ramGBOverride: Int = 0

    @State private var showOverrideSheet = false
    @State private var overrideDraft: String = ""
    @State private var removalCandidate: CatalogModel?

    /// Effective RAM for classification — override wins if set.
    private var effectiveRAMGB: Int? {
        if ramGBOverride > 0 { return ramGBOverride }
        return vm.detectedRAMGB
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            header

            ramBanner

            if let error = vm.error {
                Text(error)
                    .font(.system(size: 12))
                    .foregroundStyle(.red)
                    .padding(.horizontal, 20)
            }

            if vm.catalog.isEmpty && vm.loading {
                Spacer()
                ProgressView("Loading catalog…")
                    .frame(maxWidth: .infinity)
                Spacer()
            } else if vm.catalog.isEmpty {
                Spacer()
                Text("Start the main window to load the catalog.")
                    .font(.system(size: 12))
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity)
                Spacer()
            } else {
                List {
                    Section {
                        ForEach(vm.catalog) { model in
                            row(model)
                        }
                    } header: {
                        Text("Ollama models")
                            .font(.system(size: 11).smallCaps())
                            .foregroundStyle(.secondary)
                    }
                    if let info = vm.info {
                        Section {
                            ollamaInfoRows(info)
                        } header: {
                            Text("Ollama storage")
                                .font(.system(size: 11).smallCaps())
                                .foregroundStyle(.secondary)
                        }
                    }
                }
                .listStyle(.inset)
            }
        }
        .padding(.top, 4)
        .task { await vm.refresh() }
        .sheet(isPresented: $showOverrideSheet) {
            overrideSheet
        }
        .confirmationDialog(
            removalCandidate.map { "Remove \($0.name)? This cannot be undone." } ?? "",
            isPresented: Binding(
                get: { removalCandidate != nil },
                set: { if !$0 { removalCandidate = nil } }
            ),
            titleVisibility: .visible,
            presenting: removalCandidate
        ) { model in
            Button("Remove", role: .destructive) {
                let name = model.name
                removalCandidate = nil
                Task { await vm.remove(name) }
            }
            Button("Cancel", role: .cancel) {
                removalCandidate = nil
            }
        }
    }

    private var header: some View {
        HStack {
            Text("Models and Ollama storage")
                .font(.system(size: 13, weight: .medium))
            Spacer()
            Button {
                Task { await vm.refresh() }
            } label: {
                Image(systemName: "arrow.clockwise")
            }
            .buttonStyle(.borderless)
            .disabled(vm.loading)
        }
        .padding(.horizontal, 20)
        .padding(.top, 8)
    }

    // MARK: - RAM banner

    @ViewBuilder
    private var ramBanner: some View {
        HStack(spacing: 8) {
            if ramGBOverride > 0 {
                Text("Override: \(ramGBOverride) GB")
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
                Text("·")
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
                Button("Reset") {
                    ramGBOverride = 0
                }
                .buttonStyle(.borderless)
                .font(.system(size: 11))
            } else if let ram = vm.detectedRAMGB {
                Text("Detected RAM: \(ram) GB")
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
                Text("·")
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
                Button("Set manually…") {
                    overrideDraft = ramGBOverride > 0 ? String(ramGBOverride) : String(ram)
                    showOverrideSheet = true
                }
                .buttonStyle(.borderless)
                .font(.system(size: 11))
            } else {
                Text("RAM not detected")
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
                Text("·")
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
                Button("Set manually…") {
                    overrideDraft = ""
                    showOverrideSheet = true
                }
                .buttonStyle(.borderless)
                .font(.system(size: 11))
            }
            Spacer()
        }
        .padding(.horizontal, 20)
    }

    private var overrideSheet: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Set RAM override")
                .font(.system(size: 13, weight: .semibold))
            Text("Used only for the local recommendation chips. Does not change backend behavior.")
                .font(.system(size: 11))
                .foregroundStyle(.secondary)
            HStack {
                TextField("GB", text: $overrideDraft)
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 100)
                Text("GB")
                    .font(.system(size: 12))
                    .foregroundStyle(.secondary)
            }
            HStack {
                Spacer()
                Button("Cancel") {
                    showOverrideSheet = false
                }
                Button("Save") {
                    if let gb = Int(overrideDraft.trimmingCharacters(in: .whitespaces)), gb > 0 {
                        ramGBOverride = gb
                    }
                    showOverrideSheet = false
                }
                .keyboardShortcut(.defaultAction)
                .disabled(Int(overrideDraft.trimmingCharacters(in: .whitespaces)) ?? 0 <= 0)
            }
        }
        .padding(20)
        .frame(minWidth: 320)
    }

    // MARK: - Row

    @ViewBuilder
    private func row(_ model: CatalogModel) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 8) {
                Text(model.name)
                    .font(.system(size: 13, weight: .medium))
                if model.current {
                    Text("ACTIVE")
                        .font(.system(size: 9, weight: .semibold))
                        .foregroundStyle(.green)
                }
                if let ram = effectiveRAMGB {
                    fitChip(RAMFit.classify(modelSizeGB: model.sizeGBEstimate, ramGB: ram))
                }
                Spacer()
                if vm.busyModel == model.name {
                    ProgressView().controlSize(.small)
                } else if model.installed {
                    // Toolbar picker in the main window is the single switcher;
                    // this tab only installs or removes. Remove uses a secondary
                    // (not bright red) style plus a confirmation dialog.
                    Button("Remove") {
                        removalCandidate = model
                    }
                    .buttonStyle(.borderless)
                    .foregroundStyle(.secondary)
                } else {
                    Button("Install") {
                        Task { await vm.pull(model.name) }
                    }
                    .buttonStyle(.borderless)
                }
            }

            HStack(spacing: 8) {
                Text(sizeText(model))
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
                if let cls = model.ramClass, cls != "custom" {
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

            if let progress = vm.pullProgress[model.name], vm.busyModel == model.name || progress == "Downloaded ✓" {
                Text(progress)
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundStyle(progress.hasPrefix("Error") ? .red : .secondary)
                    .lineLimit(1)
                    .truncationMode(.tail)
            }
        }
        .padding(.vertical, 4)
    }

    private func fitChip(_ fit: RAMFit) -> some View {
        Text(fit.label)
            .font(.system(size: 10, weight: .medium))
            .foregroundStyle(fit.color)
    }

    private func sizeText(_ model: CatalogModel) -> String {
        if let actual = model.installedSizeGB {
            return "\(String(format: "%.1f", actual)) GB installed"
        }
        return "~\(String(format: "%.1f", model.sizeGBEstimate)) GB"
    }

    @ViewBuilder
    private func ollamaInfoRows(_ info: OllamaInfo) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text("Path")
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
                Text(info.modelsPath)
                    .font(.system(size: 11, design: .monospaced))
                    .textSelection(.enabled)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }
            HStack {
                Text("Source")
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
                Text(info.source == "OLLAMA_MODELS" ? "OLLAMA_MODELS env var" : "Default (~/.ollama/models)")
                    .font(.system(size: 11))
            }
            HStack {
                Text("Total size")
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
                Text(formatBytes(info.totalSizeBytes))
                    .font(.system(size: 11))
            }
            HStack {
                Text("Models on disk")
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
                Text("\(info.modelCount)")
                    .font(.system(size: 11))
            }
        }
        .padding(.vertical, 2)
    }

    private func formatBytes(_ bytes: Int) -> String {
        let gb = Double(bytes) / 1_000_000_000
        if gb >= 1 { return String(format: "%.1f GB", gb) }
        let mb = Double(bytes) / 1_000_000
        return String(format: "%.0f MB", mb)
    }
}
