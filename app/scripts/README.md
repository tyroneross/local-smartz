# app/scripts

Helper scripts for packaging Local Smartz as a self-contained macOS .app.

## embed-python.sh

Downloads a self-contained Python 3.14.4 distribution from
[python-build-standalone](https://github.com/astral-sh/python-build-standalone)
and extracts it into `app/build/embedded-python/`. The tarball is cached under
`app/build/cache/` so repeat runs are fast.

Detects host arch (arm64 / x86_64), pins the PBS release tag inside the script,
and verifies SHA256 for the pinned tarball.

Invoke directly:

    bash app/scripts/embed-python.sh

It is also invoked automatically by `build-dmg.sh` before DMG creation.

## build-dmg.sh (in app/)

Orchestrator: runs `xcodegen`, `xcodebuild`, embeds Python into the built
`.app` bundle, installs the `localsmartz` package into the bundled Python's
site-packages, strips `__pycache__`, optionally ad-hoc codesigns, then creates
the DMG with `hdiutil`.
