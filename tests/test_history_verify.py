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

    def test_rewritten_index_cache_is_not_a_problem(self) -> None:
        # Reading the history (the History Browser, for one) rewrites the
        # SQLite index, so it cannot be held to the archived checksum.
        with open(self.hist / "index.sqlite", "ab") as fh:
            fh.write(b"\0")
        self.assertEqual(self._problems(), [])

    def test_damaged_ai_run_blob_does_not_stop_the_check(self) -> None:
        swc = self.tmp / "ai.swc"
        swc.write_bytes(BASE_SWC)
        with tracked_op(swc, kind=OpKind.AUTO_LABEL, params={"model_sha": "x"}, message="ai") as op:
            op.set_output(TYPE_EDIT)
        hist = self.tmp / "ai_history"
        with open_history_for_read(swc, history_dir_for(swc)) as live:
            shutil.copytree(live, hist)
        event = json.loads((hist / "events.jsonl").read_bytes().splitlines()[-1])
        ai_ref = next(o["ai_run_ref"] for o in event["ops"] if o.get("ai_run_ref"))
        blob = ObjectStore(hist / "objects")._path_for(ai_ref.removeprefix("sha256:"))
        blob.write_bytes(b"not zstd at all")

        problems = _history_problems(hist, ObjectStore(hist / "objects"))
        self.assertIsInstance(problems, list)

    def test_numeric_parameter_keys_still_verify(self) -> None:
        swc = self.tmp / "keys.swc"
        swc.write_bytes(BASE_SWC)
        with tracked_op(swc, kind=OpKind.SET_TYPE, params={"counts": {2: 1, 10: 3}}, message="keys") as op:
            op.set_output(TYPE_EDIT)
        hist = self.tmp / "keys_history"
        with open_history_for_read(swc, history_dir_for(swc)) as live:
            shutil.copytree(live, hist)
        self.assertEqual(_history_problems(hist, ObjectStore(hist / "objects")), [])

    def test_file_changed_after_archiving_is_detected(self) -> None:
        with open(self.events, "ab") as fh:
            fh.write(b"\n")
        self.assertIn("events.jsonl does not match checksums.json", self._problems())


if __name__ == "__main__":
    unittest.main()
