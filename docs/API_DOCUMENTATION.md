# API Documentation (Library Reference)

This document covers Python-callable APIs in SWC-Studio (Python package: `swcstudio`).

## How To Read This Doc

- Use `swcstudio.api` for most integrations.
- Use feature modules directly only when you need tool-specific behavior.
- Use plugin registry APIs to override method implementations.

## API Layers

1. Stable convenience API: `swcstudio.api`
2. Tool feature modules: `swcstudio.tools.<tool>.features.<feature>`
3. Plugin registry: `swcstudio.plugins`

## Recommended Import

```python
import swcstudio.api as swc
```

## 1) Public Convenience API (`swcstudio.api`)

Source: `swcstudio/api.py`

### Data Types

#### `BatchOptions`

Dataclass for auto-typing input options:

- `soma: bool` (default `True`): write the predicted soma label
- `axon: bool` (default `True`): write predicted axon labels
- `basal: bool` (default `True`): write predicted basal labels
- `apic: bool` (default `True`): write predicted apical labels
- `rad: bool` (default `False`): copy the parent's radius onto nodes
  whose radius is not positive
- `zip_output: bool` (default `False`): write a zip of the output
  folder (`run_batch` only)
- `cell_type: str | None` (default `None`): the cell class. `None` or
  `"auto"` lets Stage 1 decide. `"3-class"` (axon, basal, apical) or
  `"2-class"` (axon, dendrite) chooses it; the labeler's names
  `"pyramidal"` and `"interneuron"` are accepted too.
- `flag_enabled: bool` (default `True`): score the quality flag
- `flag_budget: float` (default `0.10`): in a batch, the share of each
  cell class flagged for review, riskiest first

A label type that is switched off keeps the file's original type on
those nodes.

#### `BatchResult`

Returned by folder runs (`run_batch` / `batch_auto_typing`). Fields
include `folder`, `out_dir`, `zip_path`, `files_total`, `files_processed`,
`files_failed`, `files_qc_failed`, `total_nodes`, `total_type_changes`,
`total_radius_changes`, `failures`, `per_file`, `log_path`,
`files_flagged`, `commits`, `flag_records`, `flag_review_path`, and
`flag_budget`. `flag_records` holds one record per file for the Flag
Review window. `out_dir` and `log_path` are `None` for the tracked
in-place batch feature; each `commits` item identifies the file's
independent commit SHA, branch, and operation ID.

#### `FileResult`

Returned by single-file runs (`run_file`). Fields include `input_file`,
`output_file`, `nodes_total`, `type_changes`, `radius_changes`,
`out_type_counts`, `cell_type`, `cell_type_source`, `stage1_cell_type`,
`stage1_confidence`, `class_mismatch`, `qc_result`, `flag_result`,
`notes`, `labeler_version`, `failures`, `change_details`, `log_path`,
`headers`, `rows`, `types`, `radii`.

`cell_type` is the class used for labeling, by the labeler's name
(`"pyramidal"` for 3-class, `"interneuron"` for 2-class).
`cell_type_source` is `"user"` or `"stage1"`. `flag_result` holds
`enabled`, `flagged`, `predicted_f1`, `suspicion` (1 minus the predicted
F1), `threshold`, `flagged_by_threshold` and `reason`; it is `None` when
the flag is off.

### Batch Processing

#### `batch_validate_folder(folder, *, config_overrides=None) -> dict`

Runs validation over all SWC files in a folder.

#### `batch_split_folder(folder, *, config_overrides=None) -> dict`

Splits each SWC by soma-root trees. Each derived output receives its own
history archive and lineage back to the source; the result includes
`output_commits`.

#### `batch_auto_typing(folder, *, options=None, config_overrides=None, progress_callback=None) -> BatchResult`

Runs the SWC auto-labeler over every SWC file in a folder. Each labeled
source file is updated in place and gets its own history commit; the
commit details are in `BatchResult.commits`. Files that are not labeled
are listed in `BatchResult.failures`.

When the quality flag is on, each cell class flags its riskiest share
(the flag budget, 10% by default), and the ranking is written into the
folder as `<folder>_flag_review_<timestamp>.json` with a `.csv` copy.
`BatchResult.flag_review_path` points to it, and
`BatchResult.flag_records` holds the same records.

The `config_overrides` dict is merged into the auto-typing configuration
file for this run. It accepts:

- `model_dir` (str): use this model folder; it is the only folder
  searched
- `cell_type` (str, default `"auto"`): `"auto"` lets Stage 1 decide;
  `"3-class"` or `"2-class"` chooses the class
- `flag_enabled` (bool, default `True`): score the quality flag
- `flag_budget` (float, default `0.10`): the share of each cell class
  flagged for review

`cell_type`, `flag_enabled` and `flag_budget` apply only when you pass
no `options`; otherwise the `BatchOptions` values are used.
`progress_callback(index, total, file_name)` is called once per file,
before the file is labeled.

#### `batch_radii_cleaning(folder, *, config_overrides=None) -> dict`

Runs radii cleaning on a folder, updating and tracking each source file
independently.

#### `batch_simplify_folder(folder, *, config_overrides=None) -> dict`

Runs simplification on all SWC files in a folder, updating and tracking
each source file independently.

#### `batch_index_clean_folder(folder, *, config_overrides=None) -> dict`

Runs index clean on all SWC files in a folder, updating and tracking
each source file independently.

#### `radii_clean_path(path, *, config_overrides=None) -> dict`

Runs radii cleaning on either one file or one folder.

### Validation

#### `validation_run_text(swc_text, *, config_overrides=None, feature_overrides=None) -> ValidationReport`

Runs structured validation from SWC text.

#### `validation_run_file(path, *, config_overrides=None, feature_overrides=None) -> ValidationReport`

Runs structured validation from file path.

#### `auto_fix_text(swc_text, *, config_overrides=None) -> dict`

Runs sanitize + validation from text.

#### `auto_fix_file(path, *, out_path=None, write_output=False, config_overrides=None) -> dict`

Runs sanitize + validation from file, optionally writes output.

#### `validation_auto_typing_file(file_path, *, options=None, config_overrides=None, output_path=None, write_output=True, write_log=True) -> FileResult`

Runs the SWC auto-labeler on a single file and returns a `FileResult`.
It does not change the input file. By default it writes a labeled copy
and a text report into `<stem>_swc_studio_output/`; `write_output` and
`write_log` turn these off. The `config_overrides` dict accepts
the same keys as `batch_auto_typing`: `model_dir`, `cell_type`,
`flag_enabled`, and `flag_budget`. Without `options`, the options come
from the configuration file merged with `config_overrides`.

`FileResult.flag_result` holds the quality flag: `enabled`, `flagged`,
`predicted_f1`, `suspicion`, `threshold`, `flagged_by_threshold`,
`reason`, `cell_type`, `stage1_cell_type`, and `stage1_confidence`. A
single file is flagged when its predicted per-cell F1 falls below the
flag's fixed cut-off, or when the chosen cell class differs from Stage
1's prediction.

#### `validation_auto_label_file(file_path, *, options=None, config_overrides=None, output_path=None, write_output=True, write_log=False) -> dict`

Runs the auto-labeler on one file and returns a dict with the labeled
SWC (`bytes`, `dataframe`) and the result fields (`nodes_total`,
`type_changes`, `out_type_counts`, `cell_type`, `cell_type_source`,
`stage1_cell_type`, `stage1_confidence`, `class_mismatch`, `notes`,
`labeler_version`, `qc_result`, `flag_result`, `change_details`). Only
node types change; the rest of each row is kept as written. With
`write_output=True` it writes a labeled copy and leaves the input file
unchanged.

The CLI `swcstudio auto-label` command wraps this in the provenance
history layer and writes the labels back into the source SWC.

#### `swcstudio.core.auto_typing.run_file(file_path, opts, *, output_path=None, write_output=True, write_log=True, model_dir=None) -> FileResult`

Direct engine entry point for a single file. The convenience wrappers
`validation_auto_typing_file` and `validation_auto_label_file` call
into this. It raises `ValueError` for a file that fails the structural
checks (the message says `QC rejected` and lists the reasons) or holds
several disconnected soma groups.

#### `swcstudio.core.auto_typing.run_batch(folder, opts, *, model_dir=None, progress_callback=None) -> BatchResult`

Direct engine entry point for a folder run. Unlike the tracked
`batch_auto_typing` convenience feature, it leaves the input files
unchanged. It writes labeled copies into
`<folder>/<folder>_batch_auto_typing_<timestamp>/`, together with a
text report and, when the flag is on, the flag review
(`flag_review.json` and `flag_review.csv`). It can also zip the copies
(`opts.zip_output`).

#### `swcstudio.core.auto_typing.apply_flag_budget(records, budget) -> None`

Decides `flagged` for each record of a batch, in place. Each cell class
flags its riskiest `budget` share, ranked by suspicion and rounded to
the nearest cell. A class with fewer than `1 / budget` cells uses the
flag's fixed cut-off. A class mismatch is always flagged; files that
were not labeled are never flagged. The Flag Review window calls it
when you change the share.

#### `swcstudio.core.auto_typing.write_flag_review(json_path, records, *, budget, folder, timestamp, labeler_version=None) -> Path`

Writes a batch's flag review to `json_path` and a CSV copy with the same
name beside it. The Flag Review window reads this file.

#### `swcstudio.core.auto_typing.normalize_cell_class(raw)` and `cell_class_name(cell_type)`

`normalize_cell_class` turns `"auto"`, `"3-class"`, `"2-class"`,
`"pyramidal"` or `"interneuron"` into the labeler's name, or `None` for
automatic. `cell_class_name` turns the labeler's name back into
`"3-class"`, `"2-class"` or `"auto"`.

#### `swcstudio.core.auto_typing.is_available(*, model_dir=None) -> tuple[bool, str]`

Check whether the engine can run: it needs a complete model folder and
the packages the labeler imports (joblib, scikit-learn, XGBoost and
pandas). Used by the GUI for the `ready` / `unavailable` indicator next
to the model folder picker, and by the CLI to fail fast before doing
any work.

#### `swcstudio.core.auto_typing.backend_status(*, model_dir=None) -> dict`

Structured status report, as printed by `swcstudio models status`: the
labeler version and commit, the model folder found, the path and status
of each model file, and a description of where the resolver looked.

#### `swcstudio.core.auto_typing_train.train_user_models(data_dir, output_dir, *, seed=None, device="cpu", skip=(), test_size=0.20) -> TrainingResult`

Runs the labeler's own trainer on a labeled SWC dataset (`pyramidal/`
and `interneuron/` subfolders) and writes the four model files to
`output_dir`, with `eval_split.json` and `train_manifest.json`. The
result holds `output_dir`, `seed`, `model_paths`, `counts`,
`timings_min`, and `skipped`. End users typically invoke this via
`swcstudio train auto-typing` on the CLI; the Python function exists
for scripted training pipelines.

#### `validation_index_clean_text(swc_text, *, config_overrides=None) -> dict`

Runs validation index clean in memory.

#### `validation_index_clean_file(path, *, out_path=None, write_output=False, write_report=True, config_overrides=None) -> dict`

Runs validation index clean on one file.

### Visualization

#### `build_mesh_from_text(swc_text, *, config_overrides=None) -> dict`

Builds mesh summary payload from SWC text.

#### `build_mesh_from_file(path, *, config_overrides=None) -> dict`

Builds mesh summary payload from file.

### Morphology Editing

#### `reassign_subtree_types(swc_text, *, node_id, new_type, config_overrides=None) -> dict`

Reassigns subtree node types in memory.

#### `reassign_subtree_types_in_file(path, *, node_id, new_type, out_path=None, write_output=False, config_overrides=None) -> dict`

File wrapper for subtree type reassignment.

#### `morphology_set_node_radius_text(swc_text, *, node_id, radius, config_overrides=None) -> dict`

Sets one node radius in memory.

#### `morphology_set_node_radius_file(path, *, node_id, radius, out_path=None, write_output=False, config_overrides=None) -> dict`

File wrapper for single-node radius editing.

#### `morphology_smart_decimation_text(swc_text, *, config_overrides=None) -> dict`

Legacy compatibility name for the shared simplification backend.

#### `morphology_smart_decimation_file(path, *, out_path=None, write_output=False, config_overrides=None) -> dict`

Legacy compatibility name for the shared simplification backend used by `Geometry Editing -> Simplification`.

### Geometry Editing

These functions expose the shared geometry-editing core used by the app:

- `geometry_move_node_absolute(df, node_id, x, y, z)`
- `geometry_move_subtree_absolute(df, root_id, x, y, z)`
- `geometry_reconnect_branch(df, source_id, target_id)`
- `geometry_disconnect_branch(df, start_id, end_id)`
- `geometry_delete_node(df, node_id, reconnect_children=False)`
- `geometry_delete_subtree(df, root_id)`
- `geometry_insert_node_between(df, start_id, end_id, x, y, z, radius=None, type_id=None)`
- `geometry_reindex_dataframe_with_map(df)`

### Plugins

#### `load_plugin_module(module_name, *, force_reload=False) -> dict`

Load one plugin module (manifest validation + method registration).
Note: registrations are process-local; use `autoload_plugins_from_environment`
for automatic loading in new CLI processes.

#### `load_plugins(modules) -> list[dict]`

Load multiple plugin modules and return per-module status.

#### `autoload_plugins_from_environment(env_var="SWCSTUDIO_PLUGINS") -> list[dict]`

Load comma-separated plugin module list from environment variable.

#### `register_plugin_manifest(manifest) -> PluginManifest`

Register validated plugin manifest metadata.

#### `register_plugin_method(plugin_id, feature_key, method_name, func) -> None`

Register a plugin-owned method for one feature/method key.

#### `unregister_plugin(plugin_id) -> None`

Remove plugin manifest and methods owned by that plugin.

#### `register_method(feature_key, method_name, func) -> None`

Register plugin override method.

#### `unregister_method(feature_key, method_name) -> None`

Remove plugin override method.

#### `list_feature_methods(feature_key) -> dict`

List methods for a single feature key.

#### `list_all_feature_methods() -> dict`

List methods for all feature keys.

#### `list_plugins() -> list[dict]`

List loaded/registered plugin manifests.

#### `get_plugin(plugin_id) -> dict | None`

Get one plugin manifest by plugin id.

## 2) Tool Feature Module Entry Points

These are callable if you want per-feature direct imports.

## Batch Processing

- `swcstudio.tools.batch_processing.features.batch_validation`
  - `validate_swc_text(...)`
  - `validate_folder(...)`
- `swcstudio.tools.batch_processing.features.swc_splitter`
  - `split_swc_text(...)`
  - `split_folder(...)`
- `swcstudio.tools.batch_processing.features.auto_typing`
  - `run_folder(...)`
  - `options_to_dict(...)`
- `swcstudio.tools.batch_processing.features.radii_cleaning`
  - `clean_swc_text(...)`
  - `clean_file(...)`
  - `clean_folder(...)`
  - `clean_path(...)`
- `swcstudio.tools.batch_processing.features.simplification`
  - `run_folder(...)`
- `swcstudio.tools.batch_processing.features.index_clean`
  - `run_folder(...)`

## Validation

- `swcstudio.tools.validation.features.run_checks`
  - `validate_text(...)`
  - `validate_file(...)`
- `swcstudio.tools.validation.features.auto_fix`
  - `auto_fix_text(...)`
  - `auto_fix_file(...)`
- `swcstudio.tools.validation.features.auto_typing`
  - `run_file(...)`
  - `auto_label_file(...)`
- `swcstudio.tools.validation.features.radii_cleaning`
  - `clean_path(...)`
  - `clean_file(...)`
  - `clean_folder(...)`
- `swcstudio.tools.validation.features.index_clean`
  - `index_clean_text(...)`
  - `index_clean_file(...)`

## Visualization

- `swcstudio.tools.visualization.features.mesh_editing`
  - `build_mesh_from_text(...)`
  - `build_mesh_from_file(...)`

## Morphology Editing

- `swcstudio.tools.morphology_editing.features.dendrogram_editing`
  - `reassign_subtree_types(...)`
  - `reassign_subtree_types_in_file(...)`
- `swcstudio.tools.morphology_editing.features.manual_radii`
  - `set_node_radius_text(...)`
  - `set_node_radius_file(...)`
- `swcstudio.tools.morphology_editing.features.simplification`
  - `simplify_dataframe(...)`
  - `simplify_swc_text(...)`
  - `simplify_file(...)`
  - shared simplification backend used by the current Geometry Editing tool

## 3) Validation Engine and Models

Module: `swcstudio.tools.validation`

Exposed helpers:

- `load_validation_config(...)`
- `build_precheck_summary(...)`
- `run_validation_text(...)`
- `register_check(...)`
- `register_plugin_check(...)`
- `get_check(...)`
- `list_checks(...)`

Result models:

- `PreCheckItem`
- `CheckResult`
- `ValidationReport`

## Simplification (How It Works)

1. Build directed graph from SWC `id`/`parent`.
2. Protect structural nodes (roots, optional tips, optional bifurcations).
3. Extract anchor-to-anchor linear paths.
4. Apply RDP with `thresholds.epsilon`.
5. Protect radius-sensitive nodes where:
   - `abs(node_radius - path_mean_radius) / path_mean_radius > thresholds.radius_tolerance`
6. Rewire kept nodes to nearest kept ancestor.

Key parameters (`simplification.json`):

- `thresholds.epsilon`
- `thresholds.radius_tolerance`
- `flags.keep_tips`
- `flags.keep_bifurcations`
- `flags.keep_roots`

## Plugin Override Pattern

```python
from swcstudio.plugins import load_plugin_module, register_method

def my_method(*args, **kwargs):
    ...

register_method("batch_processing.auto_typing", "lab_summary", my_method)
load_plugin_module("my_lab_plugins.summary_plugin")
```

## Plugin Contract (Versioned)

External plugin modules should expose:

1. `PLUGIN_MANIFEST` dictionary (or `get_plugin_manifest()`)
2. one of:
   - `register_plugin(registrar)` function
   - `PLUGIN_METHODS` mapping/list

Minimal `PLUGIN_MANIFEST`:

```python
PLUGIN_MANIFEST = {
    "plugin_id": "my_lab.summary_plugin",
    "name": "Custom Summary Plugin",
    "version": "0.1.0",
    "api_version": "1",
    "capabilities": ["batch_processing", "custom_methods"],
}
```

## Config Files

Per-feature config path pattern:

- `swcstudio/tools/<tool>/configs/<feature>.json`

Examples:

- `swcstudio/tools/validation/configs/default.json`
- `swcstudio/tools/batch_processing/configs/radii_cleaning.json`
- `swcstudio/tools/morphology_editing/configs/simplification.json`
