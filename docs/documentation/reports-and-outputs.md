# Reports and Outputs

This page explains where `SWC-Studio` writes outputs and how history/reports are structured.

```{toctree}
:hidden:
:maxdepth: 1

Logs And Reports <../LOGS_AND_REPORTS>
```

## Shared reporting layer

Text reports use the shared helpers in `swcstudio.core.reporting`. That shared layer is responsible for:

- output directory naming
- operation report naming
- validation report formatting
- explicit text reports for report/export workflows

GUI morphology history is stored separately in the encrypted
`<stem>_history.swcstudio` archive.

## Default single-file output directory

For a source file:

- `<parent>/<stem>.swc`

the default report/export directory is:

- `<parent>/<stem>_swc_studio_output/`

That directory is used mainly for:

- validation reports

## Current CLI behavior for edit commands

Mutating CLI edit commands update the source SWC directly. You do not
need a separate `--write` flag.

When you run single-file edit commands such as:

- `auto-fix`
- `auto-label`
- `radii-clean`
- `index-clean`
- `set-type`
- `dendrogram-edit`
- `set-radius`
- geometry commands such as `move-node`, `connect`, `disconnect`, `delete-node`, `insert`, or `simplify`

the CLI writes:

- the updated source SWC
- a per-file history archive: `<stem>_history.swcstudio`

Mutating batch commands record each processed SWC in place, with an
independent history and operation-ID sequence for every file. They do
not create a shared mutation-output folder. Text reports remain for
validation/report-only commands, while `split`, `history
checkout`, and `history checkpoint` intentionally create separate SWC
files.

Batch auto-labeling (`swcstudio auto-typing` or the GUI's Batch
Processing -> Auto Label Editing) with the quality flag on also writes a
flag review into the folder: `<folder>_flag_review_<timestamp>.json`
and a `.csv` copy. It
lists every file with its cell class, predicted per-cell F1, rank and
flag. The GUI's Flag Review window reads it and saves your review marks
back into it. See
[Reviewing Flagged Cells](auto-typing-backends.md#reviewing-flagged-cells).

## GUI history behavior

Tracked GUI morphology edits update the source SWC directly and record
the operation in the per-file history archive.

Typical GUI history files:

- source SWC with compact `# @PROV` pointer lines
- encrypted history archive
  - `<stem>_history.swcstudio`

The History Browser opens on the Operation History tab. Each file has
its own chronological operation IDs (`op-1`, `op-2`, `op-3`, ...).
Each row summarizes date/time, actor, operation name, parameters, and
changed-node counts; expanding a row shows node-level old/new values.
Restore operations identify the operation/version they restored from.
Exact version IDs and SHA details are available in the Commit History
tab for technical review.

## What the logs contain

Depending on the operation, reports or history records can include:

- grouped validation summaries
- thresholds and metrics
- per-operation summaries
- node-level change tables
- software version information
- label legends, including saved custom type names, colors, and notes

That last point is important for custom labels: if you define custom type metadata in the GUI, the shared log builders can include those labels in generated reports.

## More detail

For the exact naming patterns and report helper list, use:

- [Logs And Reports](../LOGS_AND_REPORTS.md)
