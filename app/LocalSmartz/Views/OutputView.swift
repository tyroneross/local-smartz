import SwiftUI

struct OutputView: View {
    let outputText: String
    let toolCalls: [ToolCallEntry]
    let isStreaming: Bool

    var streamingStatus: String {
        if toolCalls.isEmpty { return "Thinking…" }
        if let last = toolCalls.last {
            switch last.name {
            case "web_search": return "Searching the web…"
            case "scrape_url": return "Reading a page…"
            case "parse_pdf":  return "Reading a PDF…"
            case "python_exec": return "Running calculations…"
            case "create_report", "create_spreadsheet": return "Writing the report…"
            case "read_text_file", "read_spreadsheet": return "Reading a file…"
            default:
                if last.name.starts(with: "plugin_") { return "Running plugin command…" }
                if last.name.starts(with: "mcp_") { return "Calling MCP tool…" }
                return "Working…"
            }
        }
        return "Researching…"
    }

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

                    // Research output — render markdown natively so bold,
                    // italics, links, and inline code display as formatted
                    // text. `.inlineOnlyPreservingWhitespace` keeps newlines
                    // so lists/headings remain readable even though their
                    // block styling isn't applied.
                    if !outputText.isEmpty {
                        if let attributed = try? AttributedString(
                            markdown: outputText,
                            options: AttributedString.MarkdownParsingOptions(
                                interpretedSyntax: .inlineOnlyPreservingWhitespace
                            )
                        ) {
                            Text(attributed)
                                .font(.body)
                                .textSelection(.enabled)
                                .frame(maxWidth: .infinity, alignment: .leading)
                        } else {
                            Text(outputText)
                                .font(.body)
                                .textSelection(.enabled)
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }

                    if isStreaming {
                        HStack(spacing: 8) {
                            ProgressView()
                                .controlSize(.small)
                            VStack(alignment: .leading, spacing: 1) {
                                Text(streamingStatus)
                                    .font(.system(size: 13, weight: .medium))
                                    .foregroundStyle(.primary)
                                if let last = toolCalls.last {
                                    Text("Last step: \(last.display)")
                                        .font(.system(size: 11, design: .monospaced))
                                        .foregroundStyle(.secondary)
                                }
                            }
                        }
                        .padding(.top, 4)
                    }

                    // Scroll anchor
                    Color.clear
                        .frame(height: 1)
                        .id("bottom")
                }
                .frame(maxWidth: .infinity, alignment: .leading)
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

