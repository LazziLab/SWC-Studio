"""Unit tests for the validation checks.

Each native check gets one SWC that passes and one that fails, run through the
same engine entry point the CLI ``validate`` command uses, with every other
check disabled. The NeuroM-wrapped checks are run on a clean synthetic cell.
"""
from __future__ import annotations

import math
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from swcstudio.core.custom_types import load_custom_type_definitions, set_custom_type_definition
from swcstudio.core.validation_checks.native_checks import register_native_checks
from swcstudio.core.validation_checks.neuron_morphology_checks import (
    register_neuron_morphology_checks,
)
from swcstudio.core.validation_engine import (
    ValidationContext,
    load_validation_config,
    run_validation_text,
)
from swcstudio.core.validation_registry import list_checks
from swcstudio.tools.validation.features.run_checks import validate_file


NATIVE_CHECK_KEYS = {
    "valid_soma_format",
    "multiple_somas",
    "has_soma",
    "no_invalid_negative_types",
    "custom_types_defined",
    "has_axon",
    "has_basal_dendrite",
    "has_apical_dendrite",
    "all_neurite_radii_nonzero",
    "radius_upper_bound",
    "all_section_lengths_nonzero",
    "all_segment_lengths_nonzero",
    "no_duplicate_3d_points",
    "no_extreme_spatial_jump",
    "no_dangling_branches",
    "no_self_loop",
    "parent_id_less_than_child_id",
    "no_node_id_gaps",
}

NEURON_MORPHOLOGY_CHECK_KEYS = {
    "has_multifurcation",
    "no_back_tracking",
    "no_fat_terminal_ends",
    "no_flat_neurites",
    "no_section_index_jumps",
    "no_ultranarrow_sections",
    "no_ultranarrow_starts",
    "no_root_index_jumps",
    "no_single_child_chains",
    "soma_radius_nonzero",
    "has_unifurcation",
}

# A small, well-formed cell: soma, one axon node, a two-node basal branch and
# one apical node. Every native check passes on it.
SMALL_CELL = [
    "1 1 0 0 0 5 -1",
    "2 2 -6 0 0 0.5 1",
    "3 3 6 0 0 1 1",
    "4 3 12 0 0 0.8 3",
    "5 4 0 6 0 1 1",
]


def _swc(rows: list[str]) -> str:
    return "\n".join(rows) + "\n"


def _helix_path(start, axis, n, *, step=2.5, coil=2.0, phase=0.0):
    """``n`` points after ``start`` along ``axis``, wound in a small helix."""
    norm = math.sqrt(sum(v * v for v in axis))
    ax = [v / norm for v in axis]
    helper = (0.0, 0.0, 1.0) if abs(ax[2]) < 0.9 else (1.0, 0.0, 0.0)
    u = _cross(ax, helper)
    u_norm = math.sqrt(sum(v * v for v in u))
    u = [v / u_norm for v in u]
    w = _cross(ax, u)

    def offset(k):
        a = phase + 0.6 * k
        return [coil * (math.cos(a) * u[i] + math.sin(a) * w[i]) for i in range(3)]

    o0 = offset(0)
    points = []
    for k in range(1, n + 1):
        ok = offset(k)
        points.append([start[i] + k * step * ax[i] + ok[i] - o0[i] for i in range(3)])
    return points


def _cross(a, b):
    return [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]


def _clean_neuron_rows(*, soma_radius: float = 5.0) -> list[str]:
    """A soma with an axon, a basal and an apical tree, each bifurcating once.

    The neurites are helical (so not flat), taper smoothly, start close to the
    soma and use evenly spaced points, so every enabled check should pass.
    """
    rows = [f"1 1 0 0 0 {soma_radius:g} -1"]
    nid = 1
    specs = (
        (2, (1.0, 0.0, 0.2), (0.0, 1.0, 0.0)),
        (3, (-1.0, 0.3, -0.2), (0.0, 0.0, 1.0)),
        (4, (0.1, 1.0, 0.3), (1.0, 0.0, 0.0)),
    )
    for node_type, axis, spread in specs:
        norm = math.sqrt(sum(v * v for v in axis))
        ax = [v / norm for v in axis]
        trunk_start = [1.5 * v for v in ax]
        trunk = _helix_path(trunk_start, ax, 12)
        parent = 1
        for k, p in enumerate(trunk):
            nid += 1
            radius = 1.2 - 0.3 * k / (len(trunk) - 1)
            rows.append(f"{nid} {node_type} {p[0]:.4f} {p[1]:.4f} {p[2]:.4f} {radius:.4f} {parent}")
            parent = nid
        branch_id = nid
        for sign, phase in ((1.0, 1.0), (-1.0, 2.5)):
            child_axis = [ax[i] + sign * 0.7 * spread[i] for i in range(3)]
            child = _helix_path(trunk[-1], child_axis, 10, phase=phase)
            parent = branch_id
            for k, p in enumerate(child):
                nid += 1
                radius = 0.7 - 0.3 * k / 9
                rows.append(f"{nid} {node_type} {p[0]:.4f} {p[1]:.4f} {p[2]:.4f} {radius:.4f} {parent}")
                parent = nid
    return rows


class _CheckTestCase(unittest.TestCase):
    """Runs one check at a time through ``run_validation_text``."""

    def _only(self, key: str, params: dict | None = None) -> dict:
        checks = {name: {"enabled": False} for name in load_validation_config()["checks"]}
        rule: dict = {"enabled": True}
        if params is not None:
            rule["params"] = dict(params)
        checks[key] = rule
        return {"checks": checks}

    def _run(self, key: str, rows: list[str], params: dict | None = None):
        report = run_validation_text(_swc(rows), config_overrides=self._only(key, params))
        self.assertEqual([r.key for r in report.results], [key])
        result = report.results[0]
        self.assertFalse(
            result.message.startswith("Validation error"),
            f"{key} errored instead of evaluating: {result.message}",
        )
        return result

    def _assert_pass(self, key: str, rows: list[str], params: dict | None = None):
        result = self._run(key, rows, params)
        self.assertTrue(result.passed, f"{key} unexpectedly failed: {result.message}")
        self.assertEqual(result.failing_node_ids, [])
        return result

    def _assert_fail(
        self,
        key: str,
        rows: list[str],
        expected_ids: list[int] | None = None,
        params: dict | None = None,
    ):
        result = self._run(key, rows, params)
        self.assertFalse(result.passed, f"{key} unexpectedly passed: {result.message}")
        if expected_ids is not None:
            self.assertEqual(sorted(result.failing_node_ids), sorted(expected_ids))
        return result


class NativeCheckRegistryTests(unittest.TestCase):
    def test_native_check_set_is_the_documented_eighteen(self) -> None:
        register_native_checks()
        native = {spec.key for spec in list_checks() if spec.source == "native"}
        self.assertEqual(native, NATIVE_CHECK_KEYS)

    def test_neuron_morphology_check_set_is_the_documented_eleven(self) -> None:
        register_neuron_morphology_checks()
        wrapped = {spec.key for spec in list_checks() if spec.source == "neuron_morphology"}
        self.assertEqual(wrapped, NEURON_MORPHOLOGY_CHECK_KEYS)


class SomaCheckTests(_CheckTestCase):
    def test_valid_soma_format_passes_single_node_soma(self) -> None:
        self._assert_pass("valid_soma_format", SMALL_CELL)

    def test_valid_soma_format_reports_connected_multi_node_soma(self) -> None:
        rows = [
            "1 1 0 0 0 2 -1",
            "2 1 2 0 0 2 1",
            "3 1 4 0 0 2 2",
            "4 3 10 0 0 1 3",
            "5 2 -6 0 0 0.5 1",
        ]
        result = self._assert_fail("valid_soma_format", rows, expected_ids=[1, 2, 3])
        self.assertEqual(result.metrics["complex_soma_group_count"], 1)

    def test_multiple_somas_passes_one_soma(self) -> None:
        self._assert_pass("multiple_somas", SMALL_CELL)

    def test_multiple_somas_passes_connected_soma_group(self) -> None:
        # A connected multi-node soma is consolidated first, so it is one soma.
        rows = [
            "1 1 0 0 0 2 -1",
            "2 1 2 0 0 2 1",
            "3 3 10 0 0 1 2",
        ]
        self._assert_pass("multiple_somas", rows)

    def test_multiple_somas_reports_each_disconnected_soma(self) -> None:
        rows = [
            "1 1 0 0 0 5 -1",
            "2 3 6 0 0 1 1",
            "3 1 100 0 0 5 -1",
            "4 3 106 0 0 1 3",
        ]
        result = self._assert_fail("multiple_somas", rows, expected_ids=[1, 3])
        self.assertTrue(result.metrics["can_split_trees"])

    def test_has_soma_passes_with_soma(self) -> None:
        self._assert_pass("has_soma", SMALL_CELL)

    def test_has_soma_fails_without_type_1_nodes(self) -> None:
        rows = ["1 3 0 0 0 1 -1", "2 3 5 0 0 1 1"]
        result = self._assert_fail("has_soma", rows, expected_ids=[])
        self.assertEqual(result.metrics["soma_count"], 0)


class TypeCheckTests(_CheckTestCase):
    def test_no_invalid_negative_types_passes(self) -> None:
        self._assert_pass("no_invalid_negative_types", SMALL_CELL)

    def test_no_invalid_negative_types_reports_negative_type_nodes(self) -> None:
        rows = list(SMALL_CELL)
        rows[2] = "3 -3 6 0 0 1 1"
        rows[4] = "5 -1 0 6 0 1 1"
        result = self._assert_fail("no_invalid_negative_types", rows, expected_ids=[3, 5])
        self.assertEqual(result.metrics["invalid_negative_types"], [-3, -1])

    def test_has_axon(self) -> None:
        self._assert_pass("has_axon", SMALL_CELL)
        rows = [row for row in SMALL_CELL if row.split()[1] != "2"]
        self._assert_fail("has_axon", rows, expected_ids=[])

    def test_has_basal_dendrite(self) -> None:
        self._assert_pass("has_basal_dendrite", SMALL_CELL)
        rows = ["1 1 0 0 0 5 -1", "2 2 -6 0 0 0.5 1", "3 4 0 6 0 1 1"]
        self._assert_fail("has_basal_dendrite", rows, expected_ids=[])

    def test_has_apical_dendrite(self) -> None:
        self._assert_pass("has_apical_dendrite", SMALL_CELL)
        rows = [row for row in SMALL_CELL if row.split()[1] != "4"]
        self._assert_fail("has_apical_dendrite", rows, expected_ids=[])


class CustomTypeCheckTests(_CheckTestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="swcstudio-custom-types-")
        registry = Path(self._tmpdir.name) / "custom_types.json"
        self._env = patch.dict(os.environ, {"SWCSTUDIO_CUSTOM_TYPES_PATH": str(registry)})
        self._env.start()
        load_custom_type_definitions(force=True)

    def tearDown(self) -> None:
        self._env.stop()
        load_custom_type_definitions(force=True)
        self._tmpdir.cleanup()

    def _rows_with_type_7(self) -> list[str]:
        rows = list(SMALL_CELL)
        rows[3] = "4 7 12 0 0 0.8 3"
        rows.append("6 7 18 0 0 0.6 4")
        return rows

    def test_custom_types_defined_passes_without_custom_types(self) -> None:
        self._assert_pass("custom_types_defined", SMALL_CELL)

    def test_custom_types_defined_reports_undefined_custom_type_nodes(self) -> None:
        result = self._assert_fail("custom_types_defined", self._rows_with_type_7(), expected_ids=[4, 6])
        self.assertEqual(result.metrics["custom_type_ids"], [7])

    def test_custom_types_defined_passes_once_type_is_defined(self) -> None:
        set_custom_type_definition(7, name="glia contact", color="#22aa44")
        self._assert_pass("custom_types_defined", self._rows_with_type_7())


class RadiusCheckTests(_CheckTestCase):
    def test_all_neurite_radii_nonzero_passes(self) -> None:
        self._assert_pass("all_neurite_radii_nonzero", SMALL_CELL)

    def test_all_neurite_radii_nonzero_reports_zero_negative_and_nan(self) -> None:
        rows = list(SMALL_CELL)
        rows[1] = "2 2 -6 0 0 0 1"
        rows[3] = "4 3 12 0 0 -0.5 3"
        rows[4] = "5 4 0 6 0 nan 1"
        self._assert_fail("all_neurite_radii_nonzero", rows, expected_ids=[2, 4, 5])

    def test_all_neurite_radii_nonzero_ignores_soma_radius(self) -> None:
        rows = list(SMALL_CELL)
        rows[0] = "1 1 0 0 0 0 -1"
        self._assert_pass("all_neurite_radii_nonzero", rows)

    def test_radius_upper_bound_passes_below_default_bound(self) -> None:
        self._assert_pass("radius_upper_bound", SMALL_CELL)

    def test_radius_upper_bound_reports_nodes_above_bound(self) -> None:
        rows = list(SMALL_CELL)
        rows[3] = "4 3 12 0 0 25 3"
        self._assert_fail("radius_upper_bound", rows, expected_ids=[4])

    def test_radius_upper_bound_uses_configured_bound(self) -> None:
        result = self._assert_fail(
            "radius_upper_bound", SMALL_CELL, expected_ids=[1], params={"max_radius": 2.0}
        )
        self.assertEqual(result.params_used["max_radius"], 2.0)


class LengthCheckTests(_CheckTestCase):
    def _zero_length_rows(self) -> list[str]:
        rows = list(SMALL_CELL)
        rows[3] = "4 3 6 0 0 0.8 3"  # same point as its parent, node 3
        return rows

    def test_all_section_lengths_nonzero_passes(self) -> None:
        self._assert_pass("all_section_lengths_nonzero", SMALL_CELL)

    def test_all_section_lengths_nonzero_reports_zero_length(self) -> None:
        self._assert_fail("all_section_lengths_nonzero", self._zero_length_rows(), expected_ids=[4])

    def test_all_segment_lengths_nonzero_passes(self) -> None:
        self._assert_pass("all_segment_lengths_nonzero", SMALL_CELL)

    def test_all_segment_lengths_nonzero_reports_zero_length(self) -> None:
        self._assert_fail("all_segment_lengths_nonzero", self._zero_length_rows(), expected_ids=[4])

    # Bug: the parent lookup indexes past the end of the sorted ID array when a
    # parent ID is larger than every node ID (native_checks.py:45-46).
    @unittest.expectedFailure
    def test_segment_lengths_ignore_parent_id_above_max_id(self) -> None:
        rows = list(SMALL_CELL) + ["6 3 30 0 0 0.5 99"]
        self._assert_pass("all_segment_lengths_nonzero", rows)

    # Bug: same out-of-range parent lookup as above (native_checks.py:45-46).
    @unittest.expectedFailure
    def test_section_lengths_ignore_parent_id_above_max_id(self) -> None:
        rows = list(SMALL_CELL) + ["6 3 30 0 0 0.5 99"]
        self._assert_pass("all_section_lengths_nonzero", rows)

    def test_no_extreme_spatial_jump_passes_even_spacing(self) -> None:
        rows = ["1 1 0 0 0 2 -1"] + [f"{k} 3 {k - 1} 0 0 1 {k - 1}" for k in range(2, 22)]
        self._assert_pass("no_extreme_spatial_jump", rows)

    def test_no_extreme_spatial_jump_reports_long_segment(self) -> None:
        rows = ["1 1 0 0 0 2 -1"] + [f"{k} 3 {k - 1} 0 0 1 {k - 1}" for k in range(2, 22)]
        rows[-1] = "21 3 520 0 0 1 20"  # about 500 um from node 20
        result = self._assert_fail("no_extreme_spatial_jump", rows, expected_ids=[21])
        self.assertGreater(result.metrics["max_segment_length_um"], 400.0)


class PointAndTopologyCheckTests(_CheckTestCase):
    def test_no_duplicate_3d_points_passes_when_only_some_axes_match(self) -> None:
        rows = list(SMALL_CELL)
        rows[4] = "5 4 6 0 3 1 1"  # same x and y as node 3, different z
        self._assert_pass("no_duplicate_3d_points", rows)

    def test_no_duplicate_3d_points_reports_every_node_in_group(self) -> None:
        rows = list(SMALL_CELL)
        rows[4] = "5 4 12 0 0 1 1"  # same point as node 4
        self._assert_fail("no_duplicate_3d_points", rows, expected_ids=[4, 5])

    def test_no_dangling_branches_passes(self) -> None:
        self._assert_pass("no_dangling_branches", SMALL_CELL)

    def test_no_dangling_branches_reports_non_soma_roots(self) -> None:
        rows = list(SMALL_CELL) + ["6 3 40 0 0 1 -1", "7 3 45 0 0 1 6"]
        self._assert_fail("no_dangling_branches", rows, expected_ids=[6])

    def test_no_self_loop_passes(self) -> None:
        self._assert_pass("no_self_loop", SMALL_CELL)

    def test_no_self_loop_reports_node_that_is_its_own_parent(self) -> None:
        rows = list(SMALL_CELL)
        rows[3] = "4 3 12 0 0 0.8 4"
        self._assert_fail("no_self_loop", rows, expected_ids=[4])

    def test_parent_id_less_than_child_id_passes(self) -> None:
        self._assert_pass("parent_id_less_than_child_id", SMALL_CELL)

    def test_parent_id_less_than_child_id_reports_out_of_order_nodes(self) -> None:
        rows = [
            "1 1 0 0 0 5 -1",
            "2 3 12 0 0 0.8 3",
            "3 3 6 0 0 1 1",
            "4 2 -6 0 0 0.5 1",
        ]
        self._assert_fail("parent_id_less_than_child_id", rows, expected_ids=[2])

    def test_no_node_id_gaps_passes_continuous_ids(self) -> None:
        self._assert_pass("no_node_id_gaps", SMALL_CELL)

    def test_no_node_id_gaps_reports_first_id_after_each_gap(self) -> None:
        rows = [
            "1 1 0 0 0 5 -1",
            "2 3 6 0 0 1 1",
            "6 3 12 0 0 1 2",
            "7 3 18 0 0 1 6",
            "10 2 -6 0 0 0.5 1",
        ]
        result = self._assert_fail("no_node_id_gaps", rows, expected_ids=[6, 10])
        self.assertEqual(result.metrics["gap_count"], 2)
        self.assertEqual(result.metrics["missing_id_count"], 5)


class FullValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="swcstudio-validation-")
        self.tmp = Path(self._tmpdir.name)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_clean_neuron_passes_every_enabled_check(self) -> None:
        path = self.tmp / "clean.swc"
        path.write_text(_swc(_clean_neuron_rows()), encoding="utf-8")

        report = validate_file(str(path))

        enabled = {
            key
            for key, rule in load_validation_config()["checks"].items()
            if rule.get("enabled", True)
        }
        self.assertEqual({r.key for r in report.results}, enabled)
        for result in report.results:
            self.assertFalse(result.message.startswith("Validation error"), f"{result.key}: {result.message}")
            self.assertTrue(result.passed, f"{result.key} failed on a clean neuron: {result.message}")
            self.assertEqual(result.failing_node_ids, [], result.key)
        self.assertEqual(report.summary()["fail"], 0)

    def test_every_neuron_morphology_check_runs_on_clean_neuron(self) -> None:
        register_neuron_morphology_checks()
        ctx = ValidationContext(_swc(_clean_neuron_rows()))
        self.assertIsNotNone(ctx.get_morphology(), ctx.morphology_error)
        checks_cfg = load_validation_config()["checks"]

        specs = [spec for spec in list_checks() if spec.source == "neuron_morphology"]
        self.assertEqual(len(specs), 11)
        for spec in specs:
            params = dict(checks_cfg.get(spec.key, {}).get("params", {}))
            result = spec.runner(ctx, params)
            self.assertFalse(result.message.startswith("Validation error"), f"{spec.key}: {result.message}")
            self.assertTrue(result.passed, f"{spec.key} failed on a clean neuron: {result.message}")

    def test_neuron_morphology_check_failure_is_reported(self) -> None:
        register_neuron_morphology_checks()
        ctx = ValidationContext(_swc(_clean_neuron_rows(soma_radius=0.0)))
        spec = next(s for s in list_checks() if s.key == "soma_radius_nonzero")
        result = spec.runner(ctx, {"threshold": 0.0})
        self.assertFalse(result.message.startswith("Validation error"), result.message)
        self.assertFalse(result.passed)


if __name__ == "__main__":
    unittest.main()
