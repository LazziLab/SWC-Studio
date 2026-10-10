"""Which files the auto-labeler labels, and why it refuses the others.

Stage 0 of the labeler requires one connected cell with a soma and finite
geometry. A file without labels is labeled: its root is taken as the soma.
"""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from swcstudio.core.auto_typing import BatchOptions, run_file


def _write_swc(path: Path, rows: list[str]) -> None:
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def _unlabeled_cell(n_per_arm: int = 12, *, node_type: int = 0) -> list[str]:
    """A root with three unbranched arms, every node of type ``node_type``."""
    rows = ["1 {t} 0.0 0.0 0.0 5.0 -1".format(t=node_type)]
    nid = 1
    for arm, (dx, dy) in enumerate(((1, 0), (-1, 0.2), (0, -1))):
        parent = 1
        for k in range(1, n_per_arm + 1):
            nid += 1
            rows.append(f"{nid} {node_type} {dx * 6 * k:.1f} {dy * 6 * k:.1f} {arm * 0.5:.1f} 0.8 {parent}")
            parent = nid
    return rows


class AutoTypingQCTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="swcstudio-qc-tests-")
        self.tmp = Path(self._tmpdir.name)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _run(self, rows: list[str]):
        path = self.tmp / "sample.swc"
        _write_swc(path, rows)
        return run_file(str(path), BatchOptions(), write_output=False, write_log=False)

    def _assert_rejected(self, rows: list[str], fragment: str) -> None:
        with self.assertRaises(ValueError) as ctx:
            self._run(rows)
        self.assertIn("QC rejected", str(ctx.exception))
        self.assertIn(fragment, str(ctx.exception))

    def test_unlabeled_file_is_labeled_with_its_root_as_soma(self) -> None:
        res = self._run(_unlabeled_cell())
        self.assertTrue(res.qc_result["passed"])
        self.assertEqual(res.types[0], 1)
        self.assertTrue(all(t in (2, 3, 4) for t in res.types[1:]))
        self.assertTrue(any("taken as the soma" in note for note in res.notes))

    def test_custom_types_are_labeled_too(self) -> None:
        res = self._run(_unlabeled_cell(node_type=10))
        self.assertEqual(res.types[0], 1)
        self.assertTrue(all(t in (2, 3, 4) for t in res.types[1:]))

    def test_multiple_roots_are_rejected(self) -> None:
        rows = _unlabeled_cell()
        rows[0] = "1 1 0.0 0.0 0.0 5.0 -1"
        rows[5] = "6 0 30.0 0.0 0.0 0.8 -1"
        self._assert_rejected(rows, "not one connected cell")

    def test_orphan_parent_is_rejected(self) -> None:
        rows = _unlabeled_cell()
        rows[0] = "1 1 0.0 0.0 0.0 5.0 -1"
        rows[4] = "5 3 24.0 0.0 0.0 0.8 999"
        self._assert_rejected(rows, "not one connected cell")

    def test_too_few_nodes_are_rejected(self) -> None:
        self._assert_rejected(_unlabeled_cell(n_per_arm=2), "node count")

    def test_non_finite_coordinates_are_rejected(self) -> None:
        rows = _unlabeled_cell()
        rows[3] = "4 0 nan 0.0 0.0 0.8 3"
        self._assert_rejected(rows, "coordinate is not finite")

    def test_disconnected_soma_groups_are_refused_before_labeling(self) -> None:
        rows = _unlabeled_cell()
        rows[0] = "1 1 0.0 0.0 0.0 5.0 -1"
        rows[6] = "7 1 36.0 0.0 0.0 5.0 6"
        with self.assertRaises(ValueError) as ctx:
            self._run(rows)
        self.assertIn("multi-soma", str(ctx.exception))

    def test_structural_checks_are_fast_on_a_large_cell(self) -> None:
        path = self.tmp / "large.swc"
        _write_swc(path, _unlabeled_cell(n_per_arm=16_000))
        from swcstudio.core.labeler.infer_pipeline import parse_swc_for_labeling
        from swcstudio.core.labeler.qc import qc_violations

        started = time.perf_counter()
        nodes, _notes = parse_swc_for_labeling(path)
        problems = qc_violations(nodes)
        elapsed = time.perf_counter() - started
        self.assertEqual(problems, [])
        self.assertLess(elapsed, 10.0, f"48k-node parse and checks took {elapsed:.2f}s")


if __name__ == "__main__":
    unittest.main()
