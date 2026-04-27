"""Tier detection extends the legacy lite/full profile split."""
from __future__ import annotations

from localsmartz import profiles


def test_detect_tier_returns_expected_keys(monkeypatch) -> None:
    monkeypatch.setattr(profiles, "_detect_ram_bytes", lambda: 24 * (1024 ** 3))
    info = profiles.detect_tier()
    assert info["tier"] == "mini"
    assert info["ram_gb"] == 24
    assert info["legacy_profile"] == "lite"
    assert info["gpu_vram_gb"] == 0


def test_tier_cutoffs(monkeypatch) -> None:
    cases = [
        (16, "mini", "lite"),
        (24, "mini", "lite"),
        (32, "standard", "lite"),
        (64, "standard", "full"),
        (96, "full", "full"),
        (128, "full", "full"),
    ]
    for gb, expected_tier, expected_legacy in cases:
        monkeypatch.setattr(
            profiles, "_detect_ram_bytes", lambda gb=gb: gb * (1024 ** 3)
        )
        info = profiles.detect_tier()
        assert info["tier"] == expected_tier, f"at {gb}GB: got {info['tier']}"
        assert info["legacy_profile"] == expected_legacy, f"at {gb}GB legacy"


def test_detect_tier_handles_unknown_ram(monkeypatch) -> None:
    monkeypatch.setattr(profiles, "_detect_ram_bytes", lambda: None)
    info = profiles.detect_tier()
    assert info["tier"] == "mini"
    assert info["ram_gb"] == 0
