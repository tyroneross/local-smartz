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

private struct AgentScorecardRow: Decodable, Identifiable {
    let name: String
    let actualRuntime: String
    let runtimeOk: Bool
    let actualRoles: [String]
    let rolesOk: Bool
    let recommendedPattern: String
    let patternAvailable: Bool
    let patternTierOk: Bool
    let score: Double
    var id: String { name }

    enum CodingKeys: String, CodingKey {
        case name
        case actualRuntime = "actual_runtime"
        case runtimeOk = "runtime_ok"
        case actualRoles = "actual_roles"
        case rolesOk = "roles_ok"
        case recommendedPattern = "recommended_pattern"
        case patternAvailable = "pattern_available"
        case patternTierOk = "pattern_tier_ok"
        case score
    }
}

private struct AgentScorecardCheck: Decodable, Identifiable {
    let name: String
    let category: String
    let ok: Bool
    let score: Double
    let evidence: String
    var id: String { name }
}

private struct AgentScorecardResponse: Decodable {
    let score: Double
    let grade: String
    let tier: String
    let pass: Int
    let fail: Int
    let checkPass: Int
    let checkFail: Int
    let rows: [AgentScorecardRow]
    let checks: [AgentScorecardCheck]

    enum CodingKeys: String, CodingKey {
        case score
        case grade
        case tier
        case pass
        case fail
        case checkPass = "check_pass"
        case checkFail = "check_fail"
        case rows
        case checks
    }
}

@MainActor
private final class EvalsVM: ObservableObject {
    @Published var provider: String = "ollama"
    @Published var model: String = ""
    @Published var running = false
    @Published var scorecardRunning = false
    @Published var error: String?
    @Published var result: EvalRunResponse?
    @Published var scorecardError: String?
    @Published var scorecard: AgentScorecardResponse?

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

    func runAgentScorecard() async {
        guard let base = await SettingsBackend.discover() else {
            scorecardError = "Backend not reachable."
            return
        }
        scorecardRunning = true
        defer { scorecardRunning = false }
        scorecardError = nil
        scorecard = nil

        let url = URL(string: "\(base)/api/evals/agent-scorecard")!
        var req = URLRequest(url: url)
        req.timeoutInterval = 60
        do {
            let (data, resp) = try await URLSession.shared.data(for: req)
            if let http = resp as? HTTPURLResponse, http.statusCode != 200 {
                let msg = String(data: data, encoding: .utf8) ?? ""
                scorecardError = "Scorecard failed (HTTP \(http.statusCode)): \(msg)"
                return
            }
            self.scorecard = try JSONDecoder().decode(AgentScorecardResponse.self, from: data)
        } catch {
            self.scorecardError = "Scorecard failed: \(error.localizedDescription)"
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
                .font(.system(size: 13))
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
                        .font(.system(size: 14, design: .monospaced))
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
                        .font(.system(size: 13))
                        .foregroundStyle(.red)
                }

                if let r = vm.result {
                    Divider().padding(.vertical, 2)

                    HStack(alignment: .firstTextBaseline, spacing: 10) {
                        Text("\(r.provider) · \(r.model)")
                            .font(.system(size: 14, design: .monospaced))
                            .foregroundStyle(.secondary)
                        Text("\(r.pass) passed")
                            .font(.system(size: 14, weight: .medium))
                            .foregroundStyle(.green)
                        Text("\(r.fail) failed")
                            .font(.system(size: 14, weight: .medium))
                            .foregroundStyle(r.fail > 0 ? Color.red : Color.secondary)
                    }

                    ForEach(r.results) { t in
                        VStack(alignment: .leading, spacing: 2) {
                            HStack(spacing: 8) {
                                Image(systemName: t.ok ? "checkmark.circle.fill" : "xmark.circle.fill")
                                    .foregroundStyle(t.ok ? .green : .red)
                                    .accessibilityLabel(t.ok ? "Passed" : "Failed")
                                Text(t.task)
                                    .font(.system(size: 14, weight: .medium))
                                Spacer()
                                Text("\(t.latencyMs) ms")
                                    .font(.system(size: 13))
                                    .foregroundStyle(.tertiary)
                            }
                            if let err = t.error, !err.isEmpty {
                                Text("Error: \(err)")
                                    .font(.system(size: 13, design: .monospaced))
                                    .foregroundStyle(.red)
                                    .fixedSize(horizontal: false, vertical: true)
                            } else if !t.reply.isEmpty {
                                Text(t.reply)
                                    .font(.system(size: 13, design: .monospaced))
                                    .foregroundStyle(.secondary)
                                    .lineLimit(3)
                                    .truncationMode(.tail)
                                    .textSelection(.enabled)
                            }
                        }
                        .padding(.vertical, 2)
                    }
                }

                Divider().padding(.vertical, 2)

                HStack {
                    Text("Agent scorecard")
                        .font(.system(size: 15, weight: .medium))
                    Spacer()
                    Button("Run agent scorecard") {
                        Task { await vm.runAgentScorecard() }
                    }
                    .controlSize(.small)
                    .disabled(vm.scorecardRunning)
                    if vm.scorecardRunning { ProgressView().controlSize(.small) }
                }

                if let err = vm.scorecardError {
                    Text(err)
                        .font(.system(size: 13))
                        .foregroundStyle(.red)
                }

                if let scorecard = vm.scorecard {
                    HStack(alignment: .firstTextBaseline, spacing: 10) {
                        Text("\(scorecard.grade.uppercased()) · \(String(format: "%.1f", scorecard.score))")
                            .font(.system(size: 14, weight: .medium, design: .monospaced))
                            .foregroundStyle(scorecard.grade == "pass" ? .green : .orange)
                        Text("tier \(scorecard.tier)")
                            .font(.system(size: 13))
                            .foregroundStyle(.secondary)
                        Text("\(scorecard.pass) tasks passed")
                            .font(.system(size: 13))
                            .foregroundStyle(.secondary)
                        Text("\(scorecard.checkPass) checks passed")
                            .font(.system(size: 13))
                            .foregroundStyle(.secondary)
                    }

                    ForEach(scorecard.rows) { row in
                        HStack(spacing: 8) {
                            Image(systemName: (row.runtimeOk && row.rolesOk && row.patternAvailable && row.patternTierOk) ? "checkmark.circle.fill" : "xmark.circle.fill")
                                .foregroundStyle((row.runtimeOk && row.rolesOk && row.patternAvailable && row.patternTierOk) ? .green : .red)
                                .accessibilityLabel((row.runtimeOk && row.rolesOk && row.patternAvailable && row.patternTierOk) ? "Passed" : "Failed")
                            VStack(alignment: .leading, spacing: 1) {
                                Text(row.name)
                                    .font(.system(size: 14, weight: .medium))
                                Text("\(row.actualRuntime) · \(row.recommendedPattern) · \(rolesText(row.actualRoles))")
                                    .font(.system(size: 12, design: .monospaced))
                                    .foregroundStyle(.secondary)
                                    .lineLimit(1)
                                    .truncationMode(.middle)
                            }
                            Spacer()
                            Text(String(format: "%.0f", row.score))
                                .font(.system(size: 13, design: .monospaced))
                                .foregroundStyle(.tertiary)
                        }
                        .padding(.vertical, 1)
                    }

                    Divider().padding(.vertical, 2)

                    ForEach(scorecard.checks) { check in
                        HStack(spacing: 8) {
                            Image(systemName: check.ok ? "checkmark.circle.fill" : "xmark.circle.fill")
                                .foregroundStyle(check.ok ? .green : .red)
                                .accessibilityLabel(check.ok ? "Passed" : "Failed")
                            VStack(alignment: .leading, spacing: 1) {
                                Text(check.name)
                                    .font(.system(size: 14, weight: .medium))
                                Text(check.category)
                                    .font(.system(size: 12, design: .monospaced))
                                    .foregroundStyle(.secondary)
                            }
                            Spacer()
                            Text(String(format: "%.0f", check.score))
                                .font(.system(size: 13, design: .monospaced))
                                .foregroundStyle(.tertiary)
                        }
                        .padding(.vertical, 1)
                    }
                }
            }
        }
    }

    private var header: some View {
        HStack {
            Text("Eval suite")
                .font(.system(size: 15, weight: .medium))
            Spacer()
        }
    }

    private func rolesText(_ roles: [String]) -> String {
        roles.isEmpty ? "no roles" : roles.joined(separator: ",")
    }
}
