"""SWC-Studio labels exactly as the released SWC auto-labeler does.

``tests/fixtures/labeler/expected.json`` holds the labeler's own results for the
test cells (written by ``scripts/make_labeler_golden.py`` from the labeler
repository). These tests run SWC-Studio's auto-typing engine on the same cells
and require the same labels, cell class and quality flag.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from swcstudio.core.auto_typing import BatchOptions, run_file
from swcstudio.core.model_paths import labeler_info

FIX = Path(__file__).resolve().parent / "fixtures"
EXPECTED = json.loads((FIX / "labeler" / "expected.json").read_text(encoding="utf-8"))
CELLS = {name: (FIX / "labeler" / name if (FIX / "labeler" / name).is_file() else FIX / name)
         for name in EXPECTED["cells"]}


def _run(path: Path, cell_class: str):
    return run_file(str(path), BatchOptions(cell_type=cell_class), write_output=False, write_log=False)


def _labels(result) -> str:
    return "".join(str(int(t)) for t in result.types)


class LabelerParityTests(unittest.TestCase):
    def test_automatic_class_matches_the_labeler(self) -> None:
        for name, path in CELLS.items():
            exp = EXPECTED["cells"][name]
            with self.subTest(cell=name):
                res = _run(path, "auto")
                self.assertEqual(_labels(res), exp["labels"])
                self.assertEqual(res.cell_type, exp["cell_type"])
                self.assertEqual(res.cell_type_source, "stage1")
                self.assertAlmostEqual(res.stage1_confidence, exp["stage1_confidence"], places=9)
                self.assertFalse(res.class_mismatch)
                self.assertEqual(res.notes, exp["notes"])
                f1 = min(max(exp["predicted_f1"], 0.0), 1.0)
                self.assertAlmostEqual(res.flag_result["predicted_f1"], f1, places=9)
                self.assertEqual(res.flag_result["flagged"], exp["flagged"])
                self.assertEqual(res.labeler_version, labeler_info()["version"])

    def test_chosen_class_matches_the_labeler_given_that_class(self) -> None:
        for name, path in CELLS.items():
            exp = EXPECTED["cells"][name]
            for chosen, internal in (("3-class", "pyramidal"), ("2-class", "interneuron")):
                with self.subTest(cell=name, cell_class=chosen):
                    res = _run(path, chosen)
                    self.assertEqual(res.cell_type, internal)
                    self.assertEqual(res.cell_type_source, "user")
                    self.assertEqual(res.stage1_cell_type, exp["cell_type"])
                    if internal == exp["cell_type"]:
                        # Stage 1 agrees: the run is the labeler's automatic one.
                        self.assertFalse(res.class_mismatch)
                        self.assertEqual(_labels(res), exp["labels"])
                        self.assertIsNotNone(res.flag_result["predicted_f1"])
                    else:
                        self.assertTrue(res.class_mismatch)
                        self.assertEqual(_labels(res), exp[f"labels_given_{internal}"])
                        self.assertTrue(res.flag_result["flagged"])
                        self.assertEqual(res.flag_result["reason"], "class_mismatch")
                        self.assertIsNone(res.flag_result["predicted_f1"])

    def test_two_class_labels_use_axon_and_dendrite_only(self) -> None:
        path = CELLS["synthetic_three_class.swc"]
        res = _run(path, "2-class")
        self.assertEqual(set(res.types) - {1, 2, 3}, set())

    def test_flag_can_be_switched_off(self) -> None:
        path = CELLS["synthetic_two_class.swc"]
        res = run_file(str(path), BatchOptions(flag_enabled=False), write_output=False, write_log=False)
        self.assertIsNone(res.flag_result)
        self.assertEqual(_labels(res), EXPECTED["cells"]["synthetic_two_class.swc"]["labels"])


if __name__ == "__main__":
    unittest.main()
