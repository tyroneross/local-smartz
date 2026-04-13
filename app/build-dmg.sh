#!/bin/bash
# Package Local Smartz as a DMG installer with drag-to-Applications
set -euo pipefail

APP_NAME="LocalSmartz"
DISPLAY_NAME="Local Smartz"
DMG_NAME="${APP_NAME}-Installer"
VERSION="0.1"
VOLUME_NAME="${DISPLAY_NAME} ${VERSION}"
DMG_FILE="${DMG_NAME}.dmg"
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

# Slim the bundle: drop __pycache__ directories.
echo "  -> Stripping __pycache__ from bundled Python"
find "${APP_PY_DIR}" -type d -name __pycache__ -prune -exec rm -rf {} +

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
rm -rf "${STAGING_DIR}" "${DMG_FILE}"
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
    "${DMG_FILE}"

rm -rf "${STAGING_DIR}"

DMG_SIZE=$(du -h "${DMG_FILE}" | cut -f1)
echo ""
echo "=== Done ==="
echo "  ${DMG_FILE} (${DMG_SIZE})"
echo ""
echo "  To install: Open the DMG and drag Local Smartz to Applications"
