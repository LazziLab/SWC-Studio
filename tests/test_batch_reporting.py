from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from swcstudio.core.auto_typing.runner import flag_record, label_warnings, write_flag_review
from swcstudio.core.provenance import OpKind, run_tracked_batch


def _labeled(cell_type: str) -> SimpleNamespace:
    return SimpleNamespace(
        cell_type=cell_type, cell_type_source="auto", stage1_cell_type=cell_type,
        stage1_confidence=0.9, class_mismatch=False, flag_result={}, notes=[],
    )


class LabelWarningTests(unittest.TestCase):
    """Batch results say when a labeled cell lacks a compartment of its class."""

    def test_three_class_cell_without_axon(self) -> None:
        self.assertEqual(label_warnings("pyramidal", {1: 1, 2: 0, 3: 40, 4: 30}), ["no axon labeled"])

    def test_three_class_cell_without_apical(self) -> None:
        self.assertEqual(label_warnings("pyramidal", {1: 1, 2: 9, 3: 40, 4: 0}), ["no apical dendrite labeled"])

    def test_two_class_cell_needs_axon_and_dendrite_only(self) -> None:
        self.assertEqual(label_warnings("interneuron", {1: 1, 2: 9, 3: 40}), [])
        self.assertEqual(label_warnings("interneuron", {1: 1, 2: 0, 3: 0}),
                         ["no axon labeled", "no dendrite labeled"])

    def test_record_carries_warnings_and_csv_lists_them(self) -> None:
        rec = flag_record(Path("cell.swc"), Path("cell.swc"), _labeled("pyramidal"), 71, 5,
                          out_type_counts={1: 1, 2: 0, 3: 40, 4: 30})
        self.assertEqual(rec["warnings"], ["no axon labeled"])
        unlabeled = flag_record(Path("bad.swc"), None, None, 0, 0, qc_reasons=["no soma"])
        self.assertEqual(unlabeled["warnings"], [])

        with tempfile.TemporaryDirectory() as tmp:
            review = write_flag_review(Path(tmp) / "review.json", [rec, unlabeled], budget=0.1,
                                       folder=tmp, timestamp="t")
            with review.with_suffix(".csv").open(encoding="utf-8", newline="") as fh:
                rows = {row["file"]: row for row in csv.DictReader(fh)}
        self.assertEqual(rows["cell.swc"]["warnings"], "no axon labeled")
        self.assertEqual(rows["bad.swc"]["warnings"], "")


class BatchFailureMessageTests(unittest.TestCase):
    def test_file_name_is_not_repeated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "a.swc").write_text("1 1 0 0 0 1 -1\n", encoding="utf-8")
            (folder / "b.swc").write_text("1 1 0 0 0 1 -1\n", encoding="utf-8")

            def transform(path: Path, _text: str):
                if path.name == "a.swc":
                    raise ValueError("a.swc: QC rejected; no neurite")
                raise ValueError("boom")

            out = run_tracked_batch(folder, kind=OpKind.AUTO_LABEL, transform=transform)
        self.assertEqual(sorted(out["failures"]), ["a.swc: QC rejected; no neurite", "b.swc: boom"])


if __name__ == "__main__":
    unittest.main()
