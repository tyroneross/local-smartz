import SwiftUI

// MARK: - Eval tab (2026-04-23 phase-2 follow-up, Item 6)
//
// "Run eval suite" button POSTs /api/evals/run against the active provider
// (or a user-picked one) and shows pass/fail + latency per task. The
// grader is a simple substring check — this is a smoke test for the
// cloud-toggle wiring, not a benchmark. Results include raw reply so the
// user can see what the model actually produced.

private struct EvalTaskResult: Decodable, Identifiable {
    let task: String
    let ok: Bool
    let latencyMs: Int
    let reply: String
    let error: String?
    var id: String { task }

    enum CodingKeys: String, CodingKey {
        case task
        case ok
        case latencyMs = "latency_ms"
        case reply
        case error
    }
}

private struct EvalRunResponse: Decodable {
    let provider: String
    let model: String
    let pass: Int
    let fail: Int
    let results: [EvalTaskResult]
}

@MainActor
private final class EvalsVM: ObservableObject {
    @Published var provider: String = "ollama"
    @Published var model: String = ""
    @Published var running = false
    @Published var error: String?
    @Published var result: EvalRunResponse?

    let providers = ["ollama", "anthropic", "openai", "groq"]

    func run() async {
        guard let base = await SettingsBackend.discover() else {
            error = "Backend not reachable."
            return
        }
        running = true
        defer { running = false }
        error = nil
        result = nil

        let url = URL(string: "\(base)/api/evals/run")!
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.timeoutInterval = 600
        var body: [String: Any] = ["provider": provider]
        let trimmed = model.trimmingCharacters(in: .whitespacesAndNewlines)
        if !trimmed.isEmpty { body["model"] = trimmed }
        req.httpBody = try? JSONSerialization.data(withJSONObject: body)
        do {
            let (data, resp) = try await URLSession.shared.data(for: req)
            if let http = resp as? HTTPURLResponse, http.statusCode != 200 {
                let msg = String(data: data, encoding: .utf8) ?? ""
                error = "Run failed (HTTP \(http.statusCode)): \(msg)"
                return
            }
            self.result = try JSONDecoder().decode(EvalRunResponse.self, from: data)
        } catch {
            self.error = "Run failed: \(error.localizedDescription)"
        }
    }
}

struct EvalTab: View {
    @StateObject private var vm = EvalsVM()

    var body: some View {
        ScrollView {
            SettingsTabsForm {
                header

                Text(
                    "Runs a fixed set of small deterministic tasks against the "
                    + "chosen provider. Use after configuring a new cloud provider "
                    + "or switching the local model to catch wiring regressions."
                )
                .font(.system(size: 11))
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)

                Divider().padding(.vertical, 2)

                SettingsTabsRow("Provider") {
                    Picker("Provider", selection: $vm.provider) {
                        ForEach(vm.providers, id: \.self) { prov in
                            Text(prov).tag(prov)
                        }
                    }
                    .pickerStyle(.segmented)
                }

                SettingsTabsRow("Model (optional)") {
                    TextField("default for provider", text: $vm.model)
                        .textFieldStyle(.roundedBorder)
                        .font(.system(size: 12, design: .monospaced))
                }

                HStack {
                    Spacer()
                    Button("Run eval suite") { Task { await vm.run() } }
                        .controlSize(.small)
                        .keyboardShortcut(.defaultAction)
                        .disabled(vm.running)
                    if vm.running { ProgressView().controlSize(.small) }
                }

                if let err = vm.error {
                    Text(err)
                        .font(.system(size: 11))
                        .foregroundStyle(.red)
                }

                if let r = vm.result {
                    Divider().padding(.vertical, 2)

                    HStack(alignment: .firstTextBaseline, spacing: 10) {
                        Text("\(r.provider) · \(r.model)")
                            .font(.system(size: 12, design: .monospaced))
                            .foregroundStyle(.secondary)
                        Text("\(r.pass) passed")
                            .font(.system(size: 12, weight: .medium))
                            .foregroundStyle(.green)
                        Text("\(r.fail) failed")
                            .font(.system(size: 12, weight: .medium))
                            .foregroundStyle(r.fail > 0 ? Color.red : Color.secondary)
                    }

                    ForEach(r.results) { t in
                        VStack(alignment: .leading, spacing: 2) {
                            HStack(spacing: 8) {
                                Image(systemName: t.ok ? "checkmark.circle.fill" : "xmark.circle.fill")
                                    .foregroundStyle(t.ok ? .green : .red)
                                Text(t.task)
                                    .font(.system(size: 12, weight: .medium))
                                Spacer()
                                Text("\(t.latencyMs) ms")
                                    .font(.system(size: 11))
                                    .foregroundStyle(.tertiary)
                            }
                            if let err = t.error, !err.isEmpty {
                                Text("Error: \(err)")
                                    .font(.system(size: 11, design: .monospaced))
                                    .foregroundStyle(.red)
                                    .lineLimit(2)
                                    .truncationMode(.tail)
                            } else if !t.reply.isEmpty {
                                Text(t.reply)
                                    .font(.system(size: 11, design: .monospaced))
                                    .foregroundStyle(.secondary)
                                    .lineLimit(3)
                                    .truncationMode(.tail)
                                    .textSelection(.enabled)
                            }
                        }
                        .padding(.vertical, 2)
                    }
                }
            }
        }
    }

    private var header: some View {
        HStack {
            Text("Eval suite")
                .font(.system(size: 13, weight: .medium))
            Spacer()
        }
    }
}
