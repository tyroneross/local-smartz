"""API-key storage for local-smartz.

Stores per-provider secrets in the macOS Keychain via ``keyring`` when
available; falls back to a chmod-600 JSON file at ``~/.localsmartz/secrets.json``
when the Keychain is unavailable (e.g. ad-hoc-signed apps where Keychain
access requires user prompt and may fail silently).

Read API:
    list_providers()         -> [str]
    is_set(provider)         -> bool
    last_four(provider)      -> str | None
    masked_all()             -> [{"provider": str, "last_four": str | None,
                                  "set": bool, "source": "keychain"|"file"|"env"}]
    get(provider)            -> str | None
    set(provider, value)
    delete(provider)
    export_to_env()          -> int   # number of vars exported

Known providers (presets shown in the UI):
    OpenAI, Anthropic, Tavily, Serper, Brave, Exa, Cohere, OpenRouter, LangSmith
Custom providers are stored under their own name and surfaced under the
``custom`` group in the UI.
"""
from __future__ import annotations

import json
import logging
import os
import stat
from pathlib import Path

log = logging.getLogger(__name__)

KEYRING_SERVICE = "local-smartz"

# (provider key, env-var name) — env var is what tools/SDKs look for.
PRESET_PROVIDERS: list[tuple[str, str]] = [
    ("OpenAI", "OPENAI_API_KEY"),
    ("Anthropic", "ANTHROPIC_API_KEY"),
    ("Tavily", "TAVILY_API_KEY"),
    ("Serper", "SERPER_API_KEY"),
    ("Brave", "BRAVE_API_KEY"),
    ("Exa", "EXA_API_KEY"),
    ("Cohere", "COHERE_API_KEY"),
    ("OpenRouter", "OPENROUTER_API_KEY"),
    ("LangSmith", "LANGSMITH_API_KEY"),
]
PRESET_BY_NAME = dict(PRESET_PROVIDERS)


def _file_path() -> Path:
    return Path.home() / ".localsmartz" / "secrets.json"


def _index_path() -> Path:
    return Path.home() / ".localsmartz" / "secrets_index.json"


def _read_file() -> dict[str, str]:
    p = _file_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _write_file(data: dict[str, str]) -> None:
    p = _file_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)  # 0600
    os.replace(tmp, p)


def _read_index() -> list[str]:
    """Read the discovery-only provider-name index.

    The index exists because Keychain can't be enumerated by service, so a
    custom provider saved via ``set()`` would otherwise be invisible to
    ``masked_all()``/``list_providers()``. Values never live here — only names.
    """
    p = _index_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return [x for x in data if isinstance(x, str)]


def _write_index(names: list[str]) -> None:
    import builtins as _b
    p = _index_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    # Dedupe while preserving order.
    seen: _b.set[str] = _b.set()
    ordered: list[str] = []
    for n in names:
        if n not in seen:
            seen.add(n)
            ordered.append(n)
    tmp.write_text(json.dumps(ordered, indent=2))
    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)  # 0600
    os.replace(tmp, p)


def _index_add(provider: str) -> None:
    names = _read_index()
    if provider not in names:
        names.append(provider)
        _write_index(names)


def _index_remove(provider: str) -> None:
    names = _read_index()
    if provider in names:
        names = [n for n in names if n != provider]
        _write_index(names)


def _keyring():
    """Return the keyring module, or None if unavailable."""
    try:
        import keyring as _kr
        return _kr
    except Exception:  # noqa: BLE001
        return None


def _kr_get(provider: str) -> str | None:
    kr = _keyring()
    if kr is None:
        return None
    try:
        return kr.get_password(KEYRING_SERVICE, provider)
    except Exception as e:  # noqa: BLE001
        log.debug("keyring.get_password failed: %s", e)
        return None


def _kr_set(provider: str, value: str) -> bool:
    kr = _keyring()
    if kr is None:
        return False
    try:
        kr.set_password(KEYRING_SERVICE, provider, value)
        return True
    except Exception as e:  # noqa: BLE001
        log.debug("keyring.set_password failed: %s", e)
        return False


def _kr_delete(provider: str) -> bool:
    kr = _keyring()
    if kr is None:
        return False
    try:
        kr.delete_password(KEYRING_SERVICE, provider)
        return True
    except Exception:
        return False


def list_providers() -> list[str]:
    """All provider names that have a stored value (preset or custom).

    Sources considered:
    - File fallback keys (always enumerable)
    - Presets that probe positive via Keychain
    - The on-disk index (covers custom providers stored in Keychain, which
      cannot be listed by service)
    """
    import builtins as _builtins
    seen: _builtins.set[str] = _builtins.set()
    for name in _read_file():
        if is_set(name):
            seen.add(name)
    for name, _ in PRESET_PROVIDERS:
        if _kr_get(name):
            seen.add(name)
    for name in _read_index():
        if is_set(name):
            seen.add(name)
    return sorted(seen)


def get(provider: str) -> str | None:
    """Resolve a provider's value: env var > Keychain > file."""
    env_name = PRESET_BY_NAME.get(provider)
    if env_name and os.environ.get(env_name):
        return os.environ[env_name]
    kv = _kr_get(provider)
    if kv:
        return kv
    return _read_file().get(provider)


def set(provider: str, value: str) -> str:  # noqa: A001 — set is the natural verb
    """Store a value. Returns 'keychain' or 'file' to indicate where it landed.

    Also records the provider name in the on-disk index so ``masked_all()`` and
    ``list_providers()`` can discover custom providers whose values are stored
    in Keychain (which is not enumerable).
    """
    if not provider or not value:
        raise ValueError("provider and value are required")
    try:
        _index_add(provider)
    except OSError:
        # Index write is best-effort; a storage failure shouldn't prevent
        # the actual secret from being stored.
        pass
    if _kr_set(provider, value):
        return "keychain"
    # Fallback to file
    data = _read_file()
    data[provider] = value
    _write_file(data)
    return "file"


def delete(provider: str) -> None:
    _kr_delete(provider)
    data = _read_file()
    if provider in data:
        del data[provider]
        _write_file(data)
    try:
        _index_remove(provider)
    except OSError:
        pass


def is_set(provider: str) -> bool:
    return get(provider) is not None


def last_four(provider: str) -> str | None:
    val = get(provider)
    if not val:
        return None
    return val[-4:] if len(val) >= 4 else "•" * len(val)


def _source(provider: str) -> str | None:
    env_name = PRESET_BY_NAME.get(provider)
    if env_name and os.environ.get(env_name):
        return "env"
    if _kr_get(provider):
        return "keychain"
    if provider in _read_file():
        return "file"
    return None


def masked_all() -> list[dict]:
    """Return a list of all KNOWN providers (presets + any custom that exist),
    each with masked status. Safe to send over HTTP — no plaintext values.

    Discovery of custom providers uses the union of: secrets.json keys (file
    fallback) and the on-disk index (covers Keychain-stored custom providers,
    which cannot be enumerated by service). Preset order is preserved.
    """
    import builtins as _builtins
    preset_names = {p for p, _ in PRESET_PROVIDERS}
    out: list[dict] = []
    custom_pool: _builtins.set[str] = _builtins.set(_read_file().keys()) | _builtins.set(_read_index())
    custom = custom_pool - preset_names
    for name, env_name in PRESET_PROVIDERS:
        out.append({
            "provider": name,
            "env_var": env_name,
            "set": is_set(name),
            "last_four": last_four(name),
            "source": _source(name),
            "preset": True,
        })
    for name in sorted(custom):
        out.append({
            "provider": name,
            "env_var": None,
            "set": is_set(name),
            "last_four": last_four(name),
            "source": _source(name),
            "preset": False,
        })
    return out


def export_to_env() -> int:
    """Export every known preset to its env var so LangChain/SDKs see it.

    Call once at backend startup, BEFORE any tool registration that relies on
    auto-discovered API keys. Custom providers are not auto-exported (they have
    no canonical env var name).
    """
    n = 0
    for name, env_name in PRESET_PROVIDERS:
        if env_name in os.environ and os.environ[env_name]:
            continue
        val = _kr_get(name) or _read_file().get(name)
        if val:
            os.environ[env_name] = val
            n += 1
    return n
