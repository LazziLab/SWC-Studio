# Checks And Issues Reference

This is the canonical reference for:

- validation checks
- GUI issue types
- related tools
- parameter sources
- the current radii-cleaning and auto-labeling algorithms

Primary code sources:

- `swcstudio/core/validation_catalog.py`
- `swcstudio/core/validation_checks/native_checks.py`
- `swcstudio/core/validation_checks/neuron_morphology_checks.py`
- `swcstudio/core/issues.py`
- `swcstudio/core/radii_cleaning.py`
- `swcstudio/core/auto_typing/` (engine package)
- `swcstudio/core/labeler/` (the SWC auto-labeler's code, copied by `scripts/sync_labeler.py`)
- `swcstudio/tools/validation/configs/default.json`
- `swcstudio/tools/batch_processing/configs/radii_cleaning.json`
- `swcstudio/tools/batch_processing/configs/auto_typing.json`

## How To Read This Page

- `Source`
  - `native`: implemented directly in SWC-Studio
  - `NeuroM`: wrapped from NeuroM
- `Severity`
  - current default from `swcstudio/tools/validation/configs/default.json`
- `JSON`
  - `yes`: params are exposed in JSON
  - `enabled/severity only`: the check can be turned on/off and reclassified, but has no extra exposed params
- `Related tool`
  - the tool the GUI routes to for inspection or repair

## Pipeline Overview

The app raises issues from three layers:

1. `Validation checks`
   - deterministic checks run through the shared validation engine
2. `Suspicious-item detectors`
   - suspicious radii and suspicious labels
3. `Issue normalization`
   - validation rows and suspicious findings become GUI issues

So one SWC can raise:

- validation issues
- blocked-check issues
- suspicious radii issues
- suspicious label issues

## Validation Check Matrix

There are 32 checks: 21 native and 11 wrapped from NeuroM.

Most checks read a working copy of the file in which each connected soma
group is merged into one node, so a complex soma does not trip them. Merging
removes the merged nodes' IDs and makes each soma a root, so the checks about
the file's own IDs and parent links (`no_duplicate_node_ids`,
`no_parent_cycles`, `no_missing_parents`, `no_self_loop`,
`parent_id_less_than_child_id` and `no_node_id_gaps`) read the file as
written.

### File Integrity

These run first, so they report even when `multiple_somas` stops validation.

| Key | Issue title | Source | Severity | How checked | Params | JSON | Related tool |
|---|---|---|---|---|---|---|---|
| `no_duplicate_node_ids` | `Duplicate node IDs found` | native | critical | Fails when a node ID is on more than one row, and lists those IDs. A parent ID that names a duplicated ID could mean either row, so the NeuroM checks are blocked, and Index Clean and the geometry edits refuse the file. | none | enabled/severity only | none: renumber or remove the duplicated rows in the file, then reload it |
| `no_parent_cycles` | `Parent cycles found` | native | critical | Follows parent IDs and fails on nodes whose chain closes on itself (two or more nodes, for example A's parent is B and B's parent is A). Those nodes, and the nodes below them, never reach a root. The NeuroM checks are blocked, and Index Clean refuses the file. A node that is its own parent is left to `no_self_loop`. | none | enabled/severity only | Geometry Editing (disconnect, then reconnect) |
| `no_missing_parents` | `Missing parent nodes found` | native | critical | Fails on nodes whose parent ID (other than -1) names no row in the file, often from a truncated file, and lists those nodes. They and the nodes below them are unattached. The NeuroM checks are blocked, and Index Clean and Split refuse the file rather than turning such a node into a new root. | none | enabled/severity only | Geometry Editing (reconnect the node to its real parent) |

### Structural Presence

| Key | Issue title | Source | Severity | How checked | Params | JSON | Related tool |
|---|---|---|---|---|---|---|---|
| `valid_soma_format` | `Complex soma format found` | native | warning | Groups connected type-`1` nodes and fails if any soma group has more than one node. The remaining checks still run, on a working copy with each soma group merged. Merging (here and in the `Consolidate Soma` action) puts one node at the group's centroid with the radius of the sphere of equal surface: the parent-child links inside the group are summed as frustum side areas and `r = sqrt(area / 4π)`, as L-Measure and the auto-labeler do. A standard three-point soma (a centre and two points at ±r, each of radius r) keeps radius r; a single soma node keeps its radius. | none | enabled/severity only | `Consolidate Soma` action |
| `multiple_somas` | `Multiple somas found` | native | critical | Runs after temporary soma consolidation and fails if more than one soma anchor remains. The file holds several cells, so validation stops here; split it first. | none | enabled/severity only | `Split` action |
| `has_soma` | `Soma missing` | native | critical | Fails if no node has type `1`. | none | enabled/severity only | Manual Label Editing / Auto Label Editing |
| `no_invalid_negative_types` | `Invalid negative types found` | native | critical | Fails if any node type is `< 0`. | none | enabled/severity only | Manual Label Editing |
| `custom_types_defined` | `Custom types need definitions` | native | warning | Requires every used custom type (`>= 5`) to have a saved name and color. | none | enabled/severity only | `Define Custom Types` dialog |
| `has_axon` | `Axon missing` | native | warning | Fails if no node has type `2`. | none | enabled/severity only | Manual Label Editing / Auto Label Editing |
| `has_basal_dendrite` | `Basal dendrite missing` | native | warning | Fails if no node has type `3`. | none | enabled/severity only | Manual Label Editing / Auto Label Editing |
| `has_apical_dendrite` | `Apical dendrite missing` | native | warning | Fails if no node has type `4`. | none | enabled/severity only | Manual Label Editing / Auto Label Editing |

### Radius And Size

| Key | Issue title | Source | Severity | How checked | Params | JSON | Related tool |
|---|---|---|---|---|---|---|---|
| `all_neurite_radii_nonzero` | `Invalid neurite radii found` | native | critical | Fails on non-soma nodes with non-finite or `<= 0` radius. | none | enabled/severity only | Manual Radii Editing |
| `soma_radius_nonzero` | `Invalid soma radius found` | NeuroM | critical | Calls NeuroM `has_nonzero_soma_radius`. | `threshold=0.0` | yes | Manual Radii Editing |
| `no_ultranarrow_sections` | `Ultranarrow sections found` | NeuroM | warning | Calls NeuroM `has_no_narrow_neurite_section`. | `radius_threshold=0.05`, `considered_section_min_length=50.0` | yes | Manual Radii Editing |
| `no_ultranarrow_starts` | `Ultranarrow branch starts found` | NeuroM | warning | NeuroM's `has_no_narrow_start` rule, evaluated by SWC-Studio on the NeuroM morphology: a neurite fails when its first point's radius is below `frac` times the next point's. NeuroM 4.0.5 errors when a neurite branches at its first node; there the next point on each branch is used, and the neurite fails if its first point is narrow against any of them. | `frac=0.9` | yes | Manual Radii Editing |
| `no_fat_terminal_ends` | `Oversized terminal ends found` | NeuroM | warning | Calls NeuroM `has_no_fat_ends`. | `multiple_of_mean=2.0`, `final_point_count=5` | yes | Manual Radii Editing |
| `radius_upper_bound` | `Oversized radii found` | native | warning | Fails when any radius is above `max_radius`. | `max_radius=20.0` | yes | Auto Radii Editing |

### Length And Geometry

| Key | Issue title | Source | Severity | How checked | Params | JSON | Related tool |
|---|---|---|---|---|---|---|---|
| `all_section_lengths_nonzero` | `Zero-length sections found` | native | critical | Uses segment-based section validity and fails on zero or invalid derived section length. | none | enabled/severity only | Geometry Editing |
| `all_segment_lengths_nonzero` | `Zero-length segments found` | native | critical | Computes Euclidean parent-child distance and fails on non-finite or `<= 0` length. | none | enabled/severity only | Geometry Editing |
| `no_back_tracking` | `Geometric backtracking found` | NeuroM | warning | Calls NeuroM `has_no_back_tracking`. | none | enabled/severity only | Geometry Editing |
| `no_flat_neurites` | `Flattened neurites found` | NeuroM | warning | Calls NeuroM `has_no_flat_neurites`. | `tol=0.1`, `method="ratio"` | yes | Geometry Editing |
| `no_duplicate_3d_points` | `Duplicated points found` | native | critical | Fails only when the full `x,y,z` triplet is duplicated. | none | enabled/severity only | Geometry Editing |
| `no_extreme_spatial_jump` | `Extreme spatial jumps found` | native | warning | Flags parent-child segments above a threshold derived from absolute, median-ratio, and MAD-based bounds. | `min_jump_um=200.0`, `median_ratio=10.0`, `mad_scale=12.0`, `mad_floor_um=1.0` | yes | Geometry Editing |

### Topology

| Key | Issue title | Source | Severity | How checked | Params | JSON | Related tool |
|---|---|---|---|---|---|---|---|
| `no_dangling_branches` | `Dangling branches found` | native | critical | Fails on non-soma nodes with `parent == -1`. | none | enabled/severity only | Geometry Editing |
| `no_self_loop` | `Self loops found` | native | critical | Fails when `parent == id`, read from the file as written. | none | enabled/severity only | Geometry Editing |
| `no_single_child_chains` | `Single-child chains found` | NeuroM | warning | Calls NeuroM `has_no_single_children`. | none | enabled/severity only | Geometry Editing |
| `has_unifurcation` | `Unifurcation found` | NeuroM | warning | Calls NeuroM `has_unifurcation`. | none | enabled/severity only | Geometry Editing |
| `has_multifurcation` | `Multifurcation found` | NeuroM | warning | Calls NeuroM `has_multifurcation`. | none | enabled/severity only | Geometry Editing |

### Index Consistency

| Key | Issue title | Source | Severity | How checked | Params | JSON | Related tool |
|---|---|---|---|---|---|---|---|
| `no_section_index_jumps` | `Sections jump too far along Z` | NeuroM | critical | Calls NeuroM `has_no_jumps` and uses it as a Z-axis consistency check. | `max_distance=30.0`, `axis="z"` | yes | Geometry Editing |
| `no_root_index_jumps` | `Neurite roots too far from soma` | NeuroM | critical | Calls NeuroM `has_no_root_node_jumps`. | `radius_multiplier=2.0` | yes | Geometry Editing |
| `parent_id_less_than_child_id` | `Parent-child ID order violations found` | native | warning | Fails when a valid in-file parent ID is `>=` child ID, read from the file as written. | none | enabled/severity only | Index Clean |
| `no_node_id_gaps` | `Node ID gaps found` | native | info | Fails when sorted unique node IDs skip integers, read from the file as written (merging a complex soma does not count as a gap). | none | enabled/severity only | Index Clean |

Index Clean refuses a file with duplicate node IDs or a parent cycle,
including a node that is its own parent, and writes nothing. The error names
the IDs.

## GUI-Only Issue Generators

These are shown in the issue-driven workflow but are not entries in `default.json`.

| Source key | Title | How generated | Config source | Related tool |
|---|---|---|---|---|
| `blocked_validation_checks` | `Checks blocked by ...` | Grouped from validation rows that could not run because morphology building failed. Duplicate node IDs and parent cycles (including a node that is its own parent) block the NeuroM checks with their own titles. | none | Validation, Manual Label Editing or Geometry Editing, depending on cause |
| `radii_outlier_batch` | `Outlier radii detected` | Runs the shared radii-cleaning engine and groups nodes the cleaner would change. | `swcstudio/tools/batch_processing/configs/radii_cleaning.json` | Auto Radii Editing |
| `type_suspicion_batch` | `Likely wrong labels` | Runs the auto-labeler on the file (labels only; the quality flag is not scored) and groups the nodes whose current type differs from the labeler's type. In the GUI it runs again on each validation refresh. | `swcstudio/tools/batch_processing/configs/auto_typing.json` (its `cell_type` and `model_dir`) | Auto Label Editing |

## Issue Model

Issues normalized in `swcstudio/core/issues.py` use these fields:

- `issue_id`
- `severity`
- `certainty`
- `domain`
- `title`
- `description`
- `node_ids`
- `section_ids`
- `tool_target`
- `suggested_fix`
- `status`
- `source_key`
- `source_label`
- `source_category`
- `source_payload`

## Radii Cleaning Algorithm

The suspicious radii issue and Auto Radii Editing use the same shared backend in `swcstudio/core/radii_cleaning.py`.

### Current method

The current cleaner is directed-path and fixed-point based:

1. build topology and directed branch paths
2. look up each node type's plausible radius range (fixed, not taken from
   the file)
3. `Pass 1`: local outlier repair
4. `Pass 2`: taper enforcement
5. `Pass 3`: local polynomial smoothing
6. re-apply taper
7. repeat until no issue-visible changes remain

Soma radii are always preserved.

Plausible radius ranges (µm), `sanity_bounds`:

| Type | Minimum | Maximum |
|---|---|---|
| axon (2) | 0.05 | 5 |
| basal dendrite (3) | 0.05 | 8 |
| apical dendrite (4) | 0.05 | 10 |
| any other neurite type | 0.05 | 10 |

A radius above its type's maximum is always flagged. A radius below the
minimum is changed only when it is an outlier among its neighbours, or when
`small_radius_zero_only` is off: values such as 0.004 are common
placeholders for an unknown radius, and replacing them would invent data.
Only radii inside the range serve as reference or replacement values, so a
file whose radii are mostly wrong cannot pull good values toward them. The
taper rule caps a node only by a parent whose radius is plausible.

### Reasons recorded per changed node

- `non_finite`
- `non_positive`
- `below_type_min`
- `above_type_max`
- `local_outlier`
- `taper_cap`
- `post_smooth_taper_cap`
- `axon_floor`
- `savitzky_golay`

### Current radii-clean config keys

Main config file:

- `swcstudio/tools/batch_processing/configs/radii_cleaning.json`

Current top-level rule groups:

- `small_radius_zero_only`
- `sanity_bounds.default` (`min_radius`, `max_radius`)
- `sanity_bounds.per_type` (`enabled`, `min_radius`, `max_radius`; configs
  from earlier versions with `lower_abs`/`upper_abs` still work)
- `local_outlier`
- `taper`
- `axon_floor`
- `savgol`
- `fixed_point`
- `replacement`

Important defaults:

- `local_outlier.window_nodes = 5`
- `local_outlier.max_percent_deviation = 0.5`
- `taper.slack = 0.05`
- `axon_floor.enabled = false` (raises axon radii below `axon_floor.min_radius = 0.12` when on)
- `savgol.window_nodes = 7`
- `savgol.polyorder = 2`
- `fixed_point.max_passes = 32`
- `fixed_point.min_effective_delta = 0.005`

## Auto Labeling Algorithm

The suspicious label issue and Auto Label Editing run the same
engine, the SWC auto-labeler 1.0.0. It is implemented in:

- `swcstudio/core/auto_typing/` (engine package)
- `swcstudio/core/labeler/` (the labeler's own code)
- `swcstudio/tools/batch_processing/configs/auto_typing.json` (user-editable settings)

The suspicious label issue compares labels only. It does not score the
quality flag.

### Pipeline architecture

Each file goes through five stages. The model files live in the
`labeler-1.0.0/` model folder.

| Stage | Files | What it produces |
|---|---|---|
| **Stage 0, structural checks** | none (rules) | Pass or reject. A file must hold one connected cell (a single root and no orphan nodes) with a soma, at least one neurite node, between 10 and 200,000 nodes, finite coordinates, and finite, non-negative radii. A rejected file is not labeled; the error says `QC rejected` and lists the reasons. A file without a soma label takes its root as the soma. A file without neurite labels is labeled from geometry alone. |
| **Stage 1, cell class** | `cell_type_classifier.pkl` | 3-class (axon, basal, apical) or 2-class (axon, dendrite), decided from whole-cell shape, unless the user chooses the class. |
| **Stage 2, subtree labels** | `branch_classifier.pkl` | A gradient-boosted classifier labels each primary subtree that leaves the soma. |
| **Stage 3, topology rules and correction** | `branch_correction.joblib` | Rules enforce the expected anatomy, for example a single axon tree and, for 3-class cells, a single apical tree. For 3-class cells, a learned correction then re-decides single branches within 150 µm of path length from the soma. |
| **Stage 4, quality flag** | `flag_model.joblib` | One flag per cell class. It predicts the cell's per-cell F1. A single file is flagged below the flag's fixed cut-off; a batch flags the riskiest share of each cell class (10% by default). It never changes labels. |

A file whose soma nodes form several disconnected groups is refused
before Stage 0. Split it first.

### Cell class and apical labels

Only 3-class cells get apical labels (type 4). 2-class cells get axon
(type 2) and dendrite labels; the dendrites are written as type 3.

The cell class comes from Stage 1 when it is set to `auto`. The user can
choose `3-class` or `2-class` instead in the GUI, on the CLI
(`--cell-class`) or in the configuration file. Stage 1 still runs. If
it predicts the other class, the chosen class is used and the cell is
flagged as a class mismatch.

### Config keys

The user-editable JSON at
`swcstudio/tools/batch_processing/configs/auto_typing.json` is small.
The engine's behavior is set by the trained model files, not by
hand-tuned weights. Keys:

- `enabled`: `true` by default
- `model_dir`: a model folder to use. An empty string means the
  `SWCSTUDIO_MODEL_DIR` folder when that variable is set, and otherwise
  the default search: the user data folder, the desktop app's model
  folder, then the package folder.
- `cell_type`: `auto` (Stage 1 decides), `3-class` or `2-class`
- `flag_enabled`: score the quality flag (`true` by default)
- `flag_budget`: the share of each cell class a batch flags for review
  (`0.1` by default)
- `notes`: a description for people; the engine does not read it

To use your own trained models everywhere without editing this file,
set the `SWCSTUDIO_MODEL_DIR` environment variable to their folder.
Model updates install into the user data folder (`%APPDATA%\swcstudio\models\`
on Windows, `~/Library/Application Support/swcstudio/models/` on macOS,
or `~/.local/share/swcstudio/models/` on Linux) and replace its
contents, so do not keep your own models there.

See [Auto-Typing Engine](documentation/auto-typing-backends.md) for
model resolution, the flag review, and training.

## How Checks Become GUI Issues

Current conversion rules:

- validation `pass`
  - no issue
- validation `info` (a failing check whose severity is `info`)
  - `info` issue
- validation `warning` (a failing check whose severity is `warning`)
  - `warning` issue
- validation `fail` (a failing check whose severity is `error`)
  - `critical` issue
- blocked or dependency-failed checks
  - collapsed into grouped blocked-check issues
- suspicious radii changes
  - one aggregated `Outlier radii detected` issue
- suspicious label changes
  - one aggregated `Likely wrong labels` issue

## Where To Edit Behavior

- validation check enable/severity/params
  - `swcstudio/tools/validation/configs/default.json`
- radii cleaning behavior
  - `swcstudio/tools/batch_processing/configs/radii_cleaning.json`
- auto labeling behavior
  - `swcstudio/tools/batch_processing/configs/auto_typing.json`

## Related Docs

- [Validation Rules](VALIDATION_RULES.md)
- [Radii Cleaning Tutorial](RADII_CLEANING_TUTORIAL.md)
- [GUI Workflow Guide](GUI_WORKFLOW.md)
- [CLI Reference](CLI_REFERENCE.md)
