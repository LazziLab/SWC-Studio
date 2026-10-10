# Custom Types and Labels

This page explains how custom SWC type labels behave in the GUI, backend, and logs.

## Built-in and custom type IDs

`SWC-Studio` treats the following labels as built-in:

- `0`: undefined
- `1`: soma
- `2`: axon
- `3`: basal dendrite
- `4`: apical dendrite

Any type ID `>= 5` is treated as a custom type.

If a custom type has a saved definition, the software uses its saved label. Otherwise it falls back to a generic label such as `custom type 7`.

## Where custom types are defined

In the GUI, custom types are managed from the dendrogram editing area through the `Add/Edit Types` button.

The custom type dialog allows users to define:

- type ID
- name
- color
- optional notes

These definitions are saved immediately as they are edited.

## Persistence across sessions

Custom type definitions are persistent backend state, not temporary GUI-only state.

By default they are stored at:

- `~/.swc_studio/custom_types.json`

You can override that path by setting:

- `SWCSTUDIO_CUSTOM_TYPES_PATH`

Older installs may still use `SWCTOOLS_CUSTOM_TYPES_PATH`; SWC-Studio
continues to accept it as a fallback.

Because the backend loads this registry on startup, a custom type defined today will still be available after closing and reopening the application on later days.

## How custom types appear in the app

Custom definitions are used in multiple places:

- dendrogram editing controls
- label display helpers
- issue and inspector text that needs a human-readable type name
- report and log builders

If an SWC file contains custom type IDs that have not yet been defined, validation can emit the `custom_types_defined` issue so the user can supply names and metadata for those IDs.

## How custom types appear in logs

The shared report builders include a label legend section. That legend always contains the built-in types, and when custom type definitions exist it also includes:

- custom type ID
- saved display name
- saved color
- saved notes, when present

This means custom labels are preserved not only in the GUI, but also in generated reports and history records.

## Interaction with manual and dendrogram editing

Current label-editing paths include:

- `Morphology Editing -> Manual Label Editing`
  - change one node type directly
- `Morphology Editing -> Auto Label Editing`
  - run the SWC auto-labeler. It uses the bundled model files by
    default; point it at a custom-trained model folder with the
    panel's *Model folder* picker. It writes the built-in types 1 to 4
    only
- `dendrogram-edit`
  - reassign a subtree from the CLI

When a custom type ID is used in those workflows, the saved definition is what turns that numeric ID into a meaningful name in the UI and logs.
