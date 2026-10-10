"""Unit tests for the Consolidate Soma repair and the multiple-soma Split."""
from __future__ import annotations

import math
import unittest

import numpy as np

from swcstudio.core.validation_engine import ValidationContext, consolidate_complex_somas_array
from swcstudio.tools.batch_processing.features.swc_splitter import split_swc_text


SWC_DTYPE = [
    ("id", np.int64),
    ("type", np.int64),
    ("x", np.float64),
    ("y", np.float64),
    ("z", np.float64),
    ("radius", np.float64),
    ("parent", np.int64),
]


def _array(rows: list[tuple]) -> np.ndarray:
    return np.array(rows, dtype=SWC_DTYPE)


def _by_id(arr: np.ndarray) -> dict[int, tuple]:
    return {
        int(r["id"]): (int(r["type"]), float(r["x"]), float(r["y"]), float(r["z"]), float(r["radius"]), int(r["parent"]))
        for r in arr
    }


def _swc(rows: list[tuple]) -> str:
    return "\n".join(" ".join(str(v) for v in row) for row in rows) + "\n"


def _parse(text: str) -> dict[int, tuple[int, int]]:
    """node id -> (type, parent) for the data lines of an SWC text."""
    out = {}
    for line in text.splitlines():
        parts = line.split()
        if not parts or parts[0].startswith("#"):
            continue
        out[int(parts[0])] = (int(parts[1]), int(parts[6]))
    return out


# Two soma groups: a three-node soma 1-2-3 with neurites hanging off nodes 2
# and 3, and a separate two-node soma 10-11.
TWO_GROUPS = [
    (1, 1, 0.0, 0.0, 0.0, 2.0, -1),
    (2, 1, 3.0, 0.0, 0.0, 1.0, 1),
    (3, 1, 0.0, 4.0, 0.0, 1.5, 1),
    (4, 3, 10.0, 0.0, 0.0, 1.0, 2),
    (5, 2, 0.0, 10.0, 0.0, 0.5, 3),
    (6, 3, 20.0, 0.0, 0.0, 1.0, 4),
    (10, 1, 100.0, 0.0, 0.0, 1.0, -1),
    (11, 1, 102.0, 0.0, 0.0, 1.0, 10),
    (12, 3, 110.0, 0.0, 0.0, 1.0, 11),
]


class ConsolidateSomaTests(unittest.TestCase):
    def test_each_connected_soma_group_becomes_one_node(self) -> None:
        result = consolidate_complex_somas_array(_array(TWO_GROUPS))
        out = _by_id(result["array"])
        soma_ids = sorted(k for k, v in out.items() if v[0] == 1)
        self.assertEqual(soma_ids, [1, 10])
        self.assertEqual(result["soma_count_before"], 5)
        self.assertEqual(result["soma_count_after"], 2)
        self.assertEqual(result["group_count"], 2)
        self.assertTrue(result["changed"])
        self.assertEqual(sorted(out), [1, 4, 5, 6, 10, 12])

    def test_soma_node_sits_at_group_centroid(self) -> None:
        out = _by_id(consolidate_complex_somas_array(_array(TWO_GROUPS))["array"])
        _, x, y, z, _, parent = out[1]
        self.assertAlmostEqual(x, 1.0)
        self.assertAlmostEqual(y, 4.0 / 3.0)
        self.assertAlmostEqual(z, 0.0)
        self.assertEqual(parent, -1)
        self.assertEqual(out[10][1:4], (101.0, 0.0, 0.0))

    def test_radius_is_the_sphere_of_equal_surface(self) -> None:
        # Each soma link is a frustum; the merged soma is the sphere whose
        # surface equals their summed side area.
        out = _by_id(consolidate_complex_somas_array(_array(TWO_GROUPS))["array"])

        def side_area(length, ra, rb):
            return math.pi * (ra + rb) * math.sqrt(length**2 + (ra - rb) ** 2)

        area = side_area(3.0, 1.0, 2.0) + side_area(4.0, 1.5, 2.0)  # links 2-1 and 3-1
        self.assertAlmostEqual(out[1][4], math.sqrt(area / (4 * math.pi)))
        # Second group: one 2 um link between two nodes of radius 1.
        self.assertAlmostEqual(out[10][4], 1.0)

    def test_three_point_soma_keeps_its_radius(self) -> None:
        # The NeuroMorpho convention: a centre and two points at +-r along y,
        # each of radius r. Merging must give back r, not 2r.
        r = 6.817
        rows = [
            (1, 1, 10.0, 20.0, 5.0, r, -1),
            (2, 1, 10.0, 20.0 + r, 5.0, r, 1),
            (3, 1, 10.0, 20.0 - r, 5.0, r, 1),
            (4, 3, 10.0, 40.0, 5.0, 1.0, 1),
        ]
        result = consolidate_complex_somas_array(_array(rows))
        out = _by_id(result["array"])
        self.assertAlmostEqual(out[1][4], r)
        self.assertEqual(out[1][1:4], (10.0, 20.0, 5.0))
        self.assertAlmostEqual(result["groups"][0]["radius"], r)
        # The validation working copy uses the same rule.
        ctx = ValidationContext(_swc(rows))
        soma = [row for row in ctx.arr if int(row["type"]) == 1]
        self.assertEqual(len(soma), 1)
        self.assertAlmostEqual(float(soma[0]["radius"]), r)

    def test_radius_matches_the_auto_labeler_soma_merge(self) -> None:
        from swcstudio.core.labeler.features import SWCNode
        from swcstudio.core.labeler.swc_normalize import consolidate_multi_point_soma

        # A five-point contour soma with uneven radii, plus a neurite.
        contour = [
            (1, 1, 0.0, 0.0, 0.0, 1.0, -1),
            (2, 1, 3.0, 1.0, 0.0, 0.5, 1),
            (3, 1, 4.0, 4.0, 1.0, 0.8, 2),
            (4, 1, 1.0, 5.0, 0.0, 1.2, 3),
            (5, 1, -2.0, 3.0, 0.5, 0.4, 4),
            (6, 3, 10.0, 0.0, 0.0, 1.0, 3),
        ]
        ours = _by_id(consolidate_complex_somas_array(_array(contour))["array"])
        nodes, _ = consolidate_multi_point_soma([SWCNode(*row) for row in contour])
        theirs = {nd.id: nd for nd in nodes}
        self.assertEqual(sorted(ours), sorted(theirs))
        self.assertAlmostEqual(ours[1][4], theirs[1].radius)
        self.assertAlmostEqual(ours[1][1], theirs[1].x)
        self.assertEqual(ours[6][5], theirs[6].parent)

    def test_duplicate_and_looping_soma_ids_do_not_break_consolidation(self) -> None:
        rows = [
            (1, 1, 0.0, 0.0, 0.0, 2.0, 2),  # soma 1 and 2 are each other's parent
            (2, 1, 2.0, 0.0, 0.0, 2.0, 1),
            (3, 3, 8.0, 0.0, 0.0, 1.0, 2),
            (3, 3, 9.0, 0.0, 0.0, 1.0, 2),  # ID 3 twice
        ]
        result = consolidate_complex_somas_array(_array(rows))
        out = result["array"]
        self.assertEqual(int(np.sum(out["type"] == 1)), 1)
        self.assertEqual(len(out), 3)
        soma = out[out["type"] == 1][0]
        self.assertEqual(int(soma["parent"]), -1)

    def test_children_are_rewired_to_the_surviving_soma(self) -> None:
        out = _by_id(consolidate_complex_somas_array(_array(TWO_GROUPS))["array"])
        self.assertEqual(out[4][5], 1)   # was child of removed soma node 2
        self.assertEqual(out[5][5], 1)   # was child of removed soma node 3
        self.assertEqual(out[6][5], 4)   # untouched
        self.assertEqual(out[12][5], 10)  # was child of removed soma node 11

    def test_non_soma_nodes_keep_ids_and_values(self) -> None:
        before = _by_id(_array(TWO_GROUPS))
        after = _by_id(consolidate_complex_somas_array(_array(TWO_GROUPS))["array"])
        for node_id in (4, 5, 6, 12):
            self.assertEqual(after[node_id][:5], before[node_id][:5])

    def test_root_soma_is_the_survivor_even_when_listed_later(self) -> None:
        rows = [
            (2, 1, 2.0, 0.0, 0.0, 1.0, 5),
            (5, 1, 0.0, 0.0, 0.0, 1.0, -1),
            (6, 3, 8.0, 0.0, 0.0, 1.0, 2),
        ]
        result = consolidate_complex_somas_array(_array(rows))
        out = _by_id(result["array"])
        self.assertEqual(sorted(out), [5, 6])
        self.assertEqual(out[5][0], 1)
        self.assertEqual(out[5][1:4], (1.0, 0.0, 0.0))
        self.assertEqual(out[6][5], 5)
        self.assertEqual(result["anchor_map"], {2: 5, 5: 5})

    def test_single_node_soma_is_left_alone(self) -> None:
        rows = [
            (1, 1, 0.0, 0.0, 0.0, 5.0, -1),
            (2, 3, 6.0, 0.0, 0.0, 1.0, 1),
        ]
        result = consolidate_complex_somas_array(_array(rows))
        self.assertFalse(result["changed"])
        self.assertEqual(result["complex_groups"], [])
        self.assertEqual(_by_id(result["array"]), _by_id(_array(rows)))

    def test_file_without_soma_is_left_alone(self) -> None:
        rows = [(1, 3, 0.0, 0.0, 0.0, 1.0, -1), (2, 3, 6.0, 0.0, 0.0, 1.0, 1)]
        result = consolidate_complex_somas_array(_array(rows))
        self.assertFalse(result["changed"])
        self.assertEqual(_by_id(result["array"]), _by_id(_array(rows)))

    def test_validation_working_copy_uses_consolidated_soma(self) -> None:
        ctx = ValidationContext(_swc(TWO_GROUPS))
        self.assertEqual(sorted(int(v) for v in ctx.ids), [1, 4, 5, 6, 10, 12])
        self.assertEqual(len(ctx.soma_consolidation["complex_groups"]), 2)


# Soma roots 1 and 4; non-soma roots 7 (near soma 4) and 9 (near soma 1).
MULTI_CELL = [
    (1, 1, 0, 0, 0, 5, -1),
    (2, 3, 6, 0, 0, 1, 1),
    (3, 3, 12, 0, 0, 1, 2),
    (4, 1, 100, 0, 0, 5, -1),
    (5, 3, 106, 0, 0, 1, 4),
    (6, 3, 112, 0, 0, 1, 5),
    (7, 3, 90, 0, 0, 1, -1),
    (8, 3, 85, 0, 0, 1, 7),
    (9, 2, -5, 3, 0, 1, -1),
    (10, 2, -10, 3, 0, 1, 9),
]


class SplitTests(unittest.TestCase):
    def test_one_tree_per_soma_root(self) -> None:
        trees = split_swc_text(_swc(MULTI_CELL))
        self.assertEqual([root for root, _, _ in trees], [1, 4])

    def test_non_soma_roots_join_nearest_soma_cell(self) -> None:
        trees = {root: (_parse(text), count) for root, text, count in split_swc_text(_swc(MULTI_CELL))}
        self.assertEqual(sorted(trees[1][0]), [1, 2, 3, 9, 10])
        self.assertEqual(sorted(trees[4][0]), [4, 5, 6, 7, 8])
        self.assertEqual(trees[1][1], 5)
        self.assertEqual(trees[4][1], 5)

    def test_split_keeps_every_node_once_with_original_parents(self) -> None:
        source = {row[0]: (row[1], row[6]) for row in MULTI_CELL}
        seen: dict[int, tuple[int, int]] = {}
        for _, text, _ in split_swc_text(_swc(MULTI_CELL)):
            for node_id, value in _parse(text).items():
                self.assertNotIn(node_id, seen, f"node {node_id} written to two trees")
                seen[node_id] = value
        self.assertEqual(seen, source)

    def test_each_tree_has_exactly_one_soma_root(self) -> None:
        for root, text, _ in split_swc_text(_swc(MULTI_CELL)):
            nodes = _parse(text)
            soma_roots = [k for k, (t, p) in nodes.items() if t == 1 and p == -1]
            self.assertEqual(soma_roots, [root])

    def test_single_cell_file_gives_one_tree(self) -> None:
        trees = split_swc_text(_swc(MULTI_CELL[:3]))
        self.assertEqual(len(trees), 1)
        self.assertEqual(sorted(_parse(trees[0][1])), [1, 2, 3])

    def test_file_without_soma_root_is_not_split(self) -> None:
        rows = [(1, 3, 0, 0, 0, 1, -1), (2, 3, 6, 0, 0, 1, 1)]
        self.assertEqual(split_swc_text(_swc(rows)), [])


if __name__ == "__main__":
    unittest.main()
