"""Verify that a wheel or sdist contains every runtime data file and dependency."""
from __future__ import annotations

import argparse
import email
import glob
import hashlib
import json
import re
import tarfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
# The labeler SWC-Studio ships (written by scripts/sync_labeler.py): every model
# file must be in the artifact with exactly the recorded SHA-256, which also
# catches Git LFS pointer files and empty placeholders.
LABELER = json.loads((ROOT / "swcstudio" / "core" / "labeler" / "LABELER.json").read_text(encoding="utf-8"))
MODELS_DIR = f"swcstudio/data/models/{LABELER['models_dir']}"
MODEL_FILES = {name: meta["sha256"] for name, meta in LABELER["models"].items()}

CONFIG_FILES = (
    "batch_processing/configs/auto_typing.json",
    "batch_processing/configs/batch_validation.json",
    "batch_processing/configs/radii_cleaning.json",
    "batch_processing/configs/split.json",
    "batch_processing/configs/swc_splitter.json",
    "morphology_editing/configs/dendrogram_editing.json",
    "morphology_editing/configs/simplification.json",
    "validation/configs/auto_fix.json",
    "validation/configs/default.json",
    "validation/configs/radii_cleaning.json",
    "validation/configs/run_checks.json",
    "visualization/configs/mesh_editing.json",
)

RUNTIME_DISTRIBUTIONS = {
    "joblib",
    "morphio",
    "neurom",
    "numpy",
    "pandas",
    "pyqtgraph",
    "pyside6",
    "pyzipper",
    "scikit-learn",
    "scipy",
    "vispy",
    "xgboost",
    "zstandard",
}


def _normalise_distribution(raw: str) -> str:
    name = re.split(r"[\s(<=>;!\[]", raw, maxsplit=1)[0]
    return re.sub(r"[-_.]+", "-", name).lower()


def _normalise_specifier(raw: str) -> set[str]:
    return {part.strip() for part in raw.split(",") if part.strip()}


def _read_artifact(path: Path) -> tuple[set[str], bytes]:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            members = set(archive.namelist())
            metadata_name = next(
                name for name in members if name.endswith(".dist-info/METADATA")
            )
            return members, archive.read(metadata_name)

    if path.name.endswith(".tar.gz"):
        with tarfile.open(path, "r:gz") as archive:
            members = {member.name for member in archive.getmembers()}
            metadata_name = next(
                name for name in members if name.endswith("/PKG-INFO")
            )
            metadata_file = archive.extractfile(metadata_name)
            if metadata_file is None:
                raise RuntimeError(f"Could not read {metadata_name}")
            return members, metadata_file.read()

    raise ValueError(f"Unsupported artifact type: {path}")


def _read_member(path: Path, suffix: str) -> bytes | None:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            name = next(
                (member for member in archive.namelist() if member.endswith(suffix)),
                None,
            )
            return archive.read(name) if name else None
    with tarfile.open(path, "r:gz") as archive:
        member = next(
            (item for item in archive.getmembers() if item.name.endswith(suffix)),
            None,
        )
        if member is None:
            return None
        stream = archive.extractfile(member)
        return stream.read() if stream is not None else None


def verify_distribution(path: Path) -> dict:
    members, metadata_bytes = _read_artifact(path)
    required_suffixes = {
        *(f"{MODELS_DIR}/{name}" for name in MODEL_FILES),
        *(f"swcstudio/tools/{name}" for name in CONFIG_FILES),
        "swcstudio/core/labeler/LABELER.json",
    }
    missing_files = sorted(
        suffix
        for suffix in required_suffixes
        if not any(member.endswith(suffix) for member in members)
    )

    metadata = email.message_from_bytes(metadata_bytes)
    dependencies = {
        _normalise_distribution(value)
        for value in metadata.get_all("Requires-Dist", [])
    }
    missing_dependencies = sorted(RUNTIME_DISTRIBUTIONS - dependencies)
    invalid_models = []
    for name, sha256 in MODEL_FILES.items():
        payload = _read_member(path, f"{MODELS_DIR}/{name}")
        if payload is None:
            continue
        digest = hashlib.sha256(payload).hexdigest()
        if digest != sha256:
            invalid_models.append({"name": name, "size": len(payload), "sha256": digest,
                                   "expected_sha256": sha256})

    requires_python = str(metadata.get("Requires-Python", "")).strip()
    entry_points_ok = True
    if path.suffix == ".whl":
        entry_points = _read_member(path, ".dist-info/entry_points.txt") or b""
        entry_points_ok = (
            b"swcstudio = swcstudio.cli.cli:main" in entry_points
            and b"swcstudio-gui = swcstudio.gui.main:main" in entry_points
        )

    result = {
        "artifact": str(path),
        "ok": (
            not missing_files
            and not missing_dependencies
            and not invalid_models
            and _normalise_specifier(requires_python) == {">=3.10", "<3.13"}
            and entry_points_ok
        ),
        "model_count": sum(
            any(member.endswith(f"{MODELS_DIR}/{name}") for member in members)
            for name in MODEL_FILES
        ),
        "config_count": sum(
            any(member.endswith(f"swcstudio/tools/{name}") for member in members)
            for name in CONFIG_FILES
        ),
        "missing_files": missing_files,
        "missing_dependencies": missing_dependencies,
        "invalid_models": invalid_models,
        "requires_python": requires_python,
        "entry_points_ok": entry_points_ok,
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifacts", nargs="+", type=Path)
    args = parser.parse_args()

    artifacts: list[Path] = []
    for raw_path in args.artifacts:
        raw = str(raw_path)
        if any(char in raw for char in "*?["):
            artifacts.extend(Path(match) for match in glob.glob(raw))
        else:
            artifacts.append(raw_path)
    if not artifacts:
        parser.error("no distribution artifacts matched")

    reports = [verify_distribution(path.resolve()) for path in artifacts]
    print(json.dumps(reports, indent=2, sort_keys=True))
    return 0 if all(report["ok"] for report in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
