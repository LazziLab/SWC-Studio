# Changelog

All notable changes to SWC-Studio are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions
follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- File → Open accepts several files at once, each opening in its own tab;
  a file that is already open switches to its tab instead of opening
  twice. Window → Next File / Previous File (Ctrl+Tab / Ctrl+Shift+Tab)
  move between open files.
- Flag Review: Open Cell opens every selected cell, Previous / Next step
  through the ranked list, and a Warnings column shows cells missing a
  compartment.
- A node-type color legend in the 3D view (View → Show Node-Type Legend).
- Three validation checks under "File integrity", which run first:
  `no_duplicate_node_ids`, `no_parent_cycles` and `no_missing_parents`
  (a parent ID that names no node). There are now 32 checks.
- Auto-label runs record which models labeled the file: the model folder
  (home directory written as `~`), where it was found, the labeler version
  and a SHA-256 of the model files. `history reproduce` uses them.
- File → Reload from Disk.
- `swcstudio --version` prints the installed version.
- `THIRD_PARTY_NOTICES.md` lists the license of each package the stand-alone
  apps bundle, among them Qt for Python under LGPL-3.0. The apps ship it with
  `LICENSE` and each bundled package's own license files.

### Changed

- Auto-labeling now runs the SWC auto-labeler v1.0.1
  (<https://github.com/LazziLab/swc-autolabeler>). It has four stages:
  structural checks, cell class, subtree labeling with a learned branch
  correction, and a quality flag that ranks cells for review. You can choose
  the cell class or let Stage 1 decide it, and the Flag Review window lists
  every cell, riskiest first. The earlier graph-network stages and their
  models are removed, so auto-labeling runs on the CPU alone.
- `history export-crate` now replaces each OS username with a stable
  pseudonym (`agent-1`, `agent-2`, ...) in the copied event log, the index,
  the SWC's `@PROV` header and the crate metadata, unless you pass
  `--with-pii`. Before, usernames were only removed from the crate metadata.
- A model folder chosen with `--model-dir` is recorded in a file's history
  with the home directory written as `~`, and model lookup expands `~`.
- `history verify` also recomputes every commit id, checks that each
  parent is an earlier commit, that branches and tags point into the log,
  that every blob they reach is present, and that the files match
  `checksums.json`.
- A complex soma (several connected soma points) no longer stops
  validation. It is reported as one warning, and every other check still
  runs, on a working copy with each soma group merged. Several separate
  somas still stop it, since the file holds more than one cell.
- The auto-label batch summary reads "Labeled" and "Not labeled (N failed
  the structural checks, M errors)" instead of separate "QC rejected" and
  "Failed" counts that overlapped. It lists every file that was not
  labeled, and every batch writes a full report.
- A labeled cell missing a compartment its class should have (for example
  no axon) is marked with a warning in the batch results, the flag review
  and its CSV.
- The macOS install steps cover macOS 15, where an app Apple has not
  notarized must be allowed in System Settings → Privacy & Security →
  Open Anyway.
- Auto-labeling a file in place changes only the type field of each node
  line (and the radius field when a radius option changes it); comments,
  extra columns, spacing, line endings and a byte-order mark are kept as
  they were. Type and radius edits in the GUI do the same.
- Files with duplicate node IDs, a node that is its own parent, or a
  parent loop are refused before auto-labeling, and Index Clean, the
  geometry edits and Split refuse them instead of dropping or rewiring
  rows.
- A type-0 or custom-typed stretch attached directly to the soma is
  labeled like any other neurite (SWC auto-labeler 1.0.1). It was merged
  into the soma and written as soma before. Should the labeler ever leave
  a node without a label, it keeps its type, with a warning, instead of
  being written as soma.
- Consolidate Soma gives the merged soma the radius of the sphere with
  the soma points' surface, as the labeler does. A standard 3-point soma
  now keeps its radius; before, it was doubled.
- The model folder is now chosen from, in order: the folder you give,
  `SWCSTUDIO_MODEL_DIR`, the saved config, the user data folder, then the
  bundled models (the environment variable now wins over the config).
- Simplification always keeps soma points and the node where a
  compartment starts.
- Radii cleaning bounds each node type by a fixed plausible radius range
  (axon 0.05–5 µm, basal dendrite 0.05–8, apical dendrite 0.05–10, other
  neurite types 0.05–10) instead of percentiles of the file's own radii,
  and takes reference and replacement values only from radii inside the
  range. On a file whose axon radii ran up to 50 µm, the old bounds
  followed the bad values and raised 0.004 µm placeholders to about
  11 µm. The axon floor (raise axon radii below 0.12 µm) is now off by
  default; on clean Allen and NeuroMorpho.Org cells it changed 33–93% of
  nodes. Radii below 0.05 µm are no longer raised silently by the
  replacement clamp, and a placeholder parent no longer caps its
  children's radii.
- Radii cleaning changes only radii that are certainly wrong: invalid,
  above the type's plausible maximum, or more than 3 times thicker or
  thinner than their neighbours on both sides (`local_outlier.max_ratio`,
  replacing `max_percent_deviation`, which flagged anything 50% off). The
  taper cap (each node at most 5% thicker than its parent) is off by
  default: it changed half the radii of clean cells, cut their membrane
  surface by about 8%, and let one thin node thin the whole dendrite after
  it. Clean NeuroMorpho.Org and Allen cells now come through unchanged.

### Fixed

- Validation stopped with "could not assign tuple of length 1 to structure
  with 7 fields" on a file whose header holds a line that is not an SWC row,
  such as the `autoset #bkg_thresh = 1` line Vaa3D's APP2 tracer writes. Such
  lines are now skipped, as the file reader already skipped them.
- Editing a copy of a tracked SWC took the original's history: the copy
  got the archive and the original's `<name>_history.swcstudio` was
  deleted. The copy now starts its own archive from the original's
  history, and the original keeps its own. Only a renamed SWC moves its
  archive. A rename that changes only letter case (`cell.swc` to
  `Cell.swc`) no longer deletes the archive on Windows and macOS.
- A failing check with severity `info` (such as `no_node_id_gaps`) was
  shown as a critical issue. It is now reported as `info`.
- `radius_upper_bound` was missing from the check catalog, so it had no
  category, label or rule text when enabled.
- A source checkout that is not installed reported version 0.1.0, and the
  docs fell back to 0.2.0. Both now read the version from `pyproject.toml`.
- Geometry Editing in the GUI could not select anything (node, subtree,
  path, ...) since v0.2.0: every selection failed with an internal
  `TypeError` that was only written to the log.
- Geometry edits rewrote the number text of every row (for example
  `2.5000e+00` became `2.5`), and Index Clean replaced the file's own
  header with the default SWC+ header. Both now keep the header and the
  original text of every value they do not change.
- The zero-length segment and section checks crashed with "Validation
  error" on a file whose parent id is larger than every node id.
- Radii cleaning listed an untouched NaN soma radius as a change.
- Folder scans counted the hidden `._name.swc` files macOS writes on USB
  drives and network shares as SWC files, and each one failed. Hidden
  files are now skipped.
- The desktop app loaded a downloaded code update even when it was older
  than the code bundled with the app. It now uses the newer of the two.
- `history export-crate -o <dir>` deleted `<dir>` if it already existed,
  so pointing it at the SWC's own folder deleted the SWC and everything
  beside it. It now refuses any folder that is not new or empty, and no
  longer repacks the file's history archive.
- `history verify` reported a false failure while the History Browser
  was open (it rewrites the SQLite index, a cache) and aborted on a
  damaged AI-run record instead of reporting it.
- `history reproduce` passes `--model-dir` only when a non-default model
  folder was used, and records the model version and fingerprint.
- Simplification kept soma outlines no better than any path (a 111-point
  outline could drop to 6 points) and could remove the node where an axon
  or dendrite starts. Soma points (`keep_soma`, default on) and type
  boundaries are now always kept.
- After an in-app code update, the desktop app reported, and recorded in
  file histories, the version it was installed with instead of the code
  it runs.
- `no_ultranarrow_starts` failed with "NeuroM check error: index 1 is out
  of bounds" on neurites whose first section branches at once (about 11%
  of NeuroMorpho.Org cells), and the GUI showed the checks as blocked.
- The GUI batch auto-label recorded a chosen model folder as an absolute
  path, including the username, in every file's history.
- The GUI's Index Clean rewrote the number text of renumbered rows.
- Switching tabs during a validation froze the window for up to 5 s, and
  a validation slower than that left the tab reading "0 issues".
- The `@PROV` history header was written above a file's byte-order mark
  and with `\n` line endings in CRLF files.
- `swcstudio doctor` failed a valid model set without the optional flag
  model; checking whether models are available could download them; an
  unwritable user data folder stopped auto-labeling instead of falling
  back to the bundled models; and training read macOS `._*.swc` files.

## [0.2.1] - 2026-06-19

### Added

- Per-file provenance history. Every edit, from the GUI, the CLI or a batch,
  is recorded in a `<stem>_history.swcstudio` archive next to the SWC. The
  archive holds an append-only event log, a content-addressed blob store,
  branches and tags, and an SQLite index. Auto-label runs also record the
  model and a fingerprint of the software environment.
- `swcstudio history` commands (`log`, `show`, `checkout`, `branch`,
  `switch`, `tag`, `checkpoint`, `reproduce`, `reindex`, `verify`, `gc`,
  `export-crate`) and a History Browser with Revert and Checkout in the
  GUI.
- A quality flag for auto-labeling that marks the cells most likely to be
  mislabeled.

### Fixed

- A segmentation fault in auto-label inference on macOS, caused by two
  OpenMP runtimes loaded in one process.
- Installation on clean Linux, macOS and Windows hosts. CI now installs and
  tests the wheel on all three with Python 3.10, 3.11 and 3.12.

## [0.2.0] - 2026-05-07

### Added

- Machine-learning auto-labeling of soma, axon, basal and apical dendrite,
  with the models bundled in the app and the wheel. It runs on a worker
  thread with a progress bar.
- Modular updates: releases ship separate runtime, code and model layers,
  and Help → Check for Updates downloads only the layer that changed.
- A release workflow that builds the macOS and Windows apps and publishes
  the wheel to PyPI.

### Changed

- The documentation site was reorganized around three install paths:
  bundled app, `pip`, and source.

### Fixed

- Unreadable GUI colors under Windows dark mode.

## [0.1.0] - 2026-04-01

First public release.

### Added

- Issue-driven validation: native checks plus checks from NeuroM, each
  linked to the tool that repairs it, in the GUI and the CLI.
- Repairs and editing tools: soma consolidation, splitting of multi-soma
  files, index cleaning, radii cleaning, Ramer–Douglas–Peucker
  simplification, and manual editing of geometry, labels and radii.
- Batch processing of whole folders.
- A plugin system for adding or replacing feature methods and validation
  checks.
- One shared backend behind the desktop GUI, the `swcstudio` CLI and the
  Python API.
- Bundled desktop apps for macOS and Windows, and a documentation site.

[Unreleased]: https://github.com/Mio0v0/SWC-Studio/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/Mio0v0/SWC-Studio/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/Mio0v0/SWC-Studio/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Mio0v0/SWC-Studio/releases/tag/v0.1.0
