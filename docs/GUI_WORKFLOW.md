# GUI Workflow Guide

This page explains the current desktop workflow and layout.

## Layout model

The GUI uses a studio-style layout with shared backend logic:

- top bar
  - menus, tool buttons, and feature buttons
- center workspace
  - open SWC document tabs and the main visualization/editor area
- left panel
  - `Issues`
  - `SWC File`
- right inspector
  - issue details and active tool controls
- bottom area
  - transient status and event output
  - the `Flag Review` dock, when shown (`Window -> Show/Hide Flag Review`)

The center workspace stays focused on the active document. The side panels change as the selected issue or active tool changes.

## Tool and feature switching

Top-level tools:

- Batch Processing
- Validation
- Visualization
- Morphology Editing
- Geometry Editing

Current feature mapping:

- Batch Processing
  - Split
  - Validation
  - Auto Label Editing
  - Radii Cleaning
  - Simplification
  - Index Clean
- Validation
  - Validation
  - Index Clean
- Visualization
  - View Controls
- Morphology Editing
  - Manual Label Editing
  - Auto Label Editing
  - Manual Radii Editing
  - Auto Radii Editing
- Geometry Editing
  - Geometry Editing
  - Simplification

## What happens when a file is opened

When you open an SWC file:

1. the file is loaded into a document tab
2. the active tool panels are synchronized to that document
3. validation is run automatically for a normal editable document when no validation report exists yet
4. the resulting report is converted into the shared issue list

That automatic validation step is why the GUI can immediately present an issue-oriented workflow after a file is loaded.

### Working with several files

- `File -> Open` accepts several files at once; each opens in its own
  tab and the first one is shown. The other tabs are validated when you
  first switch to them.
- Opening a file that is already open (from `File -> Open`,
  `Recent Files` or the `Flag Review` window) switches to its tab
  instead of opening it twice.
- Choosing more than 10 new files at once asks for confirmation first:
  every tab has its own 3D views and dendrogram, so many open files use
  a lot of memory.
- `Window -> Next File` and `Window -> Previous File`
  (`Ctrl+Tab` / `Ctrl+Shift+Tab`) move between the open tabs.

### Node-type colours

A small legend in the corner of the 3D view lists the colour of each
node type in the current file: soma (green), axon (blue), basal
dendrite (red), apical dendrite (pink), undefined (grey), and any
custom types with their registered names and colours. It updates when
the file's types change, for example after auto-labeling.
`View -> Show Node-Type Legend` turns it off and on.

## Issue-driven repair flow

The intended desktop loop is:

1. open an SWC file
2. review the `Issues` panel
3. click one issue
4. inspect the problem and suggested action in the right inspector
5. let the app route you to the matching repair tool
6. apply the repair
7. rerun validation
8. review the new per-file history operation and any requested reports

### What the issue list includes

The issue list can include:

- validation findings
- blocked prerequisite summaries
- suspicious radii suggestions
- likely wrong-label suggestions
- a simplification suggestion

### How issue routing behaves

Typical routing behavior:

- index issues
  - `Validation -> Index Clean`
- label issues
  - `Morphology Editing`
- radii issues
  - `Manual Radii Editing` or `Auto Radii Editing`
- topology and geometry issues
  - `Geometry Editing`

Some checks use popup-only actions. For example, if a file contains custom type IDs that are not yet defined, the GUI can open the custom type definition dialog rather than only switching tabs.

The `Likely wrong labels` issue compares the file's current labels with
the labels the SWC auto-labeler gives. It compares labels only and does
not score the quality flag. Its action opens `Auto Label Editing`.

## Auto-labeling and flag review

Auto-labeling uses the SWC auto-labeler, version 1.0.0.

- `Morphology Editing -> Auto Label Editing` labels the current canvas.
  Choose the cell class (`Auto (Stage 1 decides)`,
  `3-class (axon, basal, apical)` or `2-class (axon, dendrite)`), turn
  the `Quality flag` on or off, and optionally pick a model folder.
  `Run` applies the labels and records them in the file's history. The
  result shows the cell class, the predicted per-cell F1, the flag state
  and its reason, and a warning when your chosen class differs from
  Stage 1's prediction.
- `Batch Processing -> Auto Label Editing` labels every SWC file in a
  folder in place, each with its own history. It flags the riskiest
  share of each cell class (`Flag the riskiest [N %] of each cell
  class`, 10% by default).
- When a batch with the quality flag on finishes, the `Flag Review`
  window opens as a dock at the bottom of the main window. It lists
  every file, riskiest first. Open a cell from it, check its labels,
  and mark it `OK` or `Corrected`. The marks are saved into the review
  file, so a review can be resumed. `Window -> Show/Hide Flag Review`
  shows or hides the window.
- `Open Cell` (or a double-click) opens every selected cell, each in
  its own tab. `Previous` and `Next` move the selection one row up or
  down the list and open that cell, so you can step through the
  riskiest cells without going back to the list. Cells opened from the
  review are shown in `Morphology Editing` when Batch Processing is
  active.
- A `Warnings` column shows a cell's warnings (for example
  `no axon labeled`) when the review records any.
- `Window -> Show/Hide Auto Typing Guide` shows a short description of
  the labeling stages.
- `Help -> About` shows the app, models, and auto-labeler versions.

See [Reviewing Flagged Cells](documentation/auto-typing-backends.md#reviewing-flagged-cells)
for the full review workflow.

## Custom types in the GUI

Custom types are managed from dendrogram editing with `Add/Edit Types`.

Users can define:

- type ID
- name
- color
- notes

These definitions are written to the persistent custom type registry, so they remain available after closing and reopening the application.

See [Custom Types and Labels](documentation/custom-types-and-labels.md) for the exact storage behavior and log integration.

## Saving, history, and outputs

The GUI writes tracked morphology edits directly back to the source SWC
and records the operation history in the encrypted sidecar archive.

Important current behavior:

- the source SWC receives compact `# @PROV` pointer lines
- the history archive is stored as `<stem>_history.swcstudio` next to the SWC
- the History Browser opens on Operation History; each file numbers its operations independently as `op-1`, `op-2`, `op-3`, and so on
- operation rows have expandable node-level changes
- undoing an operation restores the state before it, removing that operation and all later operations from the current state
- the new restore operation records which operation/version it restored from
- interactive Undo/Redo keeps at most 20 in-memory steps and stores row-level deltas instead of retaining a full SWC dataframe for every edit
- advanced users can change the in-memory step limit with the `SWCSTUDIO_UNDO_LIMIT` environment variable before launching the GUI
- after applying Auto Label Editing, the next validation reuses that result instead of launching duplicate type-suspicion inference
- the Commit History tab keeps exact version IDs and SHA details for technical review
- mutating GUI batch tools process and record each source SWC independently
- validation and report-only exports can still create text reports when requested

See [Logs And Reports](LOGS_AND_REPORTS.md) for the naming conventions.
