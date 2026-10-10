"""Resolve where the auto-labeler's model files live on disk.

SWC-Studio labels with the SWC auto-labeler (:mod:`swcstudio.core.labeler`).
Its model set is four files that always travel together in one folder:

* ``cell_type_classifier.pkl`` -- Stage 1, which cell class a file is
  (3-class: axon, basal and apical; 2-class: axon and dendrite)
* ``branch_classifier.pkl`` -- Stage 2, the subtree classifier
* ``branch_correction.joblib`` -- Stage 3, the proximal branch correction
* ``flag_model.joblib`` -- Stage 4, one quality flag per cell class

The first three are required. The flag is optional: a model set without it
labels cells but scores no flag. Files are never mixed across folders, because
each model in a set was trained against the others.

A model folder is either named for the labeler version (``labeler-<version>``,
the way SWC-Studio ships and updates its models) inside one of the search
directories, or is a search directory itself, such as a model set you trained.

A folder you name (``--model-dir``, the GUI selector, or the
``SWCSTUDIO_MODEL_DIR`` environment variable, in that order of precedence) is
the only one searched: if it is incomplete, auto-labeling reports what is missing
rather than quietly using other models. Otherwise the search order is (the first
complete folder wins):

1. The user data directory, where model updates are installed:

   * Windows: ``%APPDATA%\\swcstudio\\models``
   * macOS: ``~/Library/Application Support/swcstudio/models``
   * Linux: ``~/.local/share/swcstudio/models``

2. The desktop runtime's model directory, supplied by the bootstrap through
   ``SWCSTUDIO_BUNDLED_MODEL_DIR``.
3. The directory inside the installed package (``swcstudio/data/models``).

Public API:

* ``labeler_info()`` -- version, source commit and model checksums of the labeler
* ``resolve_model_dir(override=None)`` -- the model folder, or ``None``
* ``resolve_model_path(name, override=None)`` -- one file of that folder, or ``None``
* ``available_models(override=None)`` -- ``dict[name -> Path | None]``
* ``diagnostic_search_report(override=None)`` -- where the resolver looked
"""
from __future__ import annotations

import json
import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

# The labeler's model files, by short name.
MODEL_FILES = {
    "stage1": "cell_type_classifier.pkl",
    "stage2": "branch_classifier.pkl",
    "correction": "branch_correction.joblib",
    "flag": "flag_model.joblib",
}
REQUIRED_MODELS = ("stage1", "stage2", "correction")

ENV_VAR = "SWCSTUDIO_MODEL_DIR"
BUNDLED_ENV_VAR = "SWCSTUDIO_BUNDLED_MODEL_DIR"


@lru_cache(maxsize=1)
def labeler_info() -> dict[str, Any]:
    """The labeler SWC-Studio runs: ``LABELER.json`` written by ``scripts/sync_labeler.py``."""
    path = Path(__file__).resolve().parent / "labeler" / "LABELER.json"
    return json.loads(path.read_text(encoding="utf-8"))


def labeler_models_dirname() -> str:
    """Folder name of the shipped model set, e.g. ``labeler-1.0.0``."""
    return str(labeler_info()["models_dir"])


def _user_data_root() -> Path:
    """Per-platform user data directory root."""
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


def user_model_dir() -> Path:
    """User data directory for swcstudio models. Created on first use."""
    out = _user_data_root() / "swcstudio" / "models"
    out.mkdir(parents=True, exist_ok=True)
    return out


def bundled_model_dir() -> Path:
    """Directory shipped inside the installed swcstudio package."""
    return Path(__file__).resolve().parent.parent / "data" / "models"


def search_dirs(override: str | os.PathLike | None = None) -> list[Path]:
    """Ordered list of directories that may hold a model set.

    A named folder (``override``, else ``SWCSTUDIO_MODEL_DIR``) is searched alone."""
    named = override or os.environ.get(ENV_VAR)
    if named:
        return [Path(named).expanduser()]
    out: list[Path] = [user_model_dir()]
    bundled_override = os.environ.get(BUNDLED_ENV_VAR)
    if bundled_override:
        out.append(Path(bundled_override))
    out.append(bundled_model_dir())
    seen: set[str] = set()
    deduped: list[Path] = []
    for d in out:
        key = str(d.resolve())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(d)
    return deduped


def _candidate_dirs(override: str | os.PathLike | None = None) -> list[Path]:
    """Each search directory's versioned model folder, then the directory itself."""
    name = labeler_models_dirname()
    out: list[Path] = []
    for d in search_dirs(override):
        out.extend([d / name, d])
    return out


def _is_complete(folder: Path) -> bool:
    return all((folder / MODEL_FILES[key]).is_file() for key in REQUIRED_MODELS)


def _first_complete(override: str | os.PathLike | None) -> Path | None:
    for folder in _candidate_dirs(override):
        if _is_complete(folder):
            return folder
    return None


def resolve_model_dir(
    override: str | os.PathLike | None = None,
    *,
    auto_download: bool = True,
) -> Path | None:
    """The first complete model folder in search order, or ``None``.

    If none is found and ``auto_download`` is set, the model set of the latest
    release is fetched into the user data directory (see
    :mod:`swcstudio.core.updater`) and the search runs again. Installed copies
    carry the model set, so this is a repair path, not a first-run requirement.
    """
    found = _first_complete(override)
    if found is not None or not auto_download or override or os.environ.get(ENV_VAR):
        return found
    try:
        from swcstudio.core import updater  # noqa: PLC0415
    except ImportError:
        return None
    manifest = updater.fetch_manifest()
    if manifest is None or manifest.models is None:
        return None
    try:
        updater.apply_update("models", manifest.models)
    except Exception:  # noqa: BLE001 - network or disk failure; report "not found"
        return None
    return _first_complete(override)


def resolve_model_path(
    name_or_filename: str,
    *,
    override: str | os.PathLike | None = None,
    auto_download: bool = True,
) -> Path | None:
    """One file of the resolved model folder, by short name or file name."""
    folder = resolve_model_dir(override, auto_download=auto_download)
    if folder is None:
        return None
    candidate = folder / MODEL_FILES.get(name_or_filename, name_or_filename)
    return candidate if candidate.is_file() else None


def available_models(override: str | os.PathLike | None = None) -> dict[str, Path | None]:
    """``{short_name: Path | None}`` for every model file of the resolved folder."""
    folder = resolve_model_dir(override, auto_download=False)
    return {key: (folder / fname if folder is not None and (folder / fname).is_file() else None)
            for key, fname in MODEL_FILES.items()}


def diagnostic_search_report(override: str | os.PathLike | None = None) -> str:
    """Human-readable description of where the resolver looked."""
    lines: list[str] = [f"Labeler {labeler_info()['version']}; model folders searched, in order:"]
    for folder in _candidate_dirs(override):
        missing = [MODEL_FILES[k] for k in REQUIRED_MODELS if not (folder / MODEL_FILES[k]).is_file()]
        if not missing:
            marker = "[COMPLETE]"
        elif not folder.is_dir():
            marker = "[MISSING]"
        elif len(missing) == len(REQUIRED_MODELS):
            marker = "[no model files]"
        else:
            marker = f"[INCOMPLETE: missing {', '.join(missing)}]"
        lines.append(f"  {marker} {folder}")
    lines.append(
        "A model folder needs " + ", ".join(MODEL_FILES[k] for k in REQUIRED_MODELS)
        + f"; {MODEL_FILES['flag']} adds the quality flag.")
    lines.append(f"Point to one with {ENV_VAR}=/path/to/dir, the `--model-dir` CLI flag "
                 "or the GUI selector.")
    return "\n".join(lines)
