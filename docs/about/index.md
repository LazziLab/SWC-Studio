# About

`SWC-Studio` is a shared desktop, CLI, and Python toolkit for working with SWC morphology data.

## Overview

`SWC-Studio` is designed for researchers and developers who need to inspect neuronal reconstructions, identify structural or annotation problems, repair those problems, and run repeatable morphology-processing workflows from one shared backend.

The project combines:

- a desktop GUI for issue-driven review and repair
- a command-line interface for repeatable single-file and batch runs
- a Python package for direct integration into scripts and downstream pipelines

## What the Project Includes

The codebase follows a four-layer structure:

- `swcstudio/core`
  - shared computational logic such as parsing, validation, issue construction, radii cleaning, auto labeling, geometry editing, simplification, configuration helpers, and reporting
- `swcstudio/tools`
  - task-oriented wrappers that expose those shared capabilities in domain-specific modules such as batch processing, validation, morphology editing, visualization, and geometry editing
- `swcstudio/plugins`
  - runtime extension hooks through the plugin contract
- `swcstudio/cli` and `swcstudio/gui`
  - interface wrappers that route user actions into the tool layer rather than implementing separate algorithmic behavior

This shared-core design is what keeps GUI, CLI, and Python behavior aligned.

## Main Workflow Idea

The GUI is built around an issue-driven repair loop:

1. open an SWC file
2. review the issue list
3. let the selected issue route you to the matching repair tool
4. apply a fix
5. rerun validation
6. review the per-file operation history and any requested reports

The CLI and Python API expose the same underlying operations for scripted or batch use.

## Core Capability Areas

`SWC-Studio` currently organizes user-facing work into five tool areas:

1. Batch Processing
2. Validation
3. Visualization
4. Morphology Editing
5. Geometry Editing

Those tools cover:

- issue checking and validation
- index cleaning
- manual and automatic label editing
- manual and automatic radii editing
- geometry editing
- simplification
- folder-level batch workflows
- per-file history plus standardized reports and export folders

## Documentation Map

The documentation is organized around the current application behavior:

- `User Guide`
  - setup, issue-driven workflows, validation, auto-labeling and flag review, custom types, outputs
- `Tutorials`
  - guided end-to-end examples for GUI and CLI usage
- `Reference`
  - command, rule, API, module, plugin, and architecture details
