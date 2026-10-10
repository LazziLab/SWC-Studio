"""Unit tests for geometry editing, Index Clean and subtree relabeling.

Edits run through the tool-layer wrappers the CLI and GUI import
(swcstudio.tools.geometry_editing.features.operations), Index Clean through
``index_clean_text`` and subtree relabeling through ``reassign_subtree_types``.
"""
from __future__ import annotations

import unittest

import pandas as pd

from swcstudio.core import geometry_editing as core_geometry
from swcstudio.core.subtree_editing import reassign_subtree_types_dataframe
from swcstudio.core.swc_io import parse_swc_text_preserve_tokens, write_swc_to_bytes_preserve_tokens
from swcstudio.tools.geometry_editing.features import operations as ops
from swcstudio.tools.morphology_editing.features.dendrogram_editing import reassign_subtree_types
from swcstudio.tools.validation.features.index_clean import index_clean_text


# Soma 1; basal trunk 2-3 that bifurcates at 3 into 4 and 5 (5 has child 6);
# axon 7-8.
TREE_ROWS = [
    "1 1 0 0 0 5 -1",
    "2 3 10 0 0 1 1",
    "3 3 20 0 0 1 2",
    "4 3 30 5 0 0.8 3",
    "5 3 30 -5 0 0.9 3",
    "6 3 40 -5 0 0.7 5",
    "7 2 -10 0 0 0.5 1",
    "8 2 -20 0 0 0.4 7",
]


def _swc(rows: list[str]) -> str:
    return "\n".join(rows) + "\n"


def _tree() -> pd.DataFrame:
    return parse_swc_text_preserve_tokens(_swc(TREE_ROWS))


def _rows_by_id(df: pd.DataFrame) -> dict[int, dict]:
    return {
        int(row["id"]): {
            "type": int(row["type"]),
            "xyz": (float(row["x"]), float(row["y"]), float(row["z"])),
            "radius": float(row["radius"]),
            "parent": int(row["parent"]),
        }
        for _, row in df.iterrows()
    }


class MoveTests(unittest.TestCase):
    def test_move_node_changes_only_that_node(self) -> None:
        before = _rows_by_id(_tree())
        after = _rows_by_id(ops.move_node_absolute(_tree(), 4, 1.5, -2.0, 3.25))
        expected = dict(before)
        expected[4] = dict(before[4], xyz=(1.5, -2.0, 3.25))
        self.assertEqual(after, expected)

    def test_move_missing_node_raises(self) -> None:
        with self.assertRaises(ValueError):
            ops.move_node_absolute(_tree(), 99, 0, 0, 0)

    def test_move_subtree_shifts_every_descendant_by_same_vector(self) -> None:
        before = _rows_by_id(_tree())
        after = _rows_by_id(ops.move_subtree_absolute(_tree(), 3, 25.0, 10.0, 2.0))
        delta = (5.0, 10.0, 2.0)
        for node_id, row in before.items():
            if node_id in {3, 4, 5, 6}:
                expected = tuple(row["xyz"][i] + delta[i] for i in range(3))
                self.assertEqual(after[node_id]["xyz"], expected, f"node {node_id}")
            else:
                self.assertEqual(after[node_id]["xyz"], row["xyz"], f"node {node_id}")
            self.assertEqual(after[node_id]["parent"], row["parent"])

    def test_move_selection_moves_only_selected_nodes(self) -> None:
        after = _rows_by_id(core_geometry.move_selection_by_anchor_absolute(_tree(), [7, 8], 7, -10.0, 4.0, 0.0))
        self.assertEqual(after[7]["xyz"], (-10.0, 4.0, 0.0))
        self.assertEqual(after[8]["xyz"], (-20.0, 4.0, 0.0))
        self.assertEqual(after[2]["xyz"], (10.0, 0.0, 0.0))


class DeleteTests(unittest.TestCase):
    def test_delete_tip_node(self) -> None:
        out = ops.delete_node(_tree(), 6)
        self.assertEqual(sorted(out["id"].astype(int).tolist()), [1, 2, 3, 4, 5, 7, 8])
        self.assertEqual(_rows_by_id(out)[5]["parent"], 3)

    def test_delete_node_with_children_requires_reconnect(self) -> None:
        with self.assertRaises(ValueError):
            ops.delete_node(_tree(), 3)

    def test_delete_node_reconnects_children_to_its_parent(self) -> None:
        out = _rows_by_id(ops.delete_node(_tree(), 3, reconnect_children=True))
        self.assertNotIn(3, out)
        self.assertEqual(out[4]["parent"], 2)
        self.assertEqual(out[5]["parent"], 2)
        self.assertEqual(out[6]["parent"], 5)
        self.assertEqual(len(out), 7)

    def test_delete_subtree_removes_all_descendants(self) -> None:
        out = ops.delete_subtree(_tree(), 3)
        self.assertEqual(sorted(out["id"].astype(int).tolist()), [1, 2, 7, 8])
        with self.assertRaises(ValueError):
            ops.delete_subtree(_tree(), 99)


class InsertTests(unittest.TestCase):
    def test_insert_node_between_parent_and_child(self) -> None:
        out = _rows_by_id(ops.insert_node_between(_tree(), 2, 3, 15.0, 0.5, 0.0))
        self.assertEqual(len(out), 9)
        new = out[9]
        self.assertEqual(new["parent"], 2)
        self.assertEqual(new["xyz"], (15.0, 0.5, 0.0))
        self.assertEqual(new["type"], 3)
        self.assertEqual(new["radius"], 1.0)  # taken from the end node
        self.assertEqual(out[3]["parent"], 9)
        self.assertEqual(out[4]["parent"], 3)

    def test_insert_uses_given_radius_and_type(self) -> None:
        out = _rows_by_id(ops.insert_node_between(_tree(), 7, 8, -15.0, 0.0, 0.0, radius=0.45, type_id=2))
        self.assertEqual(out[9]["radius"], 0.45)
        self.assertEqual(out[9]["type"], 2)
        self.assertEqual(out[8]["parent"], 9)

    def test_insert_that_would_create_cycle_raises(self) -> None:
        with self.assertRaises(ValueError):
            ops.insert_node_between(_tree(), 6, 3, 0.0, 0.0, 0.0)


class ConnectTests(unittest.TestCase):
    def test_disconnect_single_edge(self) -> None:
        out = _rows_by_id(ops.disconnect_branch(_tree(), 3, 5))
        self.assertEqual(out[5]["parent"], -1)
        self.assertEqual(out[6]["parent"], 5)
        self.assertEqual(out[4]["parent"], 3)

    def test_disconnect_every_edge_on_path(self) -> None:
        out = _rows_by_id(ops.disconnect_branch(_tree(), 2, 6))
        for node_id in (3, 5, 6):
            self.assertEqual(out[node_id]["parent"], -1, f"node {node_id}")
        self.assertEqual(out[4]["parent"], 3)
        self.assertEqual(out[2]["parent"], 1)

    def test_reconnect_sets_target_parent(self) -> None:
        detached = ops.disconnect_branch(_tree(), 3, 5)
        out = _rows_by_id(ops.reconnect_branch(detached, 2, 5))
        self.assertEqual(out[5]["parent"], 2)
        self.assertEqual(out[6]["parent"], 5)

    def test_reconnect_that_would_create_cycle_raises(self) -> None:
        with self.assertRaises(ValueError):
            ops.reconnect_branch(_tree(), 6, 3)
        with self.assertRaises(ValueError):
            ops.reconnect_branch(_tree(), 4, 4)


class SelectionTests(unittest.TestCase):
    def test_core_selection_kinds(self) -> None:
        df = _tree()
        subtree = core_geometry.make_selection(df, kind="subtree", anchor_id=3)
        self.assertEqual(sorted(subtree.node_ids), [3, 4, 5, 6])
        upstream = core_geometry.make_selection(df, kind="upstream_nodes", anchor_id=6, hops=2)
        self.assertEqual(upstream.node_ids, [6, 5, 3])
        downstream = core_geometry.make_selection(df, kind="downstream_nodes", anchor_id=2, hops=1)
        self.assertEqual(sorted(downstream.node_ids), [2, 3])
        with self.assertRaises(ValueError):
            core_geometry.make_selection(df, kind="nonsense", anchor_id=3)

    # The GUI panel calls the tool-layer wrapper this way; it must match core.
    def test_tool_layer_selection_accepts_gui_call(self) -> None:
        selection = ops.make_selection(_tree(), kind="subtree", anchor_id=3, hops=None)
        self.assertEqual(sorted(selection.node_ids), [3, 4, 5, 6])


class IndexCleanTests(unittest.TestCase):
    SHUFFLED = [
        "10 1 0 0 0 5 -1",
        "30 3 2 0 0 1 20",
        "20 3 1 0 0 1 10",
        "40 3 3 1 0 0.8 30",
        "45 3 3 -1 0 0.7 30",
        "50 2 -1 0 0 0.5 10",
    ]

    def test_index_clean_renumbers_parents_before_children(self) -> None:
        out = index_clean_text(_swc(self.SHUFFLED))
        df = out["dataframe"]
        ids = df["id"].astype(int).tolist()
        self.assertEqual(sorted(ids), list(range(1, len(self.SHUFFLED) + 1)))
        for node_id, parent_id in zip(ids, df["parent"].astype(int).tolist()):
            if parent_id != -1:
                self.assertLess(parent_id, node_id)
        self.assertEqual(out["original_node_count"], 6)
        self.assertEqual(out["new_node_count"], 6)

    def test_index_clean_preserves_topology_and_values(self) -> None:
        before = _rows_by_id(parse_swc_text_preserve_tokens(_swc(self.SHUFFLED)))
        out = index_clean_text(_swc(self.SHUFFLED))
        id_map = {int(k): int(v) for k, v in out["id_map"].items()}
        self.assertEqual(set(id_map), set(before))
        after = _rows_by_id(out["dataframe"])
        for old_id, row in before.items():
            new_row = after[id_map[old_id]]
            expected_parent = -1 if row["parent"] == -1 else id_map[row["parent"]]
            self.assertEqual(new_row["parent"], expected_parent, f"old node {old_id}")
            self.assertEqual(new_row["type"], row["type"])
            self.assertEqual(new_row["xyz"], row["xyz"])
            self.assertEqual(new_row["radius"], row["radius"])

        # The bytes written to disk describe the same renumbered tree.
        reparsed = _rows_by_id(parse_swc_text_preserve_tokens(out["bytes"].decode("utf-8")))
        self.assertEqual(reparsed, after)

    def test_index_clean_is_depth_first(self) -> None:
        out = index_clean_text(_swc(self.SHUFFLED))
        after = out["dataframe"]
        id_map = {int(k): int(v) for k, v in out["id_map"].items()}
        # Soma first, then the whole basal subtree (20, 30, 40, 45) before the axon.
        self.assertEqual(id_map[10], 1)
        self.assertEqual([id_map[k] for k in (20, 30)], [2, 3])
        self.assertEqual(sorted(id_map[k] for k in (40, 45)), [4, 5])
        self.assertEqual(id_map[50], 6)
        self.assertEqual(len(after), 6)

    def test_index_clean_on_ordered_file_keeps_ids(self) -> None:
        out = index_clean_text(_swc(TREE_ROWS))
        self.assertEqual(out["remapped_id_count"], 0)
        self.assertEqual(_rows_by_id(out["dataframe"]), _rows_by_id(_tree()))

    # Bug: reindex_dataframe_with_map builds a fresh DataFrame from SWC_COLS, so
    # the parsed header lines (df.attrs) and number tokens are dropped and the
    # file's own header is replaced by the default one
    # (core/geometry_editing.py:157-164, written by tools/validation/features/index_clean.py:33-37).
    @unittest.expectedFailure
    def test_index_clean_keeps_header_comments(self) -> None:
        text = "# ORIGINAL_SOURCE lab tracer\n# SCALE 1.0 1.0 1.0\n" + _swc(self.SHUFFLED)
        out = index_clean_text(text).get("bytes").decode("utf-8")
        self.assertTrue(out.startswith("# ORIGINAL_SOURCE lab tracer\n# SCALE 1.0 1.0 1.0\n"), out[:200])


class EditTextFidelityTests(unittest.TestCase):
    # Bug: _df_copy keeps only SWC_COLS, dropping the *_str token columns that
    # write_swc_to_bytes_preserve_tokens uses, so one edit rewrites the number
    # text of every row (core/geometry_editing.py:30-31).
    @unittest.expectedFailure
    def test_move_node_keeps_untouched_number_text(self) -> None:
        rows = [
            "1 1 0.00 0.00 0.00 5.000 -1",
            "2 3 1.25 0.00 0.00 1.000 1",
            "3 3 2.5000e+00 0.00 0.00 1.000 2",
        ]
        out_df = ops.move_node_absolute(parse_swc_text_preserve_tokens(_swc(rows)), 3, 9, 9, 9)
        lines = [ln for ln in write_swc_to_bytes_preserve_tokens(out_df).decode("utf-8").splitlines()
                 if not ln.startswith("#")]
        self.assertEqual(lines[:2], rows[:2])


class SubtreeRelabelTests(unittest.TestCase):
    def test_relabel_subtree_changes_every_descendant(self) -> None:
        out = reassign_subtree_types(_swc(TREE_ROWS), node_id=3, new_type=4)
        self.assertEqual(sorted(out["changed_node_ids"]), [3, 4, 5, 6])
        self.assertEqual(out["changes"], 4)
        types = {k: v["type"] for k, v in _rows_by_id(out["dataframe"]).items()}
        self.assertEqual(types, {1: 1, 2: 3, 3: 4, 4: 4, 5: 4, 6: 4, 7: 2, 8: 2})

    def test_relabel_from_root_preserves_soma(self) -> None:
        out = reassign_subtree_types(_swc(TREE_ROWS), node_id=1, new_type=2)
        types = {k: v["type"] for k, v in _rows_by_id(out["dataframe"]).items()}
        self.assertEqual(types[1], 1)
        self.assertTrue(all(t == 2 for k, t in types.items() if k != 1))
        self.assertEqual(sorted(out["changed_node_ids"]), [2, 3, 4, 5, 6])

    def test_relabel_can_exclude_selected_node(self) -> None:
        out = reassign_subtree_types(
            _swc(TREE_ROWS),
            node_id=3,
            new_type=4,
            config_overrides={"rules": {"include_selected_node": False}},
        )
        self.assertEqual(sorted(out["changed_node_ids"]), [4, 5, 6])

    def test_relabel_leaves_geometry_and_parents_alone(self) -> None:
        before = _rows_by_id(_tree())
        out = reassign_subtree_types(_swc(TREE_ROWS), node_id=5, new_type=7)
        after = _rows_by_id(out["dataframe"])
        for node_id, row in before.items():
            self.assertEqual(after[node_id]["xyz"], row["xyz"])
            self.assertEqual(after[node_id]["parent"], row["parent"])
            self.assertEqual(after[node_id]["radius"], row["radius"])
        # Only the type token changes in the written file.
        lines = [ln for ln in out["bytes"].decode("utf-8").splitlines() if not ln.startswith("#")]
        self.assertEqual(lines[4], "5 7 30 -5 0 0.9 3")
        self.assertEqual(lines[5], "6 7 40 -5 0 0.7 5")
        self.assertEqual(lines[:4], TREE_ROWS[:4])

    def test_core_relabel_unknown_node_raises(self) -> None:
        with self.assertRaises(KeyError):
            reassign_subtree_types_dataframe(_tree(), 99, 3, {"rules": {}})


if __name__ == "__main__":
    unittest.main()
