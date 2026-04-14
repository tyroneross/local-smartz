#!/usr/bin/env bash
# UI smoke test for local-smartz macOS app.
# Launches the fresh Debug build (backgrounded with open -g), walks through
# Setup → Research → New Research sheet → Settings/Agents tab, captures an
# IBR scan at each step, and emits a pass/fail per checkpoint.
#
# Usage:   scripts/ui-smoke.sh
# Output:  .ibr/scans/smoke-<ts>/ (screenshots + findings.md)
# Prereqs: xcodebuild already succeeded; cliclick (brew install cliclick); ibr.
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

APP_PATH="/Users/tyroneross/Library/Developer/Xcode/DerivedData/LocalSmartz-cdfckkqgqzkywtcirxnxyulzemth/Build/Products/Debug/Local Smartz.app"
TS="$(date +%Y%m%d-%H%M%S)"
OUT=".ibr/scans/smoke-$TS"
mkdir -p "$OUT"

FINDINGS="$OUT/findings.md"
echo "# UI smoke findings — $TS" > "$FINDINGS"
echo "" >> "$FINDINGS"

pass=0; fail=0
check() {
    local name="$1"; local status="$2"; local detail="$3"
    if [ "$status" = "ok" ]; then
        pass=$((pass+1)); echo "- ✅ **$name** — $detail" >> "$FINDINGS"; echo "ok: $name"
    else
        fail=$((fail+1)); echo "- ❌ **$name** — $detail" >> "$FINDINGS"; echo "FAIL: $name — $detail"
    fi
}

scan() {
    local label="$1"; local shot="$OUT/$label.png"
    ibr scan:macos --pid "$PID" --screenshot "$shot" --json > "$OUT/$label.json" 2>/dev/null
}

find_text() { python3 - "$1" "$2" <<'PY'
import json, sys
path, needle = sys.argv[1], sys.argv[2]
try:
    d = json.load(open(path))
    for e in d.get('elements', {}).get('all', []):
        t = (e.get('text') or '') + ' ' + (e.get('a11y', {}).get('ariaLabel') or '')
        if needle.lower() in t.lower():
            b = e.get('bounds', {})
            print(f"{int(b.get('x',0))},{int(b.get('y',0))},{int(b.get('width',0))}x{int(b.get('height',0))}")
            sys.exit(0)
except Exception as e:
    print(f"ERR:{e}", file=sys.stderr)
sys.exit(1)
PY
}

# -- Kill any running instance; launch backgrounded --
osascript -e 'tell application "Local Smartz" to quit' >/dev/null 2>&1
sleep 2
open -g -n "$APP_PATH"
sleep 5
PID=$(pgrep -f "$APP_PATH/Contents/MacOS/Local Smartz" | head -1)
[ -z "$PID" ] && { echo "FAIL: app did not launch"; exit 1; }
echo "launched pid=$PID"

# ── 1. Setup screen ─────────────────────────────────────────────────────
scan "01-setup"
if find_text "$OUT/01-setup.json" "Get Started" >/dev/null; then
    coords=$(find_text "$OUT/01-setup.json" "Get Started")
    check "setup-get-started-visible" ok "coords=$coords"
    # Click Get Started via accessibility
    osascript -e 'tell application "System Events" to tell process "Local Smartz" to click button 8 of group 1 of window 1' >/dev/null 2>&1
    sleep 2
else
    check "setup-get-started-visible" fail "Get Started button not found in AXTree"
fi

# ── 2. Research view ────────────────────────────────────────────────────
scan "02-research"
if find_text "$OUT/02-research.json" "New Research" >/dev/null; then
    check "research-view-loaded" ok "New Research button visible"
else
    check "research-view-loaded" fail "Did not navigate to Research view"
fi

# ── 3. New Research sheet (Track C verification) ────────────────────────
coords=$(find_text "$OUT/02-research.json" "New Research")
if [ -n "$coords" ]; then
    x=$(echo "$coords" | cut -d, -f1)
    y=$(echo "$coords" | cut -d, -f2)
    # Click at button CENTER via System Events (cliclick raw mouse events
    # are ignored by SwiftUI Buttons in List/Outline rows; SE click at
    # coords goes through WindowServer properly and dispatches the action).
    w=$(echo "$coords" | cut -d, -f3 | cut -dx -f1)
    h=$(echo "$coords" | cut -d, -f3 | cut -dx -f2)
    cx=$((x + w/2))
    cy=$((y + h/2))
    osascript -e "tell application \"System Events\" to click at {$cx, $cy}" >/dev/null 2>&1
    sleep 1.5
    scan "03-new-research-sheet"
    if find_text "$OUT/03-new-research-sheet.json" "Create" >/dev/null; then
        check "new-research-sheet-opens" ok "Sheet appeared with Create button"
        # Click Cancel by coords (Escape sometimes ignored on modal SwiftUI sheets).
        cancel_coords=$(find_text "$OUT/03-new-research-sheet.json" "Cancel")
        if [ -n "$cancel_coords" ]; then
            cx=$(echo "$cancel_coords" | cut -d, -f1)
            cy=$(echo "$cancel_coords" | cut -d, -f2)
            cw=$(echo "$cancel_coords" | cut -d, -f3 | cut -dx -f1)
            ch=$(echo "$cancel_coords" | cut -d, -f3 | cut -dx -f2)
            osascript -e "tell application \"System Events\" to click at {$((cx+cw/2)), $((cy+ch/2))}" >/dev/null 2>&1
            sleep 1
        fi
    else
        check "new-research-sheet-opens" fail "Sheet did not appear"
    fi
else
    check "new-research-sheet-opens" fail "New Research button coords not resolvable"
fi

# ── 4. Settings → Agents tab (Track D2 verification) ────────────────────
osascript -e 'tell application "System Events" to keystroke "," using {command down}' >/dev/null 2>&1
sleep 1.5
scan "04-settings"
if find_text "$OUT/04-settings.json" "Agents" >/dev/null; then
    check "settings-agents-tab-present" ok "Agents tab label visible"
    # Try to click it
    coords=$(find_text "$OUT/04-settings.json" "Agents")
    if [ -n "$coords" ]; then
        x=$(echo "$coords" | cut -d, -f1)
        y=$(echo "$coords" | cut -d, -f2)
        cliclick c:$((x+15)):$((y+8)) >/dev/null 2>&1
        sleep 2
        scan "05-agents-tab"
        # Agents tab marker: "System prompt" disclosure is unique to the new tab.
        # Generic role names appear in the sidebar too — would false-positive.
        if find_text "$OUT/05-agents-tab.json" "System prompt" >/dev/null; then
            check "agents-tab-renders-roles" ok "Agents tab rendered (System prompt disclosure visible)"
        else
            check "agents-tab-renders-roles" fail "Settings opened but Agents tab not rendered (no 'System prompt' marker found)"
        fi
    fi
else
    check "settings-agents-tab-present" fail "Agents tab not found in Settings"
fi

# ── 5. Close settings ──────────────────────────────────────────────────
osascript -e 'tell application "System Events" to keystroke "w" using {command down}' >/dev/null 2>&1
sleep 1

echo "" >> "$FINDINGS"
echo "## Summary" >> "$FINDINGS"
echo "- Passed: $pass" >> "$FINDINGS"
echo "- Failed: $fail" >> "$FINDINGS"
echo "- Scans: $(ls "$OUT"/*.png 2>/dev/null | wc -l | xargs)" >> "$FINDINGS"
echo "" >> "$FINDINGS"
echo "results: $pass pass / $fail fail"
echo "output: $OUT"

[ "$fail" -eq 0 ]
