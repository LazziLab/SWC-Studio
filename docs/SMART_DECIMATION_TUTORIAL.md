# Simplification Tutorial

Simplification reduces redundant SWC geometry while preserving the main tree structure.

This page keeps the older filename for compatibility, but the current feature name in the app is `Simplification`.

## Backend and config

Current implementation path:

- `swcstudio.tools.morphology_editing.features.simplification`

Current config file:

- `swcstudio/tools/morphology_editing/configs/simplification.json`

## Main parameters

- `thresholds.epsilon`
- `thresholds.radius_tolerance`
- `flags.keep_tips`
- `flags.keep_bifurcations`
- `flags.keep_roots`
- `flags.keep_soma` (default `true`): keep every soma point, so a soma
  traced as an outline keeps its shape

A node whose type differs from its parent's (where the axon, a basal or
an apical dendrite starts) is always kept, whatever the flags, so
simplification never moves a compartment's start.

Simplification straightens paths: with the default `epsilon` of 2 µm,
tortuous neurites can lose a noticeable share of their cable length.
Lower `epsilon` when path length or tortuosity matters to your analysis.

## Parameter guidance

- lower `epsilon`
  - keeps more geometry detail
- higher `epsilon`
  - removes more intermediate points
- lower `radius_tolerance`
  - protects more radius-sensitive nodes
- higher `radius_tolerance`
  - allows stronger decimation around radius variation

## CLI usage

Run simplification on one file:

```bash
swcstudio simplify cell.swc
```

Use temporary overrides for one run:

```bash
swcstudio simplify cell.swc --config-json '{"thresholds":{"epsilon":1.2,"radius_tolerance":0.35},"flags":{"keep_tips":true,"keep_bifurcations":true}}'
```

Current single-file behavior:

- the source SWC is updated automatically
- the operation is recorded in `<stem>_history.swcstudio`
- SWC/SWC+ comment headers are preserved while `# @PROV` pointers are updated

## GUI usage

In `Geometry Editing -> Simplification`:

1. open the source SWC
2. adjust the JSON config if needed
3. run simplification
4. review the updated morphology
5. save or close the document to keep the source SWC updated and record
   the operation in history
