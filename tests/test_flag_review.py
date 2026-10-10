"""The batch flag review: which cells are flagged, and the file the Flag Review window reads."""
from __future__ import annotations

import csv
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from swcstudio.core.auto_typing import BatchOptions, apply_flag_budget, write_flag_review
from swcstudio.tools.batch_processing.features.auto_typing import run_folder

FIX = Path(__file__).resolve().parent / "fixtures"


def _rec(name: str, cell_type: str, suspicion: float | None, *, threshold_flag: bool = False,
         mismatch: bool = False, labeled: bool = True) -> dict:
    return {"file": name, "labeled": labeled, "cell_type": cell_type, "suspicion": suspicion,
            "predicted_f1": None if suspicion is None else 1 - suspicion,
            "flagged_by_threshold": threshold_flag, "class_mismatch": mismatch}


class FlagBudgetTests(unittest.TestCase):
    def test_each_class_flags_its_riskiest_share(self) -> None:
        recs = [_rec(f"p{i}", "pyramidal", i / 100) for i in range(20)]
        recs += [_rec(f"i{i}", "interneuron", i / 100) for i in range(30)]
        apply_flag_budget(recs, 0.10)
        flagged = {r["file"] for r in recs if r["flagged"]}
        self.assertEqual(flagged, {"p19", "p18", "i29", "i28", "i27"})
        top = next(r for r in recs if r["file"] == "p19")
        self.assertEqual((top["rank_in_class"], top["class_size"], top["flag_reason"]),
                         (1, 20, "riskiest_share"))

    def test_small_class_falls_back_to_the_fixed_cutoff(self) -> None:
        recs = [_rec("a", "pyramidal", 0.5, threshold_flag=True), _rec("b", "pyramidal", 0.4),
                _rec("c", "pyramidal", 0.01)]
        apply_flag_budget(recs, 0.10)
        self.assertEqual([r["flagged"] for r in recs], [True, False, False])
        self.assertEqual(recs[0]["flag_reason"], "below_cutoff")

    def test_class_mismatch_is_always_flagged_and_unlabeled_never(self) -> None:
        recs = [_rec("m", "interneuron", None, mismatch=True), _rec("q", "", None, labeled=False)]
        recs += [_rec(f"i{i}", "interneuron", i / 100) for i in range(10)]
        apply_flag_budget(recs, 0.10)
        self.assertTrue(recs[0]["flagged"])
        self.assertEqual(recs[0]["flag_reason"], "class_mismatch")
        self.assertFalse(recs[1]["flagged"])
        self.assertEqual(sum(r["flagged"] for r in recs[2:]), 1)

    def test_review_file_round_trip(self) -> None:
        recs = [_rec("a", "pyramidal", 0.3), _rec("b", "pyramidal", 0.1)]
        apply_flag_budget(recs, 0.5)
        with tempfile.TemporaryDirectory() as tmp:
            path = write_flag_review(Path(tmp) / "x_flag_review_1.json", recs, budget=0.5,
                                     folder=tmp, timestamp="1")
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["kind"], "swcstudio_flag_review")
            self.assertEqual(payload["flag_budget"], 0.5)
            self.assertEqual([r["file"] for r in payload["records"]], ["a", "b"])
            with path.with_suffix(".csv").open(encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))
            self.assertEqual(rows[0]["file"], "a")


class BatchFlagReviewTests(unittest.TestCase):
    def test_batch_labels_in_place_and_writes_the_review(self) -> None:
        with tempfile.TemporaryDirectory(prefix="swcstudio-batch-") as tmp:
            folder = Path(tmp) / "cells"
            folder.mkdir()
            for name in ("synthetic_three_class.swc", "synthetic_two_class.swc", "synthetic_unlabeled.swc"):
                shutil.copy2(FIX / "labeler" / name, folder / name)
            res = run_folder(str(folder), options=BatchOptions(flag_budget=0.10))
            self.assertEqual(res.files_processed, 3)
            self.assertIsNotNone(res.flag_review_path)
            payload = json.loads(Path(res.flag_review_path).read_text(encoding="utf-8"))
            self.assertEqual(len(payload["records"]), 3)
            self.assertTrue(all(r["labeled"] for r in payload["records"]))
            self.assertEqual(res.files_flagged, sum(r["flagged"] for r in payload["records"]))
            # Labeled in place: the unlabeled file now carries labels.
            types = {line.split()[1] for line in (folder / "synthetic_unlabeled.swc").read_text().splitlines()
                     if line and not line.startswith("#")}
            self.assertNotIn("0", types)


if __name__ == "__main__":
    unittest.main()
