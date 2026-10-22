"""Bootstrap entrypoint for modular SWC-Studio desktop bundles.

This file is the entrypoint compiled into the modular ``.app`` /
``.exe``. It is intentionally tiny and dependency-light. Its only job
is to:

1. Locate the ``swcstudio`` Python package (which is **not** baked into
   the PyInstaller bundle in modular mode) — preferring the user's
   downloaded override dir over the bundled-with-the-app copy.
2. Add that directory to ``sys.path``.
3. Call multiprocessing.freeze_support() — the standard PyInstaller fix
   that prevents joblib worker spawns from re-launching the GUI.
4. Import and run ``swcstudio.gui.main.main``.

Why this matters
----------------
The PyInstaller runtime contains third-party libraries but not the
``swcstudio`` package. Application code and models are staged beside the
runtime so both macOS and Windows can accept small layer updates.

Search order for the swcstudio package
--------------------------------------
1. **User override dir** — the auto-updater downloads here:
   * macOS:   ``~/Library/Application Support/SWC-Studio/app/swcstudio/``
   * Windows: ``%APPDATA%\\SWC-Studio\\app\\swcstudio\\``
   * Linux:   ``~/.local/share/swcstudio/app/swcstudio/``
2. **Bundled with the desktop runtime** — what ships at install time:
   ``sys._MEIPASS/app/swcstudio/``. This maps to
   ``Contents/Resources/app/swcstudio/`` on macOS and
   ``_internal/app/swcstudio/`` on Windows.
3. **Source repo** — when running this bootstrap directly from a
   checkout, the repo's ``swcstudio/`` is used.

PyInstaller note
----------------
PyInstaller's static analyzer follows ``import`` statements at the top
of the entrypoint. Because we use ``__import__("swcstudio.gui.main", …)``
(a runtime string-based import), PyInstaller does **not** bundle the
swcstudio package — exactly what we want. To make it still bundle the
heavy library dependencies (numpy, XGBoost, PySide6, vispy, etc.), the
modular spec lists those explicitly under ``hiddenimports``.
"""
from __future__ import annotations

import multiprocessing
import os
import re
import sys
from pathlib import Path
from typing import Optional


BUNDLE_FLAVOR_ENV = "SWCSTUDIO_BUNDLE_FLAVOR"
BUNDLED_MODELS_ENV = "SWCSTUDIO_BUNDLED_MODEL_DIR"


# -----------------------------------------------------------------------------
# Multiprocessing fix — must run before anything else imports.
# -----------------------------------------------------------------------------

multiprocessing.freeze_support()
try:
    multiprocessing.set_start_method("spawn", force=True)
except RuntimeError:
    pass


# -----------------------------------------------------------------------------
# Locate the swcstudio code directory
# -----------------------------------------------------------------------------

def _user_app_override_dir() -> Path:
    """User override location — the auto-updater downloads here."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "SWC-Studio" / "app"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "SWC-Studio" / "app"
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "SWC-Studio" / "app"


def _bundled_app_dir() -> Optional[Path]:
    """Return the replaceable bundled code root, if running frozen."""
    for root in _runtime_resource_roots():
        candidate = root / "app"
        if (candidate / "swcstudio" / "__init__.py").exists():
            return candidate
    return None


def _bundled_models_dir() -> Optional[Path]:
    """Return the replaceable bundled model directory."""
    for root in _runtime_resource_roots():
        candidate = root / "models"
        if candidate.is_dir():
            return candidate
    return None


def _runtime_resource_roots() -> list[Path]:
    """Candidate roots containing the modular ``app`` and ``models`` dirs."""
    roots: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(Path(meipass))

    if getattr(sys, "frozen", False):
        executable = Path(sys.executable).resolve()
        # macOS: SWC-Studio.app/Contents/MacOS/SWC-Studio
        if sys.platform == "darwin" and executable.parent.name == "MacOS":
            roots.append(executable.parent.parent / "Resources")
        # Windows onedir: SWC-Studio.exe beside _internal/.
        roots.extend((executable.parent, executable.parent / "_internal"))

    deduped: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root)
        if key not in seen:
            seen.add(key)
            deduped.append(root)
    return deduped


def _source_repo_dir() -> Optional[Path]:
    """Source repo when running this file directly from a checkout."""
    here = Path(__file__).resolve().parent
    for candidate in (here, here.parent):
        if (candidate / "swcstudio" / "__init__.py").exists():
            return candidate
    return None


def _version_key(text: Optional[str]) -> Optional[tuple[int, int, int]]:
    match = re.match(r"\s*v?(\d+)(?:\.(\d+))?(?:\.(\d+))?", text or "")
    if match is None:
        return None
    major, minor, patch = (int(part or 0) for part in match.groups())
    return (major, minor, patch)


def _code_version(app_dir: Path) -> Optional[tuple[int, int, int]]:
    """Version of the ``swcstudio`` code under ``app_dir``.

    The updater writes a ``VERSION`` file beside each code layer it
    installs; the release stamps ``BUNDLED_APP_VERSION`` into the code's
    ``core/updater.py`` (scripts/stamp_version.py).
    """
    try:
        return _version_key((app_dir / "VERSION").read_text(encoding="utf-8"))
    except OSError:
        pass
    try:
        updater = (app_dir / "swcstudio" / "core" / "updater.py").read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r'^BUNDLED_APP_VERSION\s*=\s*"([^"]+)"', updater, re.MULTILINE)
    return _version_key(match.group(1)) if match else None


def _override_is_current(override: Path, bundled: Optional[Path]) -> bool:
    """Whether the downloaded code layer is at least as new as the bundle.

    A code layer from an earlier version survives installing a newer app,
    and loading it would quietly run the old code. With no bundle to
    compare against (running from source) the override is used as before.
    """
    if bundled is None:
        return True
    bundled_version = _code_version(bundled)
    if bundled_version is None:
        return True
    override_version = _code_version(override)
    return override_version is not None and override_version >= bundled_version


def _find_app_dir() -> Optional[Path]:
    """Return the directory that *contains* ``swcstudio/`` (i.e. its parent).

    Search order:
        1. User override (auto-updater target), unless it is older than
           the bundled code
        2. Bundled in the .app
        3. Source repo (dev mode)
    """
    override = _user_app_override_dir()
    bundled = _bundled_app_dir()
    if (override / "swcstudio" / "__init__.py").exists() and _override_is_current(override, bundled):
        return override

    if bundled is not None:
        return bundled

    repo = _source_repo_dir()
    if repo is not None:
        return repo

    return None


# -----------------------------------------------------------------------------
# Entry
# -----------------------------------------------------------------------------

def main() -> int:
    app_dir = _find_app_dir()
    if app_dir is None:
        sys.stderr.write(
            "ERROR: cannot locate the swcstudio code package.\n"
            "Searched:\n"
            f"  - user override: {_user_app_override_dir() / 'swcstudio'}\n"
            f"  - bundled:       {(_bundled_app_dir() / 'swcstudio') if _bundled_app_dir() else '(not present)'}\n"
            f"  - source repo:   {(_source_repo_dir() / 'swcstudio') if _source_repo_dir() else '(not running from source)'}\n"
        )
        return 1

    # Signal that this process can load replaceable code layers. The updater
    # uses this to avoid offering ineffective code updates to pip installs.
    os.environ[BUNDLE_FLAVOR_ENV] = "modular"
    bundled_models = _bundled_models_dir()
    if bundled_models is not None:
        os.environ[BUNDLED_MODELS_ENV] = str(bundled_models)

    # Insert at the front so the override beats any system-installed swcstudio.
    sys.path.insert(0, str(app_dir))

    # Lazy / runtime import — PyInstaller's static analyzer cannot follow
    # ``__import__`` with a string argument, so swcstudio is *not* baked into
    # the PyInstaller bundle. The heavy deps (numpy, XGBoost, PySide6, …) are
    # still bundled because the modular spec lists them as hiddenimports.
    main_mod = __import__("swcstudio.gui.main", fromlist=["main"])
    main_mod.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
