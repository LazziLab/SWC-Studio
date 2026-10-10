from __future__ import annotations

import os
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

from swcstudio.cli.history_cli import _materialize_state_at, _materialize_state_before
from swcstudio.core.provenance import (
    OpKind,
    archive_path_for,
    canonical_swc,
    current_swc_path_for,
    history_dir_for,
    iter_events,
    open_history_for_read,
    operation_display_name,
    operation_display_parameters,
    parse_prov_header,
    tracked_op,
)
from swcstudio.tools.batch_processing.features.index_clean import (
    run_folder as run_batch_index_clean,
)
from swcstudio.tools.batch_processing.features.swc_splitter import (
    split_folder as run_batch_split,
)


BASE_SWC = b"# test\n1 1 0 0 0 1 -1\n2 3 1 0 0 0.5 1\n"
TYPE_EDIT = b"# test\n1 1 0 0 0 1 -1\n2 2 1 0 0 0.5 1\n"
RADIUS_EDIT = b"# test\n1 1 0 0 0 1 -1\n2 2 1 0 0 0.8 1\n"
SWC_PLUS = (
    b"# SWC plus (SWC+) format specification\n"
    b"# <SWCplus version=\"0.12\">\n"
    b"#   <MetaData>\n"
    b"#     <FileHistory originalName=\"cell.swc\" originalFormat=\"SWC\">\n"
    b"#       <Modification software=\"SWC Tools\" command=\"type-edit\" summary=\"Edited types\"/>\n"
    b"#     </FileHistory>\n"
    b"#   </MetaData>\n"
    b"# </SWCplus>\n"
    b"1 1 0 0 0 1 -1\n"
    b"2 3 1 0 0 0.5 1\n"
)
SWC_PLUS_TYPE_EDIT = SWC_PLUS.replace(
    b"2 3 1 0 0 0.5 1\n",
    b"2 2 1 0 0 0.5 1\n",
)


class ProvenanceArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="swcstudio-provenance-")
        self.tmp = Path(self._tmpdir.name)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _make_swc(self, name: str = "cell.swc") -> Path:
        path = self.tmp / name
        path.write_bytes(BASE_SWC)
        return path

    def test_manual_label_operation_names_are_user_facing(self) -> None:
        self.assertEqual(
            operation_display_name(OpKind.SET_TYPE.value, {}),
            "Manual Labeling",
        )
        self.assertEqual(
            operation_display_name(
                OpKind.PLUGIN_OP.value,
                {"source": "editor_table", "title": "Manual Label Edit"},
            ),
            "Manual Labeling",
        )

    def test_operation_parameters_hide_internals_and_keep_settings(self) -> None:
        displayed = operation_display_parameters(
            OpKind.AUTO_LABEL.value,
            {
                "options": {
                    "cell_type": "pyramidal",
                    "flag_budget": 0.2,
                    "flag_enabled": True,
                },
                "effective_config": {
                    "thresholds": {"confidence_threshold": 0.6},
                    "seed": 123,
                    "keep_tips": True,
                    "model_sha": "sha256:secret",
                    "model_dir": "D:/private/models",
                },
                "source": "editor_table",
                "title": "internal title",
                "target_sha": "sha256:hidden",
                "nodes_total": 400,
                "type_changes": 25,
                "out_type_counts": {"1": 10, "2": 20, "3": 30, "4": 40},
            },
        )

        self.assertEqual(displayed["Cell Class"], "pyramidal")
        self.assertEqual(displayed["Flag Review Budget"], "0.2")
        self.assertEqual(displayed["Flagging Enabled"], "Yes")
        self.assertEqual(displayed["Confidence Threshold"], "0.6")
        self.assertEqual(displayed["AI Seed"], 123)
        self.assertNotIn("Keep Tips", displayed)
        lowered = " ".join(f"{key} {value}" for key, value in displayed.items()).lower()
        self.assertNotIn("sha", lowered)
        self.assertNotIn("model dir", lowered)
        self.assertNotIn("nodes total", lowered)
        self.assertNotIn("type changes", lowered)
        self.assertNotIn("out type", lowered)
        self.assertNotIn(" 1 ", f" {lowered} ")
        self.assertNotIn("editor_table", lowered)

    def test_operation_parameters_are_specific_to_operation_type(self) -> None:
        displayed = operation_display_parameters(
            OpKind.SIMPLIFICATION.value,
            {
                "effective_config": {
                    "thresholds": {
                        "epsilon": 2.0,
                        "radius_tolerance": 0.5,
                    },
                    "flags": {
                        "keep_tips": True,
                        "keep_bifurcations": True,
                    },
                    "output": {"suffix": "_simplified"},
                    "method": "default",
                },
                "original_node_count": 1000,
                "new_node_count": 700,
                "reduction_percent": 30.0,
            },
        )

        self.assertEqual(
            displayed,
            {
                "Simplification Epsilon": "2",
                "Radius Tolerance": "0.5",
                "Keep Tips": "Yes",
                "Keep Bifurcations": "Yes",
            },
        )

    def test_revert_operation_displays_its_history_source(self) -> None:
        displayed = operation_display_parameters(
            OpKind.PLUGIN_OP.value,
            {
                "action": "revert_before_operation",
                "target_operation": "op-2",
                "target_sha": "sha256:hidden-technical-value",
                "reverted_from_operation": "op-2",
                "reverted_from_version": "abc123def456",
                "restore_mode": "Before selected operation",
            },
        )

        self.assertEqual(
            displayed,
            {
                "Reverted From Operation": "op-2",
                "Reverted From Version": "abc123def456",
                "Restore Mode": "Before selected operation",
            },
        )

    def test_operation_ids_increment_per_file(self) -> None:
        first = self._make_swc("first.swc")
        second = self._make_swc("second.swc")

        with tracked_op(first, kind=OpKind.SET_TYPE, params={}, message="first 1") as op:
            op.set_output(TYPE_EDIT)
        self.assertEqual(op.result.operation_id, 1)
        self.assertEqual(op.result.operation_label, "op-1")

        with tracked_op(first, kind=OpKind.SET_RADIUS, params={}, message="first 2") as op:
            op.set_output(RADIUS_EDIT)
        self.assertEqual(op.result.operation_id, 2)
        self.assertEqual(op.result.operation_label, "op-2")

        with tracked_op(second, kind=OpKind.SET_TYPE, params={}, message="second 1") as op:
            op.set_output(TYPE_EDIT)
        self.assertEqual(op.result.operation_id, 1)
        self.assertEqual(op.result.operation_label, "op-1")

    def test_batch_index_clean_tracks_each_file_independently(self) -> None:
        folder = self.tmp / "batch"
        folder.mkdir()
        source = b"10 1 0 0 0 1 -1\n20 3 1 0 0 0.5 10\n"
        first = folder / "first.swc"
        second = folder / "second.swc"
        first.write_bytes(source)
        second.write_bytes(source)

        result1 = run_batch_index_clean(str(folder))
        self.assertIsNone(result1["out_dir"])
        self.assertIsNone(result1["log_path"])
        self.assertEqual(result1["files_processed"], 2)
        self.assertEqual(
            {row["file"]: row["operation_id"] for row in result1["commits"]},
            {"first.swc": "op-1", "second.swc": "op-1"},
        )
        self.assertTrue(archive_path_for(first).exists())
        self.assertTrue(archive_path_for(second).exists())

        result2 = run_batch_index_clean(str(folder))
        self.assertEqual(
            {row["file"]: row["operation_id"] for row in result2["commits"]},
            {"first.swc": "op-2", "second.swc": "op-2"},
        )

    def test_batch_split_outputs_start_independent_histories(self) -> None:
        folder = self.tmp / "split"
        folder.mkdir()
        source = folder / "multi.swc"
        source.write_bytes(
            (Path(__file__).parent / "fixtures" / "multi-soma.swc").read_bytes()
        )

        result = run_batch_split(str(folder))

        self.assertGreaterEqual(result["trees_saved"], 2)
        self.assertEqual(len(result["output_commits"]), result["trees_saved"])
        for row in result["output_commits"]:
            output = Path(result["out_dir"]) / row["file"]
            self.assertEqual(row["operation_id"], "op-1")
            self.assertTrue(output.exists())
            self.assertTrue(archive_path_for(output).exists())

    def test_materialize_before_operation_undoes_selected_and_later_changes(self) -> None:
        swc = self._make_swc()
        with tracked_op(swc, kind=OpKind.SET_TYPE, params={}, message="operation 1") as op:
            op.set_output(TYPE_EDIT)
        with tracked_op(swc, kind=OpKind.SET_RADIUS, params={}, message="operation 2") as op:
            op.set_output(RADIUS_EDIT)

        with open_history_for_read(swc, history_dir_for(swc)) as hist:
            events = list(iter_events(hist / "events.jsonl"))
            after_first = _materialize_state_at(hist, events[0].id, swc_path=swc)
            before_first = _materialize_state_before(hist, events[0].id, swc_path=swc)
            before_second = _materialize_state_before(hist, events[1].id, swc_path=swc)

        self.assertEqual(canonical_swc(after_first), canonical_swc(TYPE_EDIT))
        self.assertEqual(canonical_swc(before_first), canonical_swc(BASE_SWC))
        self.assertEqual(canonical_swc(before_second), canonical_swc(TYPE_EDIT))

    def test_materialize_before_delete_restores_removed_node(self) -> None:
        swc = self._make_swc()
        deleted = b"# test\n1 1 0 0 0 1 -1\n"
        with tracked_op(swc, kind=OpKind.GEOMETRY_EDIT, params={}, message="delete node") as op:
            op.set_output(deleted)

        with open_history_for_read(swc, history_dir_for(swc)) as hist:
            event = next(iter_events(hist / "events.jsonl"))
            before_delete = _materialize_state_before(hist, event.id, swc_path=swc)

        self.assertEqual(canonical_swc(before_delete), canonical_swc(BASE_SWC))

    def test_tracked_op_writes_visible_archive_and_header_pointer(self) -> None:
        swc = self._make_swc()
        with tracked_op(
            swc,
            kind=OpKind.SET_TYPE,
            params={"node_id": 2, "new_type": 2},
            message="type edit",
        ) as op:
            op.set_output(TYPE_EDIT)

        archive = archive_path_for(swc)
        self.assertTrue(archive.exists())
        self.assertFalse(history_dir_for(swc).exists())
        self.assertEqual(archive.name, "cell_history.swcstudio")

        with zipfile.ZipFile(archive, "r") as zf:
            infos = zf.infolist()
            self.assertTrue(any((info.flag_bits & 0x1) or info.compress_type == 99 for info in infos))
            member = next(info.filename for info in infos if info.filename.endswith("events.jsonl"))
            with self.assertRaises(Exception):
                zf.read(member)

        current = current_swc_path_for(swc)
        self.assertEqual(current, swc)
        self.assertFalse((swc.parent / "cell_swc_studio_output").exists())
        header = parse_prov_header(current.read_bytes())
        self.assertEqual(header.root["repo"], archive.name)
        self.assertEqual(header.tip["sidecar"], archive.name)
        self.assertTrue(header.tip.get("repo_id"))

        with open_history_for_read(swc, history_dir_for(swc)) as hist:
            events = list(iter_events(hist / "events.jsonl"))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].message, "type edit")
        self.assertFalse(history_dir_for(swc).exists())

    def test_archive_follows_renamed_swc_with_matching_repo_id(self) -> None:
        swc = self._make_swc()
        with tracked_op(swc, kind=OpKind.SET_TYPE, params={}, message="first") as op:
            op.set_output(TYPE_EDIT)

        renamed = self.tmp / "renamed.swc"
        shutil.copy2(current_swc_path_for(swc), renamed)
        with tracked_op(renamed, kind=OpKind.SET_RADIUS, params={}, message="renamed") as op:
            op.set_output(RADIUS_EDIT)

        self.assertFalse(archive_path_for(swc).exists())
        self.assertTrue(archive_path_for(renamed).exists())
        self.assertFalse(history_dir_for(renamed).exists())
        with open_history_for_read(renamed, history_dir_for(renamed)) as hist:
            messages = [event.message for event in iter_events(hist / "events.jsonl")]
        self.assertEqual(messages, ["first", "renamed"])

    def test_swc_plus_header_survives_provenance_stamping(self) -> None:
        swc = self.tmp / "plus.swc"
        swc.write_bytes(SWC_PLUS)

        with tracked_op(swc, kind=OpKind.SET_TYPE, params={"node_id": 2}, message="type edit") as op:
            op.set_output(SWC_PLUS_TYPE_EDIT)

        text = swc.read_text(encoding="utf-8")
        self.assertIn("# @PROV root=", text)
        self.assertIn("# @PROV tip=", text)
        self.assertIn("# SWC plus (SWC+) format specification", text)
        self.assertIn('# <SWCplus version="0.12">', text)
        self.assertIn('<FileHistory originalName="cell.swc" originalFormat="SWC">', text)
        self.assertIn("2 2 1 0 0 0.5 1", text)

    def test_encrypted_archive_round_trips_when_password_is_set(self) -> None:
        try:
            import pyzipper  # noqa: F401
        except Exception:
            self.skipTest("pyzipper is not installed")

        old = os.environ.get("SWCSTUDIO_HISTORY_PASSWORD")
        os.environ["SWCSTUDIO_HISTORY_PASSWORD"] = "test-password"
        try:
            swc = self._make_swc()
            with tracked_op(swc, kind=OpKind.SET_TYPE, params={}, message="encrypted") as op:
                op.set_output(TYPE_EDIT)

            self.assertTrue(archive_path_for(swc).exists())
            self.assertFalse(history_dir_for(swc).exists())
            with open_history_for_read(swc, history_dir_for(swc)) as hist:
                events = list(iter_events(hist / "events.jsonl"))
            self.assertEqual([event.message for event in events], ["encrypted"])
            self.assertFalse(history_dir_for(swc).exists())
        finally:
            if old is None:
                os.environ.pop("SWCSTUDIO_HISTORY_PASSWORD", None)
            else:
                os.environ["SWCSTUDIO_HISTORY_PASSWORD"] = old


if __name__ == "__main__":
    unittest.main()
