# The SWC auto-labeler, as SWC-Studio runs it

This folder is a copy of the released SWC auto-labeler's `pipeline` package
(https://github.com/Mio0v0/swc-autolabeler), made by `scripts/sync_labeler.py`.
`LABELER.json` records the labeler version, the source commit and a SHA-256 for
every model file SWC-Studio ships with it.

The code is the labeler's own, with two mechanical changes: absolute
`pipeline.` imports are made relative, and lines that put the labeler repository
on `sys.path` do nothing. Do not edit these files by hand: change the labeler,
then run `python scripts/sync_labeler.py --source <labeler checkout>` again.
SWC-Studio reaches this package only through `swcstudio.core.auto_typing`.
