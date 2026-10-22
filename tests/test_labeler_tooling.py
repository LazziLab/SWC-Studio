"""Developer and training tools around the copied labeler.

* ``swcstudio train auto-typing`` refuses hidden ``*.swc`` files (macOS
  ``._name.swc``), which the labeler's trainer would read as cells.
* ``scripts/sync_labeler.py`` copies only files git tracks, validates the new
  copy before replacing the old one, and removes other versions' model folders.
"""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from swcstudio.core.auto_typing_train import train_user_models

ROOT = Path(__file__).resolve().parent.parent
MODEL_FILES = ("cell_type_classifier.pkl", "branch_classifier.pkl", "branch_correction.joblib", "flag_model.joblib")


class TrainingRefusesHiddenFilesTests(unittest.TestCase):
    def test_apple_double_files_are_listed_and_training_does_not_start(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data"
            for sub in ("pyramidal", "interneuron"):
                (data / sub).mkdir(parents=True)
                (data / sub / "cell.swc").write_text("1 1 0 0 0 1 -1\n", encoding="utf-8")
            (data / "pyramidal" / "._cell.swc").write_bytes(b"\x00\x05\x16\x07Mac OS X")
            (data / "interneuron" / "folder.swc").mkdir()
            with mock.patch("swcstudio.core.labeler.train_pipeline.main") as trainer, \
                    self.assertRaises(ValueError) as ctx:
                train_user_models(data_dir=data, output_dir=Path(tmp) / "out")
            trainer.assert_not_called()
        message = str(ctx.exception)
        self.assertIn("pyramidal/._cell.swc", message)
        self.assertIn("interneuron/folder.swc", message)
        self.assertNotIn("pyramidal/cell.swc", message)


def _load_sync_script():
    spec = importlib.util.spec_from_file_location("sync_labeler_under_test", ROOT / "scripts" / "sync_labeler.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=test", "-c", "user.email=test@example.com",
                    "-c", "commit.gpgsign=false", *args], check=True, capture_output=True)


@unittest.skipIf(shutil.which("git") is None, "git is not installed")
class SyncLabelerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="swcstudio-sync-")
        tmp = Path(self._tmpdir.name)
        self.repo = tmp / "labeler"
        pkg = self.repo / "pipeline"
        (pkg / "models" / "seed123").mkdir(parents=True)
        (self.repo / "CITATION.cff").write_text("version: 9.9.9\n", encoding="utf-8")
        (pkg / "__init__.py").write_text("", encoding="utf-8")
        (pkg / "qc.py").write_text("VALUE = 1\n", encoding="utf-8")
        (pkg / "infer_pipeline.py").write_text("from pipeline.qc import VALUE\n", encoding="utf-8")
        for name in MODEL_FILES:
            (pkg / "models" / "seed123" / name).write_bytes(name.encode())
        _git(self.repo, "init", "-q")
        _git(self.repo, "add", ".")
        _git(self.repo, "commit", "-q", "-m", "labeler")
        # A local file git does not track: never shipped.
        (pkg / "scratch.py").write_text("raise SystemExit('not part of the labeler')\n", encoding="utf-8")

        self.sync = _load_sync_script()
        studio = tmp / "studio"
        self.code = studio / "swcstudio" / "core" / "labeler"
        self.models = studio / "swcstudio" / "data" / "models"
        self.code.mkdir(parents=True)
        (self.code / "old_module.py").write_text("OLD = True\n", encoding="utf-8")
        (self.models / "labeler-0.9.0").mkdir(parents=True)
        (self.models / "labeler-0.9.0" / MODEL_FILES[0]).write_bytes(b"old")
        for name, value in (("ROOT", studio), ("DEST_CODE", self.code), ("DEST_MODELS_ROOT", self.models)):
            patcher = mock.patch.object(self.sync, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_tracked_files_only_and_stale_model_folders_removed(self) -> None:
        record = self.sync.sync(self.repo, "pipeline/models/seed123", False)
        self.assertEqual(sorted(record["modules"]), ["__init__.py", "infer_pipeline.py", "qc.py"])
        self.assertFalse((self.code / "scratch.py").exists())
        self.assertFalse((self.code / "old_module.py").exists())
        self.assertIn("from .qc import VALUE", (self.code / "infer_pipeline.py").read_text(encoding="utf-8"))
        self.assertEqual([p.name for p in self.models.iterdir()], ["labeler-9.9.9"])
        self.assertEqual(sorted(p.name for p in (self.models / "labeler-9.9.9").iterdir()), sorted(MODEL_FILES))
        self.assertEqual([p.name for p in self.code.parent.parent.parent.iterdir() if p.name.startswith(".")], [])

    def test_a_copy_that_does_not_import_leaves_the_old_one(self) -> None:
        (self.repo / "pipeline" / "broken.py").write_text("from pipeline.missing import X\n", encoding="utf-8")
        _git(self.repo, "add", "pipeline/broken.py")
        _git(self.repo, "commit", "-q", "-m", "broken")
        with self.assertRaises(SystemExit) as ctx:
            self.sync.sync(self.repo, "pipeline/models/seed123", False)
        self.assertIn("nothing was replaced", str(ctx.exception))
        self.assertTrue((self.code / "old_module.py").exists())
        self.assertTrue((self.models / "labeler-0.9.0").exists())
        self.assertFalse((self.models / "labeler-9.9.9").exists())

    def test_untracked_model_file_is_refused(self) -> None:
        _git(self.repo, "rm", "-q", "--cached", "pipeline/models/seed123/flag_model.joblib")
        _git(self.repo, "commit", "-q", "-m", "untrack flag")
        with self.assertRaises(SystemExit) as ctx:
            self.sync.sync(self.repo, "pipeline/models/seed123", False)
        self.assertIn("not tracked", str(ctx.exception))
        self.assertTrue((self.code / "old_module.py").exists())


if __name__ == "__main__":
    unittest.main()
