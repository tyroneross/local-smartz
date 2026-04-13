"""Tests for localsmartz.secrets.

Hermetic: HOME is redirected into tmp_path, and the keyring module is forced
to None so the file fallback path is always exercised (no real Keychain use).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


@pytest.fixture
def secrets_mod(tmp_path, monkeypatch):
    """Import localsmartz.secrets with HOME redirected and keyring disabled."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    # Clear any preset env vars so is_set() reflects only stored state.
    from localsmartz import secrets as _secrets
    for _name, env_name in _secrets.PRESET_PROVIDERS:
        monkeypatch.delenv(env_name, raising=False)
    # Force file fallback by making the keyring lookup return None.
    monkeypatch.setattr("localsmartz.secrets._keyring", lambda: None)
    return _secrets


def test_set_get_round_trip(secrets_mod):
    assert secrets_mod.get("OpenAI") is None
    source = secrets_mod.set("OpenAI", "sk-test-abcd1234")
    assert source == "file"
    assert secrets_mod.get("OpenAI") == "sk-test-abcd1234"


def test_masked_all_on_empty_store(secrets_mod):
    rows = secrets_mod.masked_all()
    # All presets present, all unset.
    preset_names = {n for n, _ in secrets_mod.PRESET_PROVIDERS}
    got_names = {r["provider"] for r in rows}
    assert preset_names.issubset(got_names)
    for r in rows:
        if r["preset"]:
            assert r["set"] is False
            assert r["last_four"] is None
            assert r["source"] is None


def test_masked_all_after_set(secrets_mod):
    secrets_mod.set("Anthropic", "key-very-long-secret-xyz9")
    rows = {r["provider"]: r for r in secrets_mod.masked_all()}
    assert rows["Anthropic"]["set"] is True
    assert rows["Anthropic"]["last_four"] == "xyz9"
    assert rows["Anthropic"]["source"] == "file"
    # Plaintext should never appear in masked_all output.
    for r in rows.values():
        assert "key-very-long-secret-xyz9" not in json.dumps(r)


def test_delete_clears(secrets_mod):
    secrets_mod.set("Tavily", "tv-1234567890")
    assert secrets_mod.is_set("Tavily")
    secrets_mod.delete("Tavily")
    assert not secrets_mod.is_set("Tavily")
    assert secrets_mod.get("Tavily") is None


def test_export_to_env_populates_os_environ(secrets_mod, monkeypatch):
    secrets_mod.set("OpenAI", "sk-openai-abcd")
    secrets_mod.set("Serper", "srp-0000-1111")
    # export_to_env only fills unset env vars.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    n = secrets_mod.export_to_env()
    assert n >= 2
    assert os.environ.get("OPENAI_API_KEY") == "sk-openai-abcd"
    assert os.environ.get("SERPER_API_KEY") == "srp-0000-1111"


def test_get_prefers_env_var_over_stored(secrets_mod, monkeypatch):
    secrets_mod.set("OpenAI", "stored-value-1234")
    monkeypatch.setenv("OPENAI_API_KEY", "env-value-9999")
    assert secrets_mod.get("OpenAI") == "env-value-9999"
    # Remove env var → falls back to stored value.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert secrets_mod.get("OpenAI") == "stored-value-1234"


def test_http_unsafe_chars_round_trip(secrets_mod):
    # Characters that can trip up JSON/HTTP encoding but must still survive.
    val = 'abc"\\{}<>&?=+%/#\n\tend-1234'
    secrets_mod.set("Cohere", val)
    assert secrets_mod.get("Cohere") == val
    assert secrets_mod.last_four("Cohere") == "1234"


def test_last_four_on_short_value(secrets_mod):
    secrets_mod.set("Brave", "xy")
    # Short values get fully masked with bullets rather than leaking the value.
    lf = secrets_mod.last_four("Brave")
    assert lf is not None
    assert "x" not in lf and "y" not in lf


def test_index_surfaces_custom_provider_stored_in_keyring(
    tmp_path, monkeypatch
):
    """Regression: a custom provider written via Keychain must still appear in
    masked_all() / list_providers() even though Keychain is not enumerable.

    We simulate a working Keychain by swapping in a fake `_keyring` that stores
    values in a dict. The value never touches the file fallback, yet the name
    index must ensure discovery.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    from localsmartz import secrets as _secrets
    for _name, env_name in _secrets.PRESET_PROVIDERS:
        monkeypatch.delenv(env_name, raising=False)

    store: dict[tuple[str, str], str] = {}

    class _FakeKR:
        def get_password(self, service, user):
            return store.get((service, user))

        def set_password(self, service, user, value):
            store[(service, user)] = value

        def delete_password(self, service, user):
            store.pop((service, user), None)

    fake = _FakeKR()
    monkeypatch.setattr("localsmartz.secrets._keyring", lambda: fake)

    # Set a custom provider — routed to (fake) Keychain.
    source = _secrets.set("MyCustomAPI", "custom-val-9876")
    assert source == "keychain"

    # File fallback must NOT contain it (values stayed in Keychain).
    file_path = tmp_path / ".localsmartz" / "secrets.json"
    if file_path.exists():
        data = json.loads(file_path.read_text())
        assert "MyCustomAPI" not in data

    # But masked_all() must surface it via the index.
    rows = {r["provider"]: r for r in _secrets.masked_all()}
    assert "MyCustomAPI" in rows
    assert rows["MyCustomAPI"]["set"] is True
    assert rows["MyCustomAPI"]["last_four"] == "9876"
    assert rows["MyCustomAPI"]["source"] == "keychain"
    assert rows["MyCustomAPI"]["preset"] is False
    # No plaintext in output.
    for r in rows.values():
        assert "custom-val-9876" not in json.dumps(r)

    # list_providers() also reflects the index.
    assert "MyCustomAPI" in _secrets.list_providers()


def test_delete_removes_from_index_and_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    from localsmartz import secrets as _secrets
    for _name, env_name in _secrets.PRESET_PROVIDERS:
        monkeypatch.delenv(env_name, raising=False)

    store: dict[tuple[str, str], str] = {}

    class _FakeKR:
        def get_password(self, service, user):
            return store.get((service, user))

        def set_password(self, service, user, value):
            store[(service, user)] = value

        def delete_password(self, service, user):
            store.pop((service, user), None)

    monkeypatch.setattr("localsmartz.secrets._keyring", lambda: _FakeKR())

    _secrets.set("MyCustomAPI", "custom-val-9876")
    index_path = tmp_path / ".localsmartz" / "secrets_index.json"
    assert index_path.exists()
    assert "MyCustomAPI" in json.loads(index_path.read_text())

    _secrets.delete("MyCustomAPI")
    # Index should no longer list it.
    assert "MyCustomAPI" not in json.loads(index_path.read_text())
    # Keychain storage gone.
    assert store == {}
    # And masked_all no longer surfaces it.
    rows = {r["provider"]: r for r in _secrets.masked_all()}
    assert "MyCustomAPI" not in rows


def test_custom_provider_via_file_fallback_still_visible(secrets_mod):
    """When keyring is unavailable, custom providers land in secrets.json and
    are naturally discoverable. Verify masked_all() surfaces them."""
    secrets_mod.set("MyFileAPI", "file-val-4321")
    rows = {r["provider"]: r for r in secrets_mod.masked_all()}
    assert "MyFileAPI" in rows
    assert rows["MyFileAPI"]["set"] is True
    assert rows["MyFileAPI"]["last_four"] == "4321"
    assert rows["MyFileAPI"]["source"] == "file"
