from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from swcstudio.core.swc_io import is_swc_data_file
from swcstudio.tools.batch_processing.features.batch_validation import validate_folder
from swcstudio.tools.batch_processing.features.index_clean import run_folder as run_batch_index_clean


CELL = b"1 1 0 0 0 5 -1\n2 3 6 0 0 1 1\n3 3 12 0 0 0.8 2\n4 2 -6 0 0 0.5 1\n"
# What macOS writes as ._cell.swc on a USB drive or network share.
APPLEDOUBLE = b"\x00\x05\x16\x07\x00\x02\x00\x00Mac OS X        \x00\x02\x00\x00\x00\x09"


class BatchFolderScanTests(unittest.TestCase):
    """Folder scans skip hidden files such as macOS ._name.swc companions."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="swcstudio-scan-")
        self.folder = Path(self._tmpdir.name) / "cells"
        self.folder.mkdir()
        (self.folder / "cell.swc").write_bytes(CELL)
        (self.folder / "UPPER.SWC").write_bytes(CELL)
        (self.folder / "._cell.swc").write_bytes(APPLEDOUBLE)
        (self.folder / ".hidden.swc").write_bytes(CELL)
        (self.folder / "notes.txt").write_bytes(b"not a cell\n")
        (self.folder / "dir.swc").mkdir()

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_only_visible_swc_files_count(self) -> None:
        found = sorted(p.name for p in self.folder.iterdir() if is_swc_data_file(p))
        self.assertEqual(found, ["UPPER.SWC", "cell.swc"])

    def test_extensions_can_be_widened(self) -> None:
        self.assertTrue(is_swc_data_file(self.folder / "notes.txt", extensions=[".swc", ".TXT"]))
        self.assertFalse(is_swc_data_file(self.folder / "._cell.swc", extensions=[".swc"]))

    def test_batch_validation_ignores_appledouble_files(self) -> None:
        out = validate_folder(str(self.folder))
        self.assertEqual(sorted(row["file"] for row in out["results"]), ["UPPER.SWC", "cell.swc"])
        self.assertEqual(out["failures"], [])

    def test_batch_edit_ignores_appledouble_files(self) -> None:
        out = run_batch_index_clean(str(self.folder))
        self.assertEqual(out.get("failures", []), [])
        self.assertEqual((self.folder / "._cell.swc").read_bytes(), APPLEDOUBLE)


if __name__ == "__main__":
    unittest.main()
