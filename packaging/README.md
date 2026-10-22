# Packaging

SWC-Studio uses one modular desktop architecture on macOS and Windows.
PyInstaller packages the Python runtime and heavy third-party libraries;
the application code and models are staged as replaceable layers.

## Layout

Both platforms expose the same runtime-relative structure:

```text
runtime-root/
  app/
    VERSION
    swcstudio/
  models/
    VERSION
    labeler-<version>/
      cell_type_classifier.pkl
      branch_classifier.pkl
      branch_correction.joblib
      flag_model.joblib
```

`runtime-root` is:

- macOS: `SWC-Studio.app/Contents/Resources`
- Windows: `SWC-Studio/_internal`

The entrypoint is `packaging/swcstudio_bootstrap.py`. Shared PyInstaller
dependency collection lives in `packaging/pyinstaller_common.py`.

## Tracked files

- `swcstudio_gui_macos.spec`
- `swcstudio_gui_windows.spec`
- `pyinstaller_common.py`
- `swcstudio_bootstrap.py`
- `stage_modular_payload.py`
- `build_macos.sh`
- `build_windows.ps1`
- platform icons and icon helpers

## Build

macOS:

```bash
PYTHON_BIN=python3.12 ./packaging/build_macos.sh
```

Windows PowerShell:

```powershell
py -3.12 -m venv .venv-packaging-windows
.\.venv-packaging-windows\Scripts\python.exe -m pip install -e ".[build]" pillow
.\packaging\build_windows.ps1
```

The build script prefers `.venv-packaging-windows` and falls back to the
project `.venv` for local development builds. The bundle needs no GPU
libraries: auto-labeling runs on the CPU.

Generated `build/`, `dist/`, app bundles, and release zips are ignored
and must not be committed.
