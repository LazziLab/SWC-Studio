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

    # Index Clean must keep the file's own header lines.
    def test_index_clean_keeps_header_comments(self) -> None:
        text = "# ORIGINAL_SOURCE lab tracer\n# SCALE 1.0 1.0 1.0\n" + _swc(self.SHUFFLED)
        out = index_clean_text(text).get("bytes").decode("utf-8")
        self.assertTrue(out.startswith("# ORIGINAL_SOURCE lab tracer\n# SCALE 1.0 1.0 1.0\n"), out[:200])


# TREE_ROWS with a second row that uses ID 2.
DUPLICATE_ID_ROWS = TREE_ROWS[:2] + ["2 3 12 3 0 1 1"] + TREE_ROWS[2:]
# TREE_ROWS plus a detached loop: 9's parent is 10 and 10's parent is 9.
CYCLE_ROWS = TREE_ROWS + ["9 3 50 0 0 1 10", "10 3 55 0 0 1 9"]


class IndexCleanMissingParentTests(unittest.TestCase):
    def test_missing_parent_is_refused_not_turned_into_a_root(self) -> None:
        text = "1 1 0 0 0 5 -1\n2 3 5 0 0 1 1\n3 3 9 0 0 1 77\n"
        with self.assertRaisesRegex(ValueError, "not in the file"):
            index_clean_text(text)
        with self.assertRaises(ValueError):
            core_geometry.reindex_dataframe_with_map(parse_swc_text_preserve_tokens(text))


class IndexCleanRefusalTests(unittest.TestCase):
    """Index Clean renumbers by ID, so it must refuse what it cannot renumber."""

    def test_duplicate_ids_are_refused_with_the_ids_named(self) -> None:
        with self.assertRaises(ValueError) as caught:
            index_clean_text(_swc(DUPLICATE_ID_ROWS))
        self.assertIn("node ID 2 appears on more than one row", str(caught.exception))

    def test_core_reindex_refuses_duplicate_ids(self) -> None:
        df = parse_swc_text_preserve_tokens(_swc(DUPLICATE_ID_ROWS))
        with self.assertRaises(ValueError):
            core_geometry.reindex_dataframe_with_map(df)
        with self.assertRaises(ValueError):
            ops.reindex_dataframe(df)

    def test_parent_cycle_is_refused(self) -> None:
        with self.assertRaises(ValueError) as caught:
            index_clean_text(_swc(CYCLE_ROWS))
        self.assertIn("nodes 9 and 10 form a parent cycle", str(caught.exception))

    def test_self_loop_is_refused(self) -> None:
        rows = list(TREE_ROWS)
        rows[5] = "6 3 40 -5 0 0.7 6"
        with self.assertRaises(ValueError) as caught:
            index_clean_text(_swc(rows))
        self.assertIn("node 6 is its own parent", str(caught.exception))

    def test_refused_file_writes_nothing(self) -> None:
        import tempfile
        from pathlib import Path

        from swcstudio.tools.validation.features.index_clean import index_clean_file

        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "dup.swc"
            src.write_text(_swc(DUPLICATE_ID_ROWS), encoding="utf-8")
            before = src.read_bytes()
            with self.assertRaises(ValueError):
                index_clean_file(str(src), write_output=True, write_report=True)
            self.assertEqual(sorted(p.name for p in Path(tmp).rglob("*")), ["dup.swc"])
            self.assertEqual(src.read_bytes(), before)

    def test_batch_index_clean_fails_only_the_bad_file_and_leaves_it_alone(self) -> None:
        import tempfile
        from pathlib import Path

        from swcstudio.tools.batch_processing.features.index_clean import run_folder

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "dup.swc").write_text(_swc(DUPLICATE_ID_ROWS), encoding="utf-8")
            (folder / "loop.swc").write_text(_swc(CYCLE_ROWS), encoding="utf-8")
            (folder / "good.swc").write_text(_swc(IndexCleanTests.SHUFFLED), encoding="utf-8")
            originals = {p.name: p.read_bytes() for p in folder.iterdir()}

            out = run_folder(str(folder))

            self.assertEqual(out["files_processed"], 1)
            failed = dict(out["failed_files"])
            self.assertEqual(sorted(failed), ["dup.swc", "loop.swc"])
            self.assertIn("node ID 2", failed["dup.swc"])
            self.assertIn("nodes 9 and 10", failed["loop.swc"])
            for name in ("dup.swc", "loop.swc"):
                self.assertEqual((folder / name).read_bytes(), originals[name])
                self.assertEqual([p.name for p in folder.iterdir() if p.name.startswith(name[:-4])], [name])
            self.assertNotEqual((folder / "good.swc").read_bytes(), originals["good.swc"])


class DuplicateIdEditTests(unittest.TestCase):
    """An edit by ID on a file with a repeated ID would change or drop the wrong rows."""

    def _df(self) -> pd.DataFrame:
        return parse_swc_text_preserve_tokens(_swc(DUPLICATE_ID_ROWS))

    def test_every_edit_refuses_duplicate_ids(self) -> None:
        edits = {
            "move node": lambda df: ops.move_node_absolute(df, 2, 0, 0, 0),
            "move subtree": lambda df: ops.move_subtree_absolute(df, 2, 0, 0, 0),
            "move selection": lambda df: ops.move_selection_by_anchor_absolute(df, [7, 8], 7, 0, 0, 0),
            "reconnect": lambda df: ops.reconnect_branch(df, 7, 4),
            "disconnect": lambda df: ops.disconnect_branch(df, 3, 5),
            "delete node": lambda df: ops.delete_node(df, 2, reconnect_children=True),
            "delete tip": lambda df: ops.delete_node(df, 8),
            "delete subtree": lambda df: ops.delete_subtree(df, 3),
            "insert between": lambda df: ops.insert_node_between(df, 2, 3, 15.0, 0.0, 0.0),
            "insert before": lambda df: core_geometry.insert_node_before_child(df, 3, x=15.0, y=0.0, z=0.0),
        }
        for name, edit in edits.items():
            with self.subTest(name), self.assertRaises(ValueError) as caught:
                edit(self._df())
            self.assertIn("node ID 2", str(caught.exception))

    def test_delete_node_no_longer_drops_both_rows(self) -> None:
        df = self._df()
        with self.assertRaises(ValueError):
            ops.delete_node(df, 2, reconnect_children=True)
        self.assertEqual(len(df), len(DUPLICATE_ID_ROWS))


class ParentCycleSelectionTests(unittest.TestCase):
    """Selection walks must stop where a parent cycle closes instead of looping."""

    def _df(self) -> pd.DataFrame:
        return parse_swc_text_preserve_tokens(_swc(CYCLE_ROWS))

    def test_branch_walks_finish_on_a_cycle(self) -> None:
        # In a child process: a walk that loops forever also grows its list
        # forever, so it must be killable rather than left on a thread.
        import json
        import subprocess
        import sys

        code = (
            "import json, sys\n"
            "from swcstudio.core.geometry_editing import make_selection\n"
            "from swcstudio.core.swc_io import parse_swc_text_preserve_tokens\n"
            "df = parse_swc_text_preserve_tokens(sys.stdin.read())\n"
            "kinds = ('branch', 'up_bifurcation', 'down_bifurcation', 'subtree')\n"
            "print(json.dumps({k: sorted(make_selection(df, kind=k, anchor_id=9).node_ids) for k in kinds}))\n"
        )
        try:
            done = subprocess.run(
                [sys.executable, "-c", code],
                input=_swc(CYCLE_ROWS),
                capture_output=True,
                text=True,
                timeout=20,
            )
        except subprocess.TimeoutExpired:
            self.fail("a selection walk did not finish on a parent cycle")
        self.assertEqual(done.returncode, 0, done.stderr)
        selections = json.loads(done.stdout.strip().splitlines()[-1])
        for kind, node_ids in selections.items():
            self.assertEqual(node_ids, [9, 10], kind)

    def test_upstream_walk_does_not_repeat_nodes(self) -> None:
        ids = core_geometry.upstream_node_ids(self._df(), 9, hops=6)
        self.assertEqual(ids, [9, 10])

    def test_cycle_can_be_broken_and_reconnected(self) -> None:
        detached = ops.disconnect_branch(self._df(), 9, 10)  # 9 becomes a root
        fixed = ops.reconnect_branch(detached, 6, 9)
        rows = _rows_by_id(fixed)
        self.assertEqual(rows[9]["parent"], 6)
        self.assertEqual(rows[10]["parent"], 9)
        out = index_clean_text(write_swc_to_bytes_preserve_tokens(fixed).decode("utf-8"))
        self.assertEqual(out["new_node_count"], len(CYCLE_ROWS))


class ParentCycleHelperTests(unittest.TestCase):
    def test_tree_has_no_cycles(self) -> None:
        df = _tree()
        self.assertEqual(core_geometry.parent_cycles(df["id"], df["parent"]), [])

    def test_cycles_self_loops_and_parent_ids_that_name_no_row(self) -> None:
        ids = [1, 2, 3, 4, 5, 6, 7]
        parents = [-1, 1, 4, 3, 5, 99, 6]
        self.assertEqual(core_geometry.parent_cycles(ids, parents), [[3, 4]])
        self.assertEqual(core_geometry.parent_cycles(ids, parents, include_self_loops=True), [[3, 4], [5]])

    def test_duplicate_ids_use_every_row_parent(self) -> None:
        # ID 2 has rows with parents 1 and 3; 3's parent is 2, so 2-3 loop.
        self.assertEqual(core_geometry.parent_cycles([1, 2, 2, 3], [-1, 1, 3, 2]), [[2, 3]])

    def test_long_chain_does_not_recurse(self) -> None:
        n = 50_000
        ids = list(range(1, n + 1))
        parents = [-1] + list(range(1, n))
        self.assertEqual(core_geometry.parent_cycles(ids, parents), [])
        parents[0] = n  # close the chain into one big loop
        self.assertEqual(len(core_geometry.parent_cycles(ids, parents)[0]), n)
        # The same loop through the duplicate-ID path (Tarjan): ID 1 gets a
        # second row, with a different parent.
        dup_ids = ids + [1]
        dup_parents = parents + [2]
        self.assertEqual(len(core_geometry.parent_cycles(dup_ids, dup_parents)[0]), n)


class EditTextFidelityTests(unittest.TestCase):
    # An edit must not rewrite the number text of rows it did not change.
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
