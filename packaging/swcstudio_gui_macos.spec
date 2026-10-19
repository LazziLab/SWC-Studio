# -*- mode: python ; coding: utf-8 -*-
"""Modular macOS PyInstaller runtime."""

from importlib.metadata import PackageNotFoundError, version as pkg_version
from pathlib import Path
import sys

ROOT_DIR = Path.cwd()
sys.path.insert(0, str(ROOT_DIR / "packaging"))

from pyinstaller_common import (  # noqa: E402
    EXCLUDES,
    MODULE_COLLECTION_MODE,
    bundle_inputs,
)

ENTRYPOINT = ROOT_DIR / "packaging" / "swcstudio_bootstrap.py"
try:
    APP_VERSION = pkg_version("swcstudio")
except PackageNotFoundError:
    APP_VERSION = "0.0.0"

datas, binaries, hiddenimports = bundle_inputs()

a = Analysis(
    [str(ENTRYPOINT)],
    pathex=[str(ROOT_DIR)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    module_collection_mode=MODULE_COLLECTION_MODE,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SWC-Studio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="SWC-Studio",
)

ICON_PATH = ROOT_DIR / "packaging" / "icon.icns"

app = BUNDLE(
    coll,
    name="SWC-Studio.app",
    icon=str(ICON_PATH) if ICON_PATH.exists() else None,
    bundle_identifier="io.github.lazzilab.swcstudio",
    info_plist={
        "CFBundleShortVersionString": APP_VERSION,
        "CFBundleVersion": APP_VERSION,
        "CFBundleDisplayName": "SWC-Studio",
        "CFBundleName": "SWC-Studio",
    },
)
