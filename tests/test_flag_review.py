"""The batch flag review: which cells are flagged, and the file the Flag Review window reads."""
from __future__ import annotations

import csv
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import contextlib
import io
from types import SimpleNamespace
from unittest.mock import patch

from swcstudio.cli import cli
from swcstudio.core.auto_typing import BatchOptions, apply_flag_budget, run_batch, write_flag_review
from swcstudio.tools.batch_processing.features.auto_typing import run_folder

FIX = Path(__file__).resolve().parent / "fixtures"
SMALL_SWC = "1 1 0 0 0 1 -1\n2 3 1 0 0 0.5 1\n"


def _same_cell_result(file_path: str, **_kwargs) -> dict:
    """What labeling returns for ten copies of one cell: the same suspicion,
    below the single-file cut-off, so no file is flagged on its own."""
    flag = {"enabled": True, "flagged": False, "flagged_by_threshold": False, "predicted_f1": 0.8,
            "suspicion": 0.2, "threshold": 0.5, "reason": None}
    data = Path(file_path).read_bytes()
    return {
        "bytes": data.replace(b"2 3 1", b"2 4 1"), "nodes_total": 2, "type_changes": 1, "radius_changes": 0,
        "out_type_counts": {1: 1, 2: 1, 3: 1, 4: 1}, "cell_type": "pyramidal", "cell_type_source": "stage1",
        "flag_result": dict(flag), "labeler_version": "1.0.0",
        "result_obj": SimpleNamespace(cell_type="pyramidal", cell_type_source="stage1",
                                      stage1_cell_type="pyramidal", stage1_confidence=0.9,
                                      class_mismatch=False, notes=[], flag_result=dict(flag)),
    }


def _flagged_in_lines(per_file: list[str]) -> set[str]:
    return {line.split(":", 1)[0] for line in per_file if "flag=True" in line}


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


class PerFileLinesSayTheFinalFlagTests(unittest.TestCase):
    """A batch's per-file lines are written after the flag budget decides."""

    def _folder(self, tmp: str) -> Path:
        folder = Path(tmp) / "cells"
        folder.mkdir()
        for i in range(10):
            (folder / f"cell{i:02d}.swc").write_text(SMALL_SWC, encoding="utf-8")
        return folder

    def test_gui_batch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = self._folder(tmp)
            with patch("swcstudio.tools.validation.features.auto_typing.auto_label_file",
                       side_effect=_same_cell_result):
                res = run_folder(str(folder), options=BatchOptions(flag_budget=0.10))
        flagged = {r["file"] for r in res.flag_records if r["flagged"]}
        self.assertEqual(len(flagged), 1)
        self.assertEqual(res.files_flagged, 1)
        self.assertEqual(_flagged_in_lines(res.per_file), flagged)
        self.assertEqual(sum("flag=False" in line for line in res.per_file), 9)

    def test_cli_batch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = self._folder(tmp)
            out = io.StringIO()
            with patch("swcstudio.tools.validation.features.auto_typing.auto_label_file",
                       side_effect=_same_cell_result), contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(io.StringIO()):
                rc = cli.main(["auto-typing", str(folder)])
            self.assertEqual(rc, 0)
            text = out.getvalue()
            payload = json.JSONDecoder().raw_decode(text[text.find("{"):])[0]
        self.assertEqual(len(payload["flagged_files"]), 1)
        self.assertEqual(_flagged_in_lines(payload["per_file"]), set(payload["flagged_files"]))

    def test_core_run_batch_on_real_cells(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "cells"
            folder.mkdir()
            for i in range(10):
                shutil.copy2(FIX / "labeler" / "synthetic_two_class.swc", folder / f"cell{i:02d}.swc")
            res = run_batch(str(folder), BatchOptions(flag_budget=0.10, cell_type="3-class"))
        flagged = {r["file"] for r in res.flag_records if r["flagged"]}
        self.assertEqual(len(flagged), 1)
        self.assertEqual(_flagged_in_lines(res.per_file), flagged)


class BatchBookkeepingTests(unittest.TestCase):
    def test_core_run_batch_counts_like_the_gui_and_cli(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "cells"
            folder.mkdir()
            # Stage 1 calls this cell 3-class, and it is labeled without an axon.
            shutil.copy2(FIX / "labeler" / "synthetic_two_class.swc", folder / "a_cell.swc")
            (folder / "b_tiny.swc").write_text(SMALL_SWC, encoding="utf-8")
            res = run_batch(str(folder), BatchOptions())
            report = Path(res.log_path).read_text(encoding="utf-8")
        self.assertEqual((res.files_total, res.files_processed), (2, 1))
        self.assertEqual((res.files_failed, res.files_qc_failed), (1, 1))
        self.assertEqual(res.files_with_warnings, 1)
        self.assertEqual(len(res.failures), 1)
        self.assertTrue(res.failures[0].startswith("b_tiny.swc: QC rejected; "), res.failures)
        self.assertNotIn("b_tiny.swc: b_tiny.swc", res.failures[0])
        self.assertTrue(report.startswith("Auto-labeling batch report"))
        self.assertNotIn("Rule-Based", report)
        self.assertIn("1 failed the structural checks", report)

    def test_gui_batch_uses_the_failed_file_pairs(self) -> None:
        # A name that holds ": " cannot be recovered by splitting "name: error".
        tracked = {"folder": "", "files_total": 1, "files_processed": 0, "files_failed": 1, "per_file": [],
                   "failures": ["odd: name.swc: QC rejected; no soma node"],
                   "failed_files": [("odd: name.swc", "QC rejected; no soma node")], "commits": []}
        with tempfile.TemporaryDirectory() as tmp:
            tracked["folder"] = tmp
            with patch("swcstudio.tools.batch_processing.features.auto_typing.run_tracked_batch",
                       return_value=tracked):
                res = run_folder(tmp, options=BatchOptions())
        self.assertEqual([r["file"] for r in res.flag_records], ["odd: name.swc"])
        self.assertEqual(res.flag_records[0]["qc_reasons"], ["no soma node"])
        self.assertEqual(res.files_qc_failed, 1)


if __name__ == "__main__":
    unittest.main()
