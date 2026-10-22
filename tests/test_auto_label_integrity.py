"""Auto-labeling writes only what it says, and only into files it can label correctly.

* Node lines that labels cannot be matched to (a node id used twice, a node
  that is its own parent, a loop of parent links) are refused before labeling,
  as a structural-check (QC) rejection, and nothing is written.
* A branch the labeler folds into the soma is not written as soma.
* An in-place auto-label rewrites only the type field of relabeled node lines:
  comments, inline comments, extra columns, spacing, line endings and a byte
  order mark are kept.
"""
from __future__ import annotations

import contextlib
import io
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from swcstudio.cli import cli
from swcstudio.core.auto_typing import BatchOptions, run_file
from swcstudio.core.auto_typing.runner import structural_violations
from swcstudio.core.provenance import archive_path_for
from swcstudio.core.provenance.header import strip_prov_lines
from swcstudio.core.swc_io import swc_data_rows_from_text
from swcstudio.tools.batch_processing.features.auto_typing import run_folder
from swcstudio.tools.validation.features.auto_typing import auto_label_file

FIX = Path(__file__).resolve().parent / "fixtures" / "labeler"
CELL = FIX / "synthetic_two_class.swc"


def _cell_lines() -> list[str]:
    """The node lines of a small labeled cell that passes the structural checks."""
    return [ln for ln in CELL.read_text(encoding="utf-8").splitlines() if ln and not ln.startswith("#")]


def _leaf_ids(lines: list[str]) -> list[int]:
    parents = {int(ln.split()[6]) for ln in lines}
    return [int(ln.split()[0]) for ln in lines if int(ln.split()[0]) not in parents]


def _with_field(line: str, index: int, value: str) -> str:
    parts = line.split()
    parts[index] = value
    return " ".join(parts)


def _run_cli(*args: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = cli.main(list(args))
    return int(rc), out.getvalue(), err.getvalue()


def _json(stdout: str) -> dict:
    payload, _ = json.JSONDecoder().raw_decode(stdout[stdout.find("{"):])
    return payload


class _TmpCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="swcstudio-integrity-")
        self.tmp = Path(self._tmpdir.name)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _write(self, name: str, lines: list[str], *, data: bytes | None = None) -> Path:
        path = self.tmp / name
        path.write_bytes(data if data is not None else ("\n".join(lines) + "\n").encode("utf-8"))
        return path


class BrokenNodeLinesAreRefusedTests(_TmpCase):
    def _duplicate_id_lines(self) -> list[str]:
        """The soma renumbered to 1206 and an axon leaf given the same id."""
        lines = []
        for ln in _cell_lines():
            parts = ln.split()
            parts[0] = str(int(parts[0]) + 1205)
            if parts[6] != "-1":
                parts[6] = str(int(parts[6]) + 1205)
            lines.append(" ".join(parts))
        axon_leaf = next(i for i, ln in enumerate(lines)
                         if ln.split()[1] == "2" and int(ln.split()[0]) - 1205 in _leaf_ids(_cell_lines()))
        lines[axon_leaf] = _with_field(lines[axon_leaf], 0, "1206")
        return lines

    def _self_parent_lines(self) -> tuple[list[str], int]:
        lines = _cell_lines()
        leaf = _leaf_ids(lines)[-1]
        idx = next(i for i, ln in enumerate(lines) if int(ln.split()[0]) == leaf)
        lines[idx] = _with_field(lines[idx], 6, str(leaf))
        return lines, leaf

    def _loop_lines(self) -> list[str]:
        return _cell_lines() + ["9001 3 50.0 50.0 0.0 1.0 9002", "9002 3 51.0 50.0 0.0 1.0 9001"]

    def test_reasons_name_the_nodes(self) -> None:
        rows = swc_data_rows_from_text("\n".join(self._duplicate_id_lines()))[1]
        self.assertEqual(structural_violations(rows), ["node id used on more than one line: 1206"])
        lines, leaf = self._self_parent_lines()
        rows = swc_data_rows_from_text("\n".join(lines))[1]
        self.assertEqual(structural_violations(rows), [f"node {leaf} is its own parent"])
        rows = swc_data_rows_from_text("\n".join(self._loop_lines()))[1]
        self.assertEqual(structural_violations(rows), ["parent links form a loop through nodes 9001, 9002"])
        self.assertEqual(structural_violations(swc_data_rows_from_text("\n".join(_cell_lines()))[1]), [])

    def _assert_rejected(self, lines: list[str], fragment: str) -> None:
        path = self._write("cell.swc", lines)
        with self.assertRaises(ValueError) as ctx:
            run_file(str(path), BatchOptions(), write_output=False, write_log=False)
        self.assertIn("QC rejected", str(ctx.exception))
        self.assertIn(fragment, str(ctx.exception))

    def test_duplicate_node_id_is_rejected(self) -> None:
        self._assert_rejected(self._duplicate_id_lines(), "node id used on more than one line: 1206")

    def test_node_that_is_its_own_parent_is_rejected(self) -> None:
        lines, leaf = self._self_parent_lines()
        self._assert_rejected(lines, f"node {leaf} is its own parent")

    def test_loop_of_parent_links_is_rejected(self) -> None:
        self._assert_rejected(self._loop_lines(), "loop through nodes 9001, 9002")

    def test_cli_writes_nothing_for_a_rejected_file(self) -> None:
        path = self._write("cell.swc", self._duplicate_id_lines())
        before = path.read_bytes()
        rc, _out, err = _run_cli("auto-label", str(path))
        self.assertEqual(rc, 2)
        self.assertIn("QC rejected", err)
        self.assertIn("1206", err)
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(archive_path_for(path).exists())

    def test_batch_counts_them_as_structural_failures(self) -> None:
        folder = self.tmp / "cells"
        folder.mkdir()
        lines, _leaf = self._self_parent_lines()
        (folder / "a_duplicate.swc").write_text("\n".join(self._duplicate_id_lines()) + "\n", encoding="utf-8")
        (folder / "b_self_parent.swc").write_text("\n".join(lines) + "\n", encoding="utf-8")
        (folder / "c_loop.swc").write_text("\n".join(self._loop_lines()) + "\n", encoding="utf-8")
        shutil.copy2(CELL, folder / "d_good.swc")
        before = {p.name: p.read_bytes() for p in folder.glob("[abc]_*.swc")}

        res = run_folder(str(folder), options=BatchOptions())
        self.assertEqual((res.files_processed, res.files_failed, res.files_qc_failed), (1, 3, 3))
        self.assertTrue(all("QC rejected" in f for f in res.failures), res.failures)
        for name, data in before.items():
            self.assertEqual((folder / name).read_bytes(), data, name)
            self.assertFalse(archive_path_for(folder / name).exists(), name)
        report = Path(res.log_path).read_text(encoding="utf-8")
        self.assertIn("3 failed the structural checks", report)
        self.assertIn("a_duplicate.swc: QC rejected; node id used on more than one line: 1206", report)


class BranchFoldedIntoSomaTests(_TmpCase):
    """A primary branch with no standard type is labeled as a neurite, never as soma.

    Labeler 1.0.0 merged such a branch into the soma; since 1.0.1 it stays a
    neurite, so the model labels it like any other branch.
    """

    def _lines_with_type0_branch(self) -> tuple[list[str], set[int]]:
        lines = _cell_lines()
        parent = {int(ln.split()[0]): int(ln.split()[6]) for ln in lines}
        # The first dendrite branch leaving the soma, with everything below it.
        first = next(int(ln.split()[0]) for ln in lines if int(ln.split()[6]) == 1 and ln.split()[1] == "3")

        def under(node: int) -> bool:
            while node != -1:
                if node == first:
                    return True
                node = parent.get(node, -1)
            return False

        branch = {nid for nid in parent if under(nid)}
        out = [_with_field(ln, 1, "0") if int(ln.split()[0]) in branch else ln for ln in lines]
        return out, branch

    def test_branch_is_labeled_as_a_neurite(self) -> None:
        lines, branch = self._lines_with_type0_branch()
        path = self._write("cell.swc", lines)
        res = run_file(str(path), BatchOptions(), write_output=False, write_log=False)
        by_id = {int(r["id"]): t for r, t in zip(res.rows, res.types)}
        self.assertLessEqual({by_id[n] for n in branch}, {2, 3, 4})
        self.assertEqual(sum(1 for t in res.types if t == 1), 1)
        self.assertEqual(res.unlabeled_nodes, 0)
        self.assertFalse(any("left unlabeled" in w for w in res.warnings), res.warnings)

    def test_batch_writes_the_branch_as_a_neurite(self) -> None:
        folder = self.tmp / "cells"
        folder.mkdir()
        lines, branch = self._lines_with_type0_branch()
        (folder / "cell.swc").write_text("\n".join(lines) + "\n", encoding="utf-8")
        res = run_folder(str(folder), options=BatchOptions())
        self.assertNotIn("left unlabeled", res.per_file[0])
        written = swc_data_rows_from_text(strip_prov_lines((folder / "cell.swc").read_bytes()).decode())[1]
        self.assertLessEqual({r["type"] for r in written if r["id"] in branch}, {2, 3, 4})
        self.assertEqual(sum(1 for r in written if r["type"] == 1), 1)


class MissingCompartmentWarningTests(_TmpCase):
    def test_warning_says_when_the_input_lacked_the_compartment_too(self) -> None:
        # Stage 1 calls this cell 3-class. Its input has an axon, which the
        # labeler missed, and no apical dendrite, which the tracing lacks.
        res = run_file(str(CELL), BatchOptions(), write_output=False, write_log=False)
        self.assertEqual(res.cell_type, "pyramidal")
        self.assertEqual(res.warnings, ["no axon labeled", "no apical dendrite labeled (none in the input either)"])
        # Unlabeled input: nothing to compare with.
        res = run_file(str(FIX / "synthetic_unlabeled.swc"), BatchOptions(cell_type="2-class"),
                       write_output=False, write_log=False)
        self.assertFalse(any("input" in w for w in res.warnings), res.warnings)


class InPlaceRewriteTests(_TmpCase):
    """The labeled file is the input with only type fields rewritten."""

    def _messy_bytes(self, *, bom: bool, crlf: bool, header: bool = True) -> bytes:
        lines = _cell_lines()
        lines[3] = lines[3] + " 0.25 extra   # inline note"
        lines[7] = lines[7].replace(" ", "\t")
        lines.insert(10, "# a comment between nodes")
        lines.insert(20, "abc 3 1.0 1.0 1.0 1.0 2")
        if header:
            lines.insert(0, "# my header")
        newline = "\r\n" if crlf else "\n"
        return (("﻿" if bom else "") + newline.join(lines) + newline).encode("utf-8")

    def _assert_only_types_changed(self, before: bytes, after: bytes) -> int:
        a_lines = before.decode("utf-8").splitlines(keepends=True)
        b_lines = after.decode("utf-8").splitlines(keepends=True)
        self.assertEqual(len(a_lines), len(b_lines))
        changed = 0
        for old, new in zip(a_lines, b_lines):
            if old == new:
                continue
            old_tok, new_tok = re.split(r"(\s+)", old), re.split(r"(\s+)", new)
            self.assertEqual(len(old_tok), len(new_tok), (old, new))
            diffs = [i for i, (x, y) in enumerate(zip(old_tok, new_tok)) if x != y]
            self.assertEqual(diffs, [2], (old, new))  # [id, space, TYPE, ...]
            changed += 1
        return changed

    def test_auto_label_file_bytes(self) -> None:
        for bom in (False, True):
            for crlf in (False, True):
                with self.subTest(bom=bom, crlf=crlf):
                    data = self._messy_bytes(bom=bom, crlf=crlf)
                    path = self._write("cell.swc", [], data=data)
                    out = auto_label_file(str(path), options=BatchOptions(), write_output=False)
                    changed = self._assert_only_types_changed(data, out["bytes"])
                    self.assertEqual(changed, out["type_changes"])
                    self.assertGreater(changed, 0)
                    self.assertEqual(out["nodes_total"], len(_cell_lines()))

    def test_byte_order_mark_before_the_soma_line(self) -> None:
        data = self._messy_bytes(bom=True, crlf=False, header=False)
        path = self._write("cell.swc", [], data=data)
        out = auto_label_file(str(path), options=BatchOptions(), write_output=False)
        self.assertTrue(out["bytes"].startswith(b"\xef\xbb\xbf1 1 "))
        self.assertEqual(out["out_type_counts"][1], 1)

    def test_cli_in_place_keeps_every_other_byte_and_can_run_again(self) -> None:
        data = self._messy_bytes(bom=True, crlf=True)
        path = self._write("cell.swc", [], data=data)
        rc, stdout, err = _run_cli("auto-label", str(path))
        self.assertEqual(rc, 0, err)
        written = path.read_bytes()
        self._assert_only_types_changed(data, strip_prov_lines(written))
        self.assertEqual(_json(stdout)["type_changes"], self._assert_only_types_changed(data, strip_prov_lines(written)))
        # A second run labels the same file again without losing a node.
        rc, stdout, err = _run_cli("auto-label", str(path))
        self.assertEqual(rc, 0, err)
        self.assertEqual(_json(stdout)["nodes_total"], len(_cell_lines()))
        self.assertEqual(_json(stdout)["type_changes"], 0)
        self._assert_only_types_changed(strip_prov_lines(written), strip_prov_lines(path.read_bytes()))

    def test_nan_radius_on_a_soma_point_is_not_a_radius_change(self) -> None:
        lines = _cell_lines()
        lines[1:1] = ["9001 1 0.0 3.0 0.0 nan 1", "9002 1 0.0 -3.0 0.0 6.0 1"]
        path = self._write("cell.swc", lines)
        res = run_file(str(path), BatchOptions(rad=False), write_output=False, write_log=False)
        self.assertEqual(res.radius_changes, 0)
        out = auto_label_file(str(path), options=BatchOptions(rad=False), write_output=False)
        self.assertEqual(out["radius_changes"], 0)
        self.assertIn(b"9001 1 0.0 3.0 0.0 nan 1", out["bytes"])

    def test_labeled_copy_is_the_source_with_types_rewritten(self) -> None:
        data = self._messy_bytes(bom=False, crlf=True)
        path = self._write("cell.swc", [], data=data)
        out_path = self.tmp / "copy.swc"
        res = run_file(str(path), BatchOptions(), output_path=str(out_path), write_log=False)
        self.assertEqual(self._assert_only_types_changed(data, out_path.read_bytes()), res.type_changes)
        self.assertEqual(path.read_bytes(), data)


if __name__ == "__main__":
    unittest.main()
