"""Archive-backed provenance history stores.

The canonical provenance layout is still the existing history tree:

    version
    events.jsonl
    objects/
    refs/
    index.sqlite
    root.json

This module packages that tree into a visible sidecar archive named
``<swc_stem>_history.swcstudio``. Writers materialize the archive into
the normal working tree, update it through the existing provenance APIs,
then atomically replace the archive and remove the working tree. Readers
can open an extracted temporary copy without leaving a hidden
``.history`` directory behind.

Archives are written with AES-ZIP encryption through ``pyzipper``. By
default SWC-Studio uses an app-managed password so the sidecar is not a
normal user-editable zip folder. ``SWCSTUDIO_HISTORY_PASSWORD`` can be
set by advanced users to override that password.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from swcstudio.core.provenance.canonical import sha256_hex
from swcstudio.core.provenance.header import parse_prov_header

try:  # Optional; declared in pyproject for packaged installs.
    import pyzipper  # type: ignore
except Exception:  # pragma: no cover - depends on optional runtime package
    pyzipper = None

ARCHIVE_FORMAT_VERSION = 1
ARCHIVE_SUFFIX = "_history.swcstudio"
LEGACY_ARCHIVE_SUFFIX = "_history.swcstudio.zip"
MANIFEST_NAME = "repo_manifest.json"
HISTORY_DIR_NAME = "history"
PASSWORD_ENV = "SWCSTUDIO_HISTORY_PASSWORD"
SOURCE_ARCHIVE_MARKER = ".source_archive_name"
APP_MANAGED_PASSWORD = b"SWC-Studio history archive v1 app-managed key"

__all__ = [
    "ARCHIVE_FORMAT_VERSION",
    "ARCHIVE_SUFFIX",
    "LEGACY_ARCHIVE_SUFFIX",
    "MANIFEST_NAME",
    "PASSWORD_ENV",
    "archive_path_for",
    "archive_name_for",
    "archive_history_dir",
    "ensure_history_materialized",
    "ensure_history_manifest",
    "history_archive_exists",
    "history_repo_info",
    "open_history_for_read",
]


def archive_name_for(swc_path: str | os.PathLike[str]) -> str:
    p = Path(swc_path)
    return f"{p.stem}{ARCHIVE_SUFFIX}"


def archive_path_for(swc_path: str | os.PathLike[str]) -> Path:
    p = Path(swc_path)
    return p.parent / archive_name_for(p)


def _legacy_archive_path_for(swc_path: str | os.PathLike[str]) -> Path:
    p = Path(swc_path)
    return p.parent / f"{p.stem}{LEGACY_ARCHIVE_SUFFIX}"


def history_archive_exists(swc_path: str | os.PathLike[str]) -> bool:
    return _archive_for_open(swc_path).exists()


def ensure_history_materialized(
    swc_path: str | os.PathLike[str],
    history_dir: str | os.PathLike[str],
) -> Path:
    """Ensure ``history_dir`` exists, extracting the visible archive if needed."""
    hist = Path(history_dir)
    if hist.exists():
        return hist
    archive = _archive_for_open(swc_path)
    if archive.exists():
        _extract_archive(archive, hist)
        (hist / SOURCE_ARCHIVE_MARKER).write_text(archive.name, encoding="utf-8")
        _validate_repo_id(hist, swc_path)
    return hist


@contextmanager
def open_history_for_read(
    swc_path: str | os.PathLike[str],
    history_dir: str | os.PathLike[str],
) -> Iterator[Path]:
    """Yield a readable history dir without leaving extracted files behind.

    If the working ``history_dir`` already exists, it is yielded directly.
    Otherwise, the visible archive is extracted into a temporary directory
    and deleted when the context exits.
    """
    hist = Path(history_dir)
    if hist.exists():
        yield hist
        return

    archive = _archive_for_open(swc_path)
    if not archive.exists():
        yield hist
        return

    with tempfile.TemporaryDirectory(prefix="swcstudio_history_") as tmp:
        temp_hist = Path(tmp) / "history"
        _extract_archive(archive, temp_hist)
        _validate_repo_id(temp_hist, swc_path)
        yield temp_hist


def ensure_history_manifest(
    history_dir: str | os.PathLike[str],
    swc_path: str | os.PathLike[str],
) -> dict:
    """Create/update the archive manifest stored inside the history tree."""
    hist = Path(history_dir)
    hist.mkdir(parents=True, exist_ok=True)
    manifest_path = hist / MANIFEST_NAME
    now = _utcnow()
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            manifest = {}
    else:
        manifest = {}

    manifest.setdefault("schema_version", ARCHIVE_FORMAT_VERSION)
    manifest.setdefault("repo_id", str(uuid.uuid4()))
    manifest.setdefault("created_utc", now)
    manifest["updated_utc"] = now
    manifest["swc_file_name"] = Path(swc_path).name
    manifest["archive_name"] = archive_name_for(swc_path)
    manifest["archive_format"] = "swcstudio-encrypted-zip"
    manifest["encryption"] = _encryption_label()
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def history_repo_info(
    history_dir: str | os.PathLike[str],
    swc_path: str | os.PathLike[str],
) -> dict:
    return ensure_history_manifest(history_dir, swc_path)


def archive_history_dir(
    history_dir: str | os.PathLike[str],
    swc_path: str | os.PathLike[str],
    *,
    remove_dir: bool = True,
) -> Path:
    """Zip the history tree into the visible sidecar archive.

    The archive is written to a temp file next to the final path and then
    atomically replaced. The internal zip layout preserves the history
    tree below a non-hidden ``history/`` root.
    """
    hist = Path(history_dir)
    if not hist.exists():
        return archive_path_for(swc_path)

    old_manifest = _read_manifest(hist)
    manifest = ensure_history_manifest(hist, swc_path)
    archive = archive_path_for(swc_path)
    archive.parent.mkdir(parents=True, exist_ok=True)
    tmp = archive.with_name(f".{archive.name}.tmp")
    if tmp.exists():
        tmp.unlink()

    password = _history_password()
    if pyzipper is None:
        raise RuntimeError(
            "History archives are encrypted by default, but pyzipper is not installed. "
            "Install SWC-Studio with the history encryption dependency."
        )

    entries = _history_entries(hist)
    comment = _archive_comment(manifest)
    assert pyzipper is not None
    with pyzipper.AESZipFile(
        tmp,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        encryption=pyzipper.WZ_AES,
    ) as zf:
        zf.setpassword(password)
        zf.comment = comment
        _write_entries(zf, hist, entries)

    os.replace(tmp, archive)
    marker = hist / SOURCE_ARCHIVE_MARKER
    if marker.exists():
        old_name = marker.read_text(encoding="utf-8").strip()
        try:
            marker.unlink()
        except OSError:
            pass
    else:
        old_name = str(old_manifest.get("archive_name", "") or "")
    if old_name and old_name != archive.name:
        old_path = archive.parent / old_name
        # A copy of a tracked SWC starts from the original's history, but
        # the original keeps its archive: only a rename moves it.
        if not _same_file(old_path, archive) and not _original_swc_present(
            old_name, swc_path
        ):
            try:
                old_path.unlink()
            except FileNotFoundError:
                pass
    if remove_dir:
        shutil.rmtree(hist, ignore_errors=True)
    return archive


def _same_file(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def _original_swc_present(old_name: str, swc_path: str | os.PathLike[str]) -> bool:
    """Whether the SWC an archive was named for still exists beside it."""
    current = Path(swc_path)
    stem = old_name
    for suffix in (LEGACY_ARCHIVE_SUFFIX, ARCHIVE_SUFFIX):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    try:
        siblings = list(current.parent.iterdir())
    except OSError:
        return False
    return any(
        p.stem == stem
        and p.suffix.lower() == ".swc"
        and p.is_file()
        and not _same_file(p, current)
        for p in siblings
    )


def _archive_for_open(swc_path: str | os.PathLike[str]) -> Path:
    current = archive_path_for(swc_path)
    if current.exists():
        return current
    header_repo = _repo_name_from_swc_header(swc_path)
    if header_repo:
        candidate = Path(swc_path).parent / header_repo
        if candidate.exists():
            return candidate
    legacy = _legacy_archive_path_for(swc_path)
    if legacy.exists():
        return legacy
    return current


def _repo_name_from_swc_header(swc_path: str | os.PathLike[str]) -> str | None:
    path = Path(swc_path)
    if not path.exists():
        return None
    try:
        header = parse_prov_header(path.read_bytes())
    except Exception:
        return None
    for record in (header.root, header.tip):
        if not record:
            continue
        for key in ("repo", "sidecar"):
            value = str(record.get(key, "") or "").strip()
            if value.endswith(ARCHIVE_SUFFIX) or value.endswith(LEGACY_ARCHIVE_SUFFIX):
                return Path(value).name
    return None


def _read_manifest(hist: Path) -> dict:
    path = hist / MANIFEST_NAME
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _validate_repo_id(hist: Path, swc_path: str | os.PathLike[str]) -> None:
    manifest = _read_manifest(hist)
    repo_id = str(manifest.get("repo_id", "") or "")
    if not repo_id:
        return
    path = Path(swc_path)
    if not path.exists():
        return
    try:
        header = parse_prov_header(path.read_bytes())
    except Exception:
        return
    expected = ""
    for record in (header.root, header.tip):
        if record and record.get("repo_id"):
            expected = str(record.get("repo_id"))
            break
    if expected and expected != repo_id:
        raise RuntimeError(
            f"History archive repo_id mismatch for {path.name}: "
            f"SWC header expects {expected}, archive has {repo_id}."
        )


def _extract_archive(archive: Path, history_dir: Path) -> None:
    history_dir.parent.mkdir(parents=True, exist_ok=True)
    if history_dir.exists():
        return
    tmp = Path(tempfile.mkdtemp(prefix=".history_extract_", dir=str(history_dir.parent)))
    try:
        if _archive_is_encrypted(archive):
            if pyzipper is None:
                raise RuntimeError(
                    "History archive is encrypted, but pyzipper is not installed."
                )
            with pyzipper.AESZipFile(archive, "r") as zf:
                zf.setpassword(_history_password())
                _safe_extract(zf, tmp)
        else:
            with zipfile.ZipFile(archive, "r") as zf:
                _safe_extract(zf, tmp)

        extracted = tmp / HISTORY_DIR_NAME
        if not extracted.exists():
            # Accept old/simple archives whose files are stored at root.
            extracted = tmp
        os.replace(extracted, history_dir)
    except RuntimeError as e:
        if "password" in str(e).lower() or "encrypted" in str(e).lower():
            raise RuntimeError(
                "History archive is encrypted, but SWC-Studio could not decrypt it. "
                f"If this archive was written with a custom password, set {PASSWORD_ENV} "
                "before opening it."
            ) from e
        raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _history_entries(hist: Path) -> list[Path]:
    out: list[Path] = []
    for path in hist.rglob("*"):
        if path.is_dir():
            continue
        if path.name == "lock":
            continue
        if path.name == "checksums.json":
            continue
        if path.name == SOURCE_ARCHIVE_MARKER:
            continue
        if path.name.endswith("-wal") or path.name.endswith("-shm"):
            continue
        out.append(path)
    return sorted(out, key=lambda p: str(p.relative_to(hist)).replace("\\", "/"))


def _archive_comment(manifest: dict) -> bytes:
    payload = {
        "schema_version": ARCHIVE_FORMAT_VERSION,
        "repo_id": manifest.get("repo_id"),
        "swc_file_name": manifest.get("swc_file_name"),
        "encryption": manifest.get("encryption"),
    }
    return json.dumps(payload, sort_keys=True).encode("utf-8")[:65535]


def _write_entries(zf, hist: Path, entries: list[Path]) -> None:
    checksums: dict[str, str] = {}
    for path in entries:
        rel = path.relative_to(hist).as_posix()
        arcname = f"{HISTORY_DIR_NAME}/{rel}"
        data = path.read_bytes()
        checksums[rel] = sha256_hex(data)
        zf.writestr(arcname, data)
    checksums_blob = json.dumps(checksums, sort_keys=True).encode("utf-8")
    zf.writestr(f"{HISTORY_DIR_NAME}/checksums.json", checksums_blob)


def _safe_extract(zf, dest: Path) -> None:
    for info in zf.infolist():
        name = info.filename.replace("\\", "/")
        if not name or name.endswith("/"):
            continue
        if name.startswith("/") or ".." in Path(name).parts:
            raise RuntimeError(f"unsafe history archive member: {info.filename!r}")
        target = dest / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info, "r") as src, open(target, "wb") as out:
            shutil.copyfileobj(src, out)


def _archive_is_encrypted(archive: Path) -> bool:
    try:
        with zipfile.ZipFile(archive, "r") as zf:
            return any((info.flag_bits & 0x1) or info.compress_type == 99 for info in zf.infolist())
    except zipfile.BadZipFile:
        raise RuntimeError(f"Invalid history archive: {archive}")


def _history_password() -> bytes:
    raw = os.environ.get(PASSWORD_ENV, "")
    if not raw:
        return APP_MANAGED_PASSWORD
    return raw.encode("utf-8")


def _encryption_label() -> str:
    if os.environ.get(PASSWORD_ENV, ""):
        return "zip-aes-custom-password"
    return "zip-aes-app-managed"


def _utcnow() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
