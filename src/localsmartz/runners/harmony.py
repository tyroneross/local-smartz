"""Harmony-format token parser for gpt-oss family models.

gpt-oss:20b emits tool calls in OpenAI Harmony format:

    assistant<|channel|>commentary<|message|>functions=web_search(query="X", max_results=10)

This module strips those markers so downstream validation sees a clean tool
name, and recovers the args string when the full call form is present.
"""
from __future__ import annotations

import re

# Harmony control tokens emitted by gpt-oss family models.
_HARMONY_TOKENS = re.compile(
    r"<\|(?:channel|message|start|end|return|constrain|call)\|>"
)

# Full call pattern:
#   assistant<|channel|>(commentary|analysis|final)<|message|>functions=NAME(args)
_HARMONY_CALL_RE = re.compile(
    r"assistant"
    r"<\|channel\|>"
    r"(?:commentary|analysis|final)"
    r"<\|message\|>"
    r"functions="
    r"([A-Za-z_][A-Za-z0-9_]*)"   # group 1: tool name
    r"(?:\(([^)]*)\))?",           # group 2: args (optional)
    re.DOTALL,
)

# Bare wrapper (no args):
#   assistant<|channel|>...<|message|>functions=NAME
_HARMONY_BARE_RE = re.compile(
    r"assistant"
    r"<\|channel\|>"
    r"[^<]*"
    r"<\|message\|>"
    r"functions="
    r"([A-Za-z_][A-Za-z0-9_]*)",
    re.DOTALL,
)


def strip_harmony_tokens(text: str) -> str:
    """Strip Harmony control tokens from a candidate tool-name string.

    If *text* is a bare Harmony-wrapped name like::

        assistant<|channel|>commentary<|message|>functions=web_search

    the function returns ``"web_search"``.

    If *text* contains a full call form (with args), the name is still
    extracted and returned without the args — use
    :func:`extract_harmony_tool_call` to recover args.

    If *text* contains no Harmony markers, it is returned unchanged.
    """
    if "<|" not in text:
        return text

    # Try full call form first (has parens with args).
    m = _HARMONY_CALL_RE.search(text)
    if m:
        return m.group(1)

    # Try bare wrapper.
    m = _HARMONY_BARE_RE.search(text)
    if m:
        return m.group(1)

    # Fallback: strip all known control tokens and clean up whitespace.
    cleaned = _HARMONY_TOKENS.sub("", text).strip()
    return cleaned


def extract_harmony_tool_call(text: str) -> tuple[str, str] | None:
    """Parse a full Harmony call string into (tool_name, raw_args_str).

    Expected input::

        assistant<|channel|>commentary<|message|>functions=web_search(query="foo", max_results=10)

    Returns ``("web_search", 'query="foo", max_results=10')`` — the args
    string is the raw substring inside the outermost parentheses, NOT parsed
    JSON.

    Returns ``None`` if *text* does not match the Harmony call pattern or if
    there are no args parentheses.
    """
    if "<|" not in text:
        return None

    m = _HARMONY_CALL_RE.search(text)
    if m and m.group(2) is not None:
        return (m.group(1), m.group(2))

    return None
