import SwiftUI

/// Sheet launched from the toolbar Model picker so the user can install a
/// new Ollama model without leaving the Research view. Lists curated models
/// with RAM-fit chips and a free-text field for any ``model:tag`` that
/// Ollama accepts (e.g. new Gemma, Mistral, or NVIDIA Nemotron releases).
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

            // Curated list
            List {
                Section {
                    if filteredModels.isEmpty && !vm.loading {
                        Text("No matches — try the custom name field below.")
                            .font(.system(size: 12))
                            .foregroundStyle(.secondary)
                    }
                    ForEach(filteredModels) { model in
                        row(model)
                    }
                } header: {
                    Text("Curated Ollama models")
                        .font(.system(size: 11).smallCaps())
                        .foregroundStyle(.secondary)
                }

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
        .frame(width: 620, height: 560)
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
            Button("Refresh") {
                Task { await vm.refresh(base: backendBaseURL) }
            }
            .disabled(vm.loading)
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 14)
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
                TextField("e.g. gemma3:27b, nemotron:70b, mistral:7b", text: $customName)
                    .textFieldStyle(.roundedBorder)
                    .font(.system(size: 12, design: .monospaced))
                    .disableAutocorrection(true)
                    .onSubmit { submitCustom() }
                Button("Install") { submitCustom() }
                    .disabled(customTrimmed.isEmpty || vm.busyModel == customTrimmed)
            }
            Text("Any Ollama model name works. Find more at ollama.com/library.")
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

    private var filteredModels: [InstallCatalogModel] {
        let q = filter.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        if q.isEmpty { return vm.catalog }
        return vm.catalog.filter { m in
            m.name.lowercased().contains(q)
                || (m.note ?? "").lowercased().contains(q)
                || (m.ramClass ?? "").lowercased().contains(q)
        }
    }

    // MARK: - Row

    @ViewBuilder
    private func row(_ model: InstallCatalogModel) -> some View {
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

// MARK: - Model

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
    @Published var catalog: [InstallCatalogModel] = []
    @Published var loading = false
    @Published var error: String?
    @Published var busyModel: String?
    @Published var pullProgress: [String: String] = [:]

    func refresh(base: String) async {
        loading = true
        defer { loading = false }
        error = nil
        guard let url = URL(string: "\(base)/api/models/catalog") else {
            error = "Bad backend URL"
            return
        }
        struct Resp: Decodable {
            let catalog: [InstallCatalogModel]
        }
        do {
            let (data, _) = try await URLSession.shared.data(from: url)
            let decoded = try JSONDecoder().decode(Resp.self, from: data)
            catalog = decoded.catalog
        } catch {
            self.error = "Could not load catalog: \(error.localizedDescription)"
        }
    }

    /// Streams `ollama pull` progress via /api/models/pull (SSE). Updates
    /// `pullProgress[model]` per emitted line.
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
            await refresh(base: base)
        } catch {
            pullProgress[model] = "Error: \(error.localizedDescription)"
        }
    }
}
