import SwiftUI

struct OutputView: View {
    let outputText: String
    let toolCalls: [ToolCallEntry]
    let isStreaming: Bool

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                VStack(alignment: .leading, spacing: 8) {
                    // Tool activity
                    ForEach(Array(toolCalls.enumerated()), id: \.offset) { _, entry in
                        HStack(spacing: 4) {
                            Text("\u{25B8}")
                                .foregroundStyle(.secondary)
                            Text(entry.display)
                                .font(.system(.caption, design: .monospaced))
                                .foregroundStyle(entry.isError ? .red : .secondary)
                        }
                    }

                    // Research output
                    if !outputText.isEmpty {
                        Text(outputText)
                            .font(.body)
                            .textSelection(.enabled)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }

                    if isStreaming {
                        HStack(spacing: 6) {
                            ProgressView()
                                .controlSize(.small)
                            Text("Researching...")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }

                    // Scroll anchor
                    Color.clear
                        .frame(height: 1)
                        .id("bottom")
                }
                .padding()
            }
            .onChange(of: outputText) {
                withAnimation {
                    proxy.scrollTo("bottom", anchor: .bottom)
                }
            }
            .onChange(of: toolCalls.count) {
                withAnimation {
                    proxy.scrollTo("bottom", anchor: .bottom)
                }
            }
        }
    }
}

struct ToolCallEntry: Equatable {
    let name: String
    let message: String
    let isError: Bool

    var display: String {
        if isError {
            return "\(name): \(message)"
        }
        return name
    }
}
