from __future__ import annotations

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
    parse_prov_header,
    tracked_op,
)

BOM = b"\xef\xbb\xbf"
BODY = b"# from Notepad\r\n1 1 0 0 0 1 -1\r\n2 3 1 0 0 0.5 1\r\n"


class ProvHeaderEncodingTests(unittest.TestCase):
    """The @PROV header keeps a BOM first and matches CRLF line endings."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="swcstudio-prov-bom-")
        self.tmp = Path(self._tmpdir.name)
        self.swc = self.tmp / "cell.swc"
        self.swc.write_bytes(BOM + BODY)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _edit(self, old: bytes, new: bytes, message: str) -> None:
        with tracked_op(self.swc, kind=OpKind.SET_TYPE, params={}, message=message) as op:
            op.set_output(self.swc.read_bytes().replace(old, new))

    def test_two_edits_keep_bom_first_and_crlf_throughout(self) -> None:
        self._edit(b"2 3 1", b"2 2 1", "first")
        self._edit(b"0.5 1\r\n", b"0.7 1\r\n", "second")

        data = self.swc.read_bytes()
        self.assertTrue(data.startswith(BOM + b"# @PROV"), data[:40])
        self.assertEqual(data.count(BOM), 1)
        self.assertNotIn(b"\n", data.replace(b"\r\n", b""))
        self.assertTrue(data.endswith(b"# from Notepad\r\n1 1 0 0 0 1 -1\r\n2 2 1 0 0 0.7 1\r\n"))

        header = parse_prov_header(data)
        self.assertIsNotNone(header.root)
        self.assertIsNotNone(header.tip)

        hist = self.tmp / "history"
        with open_history_for_read(self.swc, history_dir_for(self.swc)) as live:
            shutil.copytree(live, hist)
        self.assertEqual(_history_problems(hist, ObjectStore(hist / "objects")), [])

    def test_every_layout_has_the_same_canonical_form(self) -> None:
        from swcstudio.core.provenance import canonical_swc
        from swcstudio.core.provenance.header import strip_prov_lines

        no_header = BOM + BODY
        bom_first = BOM + b"# @PROV root=a\r\n# @PROV tip=b\r\n" + BODY
        header_first = b"# @PROV root=a\n# @PROV tip=b\n" + BOM + BODY
        self.assertEqual(canonical_swc(bom_first), canonical_swc(no_header))
        self.assertEqual(canonical_swc(header_first), canonical_swc(no_header))
        self.assertEqual(strip_prov_lines(bom_first), no_header)
        self.assertEqual(strip_prov_lines(header_first), no_header)

    def test_each_edit_starts_from_the_previous_output(self) -> None:
        from swcstudio.core.provenance import iter_events

        self._edit(b"2 3 1", b"2 2 1", "first")
        self._edit(b"0.5 1\r\n", b"0.7 1\r\n", "second")
        with open_history_for_read(self.swc, history_dir_for(self.swc)) as hist:
            events = list(iter_events(hist / "events.jsonl"))
        self.assertEqual(events[1].input_sha, events[0].output_sha)

    def test_header_written_below_the_bom_by_an_older_version_is_moved(self) -> None:
        from swcstudio.core.provenance.header import write_prov_header

        old = b"# @PROV root=a\n# @PROV tip=b\n" + BOM + BODY
        new = write_prov_header(old, root_line="# @PROV root=c", tip_line="# @PROV tip=d")
        self.assertEqual(new, BOM + b"# @PROV root=c\r\n# @PROV tip=d\r\n" + BODY)


if __name__ == "__main__":
    unittest.main()
