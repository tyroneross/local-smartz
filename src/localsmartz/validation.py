"""Tool call validation middleware for lite model hardening.

Small models (8B) produce malformed tool calls: hallucinated tool names,
stringified JSON args, wrong parameter types, repeated failed calls.
This module catches these failures before they cascade.
"""

import json


# Tool schemas: {tool_name: {param_name: {"type": str, "required": bool}}}
# Only covers custom tools. DeepAgents built-ins validated by the framework.
TOOL_SCHEMAS = {
    "web_search": {
        "query": {"type": "str", "required": True},
        "max_results": {"type": "int", "required": False},
    },
    "scrape_url": {
        "url": {"type": "str", "required": True},
        "extract_tables": {"type": "bool", "required": False},
        "selector": {"type": "str", "required": False},
    },
    "parse_pdf": {
        "file_path": {"type": "str", "required": True},
        "pages": {"type": "str", "required": False},
    },
    "read_spreadsheet": {
        "file_path": {"type": "str", "required": True},
        "sheet_name": {"type": "str", "required": False},
        "max_rows": {"type": "int", "required": False},
    },
    "read_text_file": {
        "file_path": {"type": "str", "required": True},
        "max_lines": {"type": "int", "required": False},
    },
    "python_exec": {
        "code": {"type": "str", "required": True},
        "timeout": {"type": "int", "required": False},
    },
    "create_report": {
        "title": {"type": "str", "required": True},
        "sections": {"type": "list", "required": True},
        "output_path": {"type": "str", "required": True},
        "format": {"type": "str", "required": False},
        "author": {"type": "str", "required": False},
        "date": {"type": "str", "required": False},
        "subtitle": {"type": "str", "required": False},
    },
    "create_spreadsheet": {
        "data": {"type": "list", "required": True},
        "output_path": {"type": "str", "required": True},
        "sheet_name": {"type": "str", "required": False},
    },
}

# Type name → Python types that are acceptable
_TYPE_MAP = {
    "str": (str,),
    "int": (int, float),  # Accept float for int (model might send 5.0)
    "bool": (bool,),
    "list": (list,),
}


def normalize_args(args) -> dict:
    """Normalize tool call arguments — handle stringified JSON.

    Small models sometimes send args as a JSON string instead of a dict.
    Returns a dict on success, or the original value if parsing fails.
    """
    if isinstance(args, dict):
        return args
    if isinstance(args, str):
        try:
            parsed = json.loads(args)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
    return args if isinstance(args, dict) else {}


def validate_tool_call(
    tool_call: dict,
    available_tools: list[str],
) -> tuple[bool, str, dict]:
    """Validate a tool call before execution.

    Args:
        tool_call: Dict with "name" and "args" keys
        available_tools: List of tool names available to the agent

    Returns:
        Tuple of (is_valid, error_message, normalized_args)
    """
    name = tool_call.get("name", "")

    # Check tool exists
    if not name:
        return False, "Tool call missing 'name' field.", {}

    if name not in available_tools:
        # Check for common hallucination patterns
        suggestion = _suggest_tool(name, available_tools)
        msg = f"Tool '{name}' does not exist."
        if suggestion:
            msg += f" Did you mean '{suggestion}'?"
        msg += f" Available tools: {', '.join(available_tools)}"
        return False, msg, {}

    # Normalize args (handle stringified JSON)
    raw_args = tool_call.get("args", {})
    args = normalize_args(raw_args)

    if not isinstance(args, dict):
        return False, f"Arguments for '{name}' must be a dict, got {type(args).__name__}.", {}

    # Schema validation (only for custom tools we have schemas for)
    schema = TOOL_SCHEMAS.get(name)
    if schema:
        # Check required params
        for param, spec in schema.items():
            if spec["required"] and param not in args:
                return False, f"Tool '{name}' requires parameter '{param}'.", args

        # Type-check provided params
        for param, value in args.items():
            if param in schema:
                expected_type = schema[param]["type"]
                allowed_types = _TYPE_MAP.get(expected_type)
                if allowed_types and not isinstance(value, allowed_types):
                    # Special case: str value for list param might be JSON
                    if expected_type == "list" and isinstance(value, str):
                        try:
                            parsed = json.loads(value)
                            if isinstance(parsed, list):
                                args[param] = parsed
                                continue
                        except (json.JSONDecodeError, TypeError):
                            pass
                    return (
                        False,
                        f"Parameter '{param}' for '{name}' should be {expected_type}, got {type(value).__name__}.",
                        args,
                    )

    return True, "", args


def _suggest_tool(name: str, available: list[str]) -> str | None:
    """Suggest a tool name for common hallucination patterns."""
    name_lower = name.lower().replace("-", "_").replace(" ", "_")

    # Direct substring match
    for tool in available:
        if name_lower in tool.lower() or tool.lower() in name_lower:
            return tool

    # Common hallucinations → correct name
    aliases = {
        "search": "web_search",
        "search_web": "web_search",
        "google": "web_search",
        "browse": "scrape_url",
        "fetch_url": "scrape_url",
        "get_url": "scrape_url",
        "fetch": "scrape_url",
        "read_pdf": "parse_pdf",
        "pdf": "parse_pdf",
        "read_excel": "read_spreadsheet",
        "excel": "read_spreadsheet",
        "read_file": "read_text_file",
        "execute": "python_exec",
        "python": "python_exec",
        "exec": "python_exec",
        "run_python": "python_exec",
        "run_code": "python_exec",
        "report": "create_report",
        "write_report": "create_report",
        "spreadsheet": "create_spreadsheet",
    }

    suggested = aliases.get(name_lower)
    if suggested and suggested in available:
        return suggested

    return None


class TurnCallDeduplicator:
    """Per-turn deduplication of tool calls.

    Maintains a set of (tool_name, sorted_args_tuple) keys for tool calls
    already dispatched within the current user-message turn. If the model
    emits the same call twice, the second emission is a cache hit: the caller
    should return the first result without re-running the tool.

    The cache resets on ``next_turn()`` — it MUST NOT leak across user messages
    or multi-turn UX breaks (a legitimate repeat of a prior turn's search would
    be suppressed).

    Usage::

        dedup = TurnCallDeduplicator()
        result, is_dup = dedup.check_and_record("web_search", {"query": "foo"}, result=None)
        # is_dup is False on first call; True on identical second call.
        # On hit, result is the cached value from the first invocation.
    """

    def __init__(self) -> None:
        self._cache: dict[tuple, str] = {}

    @staticmethod
    def _make_key(tool_name: str, args: dict | None) -> tuple:
        """Stable, hashable key for a (name, args) pair."""
        if not isinstance(args, dict):
            args = {}
        try:
            sorted_items: tuple = tuple(
                sorted((str(k), str(v)) for k, v in args.items())
            )
        except Exception:  # noqa: BLE001
            sorted_items = ()
        return (tool_name, sorted_items)

    def check_and_record(
        self,
        tool_name: str,
        args: dict | None,
        result: str,
    ) -> tuple[str, bool]:
        """Record a tool call and return (effective_result, is_duplicate).

        On first call: stores result in cache, returns (result, False).
        On duplicate call: returns (cached_result, True) — caller can skip
        actual tool execution and emit a cached-result event instead.
        """
        key = self._make_key(tool_name, args)
        if key in self._cache:
            return self._cache[key], True
        self._cache[key] = result
        return result, False

    def is_duplicate(self, tool_name: str, args: dict | None) -> bool:
        """Check without recording. True if the call was already dispatched."""
        return self._make_key(tool_name, args) in self._cache

    def next_turn(self) -> None:
        """Reset the per-turn cache. Call at the start of each new user message."""
        self._cache.clear()

    @property
    def call_count(self) -> int:
        return len(self._cache)


class IntentAnchor:
    """Persists the original user prompt and provides a re-injection message
    when a tool error occurs mid-turn.

    The agent tends to forget the original task after a failed tool call —
    emitting a generic 'What would you like me to help you with?' rather than
    retrying or responding to the actual request. By re-injecting the original
    prompt as a system note at the next generation step, we keep the agent
    anchored to the user's intent.

    Usage::

        anchor = IntentAnchor("draft a PowerPoint on Arista vs Juniper")
        msg = anchor.recovery_message()
        # → "Tool call failed. The user's original request was: ..."
    """

    _RECOVERY_TEMPLATE = (
        "Tool call failed. The user's original request was: {prompt}. "
        "Try again with a valid tool or respond directly."
    )

    def __init__(self, original_prompt: str) -> None:
        self.original_prompt = original_prompt
        self._error_count: int = 0

    def record_error(self) -> None:
        """Increment the error counter. Used for diagnostics / rate gating."""
        self._error_count += 1

    @property
    def error_count(self) -> int:
        return self._error_count

    def recovery_message(self) -> str:
        """Return the system re-injection message referencing the original prompt."""
        return self._RECOVERY_TEMPLATE.format(prompt=self.original_prompt)

    def should_inject(self) -> bool:
        """True when at least one tool error has been recorded."""
        return self._error_count > 0


class LoopDetector:
    """Detect when the agent is stuck calling the same tool repeatedly.

    Dual threshold:
    - Strict: max_repeats consecutive calls with same tool+args (exact loop)
    - Lenient: max_name_repeats consecutive calls to same tool name (stuck pattern)

    The lenient threshold catches models that keep refining queries to the
    same tool instead of progressing to a different tool.
    """

    def __init__(self, max_repeats: int = 3, max_name_repeats: int = 5):
        self.max_repeats = max_repeats
        self.max_name_repeats = max_name_repeats
        self._history: list[str] = []
        self._history_with_args: list[tuple[str, str]] = []

    def record(self, tool_name: str, args: dict | None = None) -> bool:
        """Record a tool call. Returns True if stuck in a loop.

        Detects both exact loops (same tool+args) and stuck patterns
        (same tool name with varied args).
        """
        self._history.append(tool_name)

        # Create a stable key from args for comparison
        args_key = ""
        if args:
            try:
                args_key = json.dumps(args, sort_keys=True, default=str)[:200]
            except (TypeError, ValueError):
                args_key = str(args)[:200]

        self._history_with_args.append((tool_name, args_key))

        # Strict check: same tool + same args
        if len(self._history_with_args) >= self.max_repeats:
            recent = self._history_with_args[-self.max_repeats:]
            if len(set(recent)) == 1:
                return True

        # Lenient check: same tool name regardless of args
        if len(self._history) >= self.max_name_repeats:
            recent_names = self._history[-self.max_name_repeats:]
            if len(set(recent_names)) == 1:
                return True

        return False

    def reset(self):
        self._history.clear()
        self._history_with_args.clear()

    @property
    def last_tool(self) -> str | None:
        return self._history[-1] if self._history else None


def check_output_quality(response: str, prompt: str, min_length: int = 20) -> tuple[bool, str]:
    """Basic output quality check.

    Args:
        response: The agent's final response text
        prompt: The original user prompt
        min_length: Minimum acceptable response length

    Returns:
        Tuple of (is_acceptable, issue_description)
    """
    if not response or not response.strip():
        return False, "empty_response"

    if len(response.strip()) < min_length:
        return False, "too_short"

    # Check keyword overlap — does the response address the prompt?
    prompt_words = set(prompt.lower().split())
    response_words = set(response.lower().split())

    # Remove common stop words
    stop_words = {"the", "a", "an", "is", "are", "was", "were", "what", "how", "why",
                  "when", "where", "which", "who", "of", "in", "on", "at", "to", "for",
                  "and", "or", "but", "not", "with", "this", "that", "it", "be", "do"}
    prompt_keywords = prompt_words - stop_words
    response_keywords = response_words - stop_words

    if prompt_keywords and not prompt_keywords & response_keywords:
        return False, "off_topic"

    return True, ""
