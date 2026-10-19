#!/usr/bin/env python3
"""Generate ``update_manifest.json`` for a SWC-Studio release.

The manifest is a small JSON file attached to each GitHub Release as an
asset. The bundled app fetches it via the always-pointing-to-latest
URL (``releases/latest/download/update_manifest.json``) to decide
whether code or model layers are out of date.

Usage::

    python scripts/generate_release_manifest.py \\
        --version            0.2.0 \\
        --code-zip           swcstudio-code-v0.2.0.zip \\
        --models-zip         swcstudio-models-v0.2.0.zip \\
        --runtime-asset-macos SWC-Studio-v0.2.0-macOS.zip \\
        --runtime-asset-windows SWC-Studio-v0.2.0-Windows.zip \\
        --output             update_manifest.json

The script computes the SHA-256 hash and byte-size of every zip and
emits a manifest matching the schema consumed by
``swcstudio.core.updater.UpdateManifest.from_json``.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
from pathlib import Path

GITHUB_REPO = "LazziLab/SWC-Studio"


def _sha256(path: Path, chunk: int = 1 << 16) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def _release_asset_url(tag: str, filename: str) -> str:
    """GitHub-Releases asset download URL."""
    return f"https://github.com/{GITHUB_REPO}/releases/download/{tag}/{filename}"


def _layer_pkg(version: str, tag: str, zip_path: Path) -> dict:
    return {
        "version": version,
        "url":     _release_asset_url(tag, zip_path.name),
        "size":    zip_path.stat().st_size,
        "sha256":  _sha256(zip_path),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--version", required=True,
                    help="Release version, e.g. 0.2.0 (no leading 'v')")
    ap.add_argument("--tag",
                    help="Git tag (defaults to 'v<version>')")
    ap.add_argument("--code-zip", type=Path, required=True,
                    help="Path to swcstudio-code-vN.zip")
    ap.add_argument("--models-zip", type=Path, required=True,
                    help="Path to swcstudio-models-vN.zip")
    ap.add_argument("--runtime-zip-macos", type=Path, default=None,
                    help="Path to SWC-Studio-vN-macOS.zip (optional)")
    ap.add_argument("--runtime-zip-windows", type=Path, default=None,
                    help="Path to SWC-Studio-vN-Windows.zip (optional)")
    ap.add_argument("--runtime-asset-macos",
                    help="macOS runtime asset filename when the zip is built "
                         "on another runner")
    ap.add_argument("--runtime-asset-windows",
                    help="Windows runtime asset filename when the zip is built "
                         "on another runner")
    ap.add_argument("--min-runtime-version", default=None,
                    help="Minimum runtime version compatible with this code "
                         "(defaults to the bundled runtime version of this release)")
    ap.add_argument("--output", type=Path, required=True,
                    help="Where to write update_manifest.json")
    args = ap.parse_args()

    tag = args.tag or f"v{args.version}"

    for p in [args.code_zip, args.models_zip]:
        if not p.is_file():
            ap.error(f"required zip not found: {p}")

    runtime_block: dict = {
        "min_version": args.min_runtime_version or args.version,
    }
    macos_asset = args.runtime_asset_macos
    if args.runtime_zip_macos:
        if not args.runtime_zip_macos.is_file():
            ap.error(f"macOS runtime zip not found: {args.runtime_zip_macos}")
        macos_asset = args.runtime_zip_macos.name
    windows_asset = args.runtime_asset_windows
    if args.runtime_zip_windows:
        if not args.runtime_zip_windows.is_file():
            ap.error(f"Windows runtime zip not found: {args.runtime_zip_windows}")
        windows_asset = args.runtime_zip_windows.name
    if macos_asset:
        runtime_block["url_macos"] = _release_asset_url(tag, macos_asset)
    if windows_asset:
        runtime_block["url_windows"] = _release_asset_url(tag, windows_asset)

    manifest = {
        "release_version": args.version,
        "released_utc":    datetime.datetime.now(datetime.timezone.utc)
                                  .replace(microsecond=0).isoformat(),
        "app":      _layer_pkg(args.version, tag, args.code_zip),
        "models":   _layer_pkg(args.version, tag, args.models_zip),
        "runtime":  runtime_block,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.output}")
    print(f"  release: {manifest['release_version']}")
    print(f"  app:     {manifest['app']['size']} bytes  sha256={manifest['app']['sha256'][:16]}…")
    print(f"  models:  {manifest['models']['size']} bytes  sha256={manifest['models']['sha256'][:16]}…")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
