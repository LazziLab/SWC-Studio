"""Copy the released SWC auto-labeler into SWC-Studio.

SWC-Studio labels with the auto-labeler's own code and models, so its labels and
quality flags are the ones the labeler's paper reports. This script copies both
from a checkout of the labeler repository (https://github.com/Mio0v0/swc-autolabeler):

* the ``pipeline`` package into ``swcstudio/core/labeler/``. The copy is the
  labeler's code with two mechanical changes: its absolute ``pipeline.`` imports
  become relative, so the copy imports itself rather than a top-level
  ``pipeline`` package, and the few lines that put the labeler repository on
  ``sys.path`` become no-ops. Modules SWC-Studio does not use (the unstaged
  all-in-one model and a corpus-scanning helper) are left out.
* the deployed model set (``pipeline/models/seed123``) into
  ``swcstudio/data/models/labeler-<version>/``.
* ``swcstudio/core/labeler/LABELER.json``: the labeler version, the source
  commit, and the size and SHA-256 of every copied model file.

Only files the labeler repository tracks in git are copied, so stray local
files (scratch modules, caches) never ship. The new copy is built in a staging
folder and checked (every module compiles and imports, every model file matches
the checksum of its source) before the old copy is replaced, and model folders
of other labeler versions (``swcstudio/data/models/labeler-*``) are removed, so
only the current set ships.

Run it from the SWC-Studio repository root:

    python scripts/sync_labeler.py --source ../Autolabeler

Never edit ``swcstudio/core/labeler/`` by hand; change the labeler and sync again.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEST_CODE = ROOT / "swcstudio" / "core" / "labeler"
DEST_MODELS_ROOT = ROOT / "swcstudio" / "data" / "models"

# Modules of the labeler's ``pipeline`` package that SWC-Studio does not use.
SKIP_MODULES = {"_scan_corpus_qc.py", "infer_unstaged.py", "unstaged.py", "train_unstaged.py"}
# The deployed model set: Stage 1, Stage 2, the Stage 3 branch correction, the Stage 4 flag.
MODEL_FILES = ("cell_type_classifier.pkl", "branch_classifier.pkl",
               "branch_correction.joblib", "flag_model.joblib")

_FROM_PIPELINE = re.compile(r"^(?P<indent>[ \t]*)from pipeline(?P<mod>(?:\.[A-Za-z_]\w*)*) import ", re.M)
_IMPORT_PIPELINE = re.compile(r"^[ \t]*import pipeline\b", re.M)
_SYS_PATH_INSERT = re.compile(r"^(?P<indent>[ \t]*)sys\.path\.insert\(0, str\(ROOT\)\)[ \t]*$", re.M)

README = """\
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
"""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _labeler_version(source: Path) -> str:
    cff = (source / "CITATION.cff").read_text(encoding="utf-8")
    m = re.search(r"^version:\s*[\"']?([0-9][^\s\"']*)", cff, re.M)
    if not m:
        raise SystemExit(f"no version in {source / 'CITATION.cff'}")
    return m.group(1)


def _git(source: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(source), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def _rewrite(text: str, depth: int, rel: str) -> str:
    """Make the module's ``pipeline.`` imports relative; neutralize sys.path inserts."""
    dots = "." * (depth + 1)

    def fix(m: re.Match) -> str:
        mod = m.group("mod").lstrip(".")
        return f"{m.group('indent')}from {dots}{mod} import "

    text = _FROM_PIPELINE.sub(fix, text)
    text = _SYS_PATH_INSERT.sub(
        lambda m: f"{m.group('indent')}pass  # SWC-Studio's copy imports its own package relatively", text)
    if _IMPORT_PIPELINE.search(text):
        raise SystemExit(f"{rel}: 'import pipeline...' cannot be rewritten; use 'from pipeline... import'")
    return text


def _tracked_files(source: Path, prefix: str) -> set[str]:
    """Paths (POSIX, relative to ``source``) git tracks under ``prefix``."""
    out = subprocess.run(["git", "-C", str(source), "ls-files", "-z", "--", prefix], check=True,
                         capture_output=True).stdout.decode("utf-8")
    return {p for p in out.split("\0") if p}


def _validate_staged(stage: Path, code_dir: Path, models_dir: Path, record: dict) -> None:
    """Refuse a staged copy that would not run: a model file that differs from
    its source checksum, or a module that does not compile or import."""
    for name, meta in record["models"].items():
        path = models_dir / name
        if not path.is_file() or _sha256(path) != meta["sha256"] or path.stat().st_size != meta["bytes"]:
            raise SystemExit(f"staged model file {name} does not match its source; nothing was replaced")
    import py_compile  # noqa: PLC0415

    for rel in record["modules"]:
        try:
            py_compile.compile(str(code_dir / rel), doraise=True, cfile=str(stage / "pyc" / (rel + "c")))
        except py_compile.PyCompileError as exc:
            raise SystemExit(f"staged module {rel} does not compile ({exc}); nothing was replaced") from exc
    modules = [code_dir.name + "." + rel[:-3].replace("/", ".").removesuffix(".__init__")
               for rel in record["modules"]]
    script = ("import importlib, sys\n"
              f"sys.path.insert(0, {str(stage)!r})\n"
              f"for name in {modules!r}:\n"
              "    importlib.import_module(name)\n")
    done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                          env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    if done.returncode != 0:
        raise SystemExit("the staged labeler does not import; nothing was replaced:\n"
                         + (done.stderr or done.stdout).strip())


def _swap_in(staged: Path, dest: Path, backup: Path) -> None:
    """Put ``staged`` where ``dest`` is, keeping the old ``dest`` at ``backup``
    until the new one is in place."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        os.replace(dest, backup)
    try:
        os.replace(staged, dest)
    except OSError:
        if backup.exists():
            os.replace(backup, dest)
        raise


def sync(source: Path, seed_dir: str, allow_dirty: bool) -> dict:
    pkg = source / "pipeline"
    if not (pkg / "infer_pipeline.py").is_file():
        raise SystemExit(f"{source} is not a labeler checkout (no pipeline/infer_pipeline.py)")
    dirty = _git(source, "status", "--porcelain", "--untracked-files=no", "--", "pipeline", "CITATION.cff")
    if dirty and not allow_dirty:
        raise SystemExit(f"{source} has uncommitted changes under pipeline/:\n{dirty}\n"
                         "Commit them first, or pass --allow-dirty for a trial sync.")
    version = _labeler_version(source)
    commit = _git(source, "rev-parse", "HEAD")
    tracked = _tracked_files(source, "pipeline")
    models_dir_name = f"labeler-{version}"
    seed_rel = seed_dir.replace("\\", "/").strip("/")

    stage = Path(tempfile.mkdtemp(prefix=".labeler-sync-", dir=ROOT))
    try:
        # --- code: the modules git tracks, rewritten ---
        code_dir = stage / "labeler"
        copied = []
        for rel_str in sorted(tracked):
            rel_path = Path(rel_str)
            if rel_path.suffix != ".py":
                continue
            rel = rel_path.relative_to("pipeline")
            if rel.parts[0] == "models" or rel.name in SKIP_MODULES:
                continue
            src = source / rel_path
            depth = len(rel.parts) - 1
            dst = code_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            text = src.read_text(encoding="utf-8")
            dst.write_text(_rewrite(text, depth, rel.as_posix()), encoding="utf-8", newline="\n")
            copied.append(rel.as_posix())
        (code_dir / "README.md").write_text(README, encoding="utf-8", newline="\n")

        # --- models: the tracked files of the deployed set ---
        src_models = source / seed_rel
        models_dir = stage / models_dir_name
        models_dir.mkdir(parents=True)
        models = {}
        for name in MODEL_FILES:
            src = src_models / name
            if not src.is_file():
                raise SystemExit(f"missing model file {src}")
            if f"{seed_rel}/{name}" not in tracked:
                raise SystemExit(f"model file {src} is not tracked by git in {source}")
            shutil.copy2(src, models_dir / name)
            models[name] = {"bytes": src.stat().st_size, "sha256": _sha256(src)}

        record = {
            "name": "SWC auto-labeler",
            "version": version,
            "source_repository": "https://github.com/Mio0v0/swc-autolabeler",
            "source_commit": commit,
            "source_model_set": seed_rel,
            "models_dir": models_dir_name,
            "models": models,
            "modules": copied,
        }
        (code_dir / "LABELER.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8",
                                               newline="\n")
        _validate_staged(stage, code_dir, models_dir, record)

        # --- replace the old copy, then drop other versions' model folders ---
        _swap_in(code_dir, DEST_CODE, stage / "old-labeler")
        _swap_in(models_dir, DEST_MODELS_ROOT / models_dir_name, stage / "old-models")
        for stale in sorted(DEST_MODELS_ROOT.glob("labeler-*")):
            if stale.is_dir() and stale.name != models_dir_name:
                shutil.rmtree(stale)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return record


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", type=Path, required=True, help="labeler repository checkout")
    ap.add_argument("--model-set", default="pipeline/models/seed123",
                    help="model folder inside the checkout to ship (default: the deployed seed123 set)")
    ap.add_argument("--allow-dirty", action="store_true",
                    help="sync even if the checkout has uncommitted changes under pipeline/")
    args = ap.parse_args()
    rec = sync(args.source.resolve(), args.model_set, args.allow_dirty)
    print(f"labeler {rec['version']} ({rec['source_commit'][:10]}): "
          f"{len(rec['modules'])} modules -> {DEST_CODE.relative_to(ROOT)}, "
          f"{len(rec['models'])} model files -> {(DEST_MODELS_ROOT / rec['models_dir']).relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
