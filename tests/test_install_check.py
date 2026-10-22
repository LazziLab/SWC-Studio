from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from swcstudio.core import model_paths
from swcstudio.core.install_check import check_installation, format_installation_report


class OptionalFlagModelTests(unittest.TestCase):
    """A model set without the optional quality flag passes, with a note."""

    def test_model_set_without_flag_model_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for key in model_paths.REQUIRED_MODELS:
                (Path(tmp) / model_paths.MODEL_FILES[key]).write_bytes(b"model")
            qt_ok = {"ok": True, "platform": "offscreen", "error": None}
            with patch.dict(os.environ, {model_paths.ENV_VAR: tmp}), \
                    patch("swcstudio.core.install_check._check_qapplication", return_value=qt_ok):
                report = check_installation(load_models=False)
        rows = {row["name"]: row for row in report["models"]}
        self.assertTrue(all(rows[k]["ok"] for k in model_paths.REQUIRED_MODELS))
        self.assertTrue(rows["flag"]["ok"])
        self.assertIsNone(rows["flag"]["path"])
        self.assertIn("optional", rows["flag"]["note"])
        self.assertEqual(report["model_source"], "env")
        self.assertTrue(all(row["ok"] for row in report["models"]))
        text = format_installation_report(report)
        self.assertIn("[NOTE] flag_model.joblib", text)
        self.assertNotIn("[FAILED] flag_model.joblib", text)

    def test_missing_required_model_still_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / model_paths.MODEL_FILES["stage1"]).write_bytes(b"model")
            qt_ok = {"ok": True, "platform": "offscreen", "error": None}
            with patch.dict(os.environ, {model_paths.ENV_VAR: tmp}), \
                    patch("swcstudio.core.install_check._check_qapplication", return_value=qt_ok):
                report = check_installation(load_models=False)
        self.assertFalse(report["ok"])
        self.assertFalse({row["name"]: row for row in report["models"]}["stage2"]["ok"])


class InstallationCheckTests(unittest.TestCase):
    def test_full_installation_check_loads_every_production_model(self) -> None:
        report = check_installation(load_models=True)
        self.assertTrue(report["ok"], report)
        self.assertTrue(report["gui"]["ok"])
        self.assertTrue(all(row["ok"] for row in report["dependencies"]))
        self.assertTrue(all(row["ok"] for row in report["configs"]))
        self.assertTrue(all(row["loaded"] is True for row in report["models"]))

    def test_repair_command_uses_checked_python_executable(self) -> None:
        with patch(
            "swcstudio.core.install_check.check_installation",
        ):
            report = {
                "ok": False,
                "swcstudio_version": "0.2.0",
                "python": "3.12.0",
                "supported_python": ">=3.10,<3.13",
                "python_ok": True,
                "platform": "test",
                "executable": "/tmp/swc env/bin/python",
                "dependencies": [],
                "configs": [],
                "models": [],
                "gui": {"ok": True, "error": None},
            }
            text = format_installation_report(report)
        self.assertIn('"/tmp/swc env/bin/python" -m pip install', text)


if __name__ == "__main__":
    unittest.main()
