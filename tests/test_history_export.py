from __future__ import annotations

import argparse
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from swcstudio.cli.history_cli import _cmd_export_crate, _format_reproduce_yaml
from swcstudio.core.provenance import OpKind, tracked_op
from swcstudio.core.provenance.crate import export_crate


BASE_SWC = b"# test\n1 1 0 0 0 1 -1\n2 3 1 0 0 0.5 1\n"
TYPE_EDIT = b"# test\n1 1 0 0 0 1 -1\n2 2 1 0 0 0.5 1\n"


class CrateOutputFolderTests(unittest.TestCase):
    """export-crate never clears a folder that already holds files."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="swcstudio-crate-out-")
        self.tmp = Path(self._tmpdir.name)
        self.swc = self.tmp / "cell.swc"
        self.swc.write_bytes(BASE_SWC)
        (self.tmp / "notes.txt").write_text("keep me\n", encoding="utf-8")
        with tracked_op(self.swc, kind=OpKind.SET_TYPE, params={}, message="type") as op:
            op.set_output(TYPE_EDIT)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_swc_folder_is_refused_and_left_intact(self) -> None:
        before = {p.name: p.read_bytes() for p in self.tmp.iterdir() if p.is_file()}
        args = argparse.Namespace(file=str(self.swc), output=str(self.tmp), with_pii=False)
        with self.assertRaises(FileExistsError), contextlib.redirect_stdout(io.StringIO()):
            _cmd_export_crate(args)
        after = {p.name: p.read_bytes() for p in self.tmp.iterdir() if p.is_file()}
        self.assertEqual(after, before)

    def test_new_or_empty_folder_is_used(self) -> None:
        fresh = export_crate(self.swc, self.tmp / "crate")
        self.assertTrue((fresh / "ro-crate-metadata.json").is_file())
        empty = self.tmp / "empty"
        empty.mkdir()
        self.assertTrue((export_crate(self.swc, empty) / "ro-crate-metadata.json").is_file())

    def test_existing_file_is_refused(self) -> None:
        with self.assertRaises(FileExistsError):
            export_crate(self.swc, self.tmp / "notes.txt")


class ReproduceCommandTests(unittest.TestCase):
    def _yaml(self, params: dict) -> str:
        ev = SimpleNamespace(ops=[{"kind": "auto_label"}], input_sha="in", output_sha="out")
        return _format_reproduce_yaml(ev, {"params": params, "env_hash": "e"}, Path("cell.swc"))

    def test_chosen_model_folder_is_passed(self) -> None:
        text = self._yaml({"model_dir": "~/models/mine", "model_source": "argument",
                           "model_version": "1.0.0", "model_sha": "abc"})
        self.assertIn("--model-dir ~/models/mine", text)
        self.assertIn("model:     {version: 1.0.0, sha256: abc}", text)

    def test_default_models_are_not_passed(self) -> None:
        for source in ("bundled", "user"):
            text = self._yaml({"model_dir": "~/app/models", "model_source": source, "model_sha": "abc"})
            self.assertNotIn("--model-dir", text, source)

    def test_history_without_model_source_keeps_its_folder(self) -> None:
        self.assertIn("--model-dir ~/old", self._yaml({"model_dir": "~/old"}))


if __name__ == "__main__":
    unittest.main()
