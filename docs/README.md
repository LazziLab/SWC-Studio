# Docs Overview

The published HTML site is organized into four top-level sections:

- `About`
- `User Guide`
- `Tutorials`
- `Reference`

This structure is intentional:

- `About` explains what the project is and how the architecture is organized.
- `User Guide` explains the main workflows and output behavior.
- `Tutorials` contains step-by-step walkthroughs.
- `Reference` collects the stable command, API, rule, module, and extension material.

## Recommended reading order

If you are new to the project, start here:

1. [Getting Started](GETTING_STARTED.md)
2. [User Guide](documentation/index.md)
3. [GUI Tutorial](tutorials/gui-tutorial.md)
4. [CLI Tutorial](tutorials/cli-tutorial.md)
5. [Logs And Reports](LOGS_AND_REPORTS.md)

## User Guide structure

The `User Guide` section is organized around how the software is actually used:

- `Setup`
  - installation, first run, verification
- `Workflows`
  - GUI and CLI usage, issue-driven repair, tool routing
- `Validation and Repair`
  - what creates issues, how repairs are suggested, where the check details live
- `Auto-Typing Engine`
  - the SWC auto-labeler's stages, model files, reviewing flagged cells, and training
- `Custom Types and Labels`
  - persistent custom labels, dendrogram editing, saved metadata, history/report behavior
- `Reports and Outputs`
  - output folders, history archives, reports, and optional saved copies

## Tutorials structure

The `Tutorials` section contains guided workflows:

- GUI walkthrough
- CLI walkthrough
- focused feature tutorials such as radii cleaning and simplification

## Reference structure

The `Reference` section collects:

- CLI reference
- validation rule and issue references
- Python/API documentation
- module reference
- plugin development material
- architecture notes
