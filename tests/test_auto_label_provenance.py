"""Which model folder an auto-label run used, recorded truthfully and privately.

Every auto-label result carries ``model_record``: the folder (home written as
``~``), where it came from, the labeler version, a SHA-256 of each model file
and one SHA-256 over the set. History records it in each operation's params,
and the ai_runs index carries ``model_sha`` and ``model_version``.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from swcstudio.cli import cli
from swcstudio.core import model_paths
from swcstudio.core.auto_typing import BatchOptions, backend_status, is_available, run_file
from swcstudio.core.model_paths import (
    MODEL_FILES,
    REQUIRED_MODELS,
    labeler_info,
    model_record,
    resolve_model_dir,
    select_model_dir,
)
from swcstudio.core.provenance import history_dir_for, iter_events, open_history_for_read
from swcstudio.tools.batch_processing.features.auto_typing import run_folder
from tests.test_cli_autolabel_history import SWC_PLUS, _fake_auto_label_file

FIX = Path(__file__).resolve().parent / "fixtures" / "labeler"
CELL = FIX / "synthetic_two_class.swc"
SHIPPED = model_paths.bundled_model_dir() / model_paths.labeler_models_dirname()
RECORD_KEYS = {"model_dir", "model_source", "model_version", "model_sha", "model_files"}


def _fake_model_folder(path: Path, *, flag: bool = True) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    for key, fname in MODEL_FILES.items():
        if key in REQUIRED_MODELS or flag:
            (path / fname).write_bytes(f"fake {fname}".encode())
    return path


def _history(swc: Path) -> tuple[list[dict], list[tuple]]:
    """The params of each operation and the ai_runs rows of ``swc``'s history."""
    with open_history_for_read(swc, history_dir_for(swc)) as hist:
        params = [op["params"] for ev in iter_events(hist / "events.jsonl") for op in ev.ops]
        with contextlib.closing(sqlite3.connect(hist / "index.sqlite")) as conn:
            runs = list(conn.execute("SELECT model_sha, model_version FROM ai_runs"))
    return params, runs


class _Isolated(unittest.TestCase):
    """No SWCSTUDIO_MODEL_DIR, no configured folder, an empty user data folder."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="swcstudio-models-prov-")
        self.tmp = Path(self._tmpdir.name)
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop(model_paths.ENV_VAR, None)
        os.environ.pop(model_paths.BUNDLED_ENV_VAR, None)
        self.user = self.tmp / "user"
        for patcher in (mock.patch.object(model_paths, "user_model_dir", return_value=self.user),
                        mock.patch.object(model_paths, "_config_model_dir", return_value=None)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _no_shipped_models(self) -> None:
        empty = self.tmp / "empty-package"
        empty.mkdir(exist_ok=True)
        patcher = mock.patch.object(model_paths, "bundled_model_dir", return_value=empty)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _config_names(self, folder: Path | None) -> None:
        model_paths._config_model_dir.return_value = str(folder) if folder else None


class ModelRecordTests(_Isolated):
    def test_record_of_the_shipped_set(self) -> None:
        rec = model_record(SHIPPED, "bundled")
        self.assertEqual(set(rec), RECORD_KEYS)
        self.assertEqual(rec["model_source"], "bundled")
        self.assertEqual(rec["model_version"], labeler_info()["version"])
        self.assertEqual(rec["model_files"], {name: meta["sha256"] for name, meta in labeler_info()["models"].items()})
        listing = "".join(f"{n} {rec['model_files'][n]}\n" for n in sorted(rec["model_files"]))
        self.assertEqual(rec["model_sha"], hashlib.sha256(listing.encode()).hexdigest())

    def test_folder_under_home_is_recorded_with_tilde(self) -> None:
        home = self.tmp / "home"
        mine = _fake_model_folder(home / "models" / "mine", flag=False)
        with mock.patch.object(Path, "home", return_value=home):
            rec = model_record(mine, "argument")
        self.assertEqual(rec["model_dir"], "~/models/mine")
        self.assertEqual(set(rec["model_files"]), {MODEL_FILES[k] for k in REQUIRED_MODELS})

    def test_unknown_source_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            model_record(SHIPPED, "somewhere")


class ResolutionOrderTests(_Isolated):
    def test_argument_then_env_then_config_then_default(self) -> None:
        arg, env, cfg = (_fake_model_folder(self.tmp / n) for n in ("arg", "env", "cfg"))
        self._config_names(cfg)
        with mock.patch.dict(os.environ, {model_paths.ENV_VAR: str(env)}):
            self.assertEqual(select_model_dir(arg), model_paths.ModelSelection(arg, "argument"))
            self.assertEqual(select_model_dir(), model_paths.ModelSelection(env, "env"))
        self.assertEqual(select_model_dir(), model_paths.ModelSelection(cfg, "config"))
        self._config_names(None)
        self.assertEqual(select_model_dir(), model_paths.ModelSelection(SHIPPED, "bundled"))
        user_copy = _fake_model_folder(self.user / model_paths.labeler_models_dirname())
        self.assertEqual(select_model_dir(), model_paths.ModelSelection(user_copy, "user"))

    def test_an_incomplete_configured_folder_is_not_replaced(self) -> None:
        cfg = self.tmp / "cfg"
        cfg.mkdir()
        (cfg / MODEL_FILES["stage1"]).write_bytes(b"x")
        self._config_names(cfg)
        self.assertIsNone(select_model_dir())
        ok, reason = is_available()
        self.assertFalse(ok)
        self.assertIn("auto-typing configuration", reason)
        self.assertIn("INCOMPLETE", reason)

    def _real_user_dir_under(self, root: Path):
        """The module's own ``user_model_dir``, with the user data root at ``root``."""
        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch.object(model_paths, "user_model_dir", _REAL_USER_MODEL_DIR))
        stack.enter_context(mock.patch.object(model_paths, "_user_data_root", return_value=root))
        return stack

    def test_search_creates_nothing(self) -> None:
        root = self.tmp / "fresh-appdata"
        with self._real_user_dir_under(root):
            self.assertEqual(resolve_model_dir(auto_download=False), SHIPPED)
            self.assertEqual(model_paths.user_model_dir(), root / "swcstudio" / "models")
        self.assertFalse(root.exists())

    def test_unusable_user_data_folder_falls_back_to_the_shipped_models(self) -> None:
        not_a_folder = self.tmp / "appdata"
        not_a_folder.write_text("a file where the user data folder should be", encoding="utf-8")
        with self._real_user_dir_under(not_a_folder):
            self.assertEqual(select_model_dir(), model_paths.ModelSelection(SHIPPED, "bundled"))
            self.assertTrue(is_available()[0])


_REAL_USER_MODEL_DIR = model_paths.user_model_dir


class ProbesDoNotDownloadTests(_Isolated):
    def test_is_available_and_status_never_fetch(self) -> None:
        self._no_shipped_models()
        with mock.patch("swcstudio.core.updater.fetch_manifest") as fetch:
            ok, _reason = is_available()
            status = backend_status()
        self.assertFalse(ok)
        self.assertFalse(status["models_ok"])
        fetch.assert_not_called()

    def test_gui_panel_construction_never_fetches(self) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        from swcstudio.gui.validation_auto_label_panel import ValidationAutoLabelPanel

        app = QApplication.instance() or QApplication([])
        self.assertIsNotNone(app)
        self._no_shipped_models()
        with mock.patch("swcstudio.core.updater.fetch_manifest") as fetch:
            panel = ValidationAutoLabelPanel()
            panel.deleteLater()
        fetch.assert_not_called()


class CliRecordsTheModelsTests(_Isolated):
    def _run(self, *args: str) -> tuple[int, dict, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli.main(list(args))
        text = out.getvalue()
        payload = json.JSONDecoder().raw_decode(text[text.find("{"):])[0] if "{" in text else {}
        return int(rc), payload, err.getvalue()

    def test_single_file_history_and_ai_run_index(self) -> None:
        swc = self.tmp / "cell.swc"
        shutil.copy2(CELL, swc)
        rc, payload, err = self._run("auto-label", str(swc))
        self.assertEqual(rc, 0, err)
        expected = model_record(SHIPPED, "bundled")
        self.assertEqual(payload["model_record"], expected)
        params, runs = _history(swc)
        self.assertEqual({k: params[-1][k] for k in RECORD_KEYS}, expected)
        self.assertEqual(runs, [(expected["model_sha"], expected["model_version"])])

    def test_configured_and_env_folders_pass_the_availability_check(self) -> None:
        self._no_shipped_models()
        swc = self.tmp / "cell.swc"
        for source in ("config", "env"):
            with self.subTest(source=source):
                swc.write_text(SWC_PLUS, encoding="utf-8")
                self._config_names(SHIPPED if source == "config" else None)
                env = {model_paths.ENV_VAR: str(SHIPPED)} if source == "env" else {}
                with mock.patch.dict(os.environ, env), \
                        mock.patch("swcstudio.core.updater.fetch_manifest", return_value=None), \
                        mock.patch("swcstudio.cli.cli.validation_auto_label_file",
                                   side_effect=_fake_auto_label_file):
                    rc, payload, err = self._run("auto-label", str(swc))
                self.assertEqual(rc, 0, err)
                self.assertEqual(payload["model_record"]["model_source"], source)
                params, _runs = _history(swc)
                self.assertEqual(params[-1]["model_source"], source)
                for path in swc.parent.glob("cell_history*"):
                    path.unlink()


class GuiBatchRecordsTheModelsTests(_Isolated):
    def test_chosen_folder_is_recorded_privately(self) -> None:
        home = self.tmp / "home"
        mine = _fake_model_folder(home / "models" / "mine")
        folder = self.tmp / "cells"
        folder.mkdir()
        (folder / "a.swc").write_text(SWC_PLUS, encoding="utf-8")
        with mock.patch.object(Path, "home", return_value=home), mock.patch(
                "swcstudio.tools.validation.features.auto_typing.auto_label_file",
                side_effect=_fake_auto_label_file):
            res = run_folder(str(folder), options=BatchOptions(cell_type="pyramidal"),
                             config_overrides={"model_dir": str(mine)})
        self.assertEqual(res.files_processed, 1, res.failures)
        params, runs = _history(folder / "a.swc")
        op = params[-1]
        self.assertEqual(op["model_dir"], "~/models/mine")
        self.assertEqual(op["model_source"], "argument")
        self.assertEqual(op["effective_config"]["model_dir"], "~/models/mine")
        self.assertNotIn(str(home), json.dumps(op))
        self.assertNotIn(home.as_posix(), json.dumps(op))
        self.assertEqual(runs, [(op["model_sha"], op["model_version"])])
        self.assertEqual(res.model_record["model_sha"], op["model_sha"])


class GuiProcessResultsCarryTheRecordTests(unittest.TestCase):
    def test_single_file_and_batch_results_from_the_worker_process(self) -> None:
        from swcstudio.gui.auto_typing_workers import _AutoLabelBatchWorker, _AutoLabelFileWorker

        with tempfile.TemporaryDirectory() as tmp:
            swc = Path(tmp) / CELL.name
            shutil.copy2(CELL, swc)
            single = _AutoLabelFileWorker(1, str(swc), BatchOptions(), None)._run_isolated()
            self.assertEqual(set(single.model_record), RECORD_KEYS)
            batch = _AutoLabelBatchWorker(1, tmp, BatchOptions(), None)._run_isolated()
            self.assertEqual(batch.model_record, single.model_record)


class FileResultCarriesTheRecordTests(_Isolated):
    def test_run_file_records_the_folder_it_used(self) -> None:
        res = run_file(str(CELL), BatchOptions(), write_output=False, write_log=False)
        self.assertEqual(res.model_record, model_record(SHIPPED, "bundled"))
        mine = self.tmp / "mine"
        shutil.copytree(SHIPPED, mine)
        res = run_file(str(CELL), BatchOptions(), write_output=False, write_log=False, model_dir=str(mine))
        self.assertEqual(res.model_record["model_source"], "argument")
        self.assertEqual(res.model_record["model_sha"], model_record(SHIPPED, "bundled")["model_sha"])


if __name__ == "__main__":
    unittest.main()
