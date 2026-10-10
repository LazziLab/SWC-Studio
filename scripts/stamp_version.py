#!/usr/bin/env python3
"""Synchronise version strings across the repo from a single source.

The release workflow runs this with ``--version 0.2.0`` (parsed from
the git tag). It updates these places so the produced wheel and
bundled .app advertise the right version at runtime, and so the
citation metadata names the release:

* ``pyproject.toml``                — ``version = "..."``
* ``swcstudio/core/updater.py``     — ``BUNDLED_APP_VERSION``,
                                       ``BUNDLED_MODELS_VERSION``
* ``CITATION.cff``                  — ``version: "..."``

This script is idempotent and only edits the lines it owns. It does
NOT commit; the workflow runs it in the throw-away release working
tree, builds the artifacts, then discards the changes.

Local pre-flight check::

    python scripts/stamp_version.py --version 0.2.0 --check

In ``--check`` mode it does not edit anything; instead it asserts that
the existing values already match the requested version and exits 0.
The release workflow uses this to fail-fast if the user forgot to bump
``pyproject.toml`` before tagging.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (relative_path, regex pattern, replacement template)
TARGETS = [
    (
        "pyproject.toml",
        re.compile(r'^(version\s*=\s*)"[^"]+"', re.MULTILINE),
        r'\1"{version}"',
    ),
    (
        "swcstudio/core/updater.py",
        re.compile(r'^(BUNDLED_APP_VERSION\s*=\s*)"[^"]+"', re.MULTILINE),
        r'\1"{version}"',
    ),
    (
        "swcstudio/core/updater.py",
        re.compile(r'^(BUNDLED_MODELS_VERSION\s*=\s*)"[^"]+"', re.MULTILINE),
        r'\1"{version}"',
    ),
    (
        "CITATION.cff",
        re.compile(r'^(version:\s*)"[^"]+"', re.MULTILINE),
        r'\1"{version}"',
    ),
]


def _check_or_apply(version: str, *, check_only: bool) -> int:
    rc = 0
    for rel, pattern, repl_tpl in TARGETS:
        path = ROOT / rel
        text = path.read_text(encoding="utf-8")
        match = pattern.search(text)
        if match is None:
            print(f"  ERROR: pattern not found in {rel}", file=sys.stderr)
            rc = 1
            continue
        current = match.group(0)
        # Strip surrounding `key =` to extract just the value
        m_val = re.search(r'"([^"]+)"', current)
        cur_value = m_val.group(1) if m_val else "?"

        if check_only:
            if cur_value != version:
                print(f"  MISMATCH {rel}: {cur_value!r} (expected {version!r})", file=sys.stderr)
                rc = 1
            else:
                print(f"  OK       {rel}: {cur_value}")
            continue

        new_text = pattern.sub(repl_tpl.format(version=version), text)
        if new_text != text:
            path.write_text(new_text, encoding="utf-8")
            print(f"  STAMPED  {rel}: {cur_value} -> {version}")
        else:
            print(f"  ALREADY  {rel}: {version}")
    return rc


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--version", required=True,
                    help="Version to stamp, e.g. 0.2.0 (no leading 'v')")
    ap.add_argument("--check", action="store_true",
                    help="Verify current values match --version. Exit 1 on mismatch.")
    args = ap.parse_args()

    if not re.match(r"^\d+\.\d+\.\d+", args.version):
        ap.error(f"version doesn't look like semver: {args.version!r}")

    return _check_or_apply(args.version, check_only=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
