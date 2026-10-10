"""Where SWC-Studio finds the labeler's model folder."""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from swcstudio.core import model_paths
from swcstudio.core.model_paths import MODEL_FILES, labeler_info, resolve_model_dir


class ModelPathTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="swcstudio-models-")
        self.tmp = Path(self._tmp.name)
        self._env = patch.dict(os.environ, {}, clear=False)
        self._env.start()
        os.environ.pop(model_paths.ENV_VAR, None)
        os.environ.pop(model_paths.BUNDLED_ENV_VAR, None)
        # An empty user data directory, so the shipped models are what is found.
        self._user = patch.object(model_paths, "user_model_dir", return_value=self.tmp / "user")
        self._user.start()
        (self.tmp / "user").mkdir()

    def tearDown(self) -> None:
        self._user.stop()
        self._env.stop()
        self._tmp.cleanup()

    def test_shipped_folder_is_complete_and_named_for_the_version(self) -> None:
        folder = resolve_model_dir(auto_download=False)
        self.assertIsNotNone(folder)
        self.assertEqual(folder.name, f"labeler-{labeler_info()['version']}")
        for key, fname in MODEL_FILES.items():
            self.assertTrue((folder / fname).is_file(), key)

    def test_shipped_models_match_the_recorded_checksums(self) -> None:
        import hashlib

        folder = resolve_model_dir(auto_download=False)
        for fname, meta in labeler_info()["models"].items():
            digest = hashlib.sha256((folder / fname).read_bytes()).hexdigest()
            self.assertEqual(digest, meta["sha256"], fname)

    def test_a_named_folder_with_its_own_models_is_used(self) -> None:
        mine = self.tmp / "mine"
        mine.mkdir()
        shipped = resolve_model_dir(auto_download=False)
        for fname in MODEL_FILES.values():
            shutil.copy2(shipped / fname, mine / fname)
        self.assertEqual(resolve_model_dir(mine, auto_download=False), mine)

    def test_an_incomplete_named_folder_is_not_replaced_by_other_models(self) -> None:
        mine = self.tmp / "mine"
        mine.mkdir()
        shutil.copy2(resolve_model_dir(auto_download=False) / MODEL_FILES["stage1"], mine / MODEL_FILES["stage1"])
        self.assertIsNone(resolve_model_dir(mine, auto_download=False))
        report = model_paths.diagnostic_search_report(mine)
        self.assertIn("INCOMPLETE", report)

    def test_user_data_folder_outranks_the_shipped_one(self) -> None:
        shipped = resolve_model_dir(auto_download=False)
        user_copy = self.tmp / "user" / shipped.name
        shutil.copytree(shipped, user_copy)
        self.assertEqual(resolve_model_dir(auto_download=False), user_copy)


if __name__ == "__main__":
    unittest.main()
