import SwiftUI

/// Slim, non-blocking pipeline-phase indicator rendered under the
/// ResearchView toolbar. When ``phase`` is nil the view collapses to
/// zero height so the surrounding VStack layout is unchanged.
///
/// Design notes:
/// - Muted material background, ≤32pt tall, single-line HStack.
/// - Spinner is only shown while ``isStreaming`` is true; the phase
///   label itself stays visible briefly after a stage transition so
///   the user sees the most recent phase without flicker.
struct StatusBanner: View {
    let phase: String?
    var model: String? = nil
    var isStreaming: Bool = false

    var body: some View {
        if let phase, !phase.isEmpty {
            HStack(spacing: 8) {
                Text(phase)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.tail)

                if let model, !model.isEmpty {
                    Text("· \(model)")
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                        .lineLimit(1)
                }

                Spacer(minLength: 0)

                if isStreaming {
                    ProgressView()
                        .controlSize(.mini)
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 6)
            .frame(maxWidth: .infinity, minHeight: 24, maxHeight: 32, alignment: .leading)
            .background(.thinMaterial)
            .accessibilityElement(children: .combine)
            .accessibilityLabel("Current phase: \(phase)")
        } else {
            EmptyView()
        }
    }
}

#if ENABLE_PREVIEWS
#Preview("With phase + streaming") {
    StatusBanner(phase: "🔍 Searching", model: "llama3.1:8b", isStreaming: true)
        .frame(width: 600)
}

#Preview("Idle phase") {
    StatusBanner(phase: "✍ Writing", isStreaming: false)
        .frame(width: 600)
}

#Preview("Nil (zero height)") {
    StatusBanner(phase: nil)
        .frame(width: 600)
        .border(.red)
}
#endif
