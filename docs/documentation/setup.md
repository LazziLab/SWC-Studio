# Setup

Setup documentation covers installation, local startup, and first-run verification.

```{toctree}
:hidden:
:maxdepth: 1

Getting Started <../GETTING_STARTED>
```

## Installation

<a href="../GETTING_STARTED.html">Getting Started</a> covers all three install paths:

- **bundled desktop app** from GitHub Releases (double-click)
- **`pip install swcstudio`** from PyPI (Python users)
- **source install** from the cloned repository (developers)

All three install paths include the auto-labeler's model files.
Auto-labeling runs on the CPU and needs no GPU.

## First Run

- if using the bundled app, extract the release zip and open `SWC-Studio.app` / the `.exe`.
  On a Mac, move the app to `/Applications` first; macOS blocks the first
  launch because the app is not signed by Apple. Allow it in System
  Settings → Privacy & Security → **Open Anyway** (see
  [First launch on macOS](../GETTING_STARTED.md#first-launch-on-macos))
- if using a pip install, run `swcstudio doctor`, then `swcstudio-gui`
  to launch the GUI or `swcstudio --help` for the CLI
- if using a source install, run the same commands inside your activated venv to verify
- run `swcstudio models status` to check that the auto-labeler's model
  files are found

## Updating

- bundled app: Help → Check for Updates (in-app updater handles modular updates)
- pip install: `python -m pip install --upgrade swcstudio`
- source install: `git pull` and re-run `python -m pip install -e .`

See <a href="../GETTING_STARTED.html#updating">Getting Started → Updating</a> for full details.
