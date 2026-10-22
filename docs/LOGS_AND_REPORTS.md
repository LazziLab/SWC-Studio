# Logs and Reports

`SWC-Studio` uses two related audit paths:

- GUI morphology edits are stored in the encrypted per-file history
  archive (`<stem>_history.swcstudio`).
- Batch tools, validation exports, and CLI report commands can still
  write normal text reports.

Core module:

- `swcstudio.core.reporting`

## Why this matters

- one report style across interfaces
- predictable output folders
- easier audit trails for lab workflows

## Main report helpers

Examples of the shared text-report builders:

- `format_validation_report_text`
- `format_batch_validation_report_text`
- `format_split_report_text`
- `format_auto_typing_report_text`
- `format_radii_cleaning_report_text`
- `format_simplification_report_text`
- `format_operation_report_text`
- `write_text_report`

## Default naming

Single-file report/export workflows normally use:

- output folder
  - `<input_folder>/<stem>_swc_studio_output/`
- output or report file
  - `<stem>_<operation_name>_<timestamp>.<ext>`

Batch report/export workflows, such as validation and split, use:

- output folder
  - `<input_folder>/<input_folder>_<operation_name>_<timestamp>/`
- batch report
  - `<input_folder>_<operation_name>_<timestamp>.txt`

## Current CLI edit behavior

Mutating CLI edit commands update the source SWC directly and record the
operation in the per-file history archive:

- source file: `<stem>.swc`
- history archive: `<stem>_history.swcstudio`

This applies to commands such as:

- `auto-fix`
- `auto-label`
- `radii-clean`
- `index-clean`
- `set-type`
- `dendrogram-edit`
- `set-radius`
- geometry edit commands

Mutating batch commands such as auto-typing, radii-clean, simplification,
and index-clean record each processed source SWC in place and do not
create a shared batch output folder. Unless the quality flag is off
(`--no-flag`), `auto-typing` also writes its flag review into the
folder: `<input_folder>_flag_review_<timestamp>.json` and a `.csv`
copy, read by the GUI's Flag Review window. Text reports
are still used for validation/report-only commands and explicit exports.
`split`, `history checkout`, and `history checkpoint` intentionally
materialize separate SWC files.

## GUI history outputs

For tracked GUI morphology edits, the GUI writes:

- the source SWC itself, with compact `# @PROV` pointer lines
- the encrypted history archive
  - `<stem>_history.swcstudio`

The History Browser opens on the Operation History tab, where each operation
can be expanded to show node-level old/new values. Exact version IDs and
SHA details stay in the Commit History tab for technical review.
Operation parameters show readable run settings such as thresholds,
seeds, the cell class and flag settings of an auto-label run, the
labeler version, and algorithm options; internal hashes, paths, and
result-summary fields are hidden from this normal view.
SWC/SWC+ comment headers from the original file are preserved.

## Typical report contents

Validation reports can include:

- grouped pass, warning, and fail rows
- thresholds and metrics
- failing node IDs and section IDs

Operation reports and GUI history records can include:

- operation summary
- node-level change tables
- software version information
- label type legend

When custom type definitions exist, the label legend can include:

- custom type ID
- saved custom label name
- saved color
- saved notes

That is why custom labels defined in the GUI can show up later in generated reports or history records.

## Programmatic use

Plugins and custom features should use the shared reporting helpers rather than writing ad hoc text files so logs stay aligned with the rest of the application.
