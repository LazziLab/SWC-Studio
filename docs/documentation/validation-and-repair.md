# Validation and Repair

This page explains how `SWC-Studio` turns validation output into repair-oriented workflows.

## Validation is shared across interfaces

Validation logic lives in the shared backend and is exposed through the validation tool wrappers. The GUI, CLI, and Python API therefore use the same rule catalog, the same validation engine, and the same grouped report structure.

That shared behavior matters because it keeps these operations aligned:

- GUI validation panels
- `swcstudio validate <file>`
- `swcstudio validate <folder>`
- `swcstudio check <file>`

## What validation produces

A validation run produces grouped pass, warning, and fail results together with detailed findings such as:

- failing node IDs
- failing section IDs
- thresholds and metrics
- blocked prerequisite summaries when required

The canonical rule and issue details live in:

- [Checks And Issues Reference](../CHECKS_AND_ISSUES_REFERENCE.md)
- [Validation Rules](../VALIDATION_RULES.md)

## What the issue system adds on top

The issue list used by the GUI and `swcstudio check` is broader than the raw validation report.

The shared issue builder can combine:

- direct validation findings
- blocked checks
- suspicious radii detected from the radii-cleaning backend
- likely wrong labels, found by comparing the current labels with the labels the SWC auto-labeler gives
- a simplification suggestion

This is why the issue list is useful as a repair queue rather than only as a check summary.

## Repair paths

`SWC-Studio` uses several repair paths depending on the issue type.

### Validation and index repair

For structural and ordering issues:

- run validation
- inspect the grouped findings
- use `Index Clean` when the parent-before-child ordering or ID continuity needs correction

### Label repair

For annotation problems:

- use `Manual Label Editing` for targeted node changes
- use `Auto Label Editing` to relabel the whole cell with the SWC auto-labeler
- for a folder, run `Batch Processing -> Auto Label Editing` and review the flagged cells in the Flag Review window (see [Reviewing Flagged Cells](auto-typing-backends.md#reviewing-flagged-cells))
- use dendrogram subtree reassignment when a whole branch should inherit a new label

### Radii repair

For suspicious radii:

- use `Manual Radii Editing` for direct node-level fixes
- use `Auto Radii Editing` or `radii-clean` for the shared multi-pass cleaner

### Geometry repair

For topology and geometry problems:

- move nodes or subtrees
- reconnect or disconnect branches
- delete nodes or subtrees
- insert new nodes
- simplify geometry while preserving the main tree structure

## Recommended repair loop

The intended loop is:

1. validate or check the file
2. inspect one issue at a time
3. let the issue route you to the matching repair tool
4. apply a fix
5. rerun validation
6. review the new per-file history operation and any requested report

Type-suspicion analysis normally runs in a background worker so the
rest of the issue list can appear first. It compares labels only and
does not score the quality flag. If validation immediately follows an
applied auto-label result, the GUI reuses that result instead of
running the same labeling again.

This is the same logic described in the GUI workflow and reflected by the CLI command set.
