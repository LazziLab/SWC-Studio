from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import zstandard

from swcstudio.cli.cli import _recorded_model_dir
from swcstudio.cli.history_cli import _cmd_export_crate
from swcstudio.core.model_paths import search_dirs
from swcstudio.core.provenance import OpKind, tracked_op
from swcstudio.core.provenance.events import compute_event_id


BASE_SWC = b"# test\n1 1 0 0 0 1 -1\n2 3 1 0 0 0.5 1\n"
TYPE_EDIT = b"# test\n1 1 0 0 0 1 -1\n2 2 1 0 0 0.5 1\n"
RADIUS_EDIT = b"# test\n1 1 0 0 0 1 -1\n2 2 1 0 0 0.8 1\n"


class CrateExportPrivacyTests(unittest.TestCase):
    """`history export-crate` keeps OS usernames out unless --with-pii."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="swcstudio-crate-")
        self.tmp = Path(self._tmpdir.name)
        self.swc = self.tmp / "cell.swc"
        self.swc.write_bytes(BASE_SWC)
        # A username with a space: the @PROV header stores it as "Jane_Doe".
        with tracked_op(self.swc, kind=OpKind.SET_TYPE, params={}, message="type", actor="Jane Doe") as op:
            op.set_output(TYPE_EDIT)
        with tracked_op(self.swc, kind=OpKind.SET_RADIUS, params={}, message="radius", actor="jdoe2") as op:
            op.set_output(RADIUS_EDIT)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _export(self, *, with_pii: bool) -> Path:
        out = self.tmp / ("crate_pii" if with_pii else "crate")
        args = argparse.Namespace(file=str(self.swc), output=str(out), with_pii=with_pii)
        with contextlib.redirect_stdout(io.StringIO()):
            _cmd_export_crate(args)
        return out

    @staticmethod
    def _files_mentioning(root: Path, needles: list[str]) -> set[str]:
        hits = set()
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            data = path.read_bytes()
            if path.suffix == ".zst":
                data = zstandard.ZstdDecompressor().decompressobj().decompress(data)
            if any(needle.encode("utf-8") in data for needle in needles):
                hits.add(path.relative_to(root).as_posix())
        return hits

    def test_default_export_replaces_usernames_with_pseudonyms(self) -> None:
        crate = self._export(with_pii=False)

        self.assertEqual(self._files_mentioning(crate, ["Jane", "jdoe2"]), set())
        # closing(): the connection's own context manager only commits, and
        # an open handle stops the temp folder being removed on Windows.
        with contextlib.closing(sqlite3.connect(crate / "history" / "index.sqlite")) as conn:
            users = sorted(row[0] for row in conn.execute("SELECT os_user FROM commits"))
        self.assertEqual(users, ["agent-1", "agent-2"])
        header = (crate / "data" / "cell.swc").read_text(encoding="utf-8")
        self.assertIn("actor=agent-2 ", header)
        metadata = json.loads((crate / "ro-crate-metadata.json").read_text(encoding="utf-8"))
        people = sorted(node["@id"] for node in metadata["@graph"] if node.get("@type") == "Person")
        self.assertEqual(people, ["#agent-1", "#agent-2"])

    def test_scrubbed_export_still_verifies(self) -> None:
        hist = self._export(with_pii=False) / "history"

        for line in (hist / "events.jsonl").read_bytes().splitlines():
            event = json.loads(line)
            self.assertEqual(compute_event_id(event), event["id"])
        checksums = json.loads((hist / "checksums.json").read_bytes())
        self.assertIn("events.jsonl", checksums)
        for rel, digest in checksums.items():
            self.assertEqual(hashlib.sha256((hist / rel).read_bytes()).hexdigest(), digest, rel)
        self.assertEqual([p.name for p in hist.iterdir() if p.name.endswith(("-wal", "-shm"))], [])

    def test_with_pii_keeps_usernames(self) -> None:
        crate = self._export(with_pii=True)

        self.assertIn("history/events.jsonl", self._files_mentioning(crate, ["Jane Doe"]))
        self.assertIn("actor=jdoe2 ", (crate / "data" / "cell.swc").read_text(encoding="utf-8"))


class RecordedModelDirTests(unittest.TestCase):
    """A chosen model folder is stored with the home folder written as ~."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="swcstudio-home-")
        self.home = Path(self._tmpdir.name) / "home"
        (self.home / "models" / "mine").mkdir(parents=True)
        patcher = mock.patch.object(Path, "home", return_value=self.home)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_folder_under_home_is_recorded_with_tilde(self) -> None:
        self.assertEqual(_recorded_model_dir(str(self.home / "models" / "mine")), "~/models/mine")

    def test_folder_outside_home_is_recorded_as_given(self) -> None:
        outside = str(Path(self._tmpdir.name) / "shared" / "models")
        self.assertEqual(_recorded_model_dir(outside), outside)

    def test_no_folder_records_nothing(self) -> None:
        self.assertIsNone(_recorded_model_dir(None))

    def test_model_lookup_expands_tilde(self) -> None:
        with mock.patch.dict("os.environ", {"HOME": str(self.home), "USERPROFILE": str(self.home)}):
            self.assertEqual(search_dirs("~/models/mine"), [self.home / "models" / "mine"])


if __name__ == "__main__":
    unittest.main()
