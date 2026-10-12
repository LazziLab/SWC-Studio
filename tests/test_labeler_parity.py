"""SWC-Studio labels exactly as the released SWC auto-labeler does.

``tests/fixtures/labeler/expected.json`` holds the labeler's own results for the
test cells (written by ``scripts/make_labeler_golden.py`` from the labeler
repository). These tests run SWC-Studio's auto-typing engine on the same cells
and require the same labels, cell class and quality flag.
"""
from __future__ import annotations

import json
import tempfile
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


def _labeler_writes(path: Path, out: Path) -> bytes:
    """The labeled file the labeler's own command writes for ``path``, without
    the comment line it adds on top."""
    from swcstudio.core.auto_typing._pickle_compat import labeler_pickle_aliases
    from swcstudio.core.labeler.infer_pipeline import (
        parse_swc_for_labeling,
        run_pipeline_on_nodes,
        write_labeled_swc,
    )
    from swcstudio.core.model_paths import resolve_model_dir

    folder = resolve_model_dir(auto_download=False)
    nodes, _notes = parse_swc_for_labeling(path)
    with labeler_pickle_aliases():
        result = run_pipeline_on_nodes(nodes, str(path), folder / "cell_type_classifier.pkl",
                                       folder / "branch_classifier.pkl")
    write_labeled_swc(path, nodes, result.node_labels, out)
    return out.read_bytes().split(b"\n", 1)[1]


class LabelerWriterParityTests(unittest.TestCase):
    """SWC-Studio's labeled file is the one the labeler's own writer produces."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="swcstudio-parity-")
        self.tmp = Path(self._tmpdir.name)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_labeled_copies_equal_the_labelers_output(self) -> None:
        for name, path in CELLS.items():
            with self.subTest(cell=name):
                ours = self.tmp / f"ours_{name}"
                run_file(str(path), BatchOptions(), output_path=str(ours), write_log=False)
                self.assertEqual(ours.read_bytes(), _labeler_writes(path, self.tmp / f"theirs_{name}"))

    def test_duplicate_ids_are_refused_by_both(self) -> None:
        from swcstudio.core.labeler.infer_pipeline import parse_swc_for_labeling, write_labeled_swc

        lines = CELLS["synthetic_two_class.swc"].read_text(encoding="utf-8").splitlines()
        leaf = lines[-1].split()
        leaf[0] = "1"  # the soma's id, on an axon or dendrite line
        path = self.tmp / "dup.swc"
        path.write_text("\n".join(lines[:-1] + [" ".join(leaf)]) + "\n", encoding="utf-8")
        # The labeler's checks pass such a file (and its pipeline does not
        # finish on it), but its writer refuses to match labels to the lines.
        nodes, _notes = parse_swc_for_labeling(path)
        with self.assertRaises(ValueError):
            write_labeled_swc(path, nodes, [3] * len(nodes), self.tmp / "theirs.swc")
        with self.assertRaises(ValueError) as ctx:
            run_file(str(path), BatchOptions(), write_output=False, write_log=False)
        self.assertIn("QC rejected; node id used on more than one line: 1", str(ctx.exception))

    def test_a_branch_with_no_standard_type_matches_the_labeler(self) -> None:
        # Labeler 1.0.0 merged such a branch into the soma and its writer wrote
        # it as soma; since 1.0.1 it is labeled like any neurite, and SWC-Studio
        # writes exactly what the labeler writes, node for node.
        lines = CELLS["synthetic_three_class.swc"].read_text(encoding="utf-8").splitlines()
        nodes = [ln.split() for ln in lines if ln and not ln.startswith("#")]
        parent = {int(p[0]): int(p[6]) for p in nodes}
        first = next(int(p[0]) for p in nodes if p[6] == "1" and p[1] == "3")

        def under(n: int) -> bool:
            while n != -1:
                if n == first:
                    return True
                n = parent[n]
            return False

        branch = {n for n in parent if under(n)}
        out_lines = [" ".join([p[0], "0", *p[2:]]) if int(p[0]) in branch else " ".join(p) for p in nodes]
        path = self.tmp / "branch0.swc"
        path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
        ours = self.tmp / "ours.swc"
        res = run_file(str(path), BatchOptions(), output_path=str(ours), write_log=False)
        theirs = _labeler_writes(path, self.tmp / "theirs.swc").decode().splitlines()
        mine_lines = ours.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(mine_lines), len(theirs))
        for mine, other in zip(mine_lines, theirs):
            self.assertEqual(mine, other)
            if int(mine.split()[0]) in branch:
                self.assertIn(mine.split()[1], {"2", "3", "4"})
        self.assertEqual(res.unlabeled_nodes, 0)


if __name__ == "__main__":
    unittest.main()
