from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from swcstudio.core.model_paths import resolve_model_path
from swcstudio.gui.edit_history import DataFrameDelta, DataFrameUndoHistory


def _frame(count: int = 100) -> pd.DataFrame:
    df = pd.DataFrame(
        {
            "id": list(range(1, count + 1)),
            "type": [3] * count,
            "x": [float(value) for value in range(count)],
            "y": [0.0] * count,
            "z": [0.0] * count,
            "radius": [0.5] * count,
            "parent": [-1, *range(1, count)],
        }
    )
    df.attrs["header_lines"] = ["# test"]
    return df


class PerformancePathTests(unittest.TestCase):
    def test_row_edit_history_stores_only_changed_rows(self) -> None:
        before = _frame(10_000)
        after = before.copy()
        after.attrs.update(before.attrs)
        after.loc[after["id"] == 5000, "type"] = 4

        delta = DataFrameDelta.build(before, after)

        self.assertIsNotNone(delta)
        self.assertIsNone(delta.compressed)
        self.assertEqual(delta.changed_row_count, 1)
        self.assertTrue(delta.apply(after, forward=False).equals(before))
        self.assertTrue(delta.apply(before, forward=True).equals(after))

    def test_structural_delta_supports_undo_redo_and_history_limit(self) -> None:
        history = DataFrameUndoHistory(limit=3)
        current = _frame(8)
        states = [current.copy()]
        for node_id in (8, 7, 6, 5, 4):
            next_df = current.loc[current["id"] != node_id].reset_index(drop=True)
            history.push(current, next_df)
            current = next_df
            states.append(current.copy())

        self.assertEqual(history.step_count, 3)
        for expected in reversed(states[-4:-1]):
            current = history.undo(current)
            self.assertIsNotNone(current)
            self.assertTrue(current.equals(expected))
        self.assertFalse(history.can_undo)

        for expected in states[-3:]:
            current = history.redo(current)
            self.assertIsNotNone(current)
            self.assertTrue(current.equals(expected))
        self.assertFalse(history.can_redo)

    def test_stage_models_are_loaded_once_per_unchanged_file(self) -> None:
        from swcstudio.core.auto_typing._pickle_compat import labeler_pickle_aliases
        from swcstudio.core.labeler import _model_cache, infer_pipeline
        from swcstudio.core.labeler.stage1 import cell_type_detector

        stage1 = resolve_model_path("stage1")
        stage2 = resolve_model_path("stage2")
        self.assertIsNotNone(stage1)
        self.assertIsNotNone(stage2)

        _model_cache.clear()
        original_load = infer_pipeline.pickle.load
        with labeler_pickle_aliases(), patch.object(
            infer_pipeline.pickle, "load", wraps=original_load,
        ) as stage2_load:
            first_bundle = infer_pipeline.load_stage2_bundle(Path(stage2))
            second_bundle = infer_pipeline.load_stage2_bundle(Path(stage2))
        self.assertIs(first_bundle, second_bundle)
        self.assertEqual(stage2_load.call_count, 1)

        with labeler_pickle_aliases():
            first = cell_type_detector.load_classifier(stage1)
            second = cell_type_detector.load_classifier(stage1)
        self.assertIs(first, second)

    def test_non_unique_ids_use_compressed_fallback(self) -> None:
        before = _frame(4)
        after = before.copy()
        after.loc[3, "id"] = 3

        delta = DataFrameDelta.build(before, after)

        self.assertIsNotNone(delta)
        self.assertIsNotNone(delta.compressed)
        self.assertTrue(delta.apply(after, forward=False).equals(before))


if __name__ == "__main__":
    unittest.main()
