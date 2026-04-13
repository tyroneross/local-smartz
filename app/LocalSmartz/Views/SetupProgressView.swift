import SwiftUI

/// Download progress for a single Ollama model, driven by
/// `SetupSSEClient.startSetup()` events. `percent == nil` indicates the
/// backend has started the pull but has not reported discrete progress yet;
/// we render this as an indeterminate bar.
struct ModelDownloadProgress: Identifiable, Equatable {
    let id: String   // model name, e.g. "llama3.1:8b"
    var percent: Int?
    var downloadedMB: Int
    var totalMB: Int
    var isComplete: Bool

    init(
        id: String,
        percent: Int? = nil,
        downloadedMB: Int = 0,
        totalMB: Int = 0,
        isComplete: Bool = false
    ) {
        self.id = id
        self.percent = percent
        self.downloadedMB = downloadedMB
        self.totalMB = totalMB
        self.isComplete = isComplete
    }
}

/// Calm Precision-aligned progress panel. A single border wraps all
/// in-progress model rows. Dividers separate rows; no per-row badges. Status
/// is conveyed by text color + weight, not chips. Typography follows the
/// 8pt grid with 12–14pt metadata.
struct SetupProgressView: View {
    let progresses: [ModelDownloadProgress]
    let currentStep: String?
    let isComplete: Bool
    let errorMessage: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            if !progresses.isEmpty {
                VStack(spacing: 0) {
                    ForEach(Array(progresses.enumerated()), id: \.element.id) { index, progress in
                        progressRow(progress)
                            .padding(.vertical, 12)
                            .padding(.horizontal, 16)
                        if index < progresses.count - 1 {
                            Divider()
                        }
                    }
                }
                .overlay(
                    RoundedRectangle(cornerRadius: 8)
                        .stroke(Color.secondary.opacity(0.25), lineWidth: 1)
                )
            }

            if let step = currentStep, !step.isEmpty {
                Text(step)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            if isComplete {
                Text("All required models are ready.")
                    .font(.caption)
                    .foregroundStyle(.green)
            }

            if let error = errorMessage, !error.isEmpty {
                Text(error)
                    .font(.caption)
                    .foregroundStyle(.red)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .frame(maxWidth: 480)
    }

    private func progressRow(_ progress: ModelDownloadProgress) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline) {
                Text(progress.id)
                    .font(.system(size: 14, weight: .medium))
                    .foregroundStyle(progress.isComplete ? .green : .primary)

                Spacer()

                Text(statusText(for: progress))
                    .font(.system(size: 12))
                    .foregroundStyle(.secondary)
                    .monospacedDigit()
            }

            progressBar(for: progress)
        }
    }

    @ViewBuilder
    private func progressBar(for progress: ModelDownloadProgress) -> some View {
        if progress.isComplete {
            ProgressView(value: 1.0)
                .progressViewStyle(.linear)
                .tint(.green)
        } else if let percent = progress.percent {
            let fraction = max(0.0, min(1.0, Double(percent) / 100.0))
            ProgressView(value: fraction)
                .progressViewStyle(.linear)
        } else {
            // Indeterminate: backend hasn't reported discrete progress yet.
            ProgressView()
                .progressViewStyle(.linear)
        }
    }

    private func statusText(for progress: ModelDownloadProgress) -> String {
        if progress.isComplete {
            return "Ready"
        }
        if let percent = progress.percent {
            if progress.totalMB > 0 {
                return "\(percent)%  \(progress.downloadedMB) / \(progress.totalMB) MB"
            }
            return "\(percent)%"
        }
        return "Downloading..."
    }
}

#Preview {
    SetupProgressView(
        progresses: [
            ModelDownloadProgress(id: "llama3.1:8b", percent: 42, downloadedMB: 1700, totalMB: 4100),
            ModelDownloadProgress(id: "qwen2.5:3b", percent: nil),
            ModelDownloadProgress(id: "nomic-embed-text", isComplete: true),
        ],
        currentStep: "Downloading llama3.1:8b...",
        isComplete: false,
        errorMessage: nil
    )
    .padding(32)
    .frame(width: 560)
}
