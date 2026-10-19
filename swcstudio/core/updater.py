"""Modular update mechanism for SWC-Studio.

The app ships in two flavors:

* **Pip wheel** — installed via ``pip install swcstudio``. Pip handles
  upgrades natively; this module is mostly a no-op for those users.

* **Bundled desktop app** — downloaded from GitHub Releases. The bundle
  is split into three independent pieces, each updatable separately:

  ============== ================================================== ============
  Module          What it contains                                   Re-download
  ============== ================================================== ============
  Runtime        Python interpreter + heavy libraries                full .app
                 (PyTorch, Qt, vispy, sklearn). Rare bumps.
  Application    Your Python code (``swcstudio/`` package).          5 MB zip
                 Changes most often.
  Models         Trained sklearn pickles, torch checkpoints, QC      ~75-80 MB
                 gate, and compact flag models.
  ============== ================================================== ============

The bundled desktop app contains its own internal copy of each piece. When the
user opts to update, the new version is downloaded into a per-user
override location (``~/Library/Application Support/SWC-Studio/...``)
that takes precedence over the bundled copy. The next launch picks up
the fresh module without touching the installed bundle itself.

Update flow
-----------
1. App fetches a small JSON manifest from GitHub Releases on startup
   (or on user request).
2. Compares the manifest's ``app_version`` / ``models_version`` to the
   versions currently in use (bundled or cached).
3. If an update is available, presents a dialog. User clicks "Update".
4. The relevant zip is downloaded into a temp file, verified by SHA-256,
   extracted into the user override location atomically.
5. The user is told to relaunch for code updates. Model updates are
   available to subsequent auto-label runs; the large Stage 1/Stage 2
   caches include resolved-path metadata, while a relaunch guarantees
   that all already-initialized neural-model state is refreshed.

Public API
----------
* :func:`current_versions` — what's running now
* :func:`fetch_manifest`    — pull manifest JSON from GitHub
* :func:`available_updates` — diff current vs latest, return what's new
* :func:`apply_update`      — download + verify + extract one module

This module is intentionally pure-Python with stdlib only — no heavy
imports. It's imported by the bootstrap before the rest of the app
loads.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Optional

# -----------------------------------------------------------------------------
# Versions baked into the app. Updated on each release.
# -----------------------------------------------------------------------------

# These are the versions of the *bundled* code and models that ship with
# the .app. The user may have a newer version cached in the override
# directory; in that case the override wins.
BUNDLED_APP_VERSION    = "0.2.1"
BUNDLED_MODELS_VERSION = "0.2.1"
BUNDLE_FLAVOR_ENV = "SWCSTUDIO_BUNDLE_FLAVOR"

# URL of the always-pointing-to-latest manifest. GitHub's "latest"
# redirect makes this stable across releases. If a release has no
# `update_manifest.json` asset, this 404s and the app falls back to the
# /releases/latest API.
DEFAULT_MANIFEST_URL = (
    "https://github.com/LazziLab/SWC-Studio/releases/latest/download/update_manifest.json"
)
GITHUB_LATEST_RELEASE_API = (
    "https://api.github.com/repos/LazziLab/SWC-Studio/releases/latest"
)


# -----------------------------------------------------------------------------
# Override locations (the user-writable cache where updates land)
# -----------------------------------------------------------------------------

def _user_data_root() -> Path:
    """Per-platform user data dir. Mirrors model_paths._user_data_root()."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        if base:
            return Path(base)
        return Path.home() / "AppData" / "Roaming"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    base = os.environ.get("XDG_DATA_HOME")
    if base:
        return Path(base)
    return Path.home() / ".local" / "share"


def user_app_override_dir() -> Path:
    """Where downloaded ``swcstudio/`` code packages land.

    Uses ``SWC-Studio/app/`` (matching the bootstrap loader's search
    path in ``packaging/swcstudio_bootstrap.py``).
    """
    return _user_data_root() / "SWC-Studio" / "app"


def user_models_override_dir() -> Path:
    """Where downloaded model packages land.

    Uses ``swcstudio/models/`` (matching
    :func:`swcstudio.core.model_paths.user_model_dir`) so that the
    existing model resolver finds them automatically without any
    further plumbing.
    """
    return _user_data_root() / "swcstudio" / "models"


def app_updates_supported() -> bool:
    """Whether this process was launched by the modular desktop bootstrap."""
    return os.environ.get(BUNDLE_FLAVOR_ENV, "").strip().lower() == "modular"


# -----------------------------------------------------------------------------
# Manifest types
# -----------------------------------------------------------------------------

@dataclasses.dataclass
class ModulePackage:
    """Description of one updatable module."""
    version: str
    url: str
    size: int                # bytes (informational)
    sha256: Optional[str]    # hex digest (None to skip integrity check)


@dataclasses.dataclass
class UpdateManifest:
    """The structure pulled from the GitHub-Releases manifest JSON."""
    release_version: str
    released_utc: str
    app: Optional[ModulePackage]
    models: Optional[ModulePackage]
    runtime_url_macos: Optional[str]   # full .app re-download for major bumps
    runtime_url_windows: Optional[str]

    @classmethod
    def from_json(cls, data: dict) -> "UpdateManifest":
        def _pkg(key: str) -> Optional[ModulePackage]:
            section = data.get(key)
            if not section:
                return None
            return ModulePackage(
                version=str(section.get("version", "")),
                url=str(section.get("url", "")),
                size=int(section.get("size", 0)),
                sha256=section.get("sha256"),
            )
        return cls(
            release_version=str(data.get("release_version", "")),
            released_utc=str(data.get("released_utc", "")),
            app=_pkg("app"),
            models=_pkg("models"),
            runtime_url_macos=data.get("runtime", {}).get("url_macos"),
            runtime_url_windows=data.get("runtime", {}).get("url_windows"),
        )


# -----------------------------------------------------------------------------
# Version probing
# -----------------------------------------------------------------------------

def current_app_version() -> str:
    """Return the currently-loaded swcstudio code version.

    Priority: VERSION file inside override dir > BUNDLED_APP_VERSION.
    """
    override_version = user_app_override_dir() / "VERSION"
    if app_updates_supported() and override_version.is_file():
        try:
            return override_version.read_text(encoding="utf-8").strip()
        except OSError:
            pass
    return BUNDLED_APP_VERSION


def current_models_version() -> str:
    """Return the currently-active models version.

    Priority: VERSION file inside override dir > BUNDLED_MODELS_VERSION.
    """
    override_version = user_models_override_dir() / "VERSION"
    if override_version.is_file():
        try:
            return override_version.read_text(encoding="utf-8").strip()
        except OSError:
            pass
    return BUNDLED_MODELS_VERSION


def current_versions() -> dict[str, str]:
    """{'app': '0.1.0', 'models': '0.1.0'} for whatever is loaded right now."""
    return {
        "app":    current_app_version(),
        "models": current_models_version(),
    }


# -----------------------------------------------------------------------------
# Manifest fetching
# -----------------------------------------------------------------------------

def fetch_manifest(url: str = DEFAULT_MANIFEST_URL, timeout: float = 10.0) -> Optional[UpdateManifest]:
    """Fetch the update manifest. Returns None on any network/parse error.

    Designed to fail silently — missing/unreachable manifest is treated
    as "no updates", not a hard error.
    """
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "SWC-Studio-Updater"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return UpdateManifest.from_json(data)
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError, ValueError):
        return None


def available_updates(
    manifest: Optional[UpdateManifest] = None,
) -> dict[str, str]:
    """Return ``{'app': new_ver, 'models': new_ver}`` for modules with a
    newer version. Empty dict means everything is current."""
    if manifest is None:
        manifest = fetch_manifest()
    if manifest is None:
        return {}

    out: dict[str, str] = {}
    cur = current_versions()

    if (
        app_updates_supported()
        and manifest.app
        and _is_newer(manifest.app.version, cur["app"])
    ):
        out["app"] = manifest.app.version
    if manifest.models and _is_newer(manifest.models.version, cur["models"]):
        out["models"] = manifest.models.version
    return out


def _is_newer(candidate: str, current: str) -> bool:
    """Compare semver-ish version strings. '0.2.0' > '0.1.0'.

    Falls back to string comparison if either side isn't parseable.
    """
    def _parse(v: str) -> tuple[int, ...]:
        parts = v.lstrip("v").split(".")
        out: list[int] = []
        for p in parts:
            digits = "".join(c for c in p if c.isdigit())
            out.append(int(digits) if digits else 0)
        return tuple(out)
    try:
        return _parse(candidate) > _parse(current)
    except Exception:
        return candidate != current and candidate > current


# -----------------------------------------------------------------------------
# Download + verify + apply
# -----------------------------------------------------------------------------

def apply_update(
    module: str,
    package: ModulePackage,
    *,
    progress_cb=None,
) -> Path:
    """Download a module package, verify it, extract atomically.

    ``module`` must be ``"app"`` or ``"models"``.

    ``progress_cb(downloaded_bytes, total_bytes)`` is called periodically
    during download (optional).

    Returns the path to the extracted module directory.

    On any failure (network, integrity, unzip) the partially-extracted
    files are cleaned up and the caller's previous state is preserved.
    """
    if module not in ("app", "models"):
        raise ValueError(f"unknown module: {module!r}")
    if module == "app" and not app_updates_supported():
        raise RuntimeError(
            "application code updates require the modular desktop bundle; "
            "pip/source installs should upgrade with pip"
        )

    target_root = (
        user_app_override_dir() if module == "app" else user_models_override_dir()
    )
    target_root.parent.mkdir(parents=True, exist_ok=True)

    # Atomic extract: download to temp, extract to staging, move to final.
    with tempfile.TemporaryDirectory(prefix="swcstudio_update_") as tmpdir:
        zip_path = Path(tmpdir) / "package.zip"
        _download(package.url, zip_path, progress_cb=progress_cb)

        if package.sha256:
            digest = _sha256_of(zip_path)
            if digest.lower() != package.sha256.lower():
                raise RuntimeError(
                    f"integrity check failed for {module} update: "
                    f"expected {package.sha256}, got {digest}"
                )

        staging = Path(tmpdir) / "extracted"
        staging.mkdir()
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(staging)

        # Write the version marker so future loads know what's installed.
        (staging / "VERSION").write_text(package.version + "\n", encoding="utf-8")

        # Replace the target dir atomically: move existing aside, move new
        # into place, delete old. Crash-safe within reason — if the move
        # is interrupted the user can re-run the update.
        backup = target_root.with_suffix(".bak")
        if backup.exists():
            shutil.rmtree(backup)
        if target_root.exists():
            target_root.rename(backup)
        shutil.move(str(staging), str(target_root))
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)

    return target_root


def _download(url: str, dest: Path, *, progress_cb=None, chunk_size: int = 65536) -> None:
    """Stream a URL to a file, calling progress_cb if supplied."""
    req = urllib.request.Request(url, headers={"User-Agent": "SWC-Studio-Updater"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        total = int(resp.headers.get("Content-Length", 0))
        downloaded = 0
        with open(dest, "wb") as fh:
            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                fh.write(chunk)
                downloaded += len(chunk)
                if progress_cb:
                    progress_cb(downloaded, total)


def _sha256_of(path: Path, chunk_size: int = 65536) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


# -----------------------------------------------------------------------------
# Diagnostic helper for the GUI / CLI
# -----------------------------------------------------------------------------

def diagnostic_report() -> str:
    """Human-readable summary of current versions + override locations."""
    cur = current_versions()
    lines = [
        "SWC-Studio update state",
        "",
        f"  App code version:   {cur['app']}",
        f"     bundled:        {BUNDLED_APP_VERSION}",
        f"     layer updates:  {app_updates_supported()}",
        f"     override dir:   {user_app_override_dir()}",
        f"     override exists: {user_app_override_dir().exists()}",
        "",
        f"  Models version:     {cur['models']}",
        f"     bundled:        {BUNDLED_MODELS_VERSION}",
        f"     override dir:   {user_models_override_dir()}",
        f"     override exists: {user_models_override_dir().exists()}",
        "",
        f"  Manifest URL:       {DEFAULT_MANIFEST_URL}",
    ]
    return "\n".join(lines)
