#!/usr/bin/env python3
"""Pricing freshness signal helper (feat: c9).

This script is INFORMATIONAL — it does NOT scrape provider pages. Per the
build-loop user constraint, pricing updates are a manual chore: open the
URLs below, copy the latest per-1M rates, edit ``src/localsmartz/cost.py``
RATES dict, and bump ``LAST_VERIFIED`` to today's date.

Provider pricing pages (verify before deploying at scale):

    Anthropic: https://www.anthropic.com/pricing
    OpenAI:    https://openai.com/api/pricing
    Groq:      https://groq.com/pricing/

Usage:

    python scripts/refresh-pricing.py            # human-readable summary
    python scripts/refresh-pricing.py --check    # exit 0 + JSON envelope
    python scripts/refresh-pricing.py --json     # JSON envelope, exit 0

The script is intentionally a stub: a real auto-refresh would scrape pricing
pages, which we don't trust for production. The stub keeps the
re-verification loop visible without introducing fragile automation.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROVIDER_PRICING_URLS: dict[str, str] = {
    "anthropic": "https://www.anthropic.com/pricing",
    "openai": "https://openai.com/api/pricing",
    "groq": "https://groq.com/pricing/",
}


def _add_repo_root_to_path() -> None:
    here = Path(__file__).resolve()
    repo_root = here.parent.parent
    src = repo_root / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="refresh-pricing",
        description="Pricing freshness signal — does NOT scrape, prints URLs to verify manually.",
    )
    parser.add_argument("--check", action="store_true", help="Print freshness envelope as JSON.")
    parser.add_argument("--json", action="store_true", help="Same as --check (alias).")
    args = parser.parse_args(argv)

    _add_repo_root_to_path()
    from localsmartz.cost import pricing_freshness  # type: ignore

    envelope = pricing_freshness()

    if args.check or args.json:
        envelope["pricing_urls"] = PROVIDER_PRICING_URLS
        print(json.dumps(envelope, indent=2))
        return 0

    print(f"Last verified: {envelope['last_verified']} ({envelope['age_days']} days ago)")
    print(f"Stale: {'YES — update RATES in src/localsmartz/cost.py' if envelope['stale'] else 'no'}")
    print(f"Threshold: {envelope['stale_threshold_days']} days")
    print()
    print("Verify pricing manually at:")
    for provider, url in PROVIDER_PRICING_URLS.items():
        print(f"  {provider:10s}{url}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
