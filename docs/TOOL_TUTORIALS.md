# CLI Task Guide

This page groups the current CLI command surface by task.

It is a quick practical companion to the full [CLI Reference](CLI_REFERENCE.md).

## Inspect one file

```bash
swcstudio check cell.swc
swcstudio validate cell.swc
swcstudio rule-guide
```

## Repair one file

Validation-oriented edits:

```bash
swcstudio auto-fix cell.swc
swcstudio auto-label cell.swc
swcstudio radii-clean cell.swc
swcstudio index-clean cell.swc
```

Morphology edits:

```bash
swcstudio set-type cell.swc --node-id 14169 --new-type 3
swcstudio dendrogram-edit cell.swc --node-id 42 --new-type 3
swcstudio set-radius cell.swc --node-id 42 --radius 0.75
```

Geometry edits:

```bash
swcstudio simplify cell.swc
swcstudio connect cell.swc --start-id 10 --end-id 22
swcstudio disconnect cell.swc --start-id 10 --end-id 22
swcstudio move-subtree cell.swc --root-id 40 --x 100 --y 120 --z 5
```

Current single-file edit behavior:

- auto-label runs the SWC auto-labeler and labels every node as soma, axon, basal dendrite or apical dendrite
- Stage 1 decides the cell class by default; `--cell-class 3-class` (axon, basal, apical) or `--cell-class 2-class` (axon, dendrite) chooses it, and a choice that Stage 1 disputes is flagged as a class mismatch
- the quality flag is on by default: it predicts the cell's per-cell F1 and flags the cell for review when that falls below the flag's cut-off; `--no-flag` turns it off
- the source SWC is updated automatically
- the operation is recorded in `<stem>_history.swcstudio`
- SWC/SWC+ comment headers are preserved while `# @PROV` pointers are updated

## Process a folder

```bash
swcstudio validate ./swc-folder
swcstudio split ./swc-folder
swcstudio auto-typing ./swc-folder
swcstudio radii-clean ./swc-folder
swcstudio simplify ./swc-folder
swcstudio index-clean ./swc-folder
```

`auto-typing` labels each file in place and flags the riskiest 10% of
each cell class (`--flag-budget` changes the share). It writes the
ranking into the folder as `<folder>_flag_review_<timestamp>.json` with
a `.csv` copy, for the GUI's Flag Review window. `--no-flag` turns the
flag and the ranking off.

## Train auto-labeling models

```bash
swcstudio train auto-typing --data-dir ./labeled --output-dir ./my-models
swcstudio models status --model-dir ./my-models
swcstudio auto-label cell.swc --model-dir ./my-models
```

## Use temporary config overrides

```bash
swcstudio validate cell.swc --config-json '{"checks":{"has_soma":{"enabled":true,"severity":"warning","params":{}}}}'
```

## Related pages

- [CLI Reference](CLI_REFERENCE.md)
- [CLI Tutorial](tutorials/cli-tutorial.md)
- [Logs And Reports](LOGS_AND_REPORTS.md)
