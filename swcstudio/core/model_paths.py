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

A folder you name is the only one searched: if it is incomplete,
auto-labeling reports what is missing rather than quietly using other models.
The first of these that is set names the folder:

1. the explicit argument (``--model-dir``, the GUI selector, ``model_dir=``);
2. the ``SWCSTUDIO_MODEL_DIR`` environment variable;
3. ``model_dir`` in the auto-typing configuration file
   (``swcstudio/tools/batch_processing/configs/auto_typing.json``).

Without a named folder the search order is (the first complete folder wins):

1. The user data directory, where model updates are installed:

   * Windows: ``%APPDATA%\\swcstudio\\models``
   * macOS: ``~/Library/Application Support/swcstudio/models``
   * Linux: ``~/.local/share/swcstudio/models``

2. The desktop runtime's model directory, supplied by the bootstrap through
   ``SWCSTUDIO_BUNDLED_MODEL_DIR``.
3. The directory inside the installed package (``swcstudio/data/models``).

Searching never creates or writes anything; an unreadable user data
directory is skipped.

Public API:

* ``labeler_info()`` -- version, source commit and model checksums of the labeler
* ``select_model_dir(override=None)`` -- the model folder and where it came from
* ``resolve_model_dir(override=None)`` -- the model folder, or ``None``
* ``resolve_model_path(name, override=None)`` -- one file of that folder, or ``None``
* ``available_models(override=None)`` -- ``dict[name -> Path | None]``
* ``diagnostic_search_report(override=None)`` -- where the resolver looked
* ``model_record(folder, source)`` -- the provenance record of a model folder
* ``recorded_model_dir(path)`` -- a folder as history records it (home as ``~``)
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import dataclass
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

# Where a model folder came from, as ``model_record`` reports it.
MODEL_SOURCES = ("argument", "env", "config", "user", "bundled")


@lru_cache(maxsize=1)
def labeler_info() -> dict[str, Any]:
    """The labeler SWC-Studio runs: ``LABELER.json`` written by ``scripts/sync_labeler.py``."""
    path = Path(__file__).resolve().parent / "labeler" / "LABELER.json"
    return json.loads(path.read_text(encoding="utf-8"))


def labeler_models_dirname() -> str:
    """Folder name of the shipped model set, e.g. ``labeler-1.0.1``."""
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
    """User data directory for swcstudio models.

    Only the path: searching for models never creates it. Model updates
    create it when they install (:mod:`swcstudio.core.updater`).
    """
    return _user_data_root() / "swcstudio" / "models"


def bundled_model_dir() -> Path:
    """Directory shipped inside the installed swcstudio package."""
    return Path(__file__).resolve().parent.parent / "data" / "models"


def recorded_model_dir(model_dir: str | os.PathLike | None) -> str | None:
    """A model folder as history records it: the home directory written as ``~``.

    The history travels with the SWC, so a path under the home folder would
    carry the username. Model lookup expands ``~`` again, so the command
    ``history reproduce`` prints still finds the folder.
    """
    if not model_dir:
        return model_dir  # type: ignore[return-value]
    try:
        path = Path(model_dir).expanduser().absolute()
        return "~/" + path.relative_to(Path.home()).as_posix()
    except (ValueError, RuntimeError, KeyError):  # outside home, or no home folder
        return str(model_dir)


def _is_file(path: Path) -> bool:
    """``path.is_file()``, but ``False`` instead of an error for a location that
    cannot be read (a user data directory without permission, say)."""
    try:
        return path.is_file()
    except OSError:
        return False


def _config_model_dir() -> str | None:
    """``model_dir`` of the auto-typing configuration file, or ``None`` when empty."""
    try:
        from swcstudio.core.config import load_feature_config  # noqa: PLC0415

        value = load_feature_config("batch_processing", "auto_typing", default={}).get("model_dir")
    except Exception:  # noqa: BLE001 - an unreadable config names no folder
        return None
    text = str(value or "").strip()
    return text or None


def named_model_dir(override: str | os.PathLike | None = None) -> tuple[Path, str] | None:
    """The folder the user named and how, or ``None`` for the default search.

    In order of precedence: ``override`` (``"argument"``), ``SWCSTUDIO_MODEL_DIR``
    (``"env"``), ``model_dir`` in the auto-typing configuration (``"config"``).
    """
    if override:
        return Path(override).expanduser(), "argument"
    env = (os.environ.get(ENV_VAR) or "").strip()
    if env:
        return Path(env).expanduser(), "env"
    configured = _config_model_dir()
    if configured:
        return Path(configured).expanduser(), "config"
    return None


def _search_dirs_with_source(override: str | os.PathLike | None = None) -> list[tuple[Path, str]]:
    named = named_model_dir(override)
    if named is not None:
        return [named]
    out: list[tuple[Path, str]] = []
    try:
        out.append((user_model_dir(), "user"))
    except (OSError, RuntimeError, KeyError):  # no usable home or user data folder
        pass
    bundled_override = os.environ.get(BUNDLED_ENV_VAR)
    if bundled_override:
        out.append((Path(bundled_override), "bundled"))
    out.append((bundled_model_dir(), "bundled"))
    seen: set[str] = set()
    deduped: list[tuple[Path, str]] = []
    for d, source in out:
        try:
            key = str(d.resolve())
        except OSError:
            key = str(d)
        if key in seen:
            continue
        seen.add(key)
        deduped.append((d, source))
    return deduped


def search_dirs(override: str | os.PathLike | None = None) -> list[Path]:
    """Ordered list of directories that may hold a model set.

    A named folder (see :func:`named_model_dir`) is searched alone."""
    return [d for d, _source in _search_dirs_with_source(override)]


def _candidate_dirs_with_source(override: str | os.PathLike | None = None) -> list[tuple[Path, str]]:
    """Each search directory's versioned model folder, then the directory itself."""
    name = labeler_models_dirname()
    out: list[tuple[Path, str]] = []
    for d, source in _search_dirs_with_source(override):
        out.extend([(d / name, source), (d, source)])
    return out


def _candidate_dirs(override: str | os.PathLike | None = None) -> list[Path]:
    return [d for d, _source in _candidate_dirs_with_source(override)]


def _is_complete(folder: Path) -> bool:
    return all(_is_file(folder / MODEL_FILES[key]) for key in REQUIRED_MODELS)


@dataclass(frozen=True)
class ModelSelection:
    """A complete model folder and where it came from (one of ``MODEL_SOURCES``)."""
    folder: Path
    source: str


def _first_complete(override: str | os.PathLike | None) -> ModelSelection | None:
    for folder, source in _candidate_dirs_with_source(override):
        if _is_complete(folder):
            return ModelSelection(folder, source)
    return None


def select_model_dir(
    override: str | os.PathLike | None = None,
    *,
    auto_download: bool = False,
) -> ModelSelection | None:
    """The first complete model folder in search order with its source, or ``None``.

    If none is found, no folder is named and ``auto_download`` is set, the
    model set of the latest release is fetched into the user data directory
    (see :mod:`swcstudio.core.updater`) and the search runs again. Installed
    copies carry the model set, so this is a repair path, not a first-run
    requirement. Status probes leave ``auto_download`` off.
    """
    found = _first_complete(override)
    if found is not None or not auto_download or named_model_dir(override) is not None:
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


def resolve_model_dir(
    override: str | os.PathLike | None = None,
    *,
    auto_download: bool = True,
) -> Path | None:
    """The first complete model folder in search order, or ``None``.

    See :func:`select_model_dir`; this returns only the folder.
    """
    found = select_model_dir(override, auto_download=auto_download)
    return found.folder if found is not None else None


# (resolved path, size, mtime_ns) -> sha256 hex, so a run hashes each model file once.
_SHA_CACHE: dict[tuple[str, int, int], str] = {}


def model_file_sha256(path: str | os.PathLike) -> str:
    """SHA-256 of one model file, cached by path, size and modification time."""
    p = Path(path)
    st = p.stat()
    try:
        resolved = str(p.resolve())
    except OSError:
        resolved = str(p)
    key = (resolved, int(st.st_size), int(st.st_mtime_ns))
    digest = _SHA_CACHE.get(key)
    if digest is None:
        h = hashlib.sha256()
        with p.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        digest = h.hexdigest()
        _SHA_CACHE[key] = digest
    return digest


def model_record(folder: str | os.PathLike, source: str) -> dict[str, Any]:
    """The provenance record of the model folder an auto-label run used.

    * ``model_dir``: the folder, home directory written as ``~``
      (:func:`recorded_model_dir`)
    * ``model_source``: where it came from, one of ``MODEL_SOURCES``
    * ``model_version``: the labeler version
    * ``model_files``: ``{file name: SHA-256}`` of the set's model files in
      the folder (the optional flag model only when present)
    * ``model_sha``: SHA-256 of the lines ``"<file name> <sha256>\\n"`` of
      ``model_files``, sorted by file name: one fingerprint for the set
    """
    if source not in MODEL_SOURCES:
        raise ValueError(f"unknown model source {source!r}; expected one of {MODEL_SOURCES}")
    folder = Path(folder)
    files = {fname: model_file_sha256(folder / fname)
             for fname in MODEL_FILES.values() if _is_file(folder / fname)}
    listing = "".join(f"{name} {files[name]}\n" for name in sorted(files))
    return {
        "model_dir": recorded_model_dir(folder.absolute()),
        "model_source": source,
        "model_version": str(labeler_info()["version"]),
        "model_sha": hashlib.sha256(listing.encode("utf-8")).hexdigest(),
        "model_files": files,
    }


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
    return candidate if _is_file(candidate) else None


def available_models(override: str | os.PathLike | None = None) -> dict[str, Path | None]:
    """``{short_name: Path | None}`` for every model file of the resolved folder."""
    folder = resolve_model_dir(override, auto_download=False)
    return {key: (folder / fname if folder is not None and _is_file(folder / fname) else None)
            for key, fname in MODEL_FILES.items()}


_SOURCE_TEXT = {
    "argument": "the folder given (--model-dir or the GUI selector)",
    "env": f"the folder named by {ENV_VAR}",
    "config": "the folder named by model_dir in the auto-typing configuration",
}


def diagnostic_search_report(override: str | os.PathLike | None = None) -> str:
    """Human-readable description of where the resolver looked."""
    named = named_model_dir(override)
    scope = (f"only {_SOURCE_TEXT[named[1]]}" if named is not None
             else "no folder named, so the default places")
    lines: list[str] = [f"Labeler {labeler_info()['version']}; model folders searched, in order "
                        f"({scope}):"]
    for folder in _candidate_dirs(override):
        missing = [MODEL_FILES[k] for k in REQUIRED_MODELS if not _is_file(folder / MODEL_FILES[k])]
        try:
            is_dir = folder.is_dir()
        except OSError:
            is_dir = False
        if not missing:
            marker = "[COMPLETE]"
        elif not is_dir:
            marker = "[MISSING]"
        elif len(missing) == len(REQUIRED_MODELS):
            marker = "[no model files]"
        else:
            marker = f"[INCOMPLETE: missing {', '.join(missing)}]"
        lines.append(f"  {marker} {folder}")
    lines.append(
        "A model folder needs " + ", ".join(MODEL_FILES[k] for k in REQUIRED_MODELS)
        + f"; {MODEL_FILES['flag']} adds the quality flag.")
    lines.append("Name one with the `--model-dir` CLI flag or the GUI selector, "
                 f"{ENV_VAR}=/path/to/dir, or model_dir in the auto-typing configuration "
                 "(in that order of precedence).")
    return "\n".join(lines)
