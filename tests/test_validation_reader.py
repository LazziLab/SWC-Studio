"""Validation reads files whose header has a line that is not an SWC row."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from swcstudio.api import validation_run_file

# Vaa3D's APP2 tracer writes "autoset #bkg_thresh = 1" without a leading "#".
TEXT = """#name traced.tif_app2.swc
##Vaa3D-Neuron-APP2 v2.620
#bkg_thresh = -1
autoset #bkg_thresh = 1
#n type x y z radius parent
1 1 0 0 0 5 -1
2 3 0 10 0 1 1
3 3 0 20 0 1 2
4 3 5 30 0 1 3
5 3 -5 30 0 1 3
"""


class StrayHeaderLineTests(unittest.TestCase):
    def test_stray_header_line_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "traced.swc"
            path.write_text(TEXT, encoding="utf-8")
            report = validation_run_file(str(path))
        status = {r.key: r.status for r in report.results}
        self.assertEqual(status["no_duplicate_node_ids"], "pass")
        self.assertEqual(status["no_missing_parents"], "pass")
        self.assertEqual(status["has_soma"], "pass")
        self.assertGreater(len(status), 20)


if __name__ == "__main__":
    unittest.main()
