# Auto-Typing Engine

`SWC-Studio` labels SWC files with the SWC auto-labeler, version 1.0.1
(<https://github.com/LazziLab/swc-autolabeler>). The CLI, the GUI and the
Python API all run the same labeler. It labels every node as soma, axon,
basal dendrite or apical dendrite from the cell's geometry, so the input
file does not need labels.

The labeler's code is copied into `swcstudio/core/labeler/` by
`scripts/sync_labeler.py`. SWC-Studio runs it through
`swcstudio.core.auto_typing`.

## What The Engine Does

Each file goes through five stages:

| Stage | What it does | Model file |
|---|---|---|
| Stage 0, structural checks | Checks that the file holds one connected cell (a single root and no orphan nodes) with a soma, at least one neurite node, between 10 and 200,000 nodes, finite coordinates, and finite, non-negative radii. | none (rules) |
| Stage 1, cell class | Decides from whole-cell shape whether the cell is 3-class or 2-class. | `cell_type_classifier.pkl` |
| Stage 2, subtree labels | A gradient-boosted classifier labels each primary subtree that leaves the soma. | `branch_classifier.pkl` |
| Stage 3, topology rules and correction | Rules enforce the expected anatomy, for example a single axon tree and, for 3-class cells, a single apical tree. For 3-class cells, a learned correction then re-decides single branches within 150 µm of path length from the soma. | `branch_correction.joblib` |
| Stage 4, quality flag | Predicts the cell's per-cell F1, that is, how well the cell was labeled. | `flag_model.joblib` |

### Input files

- Before Stage 0, SWC-Studio checks the node lines themselves, because
  labels are written back to the file by node id: a node id used on more
  than one line, a node that is its own parent, and parent links that
  form a loop are refused (`QC rejected`, with the node ids), and the
  file is not labeled.
- A file with no soma label takes its root as the soma.
- A file with no neurite labels is fine. Every node other than the soma
  is treated as an unlabeled neurite, and the labels are predicted from
  geometry alone.
- The result `notes` say when either of these rules was used.
- A soma drawn with several points is merged into one node for
  labeling. All of its points are labeled soma.
- A file that fails Stage 0 is not labeled. The error says
  `QC rejected` and lists the reasons.
- A file whose soma nodes form several disconnected groups holds
  several cells. It is refused before labeling. Split it first with
  `swcstudio split <folder>` (`batch split`).
- A branch leaving the soma with no standard type anywhere in it (type 0
  or a custom type throughout) is labeled like any other branch.
- A labeled cell missing a compartment of its class gets a warning such
  as `no axon labeled`. When the input was labeled and had no axon
  either, the warning adds `(none in the input either)`: the tracing may
  simply not include it.

The labeled file (written in place by the CLI and the GUI batch, or as a
copy by `run_file` / `run_batch`) is the input file with only the type
field of each relabeled node line rewritten. Coordinates, radii and
parent IDs stay as written, and so do comment lines where they stand,
text after a `#` on a node line, columns after the seventh, spacing,
line endings, a UTF-8 byte order mark and lines that are not node lines.
In-place edits also add the two `# @PROV` history lines at the top. The
GUI's single-file **Auto Label Editing** applies the labels to the cell
open in the editor, which is saved like any other edit.

### Cell class

The labeler knows two cell classes. SWC-Studio names them by the labels
they assign:

| Cell class | Labels written | Labeler's name |
|---|---|---|
| 3-class | soma (1), axon (2), basal dendrite (3), apical dendrite (4) | `pyramidal` |
| 2-class | soma (1), axon (2), dendrite (written as type 3) | `interneuron` |

With **Auto**, the default, Stage 1 decides the class from whole-cell
shape. You can also choose the class yourself. Stage 1 still runs. If it
predicts the other class, the cell is labeled with the class you chose
and flagged as a **class mismatch**, so that you check it.

The `cell_type` fields in JSON results and in history records use the
labeler's names (`pyramidal`, `interneuron`). Commands and settings
accept both kinds of name.

### Quality flag

Stage 4 has one flag per cell class. It predicts the cell's per-cell F1,
a score from 0 to 1 of how well the cell's nodes were labeled. The
cell's suspicion score is 1 minus the predicted F1. The flag never
changes labels. It only ranks cells for review.

- **One file.** The cell is flagged when its predicted F1 falls below
  the flag's fixed cut-off. The cut-off is the score at which 10% of the
  cells the flag was fitted on are flagged.
- **A batch.** Each cell class flags its riskiest share of cells,
  rounded to the nearest whole cell. This share is the **flag budget**.
  The default is 10%, the share at which the labeler's flag was
  evaluated. A class with fewer than 1 / budget cells (fewer than 10
  cells at 10%) uses the fixed cut-off instead.
- A class mismatch is always flagged. Its predicted F1 is not scored.
- Files that were not labeled are listed in a batch's review, but they
  are never flagged.

See [Reviewing Flagged Cells](#reviewing-flagged-cells) for the review
workflow.

## Required Model Files

The model set is one folder, `labeler-1.0.1/`. It ships inside the
package at `swcstudio/data/models/labeler-1.0.1/`, so pip wheels, source
installs and the desktop apps can label without a download.

| Filename | Role | Required? | Size |
|---|---|---|---|
| `cell_type_classifier.pkl` | Stage 1, cell class | yes | about 1.3 MB |
| `branch_classifier.pkl` | Stage 2, subtree labels | yes | about 47.4 MB |
| `branch_correction.joblib` | Stage 3, branch correction | yes | about 1.2 MB |
| `flag_model.joblib` | Stage 4, one quality flag per cell class | no | about 0.4 MB |

Without `flag_model.joblib`, cells are labeled but no flag is scored.
The files form one set, because each model was trained with the
others. Keep them together in one folder.

`swcstudio/core/labeler/LABELER.json` records the labeler version, the
source commit and the SHA-256 of each model file.

Labeling needs numpy, scikit-learn 1.5.x, XGBoost 3.2.x, joblib and
pandas. It runs on the CPU and needs no GPU.

## Model Resolution

A folder you name is the only folder searched. You can name one with:

- `--model-dir` on the CLI, the `model_dir` argument in Python, or the
  **Model folder** picker in the GUI
- the `SWCSTUDIO_MODEL_DIR` environment variable
- the `model_dir` key of the auto-typing configuration file

They take precedence in that order. The named folder can hold the model
files itself, or hold them in a `labeler-1.0.1/` subfolder. If it lacks
a required file, auto-labeling stops and reports what is missing. It
does not fall back to other models.

Without a named folder, SWC-Studio searches these places in order and
uses the first complete one. In each place it looks in the
`labeler-1.0.1/` subfolder first:

1. the user data folder, where model updates are installed:
   - Windows: `%APPDATA%\swcstudio\models`
   - macOS: `~/Library/Application Support/swcstudio/models`
   - Linux: `~/.local/share/swcstudio/models`
2. the desktop app's model folder (the bundled app sets
   `SWCSTUDIO_BUNDLED_MODEL_DIR`)
3. the package folder `swcstudio/data/models`

If none of them is complete, a labeling run downloads the model set of
the latest release into the user data folder and searches again.
Installed copies already carry the model set, so this is a repair path
only. Status checks (`models status`, `doctor`, the GUI's `ready`
indicator) never download, and searching never creates the user data
folder; one that cannot be read is skipped.

Each run resolves the folder once and records it with every result and
history record as `model_record`:

| Key | Meaning |
|---|---|
| `model_dir` | the folder, with your home folder written as `~` |
| `model_source` | `argument`, `env`, `config`, `user` (the user data folder) or `bundled` (the app's or the package's folder) |
| `model_version` | the labeler version |
| `model_files` | the SHA-256 of each model file in the folder |
| `model_sha` | the SHA-256 of the lines `<file name> <sha256>` of `model_files`, sorted by file name: one fingerprint of the model set |

`swcstudio history reproduce` prints the model fingerprint, and passes
`--model-dir` only for a folder you named.

Model updates from GitHub Releases (Help -> Check for Updates) install
into the user data folder. An update replaces that folder's contents.
Keep models you trained in a folder of your own and name it with
`SWCSTUDIO_MODEL_DIR` or `--model-dir`.

Check which folder is used with:

```bash
swcstudio models status
swcstudio models status --model-dir /path/to/models
swcstudio doctor
```

`models status` prints the labeler version and commit, every folder it
searched with a marker such as `[COMPLETE]` or `[MISSING]`, the folder
it uses and its source, and the path of each model file. `doctor` also
loads each model file to check that it can be read; `doctor --quick`
only checks that the files exist. A folder without the optional
`flag_model.joblib` passes `doctor` with a note.

## Running Auto-Labeling

One file. The labels are written back into the file and recorded in its
history:

```bash
swcstudio auto-label cell.swc
swcstudio auto-label cell.swc --cell-class 3-class
swcstudio auto-label cell.swc --no-flag
swcstudio auto-label cell.swc --model-dir /path/to/models
```

A folder. Every `.swc` file is labeled in place, each with its own
history, and the cells are ranked for review. The flag review
(`<folder>_flag_review_<timestamp>.json` and `.csv`) and a text report
(`<folder>_batch_auto_typing_<timestamp>.txt`) are written into the
folder; the CLI and the GUI run the same batch:

```bash
swcstudio auto-typing ./swc-folder
swcstudio auto-typing ./swc-folder --cell-class 2-class
swcstudio auto-typing ./swc-folder --flag-budget 0.05
swcstudio auto-typing ./swc-folder --model-dir /path/to/models
```

The [CLI Reference](../CLI_REFERENCE.md) lists every option and the JSON
output.

In the GUI:

- **Morphology Editing -> Auto Label Editing** labels the current
  canvas. Choose the **Cell class** (`Auto (Stage 1 decides)`,
  `3-class (axon, basal, apical)` or `2-class (axon, dendrite)`), turn
  the **Quality flag** on or off, and optionally pick a **Model
  folder**. The indicator next to the folder says `ready` when the
  models are found. **Run** applies the labels to the canvas and
  records them in the file's history. The result shows the nodes, the
  type changes, the cell class and whether you chose it or Stage 1 did,
  the output type counts, the predicted per-cell F1, the flag state
  with its reason, a warning on a class mismatch, and notes about
  unlabeled input. **Show JSON** opens the auto-typing configuration
  file.
- **Batch Processing -> Auto Label Editing** labels a folder. Use
  **Select Folder**, then **Run**. The tab has the same model folder
  picker, cell class and quality flag controls, plus **Flag the
  riskiest [N %] of each cell class** (1% to 50%, 10% by default). Each
  source file is updated in place with its own history. When a batch
  with the quality flag on finishes, the Flag Review window opens.
  **Open Flag Review** opens the last batch's review again.

Window -> Show/Hide Auto Typing Guide shows a short description of the
stages.

## Reviewing Flagged Cells

When the quality flag is on, a batch ranks its labeled cells for review
and saves the ranking as a flag review:
`<folder>_flag_review_<timestamp>.json` with a `.csv` copy, written into
the folder. The GUI batch and `swcstudio auto-typing` both write one.
The **Flag Review** window shows it.

### Open the window

- It opens by itself when a GUI batch with the quality flag on
  finishes.
- **Window -> Show/Hide Flag Review** shows or hides it. It is a dock at
  the bottom of the main window, and it can float.
- **Open Review...** loads a saved review, for example one written by
  `swcstudio auto-typing`.
- **Open Flag Review** in the Batch Processing tab opens the last
  batch's review.

### What the table shows

Every file of the batch has a row. The rows start riskiest first:
flagged cells, then class mismatches, then the lowest predicted F1.
Files that were not labeled come last. Click a column header to sort by
that column.

| Column | Meaning |
|---|---|
| Flag | `●` when the cell is flagged |
| File | the file name |
| Class | 3-class or 2-class |
| Predicted F1 | the flag's predicted per-cell F1 |
| Rank | the cell's rank within its class, riskiest first, for example `1 / 13` |
| Reason | why the cell is flagged, or why the file was not labeled |
| Stage 1 | Stage 1's predicted class and its confidence |
| Nodes | the number of nodes |
| Changes | how many node types the labeler changed |
| Review | your mark: OK or Corrected |

The reasons are:

- `riskiest 10% of its class` (with the share you set)
- `predicted F1 below the flag's cut-off`, for a class too small for
  the share
- `class mismatch: Stage 1 predicts ...`
- `not labeled: ...` with the structural check results or the error

Flagged rows have a light red background, class mismatches a light
orange one, and files that were not labeled have grey text. A summary
line above the table counts the labeled cells, the flagged cells of
each class, the class mismatches, the reviewed cells and the files that
were not labeled.

### Controls

- **Flag the riskiest [N %] of each cell class** sets the flag budget,
  from 1% to 50%. The table updates at once.
- **Show** filters the rows: All cells, Flagged, 3-class, 2-class,
  Class mismatch, Not reviewed, Not labeled.

### Review a cell

1. Select a row and click **Open Cell**, or double-click the row. The
   labeled file opens in the main window.
2. Check the labels. Fix them with Manual Label Editing if needed.
3. Mark the row. **Mark OK** means the labels were checked and are
   right. **Mark Corrected** means you fixed them by hand. **Clear
   Mark** removes the mark. Only labeled cells can be marked.

The marks and the chosen share are saved back into the review file,
both the JSON and the CSV. You can close the window and resume later
with **Open Review...**. **Export CSV...** saves a copy of the review as
a CSV file where you choose.

The review JSON holds the folder, the time stamp, the labeler version,
the flag budget and one record per file. The CSV has one row per file
with the same fields.

## Runtime Performance

The first labeling run in a process reads the model files. Later runs in
the same process reuse them until a file changes on disk.

The GUI runs labeling in a separate process, so the window stays
responsive. After you apply an auto-label result in the GUI, the next
validation refresh reuses that result and does not label the cell
again.

## Configuration

The auto-typing settings live in
`swcstudio/tools/batch_processing/configs/auto_typing.json`:

| Key | Meaning |
|---|---|
| `enabled` | `true` by default |
| `model_dir` | a model folder to use when none is given (`--model-dir`, the GUI picker) and `SWCSTUDIO_MODEL_DIR` is not set; empty means the default search |
| `cell_type` | `auto`, `3-class` or `2-class` |
| `flag_enabled` | score the quality flag (`true` by default) |
| `flag_budget` | the share of each cell class a batch flags (`0.1` by default) |

How the values are used:

- Everywhere, including `run_file` and `run_batch` in
  `swcstudio.core.auto_typing`, `model_dir` applies when no model
  folder is given (`--model-dir`, the GUI picker, the `model_dir`
  argument) and `SWCSTUDIO_MODEL_DIR` is not set. Results record it as
  `model_source: config`.
- The GUI panels take their starting cell class and flag budget from
  the file. **Show JSON** opens the file for editing.
- The **Likely wrong labels** check uses the file's cell class.
- `swcstudio.api.batch_auto_typing` and
  `swcstudio.api.validation_auto_typing_file` use the file's values when
  you pass no options.
- The CLI takes the cell class and flag settings from its own options.

## Python API

```python
from swcstudio.core.auto_typing import (
    BatchOptions,
    backend_status,
    is_available,
    run_batch,
    run_file,
)

ok, reason = is_available()
print(backend_status())

opts = BatchOptions(cell_type="auto", flag_enabled=True, flag_budget=0.10)

single = run_file("cell.swc", opts)
print(single.cell_type, single.class_mismatch, single.flag_result)

folder = run_batch("./swc-folder", opts)
print(folder.files_flagged, folder.flag_review_path)
```

`run_file` returns a `FileResult` and does not change the input file.
By default it writes a labeled copy and a text report into
`<stem>_swc_studio_output/`; pass `write_output=False` and
`write_log=False` to skip them. `run_batch` does not change the input
files either. It writes labeled copies into the folder
`<folder>_batch_auto_typing_<timestamp>/` inside the input folder,
together with a text report and, when the flag is on,
`flag_review.json` / `flag_review.csv`. A labeled copy is its input with
only the relabeled type fields rewritten (`labeled_text`). `FileResult`
and `BatchResult` carry `model_record`; `FileResult` also carries
`warnings` and `unlabeled_nodes`. To label many files with one model
folder, resolve it once with `resolve_models(model_dir)` and pass it as
`models=`.

To label files in place with history, as the CLI and GUI do, use
`swcstudio.api.batch_auto_typing`. See
[API Documentation](../API_DOCUMENTATION.md).

## Training Custom Models

`swcstudio train auto-typing` runs the labeler's own trainer, the same
code that built the shipped models, on your labeled files:

```bash
swcstudio train auto-typing --data-dir my_dataset --output-dir my_models
```

Expected dataset layout:

```text
my_dataset/
  pyramidal/        3-class cells (axon, basal, apical)
    cell_001.swc
    cell_002.swc
  interneuron/      2-class cells (axon, dendrite)
    cell_a.swc
```

Each file's type column is the per-node ground truth: 1 soma, 2 axon,
3 basal dendrite (or dendrite), 4 apical dendrite.

The trainer holds out a fixed share of the files, chosen from the file
name and the seed (20% by default). The labeler is not fitted on these
files. The quality flag is fitted on them. The trainer then fits, in
order:

1. Stage 1, the cell-class classifier
2. Stage 2, the subtree classifier
3. Stage 3, the branch correction (3-class cells)
4. Stage 4, the quality flag for each cell class

Options:

- `--seed S` fixes the held-out split. Without it, a random seed is
  drawn and recorded.
- `--test-size 0.20` sets the held-out share.
- `--device cpu|cuda` sets where Stages 1 and 2 are fitted. The default
  is `cpu`. `cuda` needs an NVIDIA GPU and a CUDA build of XGBoost.
- `--skip stage1,stage2,correction,flag` leaves stages out. For
  example, `--skip flag` makes a model set without a quality flag.

Hidden `*.swc` files in the class folders (such as the `._name.swc`
files macOS writes next to copied files) and folders named `*.swc` are
refused before training starts; the error lists them.

The output folder holds the four model files, `eval_split.json` (the
split used) and `train_manifest.json` (counts and timings). Label with
it through `--model-dir`, `SWCSTUDIO_MODEL_DIR` or the GUI's Model
folder picker:

```bash
swcstudio auto-label cell.swc --model-dir my_models
```

A run on the labeler's own corpus (about 9,500 training cells) takes
about 45 minutes. A few hundred cells take minutes.

## Troubleshooting

### The model files are not found

```text
ERROR: auto-typing engine unavailable.
Auto-labeling cannot find its model files.
```

No complete model folder was found. The message lists every folder
searched and what is missing. Run `swcstudio models status`. If you
named a folder, check that it holds the three required files, directly
or in `labeler-1.0.1/`.

### A file is rejected

```text
cell.swc: QC rejected; not one connected cell (2 roots, 0 orphan nodes)
```

```text
cell.swc: QC rejected; node id used on more than one line: 1206
cell.swc: QC rejected; node 88 is its own parent
cell.swc: QC rejected; parent links form a loop through nodes 9001, 9002
```

The file failed the structural checks and was not labeled or changed.
The reasons say which check failed and name the nodes where they can.
Fix the file (give every node its own id, reconnect a node that is its
own parent, break a loop) with the validation and geometry tools or a
text editor, then label it again.

### Nodes were left unlabeled

```text
cell.swc: ... WARNING: 977 nodes on a branch with no standard type were left unlabeled
```

The labeler returned no label for some nodes. SWC-Studio never writes
such nodes as soma: they keep their type. Label them by hand, or give
the branch a neurite type and label the cell again.

### A file holds several cells

```text
cell.swc: multi-soma file (4 disconnected soma groups); split with `batch split` before auto-labeling
```

Run `swcstudio split <folder>` and label the split files.

### A required package is missing

```text
Auto-labeling could not import one of its required packages ...
```

Repair the environment with
`python -m pip install --upgrade --force-reinstall swcstudio`, then run
`swcstudio doctor`.

### The quality flag is not scored

The GUI shows `Quality flag: not scored`, and the CLI's `flag_result`
has `enabled: false` with the reason `no_flag_model` or
`no_flag_for_class`. The model folder has no `flag_model.joblib`, or
that file has no flag for the cell's class. The cell is still labeled.

### A model file cannot be loaded

The model files were written with scikit-learn 1.5 and XGBoost 3.2.
Keep the versions pinned in `pyproject.toml`, or train the models again
in your environment. `swcstudio doctor` loads each model file and
reports any failure.

## Maintaining The Copied Labeler

These steps are for developers who update the labeler inside
SWC-Studio:

1. `python scripts/sync_labeler.py --source <labeler checkout>` copies a
   committed labeler: its code into `swcstudio/core/labeler/` and its
   models into `swcstudio/data/models/labeler-<version>/`. It also
   writes `LABELER.json`. It refuses uncommitted changes unless you pass
   `--allow-dirty`, copies only files git tracks, checks the new copy
   (modules import, model checksums match) before replacing the old one,
   and removes model folders of other labeler versions. Do not edit
   `swcstudio/core/labeler/` by hand.
2. `python scripts/make_labeler_golden.py --source <labeler checkout>`,
   run in the labeler's own environment, rewrites
   `tests/fixtures/labeler/`: three synthetic cells and `expected.json`,
   whose labels are read from the file the labeler's own writer
   produces.
3. `tests/test_labeler_parity.py` requires SWC-Studio's labels, cell
   class and quality flag to equal the labeler's, and its labeled copies
   to equal the labeler writer's output byte for byte. The one
   deliberate difference: SWC-Studio does not write a branch the
   labeler folds into the soma as soma.

## Reference

| Public symbol | Module |
|---|---|
| `BatchOptions`, `BatchResult`, `FileResult` | `swcstudio.core.auto_typing` |
| `run_file`, `run_batch`, `run_folder` | `swcstudio.core.auto_typing` |
| `apply_flag_budget`, `write_flag_review` | `swcstudio.core.auto_typing` |
| `normalize_cell_class`, `cell_class_name` | `swcstudio.core.auto_typing` |
| `is_available`, `backend_status`, `resolve_models`, `labeled_text` | `swcstudio.core.auto_typing` |
| `train_user_models` | `swcstudio.core.auto_typing_train` |
| `resolve_model_dir`, `select_model_dir`, `model_record`, `recorded_model_dir`, `labeler_info` | `swcstudio.core.model_paths` |
| `rewrite_swc_fields` | `swcstudio.core.swc_io` |
