# Validation System And Rules

This page explains the validation backend at a high level.

For the full per-check matrix, issue titles, params, and related tools, use:

- [Checks And Issues Reference](CHECKS_AND_ISSUES_REFERENCE.md)

## Shared Backend

GUI and CLI both use the same validation stack:

- engine: `swcstudio.core.validation_engine`
- registry: `swcstudio.core.validation_registry`
- result models: `swcstudio.core.validation_results`
- labels and categories: `swcstudio.core.validation_catalog`
- feature wrapper: `swcstudio.tools.validation.features.run_checks`

Common flow:

1. load validation config
2. build the pre-check summary
3. run enabled checks
4. return a structured `ValidationReport`
5. render results in GUI, CLI, or reports

There are 32 checks: 21 native and 11 wrapped from NeuroM.

## What The Checks Read

`ValidationContext` holds two copies of the file:

- `original_arr`: the rows as written
- `arr`: a working copy with each connected soma group merged into one node

Most checks read the working copy, so a complex soma is reported once (by
`valid_soma_format`) and does not trip the other checks. Merging removes the
merged nodes' IDs and makes each soma a root, so the checks about the file's
own IDs and parent links read `original_arr`: `no_duplicate_node_ids`,
`no_parent_cycles`, `no_missing_parents`, `no_self_loop`,
`parent_id_less_than_child_id` and `no_node_id_gaps`.

The NeuroM checks need a morphology built from the working copy. When it has
duplicate node IDs, a parent ID that names no node, or a parent cycle
(including a node that is its own parent), the morphology is not built, because MorphIO would reject the file
or drop the nodes on the cycle. Those checks
then report the reason and are grouped as one blocked-checks issue.

`no_duplicate_node_ids`, `no_parent_cycles` and `no_missing_parents` run
first, so they report even when `multiple_somas` stops validation.

## Main Config

Validation config lives in:

- `swcstudio/tools/validation/configs/default.json`

Per-check shape:

```json
{
  "checks": {
    "has_soma": {
      "enabled": true,
      "severity": "error",
      "params": {}
    }
  }
}
```

Meaning:

- `enabled`
  - run or skip the check
- `severity`
  - how a failing check is classified in the report
- `params`
  - check-specific thresholds or options

## Output Model

Each check returns a `CheckResult` with fields such as:

- `key`
- `label`
- `status`
- `message`
- `failing_node_ids`
- `failing_section_ids`
- `metrics`
- `params_used`
- `thresholds_used`

A full run returns a `ValidationReport` with:

- `precheck`
- `results`
- `summary`

## GUI And CLI Consistency

Validation stays consistent across interfaces because both call the same backend and return the same result model.

Use the CLI when you want batchable text output.
Use the GUI when you want issue navigation, highlighting, and repair routing.
