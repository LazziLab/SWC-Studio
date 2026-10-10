from __future__ import annotations

import contextlib
import io
import json
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from swcstudio.cli import cli
from swcstudio.core.auto_typing import BatchOptions
from swcstudio.core.provenance import archive_path_for, parse_prov_header
from swcstudio.tools.batch_processing.features.auto_typing import (
    run_folder as run_gui_batch_auto_typing,
)


SWC_PLUS = (
    "# SWC plus (SWC+) format specification\n"
    '# <SWCplus version="0.12">\n'
    "#   <MetaData>\n"
    '#     <FileHistory originalName="cell.swc" originalFormat="SWC">\n'
    '#       <Modification software="SWC Tools" command="type-edit" summary="Edited types"/>\n'
    "#     </FileHistory>\n"
    "#   </MetaData>\n"
    "# </SWCplus>\n"
    "1 1 0 0 0 1 -1\n"
    "2 3 1 0 0 0.5 1\n"
)


def _extract_json(stdout: str) -> dict:
    start = stdout.find("{")
    if start < 0:
        raise AssertionError(f"No JSON object found in stdout:\n{stdout}")
    payload, _ = json.JSONDecoder().raw_decode(stdout[start:])
    if not isinstance(payload, dict):
        raise AssertionError(f"Expected JSON object, got {type(payload)!r}")
    return payload


def _fake_auto_label_file(file_path: str, **_kwargs) -> dict:
    path = Path(file_path)
    if path.name.startswith("bad"):
        raise ValueError(f"{path.name}: QC rejected; bad test input")
    data = path.read_text(encoding="utf-8")
    out = data.replace("2 3 1 0 0 0.5 1", "2 4 1 0 0 0.5 1").encode("utf-8")
    return {
        "dataframe": None,
        "bytes": out,
        "input_path": str(path),
        "output_path": None,
        "nodes_total": 2,
        "type_changes": 1,
        "radius_changes": 0,
        "out_type_counts": {1: 1, 2: 0, 3: 0, 4: 1},
        "cell_type": "pyramidal",
        "cell_type_source": "user",
        "stage1_confidence": None,
        "qc_result": {"passed": True, "reasons": []},
        "flag_result": {"flagged": False, "predicted_f1": 0.9, "suspicion": 0.1},
        "change_details": [],
        "result_obj": SimpleNamespace(
            cell_type="pyramidal", cell_type_source="user", stage1_cell_type="pyramidal",
            stage1_confidence=0.9, class_mismatch=False, notes=[],
            flag_result={"flagged": False, "predicted_f1": 0.9, "suspicion": 0.1,
                         "flagged_by_threshold": False, "threshold": 0.2},
        ),
    }


class CLIAutoLabelHistoryTests(unittest.TestCase):
    def _run_cli(self, *args: str) -> tuple[int, str]:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cli.main(list(args))
        return int(rc), buf.getvalue()

    def test_validation_auto_label_commits_to_source_without_text_report(self) -> None:
        with tempfile.TemporaryDirectory(prefix="swcstudio-cli-autolabel-") as tmp:
            swc = Path(tmp) / "cell.swc"
            swc.write_text(SWC_PLUS, encoding="utf-8")

            with patch("swcstudio.core.auto_typing.is_available", return_value=(True, "ok")), patch(
                "swcstudio.cli.cli.validation_auto_label_file",
                side_effect=_fake_auto_label_file,
            ):
                rc, stdout = self._run_cli(
                    "validation",
                    "auto-label",
                    str(swc),
                    "--cell-type",
                    "pyramidal",
                )

            self.assertEqual(rc, 0, stdout)
            payload = _extract_json(stdout)
            self.assertEqual(payload["output_path"], str(swc))
            self.assertIsNone(payload["operation_log_path"])
            self.assertTrue(str(payload["commit_sha"]).startswith("sha256:"))
            self.assertTrue(archive_path_for(swc).exists())
            self.assertFalse((swc.parent / "cell_swc_studio_output").exists())
            self.assertFalse(list(swc.parent.glob("*validation_auto_label*.txt")))

            text = swc.read_text(encoding="utf-8")
            self.assertIn("# @PROV root=", text)
            self.assertIn("# @PROV tip=", text)
            self.assertIn('# <SWCplus version="0.12">', text)
            self.assertIn("2 4 1 0 0 0.5 1", text)
            header = parse_prov_header(swc.read_bytes())
            self.assertEqual(header.tip["sidecar"], archive_path_for(swc).name)

            rc, log_stdout = self._run_cli("history", "log", str(swc))
            self.assertEqual(rc, 0, log_stdout)
            self.assertIn("op-", log_stdout)
            self.assertIn("Auto Label", log_stdout)
            self.assertNotIn("sha", log_stdout.splitlines()[0].lower())
            self.assertNotIn(str(payload["commit_sha"]).removeprefix("sha256:")[:12], log_stdout)

            match = re.search(r"\bop-\d+\b", log_stdout)
            self.assertIsNotNone(match, log_stdout)
            rc, show_stdout = self._run_cli("history", "show", str(swc), match.group(0))
            self.assertEqual(rc, 0, show_stdout)
            self.assertIn("Operation", show_stdout)
            self.assertIn("Action: Auto Label", show_stdout)
            self.assertIn("Cell Class: pyramidal", show_stdout)
            self.assertIn("Flagging Enabled: Yes", show_stdout)
            self.assertNotIn("model_dir", show_stdout)
            self.assertNotIn("nodes_total", show_stdout)
            self.assertNotIn("target_sha", show_stdout)
            self.assertNotIn(str(payload["commit_sha"]).removeprefix("sha256:")[:12], show_stdout)

            rc, checkpoint_stdout = self._run_cli(
                "history",
                "checkpoint",
                str(swc),
                match.group(0),
                "--label",
                "review",
            )
            self.assertEqual(rc, 0, checkpoint_stdout)
            self.assertTrue((swc.parent / "cell_review.swc").exists())

    def test_batch_auto_typing_commits_successes_and_skips_qc_failures(self) -> None:
        with tempfile.TemporaryDirectory(prefix="swcstudio-cli-batch-autolabel-") as tmp:
            folder = Path(tmp)
            good = folder / "good.swc"
            bad = folder / "bad.swc"
            good.write_text(SWC_PLUS, encoding="utf-8")
            bad.write_text(SWC_PLUS, encoding="utf-8")

            # The CLI runs the GUI's batch, which labels through this function.
            with patch("swcstudio.core.auto_typing.is_available", return_value=(True, "ok")), patch(
                "swcstudio.tools.validation.features.auto_typing.auto_label_file",
                side_effect=_fake_auto_label_file,
            ):
                rc, stdout = self._run_cli(
                    "batch",
                    "auto-typing",
                    str(folder),
                    "--cell-type",
                    "pyramidal",
                )

            self.assertEqual(rc, 0, stdout)
            payload = _extract_json(stdout)
            self.assertEqual(payload["files_total"], 2)
            self.assertEqual(payload["files_processed"], 1)
            self.assertEqual(payload["files_failed"], 1)
            self.assertEqual(payload["files_qc_failed"], 1)
            self.assertIsNone(payload["out_dir"])
            self.assertEqual(len(payload["commits"]), 1)
            # The file name is said once, not twice.
            self.assertEqual(payload["failures"], ["bad.swc: QC rejected; bad test input"])
            self.assertEqual(payload["commits"][0]["output_path"], str(good))
            self.assertEqual(set(payload["model_record"]),
                             {"model_dir", "model_source", "model_version", "model_sha", "model_files"})

            self.assertTrue(archive_path_for(good).exists())
            self.assertFalse(archive_path_for(bad).exists())
            self.assertIn("2 4 1 0 0 0.5 1", good.read_text(encoding="utf-8"))
            self.assertNotIn("# @PROV", bad.read_text(encoding="utf-8"))
            self.assertFalse((folder / f"{folder.name}_batch_auto_typing").exists())
            # Like the GUI batch, one text report inside the folder.
            report = Path(payload["log_path"])
            self.assertEqual(report.parent, folder)
            self.assertEqual([p.name for p in folder.glob("*batch_auto_typing*")], [report.name])
            report_text = report.read_text(encoding="utf-8")
            self.assertIn("1 failed the structural checks", report_text)
            self.assertIn("bad.swc: QC rejected; bad test input", report_text)
            review = Path(payload["flag_review_path"])
            self.assertTrue(review.is_file())
            records = json.loads(review.read_text(encoding="utf-8"))["records"]
            self.assertEqual({r["file"]: r["labeled"] for r in records}, {"good.swc": True, "bad.swc": False})

    def test_gui_batch_backend_uses_independent_file_histories(self) -> None:
        with tempfile.TemporaryDirectory(prefix="swcstudio-gui-batch-autolabel-") as tmp:
            folder = Path(tmp)
            first = folder / "first.swc"
            second = folder / "second.swc"
            first.write_text(SWC_PLUS, encoding="utf-8")
            second.write_text(SWC_PLUS, encoding="utf-8")

            with patch(
                "swcstudio.tools.validation.features.auto_typing.auto_label_file",
                side_effect=_fake_auto_label_file,
            ):
                result1 = run_gui_batch_auto_typing(
                    str(folder),
                    options=BatchOptions(cell_type="pyramidal"),
                )
                result2 = run_gui_batch_auto_typing(
                    str(folder),
                    options=BatchOptions(cell_type="pyramidal"),
                )

            self.assertIsNone(result1.out_dir)
            # Every batch writes one report beside the files it edited in place.
            report = Path(result1.log_path)
            self.assertEqual(report.parent, folder)
            report_text = report.read_text(encoding="utf-8")
            self.assertIn("first.swc", report_text)
            self.assertIn("second.swc", report_text)
            self.assertEqual(
                {row["file"]: row["operation_id"] for row in result1.commits},
                {"first.swc": "op-1", "second.swc": "op-1"},
            )
            self.assertEqual(
                {row["file"]: row["operation_id"] for row in result2.commits},
                {"first.swc": "op-2", "second.swc": "op-2"},
            )
            self.assertTrue(archive_path_for(first).exists())
            self.assertTrue(archive_path_for(second).exists())


if __name__ == "__main__":
    unittest.main()
