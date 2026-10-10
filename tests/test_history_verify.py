from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from swcstudio.cli.history_cli import _history_problems
from swcstudio.core.provenance import (
    ObjectStore,
    OpKind,
    history_dir_for,
    open_history_for_read,
    tracked_op,
)


BASE_SWC = b"# test\n1 1 0 0 0 1 -1\n2 3 1 0 0 0.5 1\n"
TYPE_EDIT = b"# test\n1 1 0 0 0 1 -1\n2 2 1 0 0 0.5 1\n"
RADIUS_EDIT = b"# test\n1 1 0 0 0 1 -1\n2 2 1 0 0 0.8 1\n"


class HistoryVerifyTests(unittest.TestCase):
    """`history verify` catches edits to the log, refs, blobs and checksums."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="swcstudio-verify-")
        self.tmp = Path(self._tmpdir.name)
        swc = self.tmp / "cell.swc"
        swc.write_bytes(BASE_SWC)
        with tracked_op(swc, kind=OpKind.SET_TYPE, params={}, message="type") as op:
            op.set_output(TYPE_EDIT)
        with tracked_op(swc, kind=OpKind.SET_RADIUS, params={}, message="radius") as op:
            op.set_output(RADIUS_EDIT)
        # Work on a plain copy of the history so tampering never touches
        # the archive itself.
        self.hist = self.tmp / "history"
        with open_history_for_read(swc, history_dir_for(swc)) as live:
            shutil.copytree(live, self.hist)
        self.events = self.hist / "events.jsonl"

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _problems(self) -> list[str]:
        return _history_problems(self.hist, ObjectStore(self.hist / "objects"))

    def _rewrite_events(self, edit) -> None:
        objs = [json.loads(line) for line in self.events.read_bytes().splitlines()]
        edit(objs)
        self.events.write_bytes(b"".join(json.dumps(o).encode("utf-8") + b"\n" for o in objs))
        # Re-checksum so only the check under test can fire.
        checksums = json.loads((self.hist / "checksums.json").read_bytes())
        checksums.pop("events.jsonl", None)
        (self.hist / "checksums.json").write_bytes(json.dumps(checksums).encode("utf-8"))

    def test_untouched_history_has_no_problems(self) -> None:
        self.assertEqual(self._problems(), [])

    def test_edited_commit_content_is_detected(self) -> None:
        def edit(objs):
            objs[1]["output_sha"] = "0" * 64

        self._rewrite_events(edit)
        self.assertTrue(any("does not match its content" in p for p in self._problems()))

    def test_dropped_commit_breaks_the_parent_chain(self) -> None:
        self._rewrite_events(lambda objs: objs.pop(0))
        self.assertTrue(any("is not an earlier commit" in p for p in self._problems()))

    def test_ref_to_unknown_commit_is_detected(self) -> None:
        (self.hist / "refs" / "branches" / "main").write_text("sha256:" + "f" * 64)
        self.assertTrue(any(p.startswith("branch main points to") for p in self._problems()))

    def test_missing_reachable_blob_is_detected(self) -> None:
        diff_ref = json.loads(self.events.read_bytes().splitlines()[-1])["diff_ref"]
        ObjectStore(self.hist / "objects").remove(diff_ref.removeprefix("sha256:"))
        self.assertTrue(any("is referenced but missing" in p for p in self._problems()))

    def test_file_changed_after_archiving_is_detected(self) -> None:
        with open(self.events, "ab") as fh:
            fh.write(b"\n")
        self.assertIn("events.jsonl does not match checksums.json", self._problems())


if __name__ == "__main__":
    unittest.main()
