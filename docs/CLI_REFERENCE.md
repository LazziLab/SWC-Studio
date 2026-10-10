# CLI Reference

This page documents the current public CLI surface for `swcstudio`.

## Install and Verify

The CLI is available from any of the three install paths — see
[Getting Started](GETTING_STARTED.md) for the full options.

```bash
# pip install (researchers / Python users)
python -m pip install swcstudio

# OR source install (developers)
python -m pip install -e .

# Verify
swcstudio --help
swcstudio doctor
```

If the script entrypoint is not on your path, use module mode:

```bash
python -m swcstudio.cli.cli --help
```

## Command Shape

```bash
swcstudio <command> [args] [options]
```

Public direct commands:

- `check`
- `doctor`
- `validate`
- `rule-guide`
- `split`
- `auto-typing`
- `radii-clean`
- `simplify`
- `index-clean`
- `auto-fix`
- `auto-label`
- `mesh-editing`
- `dendrogram-edit`
- `set-type`
- `set-radius`
- `move-node`
- `move-subtree`
- `connect`
- `disconnect`
- `delete-node`
- `delete-subtree`
- `insert`
- `models`
- `train`
- `plugins`

`swcstudio doctor` imports every runtime dependency, checks all packaged
configuration files, deserializes each auto-labeler model file, and
verifies that the GUI module imports. Use `swcstudio doctor --json` in
automated setup checks or `swcstudio doctor --quick` to skip model
deserialization (it then only checks that the model files exist).

The direct command form is the intended public interface. Internal grouped routes such as `validation`, `morphology`, or `geometry` still exist, but the direct form is the one to document and use.

## Common Option: `--config-json`

Most feature commands accept `--config-json` for one-run config overrides.

Example:

```bash
swcstudio radii-clean cell.swc --config-json '{"rules":{"max_passes":8}}'
```

This must be a JSON object and is merged into the feature config for that run.

## Output Behavior for Single-File Edits

Single-file edit commands update the source SWC directly and record
the operation in the per-file history archive:

- source file: `<input_folder>/<stem>.swc`
- history archive: `<input_folder>/<stem>_history.swcstudio`

This applies to:

- `auto-fix`
- `auto-label`
- `radii-clean` when the target is one file
- `index-clean` when the target is one file
- `simplify` when the target is one file
- `dendrogram-edit`
- `set-type`
- `set-radius`
- all geometry edit commands

The source SWC receives compact `# @PROV` pointer lines, and existing
SWC/SWC+ comment headers are preserved. No extra `--write` flag is
needed. Text reports are still produced by report-only commands such as
validation runs, and explicit commands such as `split`, `history
checkout`, and `history checkpoint` intentionally materialize separate
files.

## `check`

### `swcstudio check <file>`

- Purpose: print the same combined issue list the GUI builds when an SWC is opened
- Includes:
  - validation issues
  - suspicious radii issues
  - likely wrong-label issues
  - a simplification suggestion when available

Example:

```bash
swcstudio check cell.swc
```

## Direct Commands

For commands like `validate`, `radii-clean`, `simplify`, and `index-clean`:

- if the target is an SWC file, SWC-Studio runs the single-file workflow
- if the target is a folder, SWC-Studio runs the batch workflow

### `swcstudio validate <target>`

- Purpose: run validation on one SWC file or all SWC files in a folder
- Special behavior: if `target` is the literal `rule-guide`, prints the validation guide only

Examples:

```bash
swcstudio validate ./swc-folder
swcstudio validate cell.swc
swcstudio validate rule-guide
```

### `swcstudio split <folder>`

- Purpose: split SWC files by disconnected soma-root trees

Example:

```bash
swcstudio split ./swc-folder
```

### `swcstudio auto-typing <folder>`

- Purpose: auto-label every SWC file in one folder with the SWC
  auto-labeler, then rank the cells for review.
- Same as `swcstudio batch auto-typing`.
- Each labeled source SWC is updated in place and gets its own history.
- Files that are not labeled are skipped and listed under `failures`
  in the JSON summary. This covers files that fail the structural
  checks (`QC rejected`), files with several disconnected soma groups,
  and files that raise another error. They are never flagged.
- Prints a short stage guide to stderr before processing, so stdout
  stays JSON.
- Writes the flag review `<folder>_flag_review_<timestamp>.json` and a
  `.csv` copy into the folder. Open it in the GUI's Flag Review window.
  See [Reviewing Flagged Cells](documentation/auto-typing-backends.md#reviewing-flagged-cells).

Options:

- `--cell-class {auto,3-class,2-class}`: `auto` (the default) lets
  Stage 1 decide. `3-class` labels axon, basal and apical dendrites;
  `2-class` labels axon and dendrite (written as type 3). A chosen
  class that Stage 1 disputes is still used, and the cell is flagged as
  a class mismatch. `--cell-type` is an alias, and the labeler's names
  `pyramidal` and `interneuron` are accepted as values.
- `--flag-budget SHARE`: the share of each cell class flagged for
  review, riskiest first. A number above 0 and up to 1; the default is
  `0.10`. A class with fewer than 1 / SHARE cells uses the flag's fixed
  cut-off instead. A class mismatch is always flagged.
- `--no-flag`: do not score the quality flag. No flag review is
  written.
- `--model-dir DIR`: use the model folder at this path. It is the only
  folder searched.

The JSON summary includes `files_total`, `files_processed`,
`files_failed`, `files_qc_failed`, `total_nodes`, `total_type_changes`,
`files_flagged`, `flag_budget`, `flag_review_path`, `flagged_files`,
`per_file`, `failures` and `commits` (one history commit per labeled
file).

Examples:

```bash
swcstudio auto-typing ./swc-folder
swcstudio auto-typing ./swc-folder --model-dir ~/swc-models
swcstudio auto-typing ./swc-folder --cell-class 3-class --flag-budget 0.05
swcstudio auto-typing ./swc-folder --no-flag
```

Run `swcstudio models status` first if you want to confirm the engine
can find the model files on your machine.

### `swcstudio radii-clean <target>`

- Purpose: clean abnormal radii on a file or folder using the shared radii-clean backend
- File target: updates the source SWC and records operation history
- Folder target: records each processed SWC in place

Examples:

```bash
swcstudio radii-clean cell.swc
swcstudio radii-clean ./swc-folder
```

### `swcstudio simplify <target>`

- Purpose: run simplification on one SWC file or every SWC file in a folder
- File target: runs `geometry simplify`
- Folder target: runs batch simplification

Examples:

```bash
swcstudio simplify cell.swc
swcstudio simplify ./swc-folder
```

### `swcstudio index-clean <target>`

- Purpose: reorder and reindex one SWC file or every SWC file in a folder so parents come before children and IDs become continuous
- File target: runs the single-file index-clean workflow
- Folder target: runs batch index clean

Examples:

```bash
swcstudio index-clean cell.swc
swcstudio index-clean ./swc-folder
```

### `swcstudio rule-guide`

- Purpose: print the validation pre-check and rule guide only

## Single-File Repair Commands

### `swcstudio auto-fix <file>`

- Purpose: sanitize and revalidate one file

Example:

```bash
swcstudio auto-fix cell.swc
```

### `swcstudio auto-label <file>`

- Purpose: auto-label one SWC file with the SWC auto-labeler, as the
  GUI's Auto Label Editing panel does.
- Same as `swcstudio validation auto-label`.
- Changes only node types; geometry, parent IDs, and radii are preserved.
- Writes the labels back into the file and records the operation in its
  history.
- A file that fails the structural checks is not changed. The command
  exits with an error that says `QC rejected` and lists the reasons. A
  file whose soma nodes form several disconnected groups is refused;
  split it first with `swcstudio split`.
- The quality flag uses its fixed cut-off for a single file.

Options:

- `--cell-class {auto,3-class,2-class}`: as for `auto-typing`.
  `--cell-type` is an alias, and `pyramidal` and `interneuron` are
  accepted as values.
- `--no-flag`: do not score the quality flag.
- `--model-dir DIR`: use the model folder at this path. It is the only
  folder searched.

The command prints JSON. The main fields are:

| Field | Meaning |
|---|---|
| `nodes_total` | number of nodes in the file |
| `type_changes` | number of nodes whose type changed |
| `out_type_counts` | node count per type: 1 soma, 2 axon, 3 basal dendrite or dendrite, 4 apical dendrite |
| `cell_type` | the class used for labeling, by the labeler's name: `pyramidal` (3-class) or `interneuron` (2-class) |
| `cell_type_source` | `user` when you chose the class, `stage1` when Stage 1 decided |
| `stage1_cell_type`, `stage1_confidence` | Stage 1's prediction and its confidence |
| `class_mismatch` | `true` when the chosen class differs from Stage 1's prediction |
| `qc_result` | the structural checks: `passed`, `reasons`, `n_nodes` |
| `flag_result` | the quality flag (see below); empty with `--no-flag` |
| `notes` | what the labeler assumed about unlabeled input |
| `labeler_version` | the labeler version, for example `1.0.0` |
| `commit_sha`, `branch`, `input_sha`, `output_sha`, `diff_ref`, `ai_run_ref` | the history commit |

`flag_result` holds:

- `enabled`: `false` when the model folder has no flag for the cell
  (see `reason`)
- `flagged`: whether the cell is flagged for review
- `predicted_f1`: the predicted per-cell F1, from 0 to 1
- `suspicion`: 1 minus the predicted F1
- `threshold`: the flag's fixed cut-off, on the suspicion scale
- `flagged_by_threshold`: whether the suspicion reaches the cut-off
- `reason`: `below_cutoff`, `class_mismatch`, `no_flag_model` (the
  model folder has no flag), `no_flag_for_class`, or `null`
- `cell_type`, `stage1_cell_type`, `stage1_confidence`

A class mismatch is always flagged, and its `predicted_f1` is `null`.

Examples:

```bash
swcstudio auto-label cell.swc
swcstudio auto-label cell.swc --model-dir ~/my-models
swcstudio auto-label cell.swc --cell-class 2-class
swcstudio auto-label cell.swc --no-flag
```

To verify the engine can find its model files:

```bash
swcstudio models status
swcstudio models status --model-dir ~/my-models
```

### `swcstudio dendrogram-edit <file>`

- Purpose: reassign one subtree to a new node type

Flags:

- `--node-id`
- `--new-type`

Example:

```bash
swcstudio dendrogram-edit cell.swc --node-id 42 --new-type 3
```

### `swcstudio set-type <file>`

- Purpose: set one node type directly

Flags:

- `--node-id`
- `--new-type`

Example:

```bash
swcstudio set-type cell.swc --node-id 14169 --new-type 3
```

### `swcstudio set-radius <file>`

- Purpose: set one node radius directly

Flags:

- `--node-id`
- `--radius`

Example:

```bash
swcstudio set-radius cell.swc --node-id 42 --radius 0.75
```

## Geometry Commands

### `move-node`

Move one node to an absolute XYZ position.

```bash
swcstudio move-node cell.swc --node-id 42 --x 100 --y 120 --z 5
```

### `move-subtree`

Move a subtree by setting its root node to an absolute XYZ position.

```bash
swcstudio move-subtree cell.swc --root-id 40 --x 100 --y 120 --z 5
```

### `connect`

Set the end node parent to the start node.

```bash
swcstudio connect cell.swc --start-id 10 --end-id 22
```

### `disconnect`

Disconnect all parent-child edges along the path between two nodes.

```bash
swcstudio disconnect cell.swc --start-id 10 --end-id 22
```

### `delete-node`

Delete one node. Use `--reconnect-children` when the node has children and you want them reattached to the deleted node’s parent.

```bash
swcstudio delete-node cell.swc --node-id 1180
swcstudio delete-node cell.swc --node-id 13 --reconnect-children
```

### `delete-subtree`

Delete a full subtree rooted at one node.

```bash
swcstudio delete-subtree cell.swc --root-id 40
```

### `insert`

Insert one node after `start-id` and optionally before `end-id`.

```bash
swcstudio insert cell.swc --start-id 10 --end-id 22 --x 100 --y 120 --z 5
```

## History Commands

History commands inspect or materialize states from
`<stem>_history.swcstudio`.

```bash
swcstudio history log cell.swc
swcstudio history show cell.swc op-1
swcstudio history checkout cell.swc op-1 -o review_copy.swc
swcstudio history checkpoint cell.swc op-1 --label review
```

- `history log` shows user-facing operation IDs by default.
- `history show <op-id>` shows operation details and node-level old/new values.
- `checkout`, `checkpoint`, `tag`, `branch --from`, and `reproduce`
  accept either an operation ID such as `op-1` or a technical SHA.
- Add `--technical` to `history log` or `history show` when you need
  exact internal version/SHA details.

## Train Custom Auto-Typing Models

### `swcstudio train auto-typing`

Train the SWC auto-labeler on your own labeled SWC files. The command
runs the labeler's own trainer, the same code that built the shipped
models, and fits all four model files:

- `cell_type_classifier.pkl`: Stage 1, the cell-class classifier
- `branch_classifier.pkl`: Stage 2, the subtree classifier
- `branch_correction.joblib`: Stage 3, the branch correction for 3-class cells
- `flag_model.joblib`: Stage 4, the quality flag for each cell class

Required dataset layout:

```
<data-dir>/
    pyramidal/      3-class cells (axon, basal, apical)
        <files>.swc
    interneuron/    2-class cells (axon, dendrite)
        <files>.swc
```

Each SWC's type column (1=soma, 2=axon, 3=basal or dendrite, 4=apical)
is the per-node ground truth.

The trainer holds out a fixed share of the files, chosen from the file
name and the seed (20% by default). The labeler is not fitted on these
files. The quality flag is fitted on them.

Required flags:

- `--data-dir <dir>`   labeled-dataset root with `pyramidal/` and `interneuron/` subfolders
- `--output-dir <dir>` model folder to write

Optional flags:

- `--seed <int>`       seed of the held-out split (random when omitted; recorded)
- `--test-size <f>`    held-out share (default 0.20)
- `--device cpu|cuda`  where Stages 1 and 2 are fitted (default `cpu`; `cuda` needs an NVIDIA GPU and a CUDA build of XGBoost)
- `--skip <list>`      comma list of stages to leave out: `stage1`, `stage2`, `correction`, `flag`

Example:

```bash
swcstudio train auto-typing --data-dir ./labeled-dataset --output-dir ./my-models
# Then point auto-labeling at the trained models:
swcstudio auto-label cell.swc --model-dir ./my-models
# Or set the env var so all runs use them:
export SWCSTUDIO_MODEL_DIR=./my-models
```

Besides the model files, the output folder holds `eval_split.json` (the
split used) and `train_manifest.json` (counts and timings). The command
prints a JSON summary with `output_dir`, `seed`, `model_paths`,
`counts`, `timings_min` and `skipped`.

A run on the labeler's own corpus (about 9,500 training cells) takes
about 45 minutes; a few hundred cells take minutes.

For the full retraining workflow, see the
[Auto-Typing Engine](documentation/auto-typing-backends.md#training-custom-models) page.

## Models

### `swcstudio models status`

Print the auto-labeler's version and commit, the model folder in use,
and where the engine looked. Run this once after install to confirm the
bundled models are found, or any time the engine reports models missing.

```bash
swcstudio models status
swcstudio models status --model-dir ~/my-models
```

The output starts with the folders searched, in order. Each has a
marker: `[COMPLETE]`, `[INCOMPLETE: missing ...]`, `[no model files]`
or `[MISSING]`. A JSON summary follows with `labeler_version`,
`labeler_commit`, `model_dir` (the folder used), `models_ok`, and the
path and status of each model file (`stage1`, `stage2`, `correction`,
`flag`).

### Auto-Label Model Folder

Auto-labeling uses one model folder, `labeler-1.0.0/`, with four files:

| File | Stage | Required? |
|---|---|---|
| `cell_type_classifier.pkl` | Stage 1, cell class (3-class or 2-class) | yes |
| `branch_classifier.pkl` | Stage 2, subtree labels | yes |
| `branch_correction.joblib` | Stage 3, branch correction for 3-class cells | yes |
| `flag_model.joblib` | Stage 4, one quality flag per cell class | no; without it, no flag is scored |

A folder named with `--model-dir` (or `SWCSTUDIO_MODEL_DIR`) is the
only folder searched. It can hold the files itself or in a
`labeler-1.0.0/` subfolder. If it lacks a required file, auto-labeling
stops and reports what is missing. Without a named folder, the engine
searches the user data folder, the desktop app's model folder and the
package folder `swcstudio/data/models`, in that order. See
[Model Resolution](documentation/auto-typing-backends.md#model-resolution).

## Plugins

### `swcstudio plugins list`

List builtin and plugin-provided feature methods.

### `swcstudio plugins list-loaded`

List currently loaded plugin manifests.

### `swcstudio plugins load <module>`

Load one plugin module by import path.
