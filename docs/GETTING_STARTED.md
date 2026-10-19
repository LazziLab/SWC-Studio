# Getting Started

This guide gets a new user to a working `SWC-Studio` install and a first successful run.

## Installation paths

There are three supported ways to install:

| Method | Best for | Models bundled? |
|---|---|---|
| **Option 1 — bundled desktop app** | end users who want a double-clickable application | yes, inside the app |
| **Option 2 — `pip install swcstudio`** | researchers using SWC-Studio in scripts / Python | yes, inside the wheel |
| **Option 3 — install from source** | developers who want to modify the code itself | yes, in the local checkout |

## Supported Python versions

Options 2 and 3 require **Python 3.10, 3.11, or 3.12** already installed on your system.

Python 3.12 is the recommended default. Newer Python releases are added
only after the compiled scientific dependencies and the pinned model
runtime have been validated together.

Option 1 (the bundled app) ships its own Python runtime — no separate Python install needed.

## Option 1 — Bundled desktop app

Use this path if you only need the desktop application and don't want to deal with Python.

1. open <https://github.com/LazziLab/SWC-Studio/releases/latest>
2. download `SWC-Studio-vX.Y.Z-macOS.zip` (Mac) or `SWC-Studio-vX.Y.Z-Windows.zip` (Windows)
3. extract and launch:
   - **macOS** — drag `SWC-Studio.app` into `/Applications`, then open it as described in [First launch on macOS](#first-launch-on-macos).
   - **Windows** — extract anywhere, run the `.exe` inside.

The auto-labeler's model files are bundled inside the app, so the first
auto-label run works without any download. Auto-labeling runs on the
CPU and needs no GPU.

### First launch on macOS

The app is not yet signed by Apple, so macOS blocks it the first time.
You allow it once; after that it opens normally.

1. Move `SWC-Studio.app` into `/Applications` first. An app started from
   the Downloads folder or from inside the zip runs from a temporary
   copy and cannot save its settings.
2. Double-click it. macOS says it could not verify the app. Click
   **Done** (or **Cancel**).
3. Open **System Settings → Privacy & Security** and scroll down to
   **Security**. Next to "SWC-Studio was blocked", click **Open Anyway**,
   confirm with your password or Touch ID, then click **Open Anyway**
   again.

Instead of steps 2–3 you can run this once in Terminal:

```bash
xattr -cr /Applications/SWC-Studio.app
```

On macOS 14 and earlier, Control-click the app and choose **Open** also
works. macOS 15 removed that shortcut, so use **Open Anyway** there.

When SWC-Studio first opens or saves files in your Desktop, Documents or
Downloads folder, macOS asks whether to allow it. Click **Allow**, or
batch runs on those folders cannot write their results.

The macOS app is built for Apple silicon (M1 and later). On an Intel
Mac, install with `pip` instead ([Option 2](#option-2--pip-install-from-pypi)).

## Option 2 — `pip install` from PyPI

Use this path for the desktop GUI, scripts, Jupyter notebooks, batch
processing, or any Python workflow. Create a new virtual environment so
the `python`, `pip`, `swcstudio`, and `swcstudio-gui` commands all refer
to the same installation.

### macOS / Linux

```bash
python3.12 -m venv ~/swcstudio-env
source ~/swcstudio-env/bin/activate
python -m pip install --upgrade pip
python -m pip install swcstudio
swcstudio doctor
swcstudio-gui
```

### Windows PowerShell

```powershell
py -3.12 -m venv $HOME\swcstudio-env
& $HOME\swcstudio-env\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install swcstudio
swcstudio doctor
swcstudio-gui
```

That single `python -m pip install swcstudio` command installs every
Python runtime requirement: the scientific stack, XGBoost and
scikit-learn, PySide6, VisPy, pyqtgraph, and the history/provenance
libraries. The wheel also contains all runtime JSON configuration and
the four model files of the SWC auto-labeler. There is no separate
requirements file, model download, or `[gui]` extra.

The CI release gate installs the built wheel into an empty virtual
environment and runs model deserialization, GUI imports, and inference
on Windows, macOS, and Linux with Python 3.10, 3.11, and 3.12.

To upgrade later: `python -m pip install --upgrade swcstudio`. The
upgrade installs the matching code, configuration, and models.

If `swcstudio doctor` reports an executable outside the environment you
created, run the commands by absolute environment path, for example
`~/swcstudio-env/bin/swcstudio-gui` on macOS/Linux or
`$HOME\swcstudio-env\Scripts\swcstudio-gui.exe` on Windows.

## Option 3 — Source install (development)

Use this path if you want to modify the swcstudio code itself. The `-e`
flag installs in **editable** mode, meaning edits to local `.py` files
take effect on the next `python` run — no reinstall needed.

Clone the repository:

```bash
git clone https://github.com/LazziLab/SWC-Studio.git
cd SWC-Studio
```

One command installs everything: the CLI, the desktop GUI, the
auto-labeler (scikit-learn and XGBoost), and the bundled model files.

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

The editable install includes the complete scientific, ML, desktop GUI,
visualization, and history runtime. New users do not need to install a
separate requirements file or GUI extra.

### Windows PowerShell

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
```

### Conda

```bash
conda create -n swc-studio python=3.12 -y
conda activate swc-studio
python -m pip install --upgrade pip
python -m pip install -e .
```

### Optional developer extras

For maintainers who need PyInstaller (release packaging) and Sphinx
(docs build):

```bash
python -m pip install -e ".[dev]"
```

End users do not need this.

## Updating

How you update depends on how you installed:

| Install method | How to update |
|---|---|
| Option 1 (bundled app) | Help → **Check for Updates** in the GUI. The in-app updater downloads only the changed layer (code or models), no full re-download. |
| Option 2 (pip) | `python -m pip install --upgrade swcstudio` installs the matching code, dependencies, configuration, and bundled models. |
| Option 3 (source) | `git pull` followed by `python -m pip install -e .` to pick up any new dependencies or metadata. |

Internally, each release is split into three independent layers — runtime,
code, and models — so most updates only re-fetch the small ones. See
[`packaging/MODULAR_BUILD.md`](https://github.com/LazziLab/SWC-Studio/blob/main/packaging/MODULAR_BUILD.md)
for the architecture details.

## Verify installation

```bash
swcstudio --help
swcstudio doctor
swcstudio models status
swcstudio-gui --help
```

If a console script is not on your path, fall back to module mode:

```bash
python -m swcstudio.cli.cli --help
python -m swcstudio.gui.main
```

`swcstudio models status` is worth running once after install: it
prints the auto-labeler's version, the model folders it searched, and
the folder it uses. One `labeler-1.0.1` folder should be marked
`[COMPLETE]`, and the JSON summary should list the paths of
`cell_type_classifier.pkl`, `branch_classifier.pkl`,
`branch_correction.joblib`, and `flag_model.joblib`.

## First CLI checks

Start with a read-only inspection command:

```bash
swcstudio check cell.swc
swcstudio validate cell.swc
```

Then try an edit command:

```bash
swcstudio auto-label cell.swc
swcstudio set-type cell.swc --node-id 14169 --new-type 3
swcstudio simplify cell.swc
swcstudio connect cell.swc --start-id 10 --end-id 22
```

Single-file edit commands update the source SWC directly and record the
operation in `<stem>_history.swcstudio` next to the file. No separate
`--write` flag is required.

`auto-label` labels every node as soma, axon, basal dendrite or apical
dendrite with the SWC auto-labeler. By default Stage 1 decides the cell
class; use `--cell-class 3-class` (axon, basal, apical) or
`--cell-class 2-class` (axon, dendrite) to choose it. The quality flag
predicts how well the cell was labeled (its per-cell F1) and flags the
cell for review when that falls below the flag's cut-off; `--no-flag`
turns it off. See
[Auto-Typing Engine](documentation/auto-typing-backends.md).

## First GUI checks

Launch the GUI:

```bash
swcstudio-gui
```

Then:

1. open an SWC file
2. review the automatically generated issue list
3. select an issue and let the app route you to the matching repair tool
4. save or close the file to keep the original SWC updated and record
   the operation in history

## Default outputs

For a source file:

- `<folder>/<stem>.swc`

the default single-file output directory is:

- `<folder>/<stem>_swc_studio_output/`

That legacy-compatible directory is used mainly for explicitly requested
single-file validation reports and report-only exports.

Other materialized outputs use their operation-specific location:

- batch split creates a timestamped batch output directory
- history checkout writes the requested output path (or its documented default)
- history checkpoint writes a labeled SWC next to the source

GUI and mutating CLI edits are recorded in the per-file history archive
next to the SWC:

- `<stem>_history.swcstudio`

## Recommended next pages

- [User Guide](documentation/index.md)
- [Auto-Typing Engine](documentation/auto-typing-backends.md)
- [GUI Workflow Guide](GUI_WORKFLOW.md)
- [CLI Tutorial](tutorials/cli-tutorial.md)
- [Logs And Reports](LOGS_AND_REPORTS.md)
