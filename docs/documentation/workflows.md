# Workflows

This page explains how the current GUI and CLI workflows are organized around the same shared backend.

```{toctree}
:hidden:
:maxdepth: 1

GUI Workflow Guide <../GUI_WORKFLOW>
```

## GUI workflow

The desktop workflow is issue-driven.

When you open an SWC file in the GUI, the active document is synchronized into the shared tool panels. For normal editable documents, the window then triggers validation automatically if the document does not already have a validation report. The resulting report is converted into a combined issue list and shown in the left-side `Issues` panel.

Current high-level GUI layout:

- top bar
  - tool selector and feature buttons
- center workspace
  - SWC document tabs and visualization/editor canvas
- left panel
  - `Issues`
  - `SWC File`
- right inspector
  - current issue summary and active tool controls
- bottom area
  - transient status and event log output

### What the issue list includes

The issue list is not just a raw list of validation failures. The shared issue builder combines:

- validation failures and warnings
- blocked prerequisite summaries
- suspicious radii suggestions
- likely wrong-label suggestions
- a simplification suggestion when applicable

That is why the issue navigator can function as a repair queue rather than only as a report viewer.

### How issue routing works

Selecting an issue in the GUI does three things:

1. focuses the affected nodes in the active document
2. updates the inspector with the issue description and suggested next step
3. routes the control area to the matching repair tool

Examples of current routing behavior:

- index problems route to `Validation -> Index Clean`
- label problems route to `Morphology Editing`
- radii problems route to `Manual Radii Editing` or `Auto Radii Editing`
- topology and geometry problems route to `Geometry Editing`

Some checks intentionally use popup-only actions instead of a standard tool panel. A notable example is `custom_types_defined`: if the file contains user-defined type IDs that do not yet have names, the GUI can open the custom type definition dialog directly.

## Custom type workflow in the GUI

Custom type management is available from dendrogram editing through the `Add/Edit Types` button. Users can define:

- type ID
- display name
- color
- optional notes

These definitions are not temporary session state. They are written to a persistent registry on disk, so if you define a custom type today and reopen the app tomorrow, it remains available.

Default registry location:

- `~/.swc_studio/custom_types.json`

Optional override:

- `SWCSTUDIO_CUSTOM_TYPES_PATH`

Older installs may still use `SWCTOOLS_CUSTOM_TYPES_PATH`; SWC-Studio
continues to accept it as a fallback.

See [Custom Types and Labels](custom-types-and-labels.md) for the exact behavior.

## CLI workflow

The CLI uses the same shared feature logic as the GUI, but exposes it as command-driven operations.

Common entry points:

- `swcstudio check <file>`
  - print the same combined issue list used by the GUI
- `swcstudio validate <file>`
  - run grouped validation on one file
- `swcstudio <command> <folder>`
  - batch processing for validation, split, auto typing, radii cleaning, simplification, and index cleaning

### Current edit behavior

Mutating edit commands write automatically. You do not need a separate
`--write` flag.

For commands such as:

- `auto-fix`
- `auto-label`
- `radii-clean`
- `index-clean`
- `set-type`
- `dendrogram-edit`
- `set-radius`
- geometry edits such as `move-node`, `connect`, or `simplify`

the CLI writes:

- the updated source SWC
- a per-file history archive: `<stem>_history.swcstudio`

Mutating batch commands record each processed source SWC in place. Text
reports remain for validation/report-only commands, and split/checkpoint
commands intentionally materialize separate SWC files. Batch
auto-labeling with the quality flag on also writes a flag review that
ranks the cells for review; see [Reviewing Flagged Cells](auto-typing-backends.md#reviewing-flagged-cells).

## Recommended reading

- [Getting Started](../GETTING_STARTED.md)
- [GUI Workflow Guide](../GUI_WORKFLOW.md)
- [CLI Reference](../CLI_REFERENCE.md)
