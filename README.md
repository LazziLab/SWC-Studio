# SWC-Studio

`SWC-Studio` is a desktop, CLI, and Python toolkit for working with neuron morphology files in SWC format.

It is designed for inspecting reconstructions, finding structural or annotation problems, repairing them, and running repeatable morphology-processing workflows from one shared backend.

## Project Overview

`SWC-Studio` includes:

- a shared Python backend (`swcstudio`)
- a command-line interface (`swcstudio`)
- a desktop GUI (`swcstudio-gui`)

Both the CLI and GUI use the same core feature logic.

## Documentation

Project documentation lives on the docs website:

- Live docs: [https://mio0v0.github.io/SWC-Studio/](https://mio0v0.github.io/SWC-Studio/)
- All releases: [https://github.com/Mio0v0/SWC-Studio/releases](https://github.com/Mio0v0/SWC-Studio/releases)

Use the docs site for:

- installation and getting started
- GUI and CLI workflows
- validation, repair, and reporting behavior
- auto-labeling (with optional retraining)
- tutorials
- API and extension references

### Release Assets

Each GitHub Release attaches three sets of assets:

- **Bundled apps** — `SWC-Studio-vX.Y.Z-macOS.zip`, `SWC-Studio-vX.Y.Z-Windows.zip` (double-click installers)
- **Pip packages** — `swcstudio-X.Y.Z-py3-none-any.whl` and `swcstudio-X.Y.Z.tar.gz` (also published to PyPI)
- **Modular update layers** — `swcstudio-code-vX.Y.Z.zip`, `swcstudio-models-vX.Y.Z.zip`, and `update_manifest.json` (consumed by the in-app updater; end users never download these manually)

## Quick Start

Three supported install paths, depending on what you need.

### Option 1 — End user, double-click the desktop app

Use this path if you only need the desktop application and don't want to deal with Python.

1. Open <https://github.com/Mio0v0/SWC-Studio/releases/latest>
2. Download `SWC-Studio-vX.Y.Z-macOS.zip` or `SWC-Studio-vX.Y.Z-Windows.zip` (X.Y.Z is the latest version)
3. Extract and launch:
   - **macOS** — drag `SWC-Studio.app` into `/Applications`, then double-click. First launch needs `xattr -cr /Applications/SWC-Studio.app` or right-click → Open (the bundle is not yet code-signed).
   - **Windows** — extract anywhere and run the `.exe` inside.

The auto-labeling models are bundled inside the app, so no separate
download is needed.

Auto-labeling runs the SWC auto-labeler, version 1.0.0
(<https://github.com/Mio0v0/swc-autolabeler>), on every file:

1. **Structural checks** (Stage 0). The file must hold one connected
   cell with a soma, between 10 and 200,000 nodes, finite coordinates,
   and finite, non-negative radii. A file without a soma label takes its
   root as the soma. A file that fails is not labeled, and the error
   lists the reasons.
2. **Cell class** (Stage 1). 3-class cells get axon, basal dendrite and
   apical dendrite labels. 2-class cells get axon and dendrite labels.
   Stage 1 decides the class from the cell's shape, or you choose it.
3. **Labeling** (Stages 2 and 3). A gradient-boosted classifier labels
   each primary subtree that leaves the soma. Topology rules follow,
   and for 3-class cells a learned correction re-decides branches near
   the soma.
4. **Quality flag** (Stage 4). The flag predicts how well each cell was
   labeled (its per-cell F1). One file is flagged when that falls below
   the flag's cut-off. A batch flags the riskiest share of each cell
   class (10% by default), and the Flag Review window lists every cell,
   riskiest first.

Input files do not need labels: the labels are predicted from geometry.
Auto-labeling runs on the CPU and needs no GPU.

The first labeling run in a process reads the model files; later runs in
the same process reuse them. After you apply an auto-label result in the
GUI, the next validation refresh reuses it instead of labeling the cell
again.

### macOS system packages (Options 2 and 3)

`xgboost` ships precompiled binaries that dynamically link the **OpenMP
runtime** (`libomp.dylib`). macOS does not include it by default, so
without it `import xgboost` fails with a `Library not loaded:
@rpath/libomp.dylib` error and the auto-labeling pipeline cannot start.
Install it once before `pip install swcstudio`:

```bash
brew install libomp
```

(If you do not already have Homebrew, see <https://brew.sh>.) No other
macOS system packages are required.

### Linux system packages (Options 2 and 3)

`PySide6` and `vispy` are pip-installed, but the Qt platform plugin and
OpenGL stack they depend on are **OS packages**, not Python wheels. On a
fresh Debian/Ubuntu host install them before `pip install swcstudio`,
otherwise launching the GUI fails with a Qt platform-plugin error:

```bash
sudo apt-get update
sudo apt-get install -y \
  libegl1 libgl1 libxkbcommon-x11-0 libxcb-cursor0 \
  libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
  libxcb-randr0 libxcb-render-util0 libxcb-shape0 \
  libxcb-xinerama0 libxcb-xkb1 libxkbcommon0 \
  libdbus-1-3 libfontconfig1
```

Fedora / RHEL: `sudo dnf install mesa-libGL mesa-libEGL libxkbcommon-x11
xcb-util-cursor xcb-util-image xcb-util-keysyms xcb-util-renderutil
xcb-util-wm dbus-libs fontconfig`. Arch: `sudo pacman -S libgl libxkbcommon
xcb-util-cursor xcb-util-image xcb-util-keysyms xcb-util-renderutil
xcb-util-wm dbus fontconfig`.

macOS and Windows ship the equivalent libraries with the OS — no extra
step is needed on those platforms. After installation, `swcstudio doctor`
will report **Qt platform plugin: OK** when the system stack is healthy
and otherwise print the `apt-get` hint above.

### Option 2 — Researcher, `pip install` (recommended for scripted workflows)

The Python package is published on PyPI:

```bash
python3.12 -m venv ~/swcstudio-env
source ~/swcstudio-env/bin/activate
python -m pip install --upgrade pip
python -m pip install swcstudio
swcstudio doctor
swcstudio-gui          # launch the desktop GUI
swcstudio --help       # CLI
swcstudio models status
```

Requires Python 3.10, 3.11, or 3.12. Pip installs the scientific,
ML, GUI, and history dependencies into the active environment. The
wheel also contains all runtime JSON configuration and the four model
files of the SWC auto-labeler, so the first auto-label run does not
require a separate model download, requirements file, or GUI extra.

For a clean isolated install:

```bash
python3 -m venv ~/swcstudio-env
source ~/swcstudio-env/bin/activate     # Windows: ~\swcstudio-env\Scripts\Activate.ps1
python -m pip install swcstudio
swcstudio-gui
```

To upgrade later: `python -m pip install --upgrade swcstudio`.

### Option 3 — Developer, install from source

Use this path if you want to modify the swcstudio code itself. Supported Python versions: 3.10, 3.11, and 3.12.

```bash
git clone https://github.com/Mio0v0/SWC-Studio.git
cd SWC-Studio
```

**macOS / Linux:**

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
swcstudio-gui
```

**Windows PowerShell:**

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
swcstudio-gui
```

`python -m pip install -e .` installs the application runtime. The `-e`
("editable") flag means local `.py` edits take effect on the next run.
It installs the complete scientific, ML, GUI, visualization, and history
runtime; no separate requirements file or optional GUI extra is needed.
Maintainers can install packaging, documentation, test, and artifact
build tools with `python -m pip install -e ".[dev]"`.

### Verifying the install

```bash
swcstudio --help
swcstudio doctor            # import packages and deserialize every model
swcstudio models status     # confirm the auto-labeler's models are found
```

If the console scripts aren't on your path, fall back to module mode:

```bash
python -m swcstudio.cli.cli --help
python -m swcstudio.gui.main
```

## Updates

How you update depends on how you installed:

| Install method | How to update |
|---|---|
| **Option 1** (bundled app) | Help → **Check for Updates** in the GUI. The in-app updater downloads only the changed layer (code or models), no full re-download required. |
| **Option 2** (pip) | `python -m pip install --upgrade swcstudio` installs the matching code, dependencies, configuration, and bundled models. |
| **Option 3** (source) | `git pull` followed by `python -m pip install -e .` to pick up any new dependencies or metadata. |

Under the hood, releases are split into three independently-updatable layers — runtime (heavy libraries), code (`swcstudio/` package), and models — so most updates touch only the small layers. See [`packaging/MODULAR_BUILD.md`](packaging/MODULAR_BUILD.md) for the architecture and [`RELEASE.md`](RELEASE.md) for the release pipeline.

## Core Capabilities

- issue-driven SWC validation and repair
- batch processing workflows
- auto-labeling of soma / axon / basal / apical, with a quality flag that ranks cells for review (and optional retraining on your own data)
- radii cleaning
- manual morphology and geometry editing
- shared GUI, CLI, and Python integration surface

## Provenance & Versioning

A git-shaped per-file history layer records SWC edits. See
[`docs/PROVENANCE_SPEC.md`](docs/PROVENANCE_SPEC.md) for the full
design contract. Every mutation updates a
visible encrypted `<stem>_history.swcstudio` repo archive containing the
append-only event log, content-addressed `.zst` blob store, refs, and
SQLite query index. SWC headers carry a bounded `# @PROV` pointer to
that archive and its repo ID, so renamed files can be reattached to
their history. User-facing operations are numbered independently for
each file as `op-1`, `op-2`, and so on, including files handled by a
batch. AI ops capture an MLflow-shaped run record plus a full
environment fingerprint for reproducibility. CLI: `swcstudio history
{log,show,checkout,branch,switch,tag,checkpoint,reproduce,reindex,verify,gc,export-crate}`.

## Recommended Workflow

1. Open one SWC file in the GUI.
2. Run validation and review the issue list.
3. Apply the suggested repairs.
4. Rerun validation.
5. Save or export the cleaned result.

## License

Released under the MIT License. See `LICENSE`.
