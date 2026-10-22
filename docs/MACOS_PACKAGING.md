# Reproducible macOS Packaging

The macOS release is a modular PyInstaller app. Its heavy runtime,
replaceable Python application layer, and model layer are stored
separately.

## Inputs

- `packaging/swcstudio_gui_macos.spec`
- `packaging/pyinstaller_common.py`
- `packaging/swcstudio_bootstrap.py`
- `packaging/stage_modular_payload.py`
- `packaging/build_macos.sh`
- `pyproject.toml`

## Build environment

```bash
python3.12 -m venv .venv-packaging-macos
source .venv-packaging-macos/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install --no-cache-dir -e ".[build]"
```

## Build command

```bash
PYTHON_BIN=python3.12 ./packaging/build_macos.sh
```

The result is `dist/SWC-Studio.app`. Application code is staged under
`Contents/Resources/app/swcstudio`, and models are staged under
`Contents/Resources/models`.

Build on macOS for macOS. The bundle has only an ad-hoc signature, so
Gatekeeper blocks its first launch. Users allow it in System Settings →
Privacy & Security → **Open Anyway** (macOS 15 no longer offers
Control-click → Open), or run `xattr -cr` on the app; see
[First launch on macOS](GETTING_STARTED.md#first-launch-on-macos). The
release workflow builds on `macos-latest`, an Apple silicon runner, so the
bundle is arm64 only.
