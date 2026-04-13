#!/bin/bash
# embed-python.sh
# Download and extract a self-contained Python 3.12 (python-build-standalone)
# into app/build/embedded-python/ so it can be copied into the .app bundle.
#
# No external deps beyond: bash, curl, shasum, tar, uname.
set -euo pipefail

# ---- Config ---------------------------------------------------------------

# TAG:ASSUMED - python-build-standalone release tag. Pinned to a recent stable
# release. Bump this (and the SHA256s below) when updating.
# Verify latest at: https://github.com/astral-sh/python-build-standalone/releases
PBS_TAG="20240814"
PY_VERSION="3.12.5"

# SHA256 hashes for the install_only tarballs for PBS_TAG.
# Source: https://github.com/astral-sh/python-build-standalone/releases/download/20240814/cpython-3.12.5+20240814-{arch}-apple-darwin-install_only.tar.gz.sha256
PBS_SHA256_ARM64="6943873ffcede238280a8fc0dbd4916c9bff54cf6a759352f86077c556c0c3a5"
PBS_SHA256_X86_64="4c7619c25c037d377eebe8c7b98e6c818276b55714536ea82be2325d5e8ad572"

# ---- Paths ----------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
BUILD_DIR="${APP_DIR}/build"
CACHE_DIR="${BUILD_DIR}/cache"
EMBED_DIR="${BUILD_DIR}/embedded-python"

mkdir -p "${CACHE_DIR}" "${BUILD_DIR}"

# ---- Detect arch ----------------------------------------------------------

HOST_ARCH="$(uname -m)"
case "${HOST_ARCH}" in
    arm64|aarch64)
        PBS_TRIPLE="aarch64-apple-darwin"
        EXPECTED_SHA256="${PBS_SHA256_ARM64}"
        ;;
    x86_64)
        PBS_TRIPLE="x86_64-apple-darwin"
        EXPECTED_SHA256="${PBS_SHA256_X86_64}"
        ;;
    *)
        echo "ERROR: unsupported host arch: ${HOST_ARCH}" >&2
        exit 1
        ;;
esac

TARBALL_NAME="cpython-${PY_VERSION}+${PBS_TAG}-${PBS_TRIPLE}-install_only.tar.gz"
TARBALL_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_TAG}/${TARBALL_NAME}"
TARBALL_PATH="${CACHE_DIR}/${TARBALL_NAME}"

echo "=== embed-python.sh ==="
echo "  PBS tag      : ${PBS_TAG}"
echo "  Python ver   : ${PY_VERSION}"
echo "  Host arch    : ${HOST_ARCH} -> ${PBS_TRIPLE}"
echo "  Cache        : ${CACHE_DIR}"
echo "  Embed target : ${EMBED_DIR}"

# ---- Short-circuit if already extracted ----------------------------------

EMBED_PY_BIN="${EMBED_DIR}/bin/python3"
if [ -x "${EMBED_PY_BIN}" ]; then
    echo "  -> Already extracted at ${EMBED_DIR} (reusing)"
    echo "${EMBED_DIR}"
    exit 0
fi

# ---- Download (cached) ----------------------------------------------------

if [ ! -f "${TARBALL_PATH}" ]; then
    echo "  -> Downloading ${TARBALL_URL}"
    curl -fL --retry 3 --retry-delay 2 -o "${TARBALL_PATH}.partial" "${TARBALL_URL}"
    mv "${TARBALL_PATH}.partial" "${TARBALL_PATH}"
else
    echo "  -> Using cached tarball: ${TARBALL_PATH}"
fi

# ---- Verify SHA256 --------------------------------------------------------

ACTUAL_SHA256="$(shasum -a 256 "${TARBALL_PATH}" | awk '{print $1}')"
if [ "${ACTUAL_SHA256}" != "${EXPECTED_SHA256}" ]; then
    echo "ERROR: SHA256 mismatch for ${TARBALL_NAME}" >&2
    echo "  expected: ${EXPECTED_SHA256}" >&2
    echo "  actual  : ${ACTUAL_SHA256}" >&2
    rm -f "${TARBALL_PATH}"
    exit 1
fi
echo "  -> SHA256 OK"

# ---- Extract --------------------------------------------------------------

echo "  -> Extracting to ${EMBED_DIR}"
rm -rf "${EMBED_DIR}" "${EMBED_DIR}.tmp"
mkdir -p "${EMBED_DIR}.tmp"
tar -xzf "${TARBALL_PATH}" -C "${EMBED_DIR}.tmp"

# python-build-standalone install_only tarball layout: top-level `python/` dir.
if [ -d "${EMBED_DIR}.tmp/python" ]; then
    mv "${EMBED_DIR}.tmp/python" "${EMBED_DIR}"
    rm -rf "${EMBED_DIR}.tmp"
else
    # Fallback: some layouts put files directly at root.
    mv "${EMBED_DIR}.tmp" "${EMBED_DIR}"
fi

if [ ! -x "${EMBED_PY_BIN}" ]; then
    echo "ERROR: expected ${EMBED_PY_BIN} after extraction" >&2
    exit 1
fi

echo "  -> Embedded Python ready: ${EMBED_PY_BIN}"
"${EMBED_PY_BIN}" --version

# Print embed dir on final line so callers can capture it.
echo "${EMBED_DIR}"
