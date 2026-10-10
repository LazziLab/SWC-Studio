"""Unit tests for morphology simplification (swcstudio.core.simplification).

Runs through ``simplify_swc_text``, the wrapper the CLI and GUI call.
"""
from __future__ import annotations

import unittest

from swcstudio.core.simplification import simplify_morphology_dataframe
from swcstudio.core.swc_io import parse_swc_text_preserve_tokens
from swcstudio.tools.morphology_editing.features.simplification import simplify_swc_text


# Soma 1, then a straight trunk 2..11 along x that ends in a bifurcation at 11.
# Node 6 is three times thicker than its neighbours (radius-sensitive).
# Branch A (12..16) continues straight along x to tip 16.
# Branch B (17..21) runs along +y to tip 21; node 19 sits 4 um off that line.
TREE_ROWS = [
    "1 1 0 0 0 2 -1",
    "2 3 5 0 0 1 1",
    "3 3 6 0 0 1 2",
    "4 3 7 0 0 1 3",
    "5 3 8 0 0 1 4",
    "6 3 9 0 0 3 5",
    "7 3 10 0 0 1 6",
    "8 3 11 0 0 1 7",
    "9 3 12 0 0 1 8",
    "10 3 13 0 0 1 9",
    "11 3 14 0 0 1 10",
    "12 3 15 0 0 1 11",
    "13 3 16 0 0 1 12",
    "14 3 17 0 0 1 13",
    "15 3 18 0 0 1 14",
    "16 3 19 0 0 1 15",
    "17 3 14 1 0 1 11",
    "18 3 14 2 0 1 17",
    "19 3 18 3 0 1 18",
    "20 3 14 4 0 1 19",
    "21 3 14 5 0 1 20",
]
ROOT = 1
BIFURCATION = 11
TIPS = {16, 21}
DEVIATING = 19
RADIUS_SENSITIVE = 6


def _swc(rows: list[str]) -> str:
    return "\n".join(rows) + "\n"


def _parents(rows: list[str]) -> dict[int, int]:
    out = {}
    for row in rows:
        parts = row.split()
        out[int(parts[0])] = int(parts[6])
    return out


class SimplificationTests(unittest.TestCase):
    def _simplify(self, rows: list[str], *, epsilon: float = 2.0, radius_tolerance: float = 0.5):
        return simplify_swc_text(
            _swc(rows),
            config_overrides={"thresholds": {"epsilon": epsilon, "radius_tolerance": radius_tolerance}},
        )

    def _assert_valid_tree(self, out, original_rows: list[str]) -> None:
        df = out["dataframe"]
        ids = [int(v) for v in df["id"].tolist()]
        parents = [int(v) for v in df["parent"].tolist()]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(parents.count(-1), 1, "expected a single root")
        for node_id, parent_id in zip(ids, parents):
            if parent_id != -1:
                self.assertIn(parent_id, ids, f"node {node_id} points at removed parent {parent_id}")

        # Each kept node's parent is its nearest kept ancestor in the original.
        original_parent = _parents(original_rows)
        kept = set(ids)
        for node_id, parent_id in zip(ids, parents):
            ancestor = original_parent[node_id]
            while ancestor != -1 and ancestor not in kept:
                ancestor = original_parent[ancestor]
            self.assertEqual(parent_id, ancestor, f"node {node_id}")

        # The written bytes describe the same tree.
        reparsed = parse_swc_text_preserve_tokens(out["bytes"].decode("utf-8"))
        self.assertEqual(reparsed["id"].tolist(), ids)
        self.assertEqual(reparsed["parent"].tolist(), parents)

    def test_expected_nodes_are_kept_and_removed(self) -> None:
        out = self._simplify(TREE_ROWS)
        kept = set(out["kept_node_ids"])
        self.assertEqual(kept, {ROOT, BIFURCATION, DEVIATING, RADIUS_SENSITIVE} | TIPS)
        self.assertEqual(
            sorted(out["removed_node_ids"]),
            sorted(set(range(1, 22)) - kept),
        )
        self.assertEqual(out["original_node_count"], 21)
        self.assertEqual(out["new_node_count"], len(kept))
        self._assert_valid_tree(out, TREE_ROWS)

    def test_roots_tips_and_bifurcations_are_always_kept(self) -> None:
        out = self._simplify(TREE_ROWS, epsilon=1000.0, radius_tolerance=1000.0)
        self.assertEqual(set(out["kept_node_ids"]), {ROOT, BIFURCATION} | TIPS)
        self._assert_valid_tree(out, TREE_ROWS)

    def test_collinear_interior_points_are_removed(self) -> None:
        rows = ["1 1 0 0 0 1 -1"] + [f"{k} 3 {k - 1} 0 0 1 {k - 1}" for k in range(2, 12)]
        out = self._simplify(rows, epsilon=0.01)
        self.assertEqual(out["kept_node_ids"], [1, 11])
        self.assertEqual(out["dataframe"]["parent"].tolist(), [-1, 1])
        self._assert_valid_tree(out, rows)

    def test_point_beyond_epsilon_is_kept_and_point_within_is_removed(self) -> None:
        rows = ["1 1 0 0 0 1 -1"] + [f"{k} 3 {k - 1} 0 0 1 {k - 1}" for k in range(2, 12)]
        rows[4] = "5 3 4 1.5 0 1 4"  # 1.5 um off the line: below epsilon
        out = self._simplify(rows, epsilon=2.0)
        self.assertNotIn(5, out["kept_node_ids"])

        rows[4] = "5 3 4 3 0 1 4"  # 3 um off the line: above epsilon
        out = self._simplify(rows, epsilon=2.0)
        self.assertIn(5, out["kept_node_ids"])
        self._assert_valid_tree(out, rows)

    def test_radius_sensitive_node_is_kept(self) -> None:
        rows = ["1 1 0 0 0 1 -1"] + [f"{k} 3 {k - 1} 0 0 1 {k - 1}" for k in range(2, 12)]
        rows[5] = "6 3 5 0 0 4 5"
        out = self._simplify(rows, epsilon=100.0, radius_tolerance=0.5)
        self.assertEqual(out["kept_node_ids"], [1, 6, 11])
        self.assertEqual(out["protected_counts"]["radius_sensitive"], 1)
        self._assert_valid_tree(out, rows)

    def test_kept_rows_keep_their_values(self) -> None:
        out = self._simplify(TREE_ROWS)
        original = {int(row.split()[0]): row.split() for row in TREE_ROWS}
        for line in out["bytes"].decode("utf-8").splitlines():
            if line.startswith("#"):
                continue
            parts = line.split()
            src = original[int(parts[0])]
            self.assertEqual(parts[:6], src[:6], f"node {parts[0]} changed")

    def test_core_function_matches_wrapper(self) -> None:
        df = parse_swc_text_preserve_tokens(_swc(TREE_ROWS))
        core = simplify_morphology_dataframe(
            df, {"thresholds": {"epsilon": 2.0, "radius_tolerance": 0.5}}
        )
        wrapped = self._simplify(TREE_ROWS)
        self.assertEqual(core["kept_node_ids"], wrapped["kept_node_ids"])


if __name__ == "__main__":
    unittest.main()
