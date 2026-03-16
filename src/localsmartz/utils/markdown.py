"""Markdown formatting helpers."""


def heading(text: str, level: int = 1) -> str:
    return f"{'#' * level} {text}"


def bold(text: str) -> str:
    return f"**{text}**"


def italic(text: str) -> str:
    return f"*{text}*"


def code_block(text: str, language: str = "") -> str:
    return f"```{language}\n{text}\n```"


def table(headers: list[str], rows: list[list[str]]) -> str:
    """Create a markdown table from headers and rows."""
    if not headers:
        return ""

    col_count = len(headers)
    lines = ["| " + " | ".join(headers) + " |"]
    lines.append("| " + " | ".join(["---"] * col_count) + " |")

    for row in rows:
        padded = row + [""] * (col_count - len(row))
        cleaned = [str(cell).replace("|", "\\|").replace("\n", " ") for cell in padded[:col_count]]
        lines.append("| " + " | ".join(cleaned) + " |")

    return "\n".join(lines)


def bullet_list(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def numbered_list(items: list[str]) -> str:
    return "\n".join(f"{i}. {item}" for i, item in enumerate(items, 1))


def blockquote(text: str) -> str:
    lines = text.split("\n")
    return "\n".join(f"> {line}" for line in lines)


def link(text: str, url: str) -> str:
    return f"[{text}]({url})"


def divider() -> str:
    return "\n---\n"
