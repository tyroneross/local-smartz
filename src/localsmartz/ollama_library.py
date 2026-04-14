"""Fetch a refreshable list of popular Ollama models from the web.

Why not a hand-curated list?
----------------------------
The hand-picked ``SUGGESTED_MODELS`` in ``ollama.py`` had no systematic basis
— it was "what the maintainer remembered when asked." That scales poorly:
new releases like Gemma 4 silently don't show up until someone notices.

Instead, we scrape ``ollama.com/search`` (the site's own library browser)
for the fields Ollama already tags for e2e testing:

- ``x-test-search-response-title`` — model name
- ``x-test-pull-count`` — "2.9M", "145.3K"
- ``x-test-size`` — available sizes ("e2b", "27b", "70b")
- ``x-test-capability`` — "tools", "vision", "thinking", "embedding"
- ``x-test-updated`` — "2 hours ago", "9 months ago"

Results are cached to ``.localsmartz/library-cache.json`` for 24h so the
Install sheet renders instantly after the first fetch. Refresh is manual
from the UI.

Limitations (honest):

- Ollama doesn't publish "past month" pull stats anywhere public. Ranking
  is by **lifetime** pull count. For a "recent trending" view we'd have
  to compare snapshots over time — out of scope here.
- Scraping is fragile. If Ollama changes the ``x-test-*`` attribute names
  we silently return an empty list and callers fall back to the hardcoded
  ``SUGGESTED_MODELS``.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Iterable

import httpx


# Public ollama.com search endpoint. ``c=tools`` restricts to tool-calling
# models, which is the only class relevant for an agent app.
LIBRARY_SEARCH_URL = "https://ollama.com/search"

# Cache file lives next to the user's workspace so it's per-project.
DEFAULT_CACHE_TTL_SECONDS = 24 * 60 * 60


# ── Parse helpers ──────────────────────────────────────────────────────

_NAME_RE = re.compile(r'x-test-search-response-title[^>]*>([^<]+)<')
_PULL_RE = re.compile(r'x-test-pull-count[^>]*>([^<]+)<')
_SIZE_RE = re.compile(r'x-test-size[^>]*>([^<]+)<')
_CAP_RE = re.compile(r'x-test-capability[^>]*>([^<]+)<')
_UPD_RE = re.compile(r'x-test-updated[^>]*>([^<]+)<')
# Card boundary — each search result begins with `href="/library/<name>"`.
_CARD_RE = re.compile(
    r'href="/library/([a-zA-Z0-9._-]+)"(.*?)(?=href="/library/[a-zA-Z0-9._-]+"|</body>)',
    re.DOTALL,
)


def _parse_pull_count(raw: str) -> int:
    """Turn "2.9M" / "145.3K" / "1.1B" into an integer for sorting.

    Returns 0 on any parse failure so unsortable entries sink to the bottom
    rather than crashing the sort.
    """
    if not isinstance(raw, str):
        return 0
    s = raw.strip().upper()
    if not s:
        return 0
    mult = 1
    last = s[-1]
    if last == "K":
        mult, s = 1_000, s[:-1]
    elif last == "M":
        mult, s = 1_000_000, s[:-1]
    elif last == "B":
        mult, s = 1_000_000_000, s[:-1]
    try:
        return int(float(s) * mult)
    except ValueError:
        return 0


def _parse_updated_days(raw: str) -> int:
    """Turn "2 hours ago" / "9 months ago" / "1 year ago" into integer days.

    Used for "prefer the newer model within a family" ranking — 0 for
    today, ~365 for a year-old release. Returns a large sentinel (10 * 365)
    for unparseable strings so unknowns sort to the back (looks old).
    """
    if not isinstance(raw, str) or not raw.strip():
        return 10 * 365
    m = re.match(r"(\d+)\s+(second|minute|hour|day|week|month|year)s?\s+ago", raw.strip().lower())
    if not m:
        return 10 * 365
    n = int(m.group(1))
    unit = m.group(2)
    # Rough day conversions — precision doesn't matter here; this is only
    # used to order entries within the same model family.
    per_unit_days = {
        "second": 0,
        "minute": 0,
        "hour": 0,
        "day": 1,
        "week": 7,
        "month": 30,
        "year": 365,
    }
    return n * per_unit_days.get(unit, 365)


def _family_stem(name: str) -> str:
    """Collapse "gemma4" / "gemma3" / "gemma2" → "gemma" so we can dedupe
    across major versions of the same family.

    Rules (in order):
    - ``<stem><digit(s)[.digits]>`` → stem (gemma4 → gemma, qwen3.5 → qwen)
    - ``<stem><digit>-<suffix>`` → ``<stem>-<suffix>`` so a variant like
      "gemma2-instruct" still groups with "gemma3-instruct".
    - Otherwise return the name unchanged.

    This is a heuristic — a few models with numerics in the stem will be
    mis-grouped (e.g. "glm4" and "glm-4.7-flash" look similar). Acceptable
    because the dedup only affects ordering, never which models are shown.
    """
    if not isinstance(name, str):
        return ""
    # Strip trailing version like "gemma4" or "qwen3.5" or "llama3.3"
    m = re.match(r"^([a-zA-Z-]+?)(\d+(?:\.\d+)?)(?:-(.+))?$", name)
    if not m:
        return name
    stem, _ver, suffix = m.group(1), m.group(2), m.group(3)
    if suffix:
        return f"{stem}-{suffix}"
    return stem


def _parse_cards(html: str) -> list[dict]:
    """Split the search page into per-model dicts.

    Enriches each entry with:
    - ``family`` — stem shared across major versions ("gemma" for gemma4)
    - ``updated_days`` — numeric age (for ranking newer within a family)
    - ``capabilities`` — capabilities tagged by Ollama ("tools", "vision", …)
    - ``sizes`` — available size tags ("4b", "12b", "27b") — these are the
      subset the UI needs for the "Install which size?" follow-on picker
    - ``quantization_hint`` — extracted from any size tag that carries a
      quant suffix ("q4_K_M", "q5_K_M"). Usually empty at the family level.
    """
    out: list[dict] = []
    for name, body in _CARD_RE.findall(html):
        names = _NAME_RE.findall(body)
        pulls = _PULL_RE.findall(body)
        sizes = _SIZE_RE.findall(body)
        caps = _CAP_RE.findall(body)
        upd = _UPD_RE.findall(body)
        display_name = (names[0] if names else name).strip()
        primary_pulls = pulls[0].strip() if pulls else ""
        updated_str = upd[0].strip() if upd else ""
        clean_sizes = [s.strip() for s in sizes if s.strip()]
        # Quant tokens in the size tag are rare at the family level but
        # common on specific variants — surface if present.
        quant_hint = ""
        for s in clean_sizes:
            qm = re.search(r"(q\d+(?:_[A-Z0-9]+)*)", s)
            if qm:
                quant_hint = qm.group(1)
                break
        out.append({
            "name": display_name,
            "family": _family_stem(display_name),
            "pulls_raw": primary_pulls,
            "pulls": _parse_pull_count(primary_pulls),
            "sizes": clean_sizes[:8],
            "capabilities": sorted({c.strip() for c in caps if c.strip()}),
            "updated": updated_str,
            "updated_days": _parse_updated_days(updated_str),
            "quantization_hint": quant_hint,
        })
    return out


def _dedupe_by_family_prefer_newer(entries: list[dict]) -> list[dict]:
    """Within each family (gemma*, qwen*, llama*, …) keep the newest entry.

    Falls back to keeping the more-pulled one when two family members
    have identical updated ages. The returned list preserves the order
    of first appearance of each family so caller's sort is stable.
    """
    chosen: dict[str, dict] = {}
    order: list[str] = []
    for entry in entries:
        fam = entry.get("family") or entry.get("name") or ""
        cur = chosen.get(fam)
        if cur is None:
            chosen[fam] = entry
            order.append(fam)
            continue
        entry_days = entry.get("updated_days", 10 * 365)
        cur_days = cur.get("updated_days", 10 * 365)
        if entry_days < cur_days:
            chosen[fam] = entry
            continue
        if entry_days == cur_days and entry.get("pulls", 0) > cur.get("pulls", 0):
            chosen[fam] = entry
    return [chosen[f] for f in order]


# ── Fetch + cache ──────────────────────────────────────────────────────

def fetch_library(
    capability: str | None = "tools",
    timeout: float = 10.0,
) -> list[dict]:
    """Hit ollama.com/search and return parsed model cards sorted by pulls.

    Returns ``[]`` on any network / parse failure. Never raises — callers
    (cache layer, /api/models/library) should handle empty as "fall back
    to hardcoded SUGGESTED_MODELS".
    """
    params = {}
    if capability:
        params["c"] = capability
    try:
        resp = httpx.get(LIBRARY_SEARCH_URL, params=params, timeout=timeout)
        resp.raise_for_status()
    except Exception:  # noqa: BLE001 — network hiccup, swallow
        return []
    cards = _parse_cards(resp.text)
    # Collapse "gemma2/gemma3/gemma4" into one row per family, preferring
    # the newest release. Otherwise lifetime-pull sorting would surface
    # two-year-old predecessors over brand-new siblings.
    cards = _dedupe_by_family_prefer_newer(cards)
    cards.sort(key=lambda c: c.get("pulls", 0), reverse=True)
    return cards


def _cache_file(cache_dir: Path) -> Path:
    return cache_dir / "library-cache.json"


def load_cached(
    cache_dir: Path,
    ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
) -> tuple[list[dict], float | None]:
    """Load cached library list if it's within TTL.

    Returns ``(entries, fetched_at_unix)``. Entries are ``[]`` if the cache
    doesn't exist or is expired; ``fetched_at_unix`` is ``None`` in the
    missing case, or the old timestamp for an expired cache (so callers
    can decide whether to show "last updated X ago" copy).
    """
    path = _cache_file(cache_dir)
    if not path.exists():
        return [], None
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return [], None
    fetched_at = data.get("fetched_at")
    entries = data.get("entries", [])
    if not isinstance(fetched_at, (int, float)):
        return [], None
    if time.time() - fetched_at > ttl_seconds:
        return [], float(fetched_at)
    return list(entries) if isinstance(entries, list) else [], float(fetched_at)


def save_cache(cache_dir: Path, entries: list[dict]) -> None:
    """Persist entries with a fetch timestamp. Best-effort; silent on error."""
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        _cache_file(cache_dir).write_text(
            json.dumps({"fetched_at": time.time(), "entries": entries}, indent=2)
        )
    except OSError:
        pass


def get_popular(
    cache_dir: Path,
    limit: int = 10,
    *,
    capability: str | None = "tools",
    refresh: bool = False,
    ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
) -> dict:
    """Return a ready-to-serve payload for the Install sheet.

    ``{"source": "cache"|"live"|"stale-fallback"|"empty",
       "fetched_at": <unix>|None,
       "entries": [...]}``

    - ``refresh=True`` forces a fresh fetch
    - On fetch failure with an expired cache, we return the stale entries
      so the UI isn't empty — ``source="stale-fallback"``.
    """
    if not refresh:
        cached, fetched_at = load_cached(cache_dir, ttl_seconds=ttl_seconds)
        if cached:
            return {
                "source": "cache",
                "fetched_at": fetched_at,
                "entries": cached[:limit],
            }
    fresh = fetch_library(capability=capability)
    if fresh:
        save_cache(cache_dir, fresh)
        return {
            "source": "live",
            "fetched_at": time.time(),
            "entries": fresh[:limit],
        }
    # Live fetch failed — fall back to whatever's on disk, even if stale.
    stale, fetched_at = load_cached(cache_dir, ttl_seconds=10**12)
    if stale:
        return {
            "source": "stale-fallback",
            "fetched_at": fetched_at,
            "entries": stale[:limit],
        }
    return {"source": "empty", "fetched_at": None, "entries": []}


__all__: Iterable[str] = [
    "LIBRARY_SEARCH_URL",
    "fetch_library",
    "get_popular",
    "load_cached",
    "save_cache",
]
