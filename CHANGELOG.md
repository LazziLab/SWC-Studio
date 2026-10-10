# Changelog

All notable changes to SWC-Studio are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions
follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed

- Auto-labeling now runs the SWC auto-labeler v1.0.0
  (<https://github.com/Mio0v0/swc-autolabeler>). It has four stages:
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

### Fixed

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
