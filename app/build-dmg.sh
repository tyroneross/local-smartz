#!/bin/bash
# Package Local Smartz as a DMG installer with drag-to-Applications
set -euo pipefail

APP_NAME="LocalSmartz"
DISPLAY_NAME="Local Smartz"
DMG_NAME="${APP_NAME}-Installer"
VERSION="0.1"
VOLUME_NAME="${DISPLAY_NAME} ${VERSION}"
DMG_FILE="${DMG_NAME}.dmg"
TMP_DMG_FILE="${DMG_FILE}.tmp"
STAGING_DIR=".dmg-staging"
BUILD_DIR="build/Build/Products/Release"

cd "$(dirname "$0")"

# Step 1: Check for xcodegen and generate project if needed
if ! command -v xcodegen &>/dev/null; then
    echo "Error: xcodegen is required. Install with: brew install xcodegen"
    exit 1
fi

if [ ! -d "${APP_NAME}.xcodeproj" ]; then
    echo "=== Generating Xcode project ==="
    xcodegen generate
fi

# Step 2: Build the app
echo "=== Building ${DISPLAY_NAME} ==="
xcodebuild \
    -scheme "${APP_NAME}" \
    -configuration Release \
    -derivedDataPath build \
    -arch arm64 \
    build

# Step 3: Verify the .app exists
APP_BUNDLE="${BUILD_DIR}/${DISPLAY_NAME}.app"
if [ ! -d "${APP_BUNDLE}" ]; then
    # Try without space in name
    APP_BUNDLE="${BUILD_DIR}/${APP_NAME}.app"
fi

if [ ! -d "${APP_BUNDLE}" ]; then
    echo "Error: .app not found at ${BUILD_DIR}/"
    ls -la "${BUILD_DIR}/" 2>/dev/null || echo "Build directory does not exist"
    exit 1
fi

# Step 4: Embed a self-contained Python into the .app bundle
# This makes the distributed .app work without any external `python3` install.
echo ""
echo "=== Embedding Python runtime ==="
bash scripts/embed-python.sh

EMBEDDED_PY_SRC="build/embedded-python"
APP_RESOURCES="${APP_BUNDLE}/Contents/Resources"
APP_PY_DIR="${APP_RESOURCES}/python"

if [ ! -x "${EMBEDDED_PY_SRC}/bin/python3" ]; then
    echo "Error: embedded Python not found at ${EMBEDDED_PY_SRC}/bin/python3"
    exit 1
fi

echo "  -> Copying embedded Python into ${APP_PY_DIR}"
mkdir -p "${APP_RESOURCES}"
rm -rf "${APP_PY_DIR}"
# Use cp -R; preserve symlinks and perms.
cp -R "${EMBEDDED_PY_SRC}" "${APP_PY_DIR}"

# Install the local-smartz Python package into the bundled Python's
# site-packages. The repo root (containing pyproject.toml / setup.py) is
# the parent of this `app/` dir.
REPO_ROOT="$(cd .. && pwd)"
echo "  -> Installing localsmartz from ${REPO_ROOT} into bundled Python"
"${APP_PY_DIR}/bin/python3" -m pip install \
    --no-warn-script-location \
    --disable-pip-version-check \
    "${REPO_ROOT}"

# Slim the bundle: trim unused provider SDKs.
# Local Smartz is Ollama-only; langchain-anthropic + langchain-google-genai
# ride in as deepagents transitive deps but are never used at runtime.
# See references/bundle-size.md for rationale and measurements.
SP_DIR="$("${APP_PY_DIR}/bin/python3" - <<'PY'
import sysconfig
print(sysconfig.get_paths()["purelib"])
PY
)"
if [ ! -d "${SP_DIR}" ]; then
    echo "Error: bundled site-packages not found at ${SP_DIR}"
    exit 1
fi

echo "  -> Pre-slim site-packages size"
du -sh "${SP_DIR}" || true

# 1. Patch deepagents/graph.py to lazy-import langchain_anthropic and
#    install a no-op AnthropicPromptCachingMiddleware stub. Idempotent:
#    the patcher skips cleanly if the anchor lines are already replaced.
echo "  -> Applying deepagents slim patch"
if [ -f "${SP_DIR}/deepagents/graph.py" ] && [ -f "scripts/deepagents-slim.patch" ]; then
    "${APP_PY_DIR}/bin/python3" - "${SP_DIR}/deepagents/graph.py" "scripts/deepagents-slim.patch" <<'PYEOF' || echo "  WARNING: deepagents patch skipped"
import sys, pathlib
target = pathlib.Path(sys.argv[1])
patch = pathlib.Path(sys.argv[2]).read_text()
src = target.read_text()

# Parse blocks: @@ <name>\nFIND:\n<find>\nREPLACE:\n<replace>\n@@ end
blocks = []
cur = None
mode = None
buf = []
for line in patch.splitlines():
    if line.startswith("@@ end"):
        if cur and mode == "REPLACE":
            cur["replace"] = "\n".join(buf)
            blocks.append(cur)
        cur = None; mode = None; buf = []
    elif line.startswith("@@ "):
        cur = {"name": line[3:].strip()}; mode = None; buf = []
    elif line.strip() == "FIND:" and cur is not None:
        mode = "FIND"; buf = []
    elif line.strip() == "REPLACE:" and cur is not None:
        cur["find"] = "\n".join(buf); mode = "REPLACE"; buf = []
    else:
        if cur is not None and mode in ("FIND", "REPLACE"):
            buf.append(line)

applied = 0
skipped = 0
for b in blocks:
    find = b["find"]
    repl = b["replace"]
    if find and find in src:
        src = src.replace(find, repl, 1)
        applied += 1
        print(f"    patched: {b['name']}")
    elif repl and repl.split("\n", 1)[0] in src:
        skipped += 1
        print(f"    already patched: {b['name']}")
    else:
        skipped += 1
        print(f"    anchor missing (skipped): {b['name']}")

target.write_text(src)
print(f"  -> patch summary: applied={applied} skipped={skipped}")
PYEOF
else
    echo "  -> skipping deepagents patch (file or patch missing)"
fi

# 2. Remove unused provider SDKs + their dist-info.
#    Safe to delete: only consumed by the lazy-imported Anthropic/Google paths.
#    Keeping: google/protobuf (OTel proto), google/_upb (proto native accel).
echo "  -> Removing langchain_anthropic + anthropic + langchain_google_genai + google genai/auth"
for pat in \
    "langchain_anthropic" "langchain_anthropic-*.dist-info" \
    "anthropic" "anthropic-*.dist-info" \
    "langchain_google_genai" "langchain_google_genai-*.dist-info" \
    "google_genai-*.dist-info" "google_auth-*.dist-info" \
    "googleapis_common_protos-*.dist-info" \
    "jiter" "jiter-*.dist-info"; do
    for p in ${SP_DIR}/${pat}; do
        if [ -e "$p" ]; then rm -rf "$p"; fi
    done
done

# Trim unused subtrees under google/ while preserving google/protobuf + google/_upb
# (both required by opentelemetry-proto).
if [ -d "${SP_DIR}/google" ]; then
    for sub in genai auth oauth2 api cloud gapic logging longrunning rpc type; do
        if [ -d "${SP_DIR}/google/${sub}" ]; then
            rm -rf "${SP_DIR}/google/${sub}"
        fi
    done
fi

# 3. Smoke-test: deepagents must still import after slim + patch.
#    Run BEFORE stripping __pycache__ so the subsequent strip also removes
#    any bytecode this test regenerates.
echo "  -> Smoke-testing bundled Python import"
if ! PYTHONDONTWRITEBYTECODE=1 "${APP_PY_DIR}/bin/python3" -c "from deepagents import create_deep_agent; print('deepagents import ok')"; then
    echo "  ERROR: deepagents import failed after slim. Aborting DMG build."
    exit 1
fi

# 4. Drop __pycache__ directories (original slim step + anything the
#    smoke-test regenerated).
echo "  -> Stripping __pycache__ from bundled Python"
find "${APP_PY_DIR}" -type d -name __pycache__ -prune -exec rm -rf {} +

echo "  -> Post-slim site-packages size"
du -sh "${SP_DIR}" || true

# Ad-hoc codesign so Gatekeeper at least runs the bundle on the build host.
# TODO(local-smartz): replace ad-hoc "-" with a Developer ID Application
# certificate (and notarization) before public distribution.
echo "  -> Ad-hoc codesigning embedded Python (TODO: Developer ID for release)"
codesign --force --deep --sign - "${APP_PY_DIR}" || \
    echo "  WARNING: ad-hoc codesign failed; continuing"
codesign --force --deep --sign - "${APP_BUNDLE}" || \
    echo "  WARNING: ad-hoc codesign of .app failed; continuing"

# Step 5: Prepare staging directory
echo ""
echo "=== Preparing DMG contents ==="
rm -rf "${STAGING_DIR}" "${TMP_DMG_FILE}"
mkdir -p "${STAGING_DIR}"

cp -R "${APP_BUNDLE}" "${STAGING_DIR}/"
ln -s /Applications "${STAGING_DIR}/Applications"

# Step 6: Create DMG
echo ""
echo "=== Creating DMG ==="
hdiutil create \
    -volname "${VOLUME_NAME}" \
    -srcfolder "${STAGING_DIR}" \
    -ov \
    -format UDZO \
    -imagekey zlib-level=9 \
    "${TMP_DMG_FILE}"

mv "${TMP_DMG_FILE}" "${DMG_FILE}"

rm -rf "${STAGING_DIR}"

DMG_SIZE=$(du -h "${DMG_FILE}" | cut -f1)
echo ""
echo "=== Done ==="
echo "  ${DMG_FILE} (${DMG_SIZE})"
echo ""
echo "  To install: Open the DMG and drag Local Smartz to Applications"
