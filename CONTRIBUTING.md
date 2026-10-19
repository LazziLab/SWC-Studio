# Contributing to SWC-Studio

Thank you for helping. Bug reports, questions, documentation fixes, new
validation checks and plugins are all welcome, from anyone who works with
neuron morphology.

By taking part you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Getting help

- Read the documentation at <https://lazzilab.github.io/SWC-Studio/> first.
  The Getting Started page and the CLI and GUI tutorials cover most
  questions.
- For anything else, open an issue at
  <https://github.com/LazziLab/SWC-Studio/issues> and say what you are
  trying to do. Questions are fine as issues; there is no separate forum.

## Reporting a bug

Open an issue and include:

1. What you ran (the GUI steps, or the exact `swcstudio` command).
2. What you expected and what happened instead, with the full error text
   or traceback.
3. The output of `swcstudio doctor`, which lists the version, Python,
   operating system and whether the models load.
4. If you can share it, a small SWC file that shows the problem. A file
   cut down to the few nodes that trigger the bug is ideal.

If the problem involves a file's edit history, run
`swcstudio history verify <file>` and paste its output too.

## Suggesting a feature

Open an issue that describes the problem you want solved and how you
work around it today. For a new validation check, include an example file
that should fail it and one that should pass.

## Setting up a development environment

You need Python 3.10, 3.11 or 3.12. On macOS run `brew install libomp`
first, and on Linux install the Qt system packages listed in the
[README](README.md#linux-system-packages-options-2-and-3).

```bash
git clone https://github.com/LazziLab/SWC-Studio.git
cd SWC-Studio
python -m venv .venv
source .venv/bin/activate            # Windows: .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
swcstudio doctor
```

## Running the tests

```bash
QT_QPA_PLATFORM=offscreen python -m pytest -q
```

On Windows PowerShell, set the variable first with
`$env:QT_QPA_PLATFORM = "offscreen"`. The full suite takes a few minutes
because some tests load the auto-labeling models. CI runs the same suite
on Linux, macOS and Windows with Python 3.10, 3.11 and 3.12
(`.github/workflows/install-smoke.yml`).

## Building the documentation

```bash
sphinx-build -b html docs docs/_build/html
```

Then open `docs/_build/html/index.html`. The site is rebuilt and published
from `main` automatically.

## Where changes go

The CLI and the GUI share one backend, so most changes belong there:

- `swcstudio/core/`: algorithms and data logic.
- `swcstudio/tools/<tool>/features/`: the feature functions the CLI and
  GUI both call, with their JSON defaults in `swcstudio/tools/<tool>/configs/`.
- `swcstudio/cli/` and `swcstudio/gui/`: thin front ends over the features.
- `swcstudio/core/labeler/` is a copy of the SWC auto-labeler, synced by
  `scripts/sync_labeler.py`. Change the labeler in its own repository,
  <https://github.com/LazziLab/swc-autolabeler>, not here.

[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) describes the layers in more
detail.

- **New validation checks and alternative algorithms** can often be added
  as a plugin without changing SWC-Studio itself; see
  [docs/PLUGIN_DEVELOPMENT.md](docs/PLUGIN_DEVELOPMENT.md).
- **Changes to an SWC file** must go through `tracked_op` so they are
  recorded in the file's history; see
  [docs/PROVENANCE_SPEC.md](docs/PROVENANCE_SPEC.md).

## Pull requests

1. Open or comment on an issue first for anything larger than a small fix,
   so the approach can be agreed before you write it.
2. Work on a branch, and keep each pull request to one change.
3. Add or update tests for the behavior you change, and make sure the full
   suite passes.
4. Update the documentation and add a line under `Unreleased` in
   [CHANGELOG.md](CHANGELOG.md) for anything a user would notice.
5. Match the style of the code around your change.

Maintainers review pull requests and may ask for changes before merging.
Releases are cut from `main` as described in [RELEASE.md](RELEASE.md).
