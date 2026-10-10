"""Unit tests for radii cleaning (swcstudio.core.radii_cleaning).

Runs through ``clean_swc_text``, the shared backend the CLI and GUI call.
"""
from __future__ import annotations

import math
import unittest

from swcstudio.core.radii_cleaning import clean_radii_dataframe
from swcstudio.core.swc_io import parse_swc_text_preserve_tokens
from swcstudio.tools.batch_processing.features.radii_cleaning import clean_swc_text


MIN_EFFECTIVE_DELTA = 0.005
TOPOLOGY_COLS = ["id", "type", "x", "y", "z", "parent"]


def _linear_radius(k: int, n: int, r0: float = 2.0, r1: float = 1.0) -> float:
    return r0 + (r1 - r0) * k / (n - 1)


def _tapered_path(
    n: int = 30,
    *,
    node_type: int = 3,
    soma_radius: float = 6.5,
    overrides: dict[int, str] | None = None,
) -> str:
    """A soma and one unbranched neurite whose radius tapers from 2.0 to 1.0.

    ``overrides`` maps node ID to the radius text to write for that node.
    """
    overrides = dict(overrides or {})
    rows = [f"1 1 0 0 0 {soma_radius:g} -1"]
    for k in range(n):
        nid = k + 2
        radius = overrides.get(nid, f"{_linear_radius(k, n):.6g}")
        rows.append(f"{nid} {node_type} {k + 5} {0.1 * k:.1f} 0 {radius} {nid - 1}")
    return "\n".join(rows) + "\n"


def _radius_of(df, node_id: int) -> float:
    return float(df.loc[df["id"] == node_id, "radius"].iloc[0])


class RadiiCleaningTests(unittest.TestCase):
    def _clean(self, text: str):
        before = parse_swc_text_preserve_tokens(text)
        out = clean_swc_text(text)
        return before, out

    def _assert_topology_unchanged(self, before, after) -> None:
        self.assertEqual(len(after), len(before))
        for col in TOPOLOGY_COLS:
            self.assertEqual(
                after[col].tolist(),
                before[col].tolist(),
                f"radii cleaning changed column {col!r}",
            )

    def _assert_changes_have_reasons(self, before, out) -> None:
        details = {int(d["node_id"]): d for d in out["change_details"]}
        self.assertEqual(out["changes"], len(details))
        for node_id, detail in details.items():
            self.assertTrue(detail["reasons"], f"node {node_id} changed without a reason")
        after = out["dataframe"]
        for old, new, node_id in zip(before["radius"], after["radius"], before["id"]):
            old, new = float(old), float(new)
            if math.isfinite(old) and abs(old - new) <= MIN_EFFECTIVE_DELTA:
                continue
            self.assertIn(int(node_id), details, f"node {int(node_id)} changed but was not reported")

    def test_spike_on_tapered_path_is_replaced_near_local_median(self) -> None:
        n = 30
        spike_id = 16
        before, out = self._clean(_tapered_path(n, overrides={spike_id: "6.0"}))

        expected = _linear_radius(spike_id - 2, n)
        new_radius = _radius_of(out["dataframe"], spike_id)
        self.assertAlmostEqual(new_radius, expected, delta=0.05)
        changed_ids = [d["node_id"] for d in out["change_details"]]
        self.assertEqual(changed_ids, [spike_id])
        self.assertIn("local_outlier", out["change_details"][0]["reasons"])
        self._assert_topology_unchanged(before, out["dataframe"])
        self._assert_changes_have_reasons(before, out)

    def test_non_finite_and_non_positive_radii_are_fixed(self) -> None:
        bad = {10: "nan", 12: "0", 20: "-1.0", 25: "inf"}
        before, out = self._clean(_tapered_path(overrides=bad))
        after = out["dataframe"]

        reasons = {d["node_id"]: set(d["reasons"]) for d in out["change_details"]}
        for node_id in bad:
            radius = _radius_of(after, node_id)
            self.assertTrue(math.isfinite(radius) and radius > 0.0, f"node {node_id} -> {radius}")
            # The replacement should sit in the range of its tapered neighbours.
            self.assertGreaterEqual(radius, 1.0 - 0.05)
            self.assertLessEqual(radius, 2.0 + 0.05)
        self.assertIn("non_finite", reasons[10])
        self.assertIn("non_positive", reasons[12])
        self.assertIn("non_positive", reasons[20])
        self.assertIn("non_finite", reasons[25])
        self._assert_topology_unchanged(before, after)
        self._assert_changes_have_reasons(before, out)

    def test_every_radius_is_positive_and_finite_after_cleaning(self) -> None:
        bad = {3: "0", 4: "0", 5: "0", 30: "nan", 31: "-2"}
        _, out = self._clean(_tapered_path(overrides=bad))
        for value in out["dataframe"]["radius"].tolist():
            self.assertTrue(math.isfinite(float(value)) and float(value) > 0.0)

    def test_soma_radii_are_never_changed(self) -> None:
        rows = [
            "1 1 0 0 0 0.5 -1",
            "2 1 1 0 0 40 1",
            "3 1 2 0 0 0 2",
            "4 1 3 0 0 -3 3",
        ]
        rows += [f"{k} 3 {k} 0 0 1.0 {k - 1}" for k in range(5, 25)]
        text = "\n".join(rows) + "\n"
        before, out = self._clean(text)
        after = out["dataframe"]
        for node_id, expected in ((1, 0.5), (2, 40.0), (3, 0.0), (4, -3.0)):
            self.assertEqual(_radius_of(after, node_id), expected)
        self.assertNotIn(1, [d["node_id"] for d in out["change_details"]])
        self.assertNotIn(2, [d["node_id"] for d in out["change_details"]])
        self._assert_topology_unchanged(before, after)

    # NaN != NaN: an untouched NaN soma radius must not be listed as a change.
    def test_untouched_nan_soma_radius_is_not_reported_as_change(self) -> None:
        rows = ["1 1 0 0 0 5 -1", "2 1 1 0 0 nan 1"]
        rows += [f"{k} 3 {k} 0 0 1.0 {k - 1}" for k in range(3, 23)]
        before, out = self._clean("\n".join(rows) + "\n")
        self.assertTrue(math.isnan(_radius_of(out["dataframe"], 2)))
        self.assertEqual(out["change_details"], [])
        self.assertEqual(out["changes"], 0)

    def test_clean_file_is_left_nearly_unchanged(self) -> None:
        before, out = self._clean(_tapered_path())
        self.assertEqual(out["changes"], 0)
        self.assertEqual(out["change_details"], [])
        for old, new in zip(before["radius"], out["dataframe"]["radius"]):
            self.assertAlmostEqual(float(new), float(old), delta=0.01)
        self._assert_topology_unchanged(before, out["dataframe"])

    def test_constant_radius_file_is_byte_identical(self) -> None:
        rows = ["# header kept", "1 1 0 0 0 5 -1"]
        rows += [f"{k} 3 {k}.5 0 0 1.25 {k - 1}" for k in range(2, 22)]
        text = "\n".join(rows) + "\n"
        out = clean_swc_text(text)
        self.assertEqual(out["changes"], 0)
        self.assertEqual(out["bytes"].decode("utf-8"), text)

    def test_branched_tree_keeps_topology_and_reports_reasons(self) -> None:
        rows = ["1 1 0 0 0 5 -1"]
        nid = 1
        for branch, (dx, dy, node_type) in enumerate(((1, 0, 3), (-1, 0, 2), (0, 1, 4))):
            parent = 1
            for k in range(15):
                nid += 1
                radius = 1.5 - 0.04 * k
                if k == 7:
                    radius = 9.0  # one spike per branch
                rows.append(f"{nid} {node_type} {dx * (k + 6)} {dy * (k + 6)} {branch} {radius:g} {parent}")
                parent = nid
        before, out = self._clean("\n".join(rows) + "\n")
        after = out["dataframe"]
        for spike_id in (9, 24, 39):
            self.assertLess(_radius_of(after, spike_id), 2.0)
        self._assert_topology_unchanged(before, after)
        self._assert_changes_have_reasons(before, out)

    def test_dataframe_api_matches_text_api(self) -> None:
        text = _tapered_path(overrides={16: "6.0"})
        df = parse_swc_text_preserve_tokens(text)
        direct = clean_radii_dataframe(df)
        via_text = clean_swc_text(text)
        self.assertEqual(direct["change_details"], via_text["change_details"])
        self.assertEqual(direct["dataframe"]["radius"].tolist(), via_text["dataframe"]["radius"].tolist())


if __name__ == "__main__":
    unittest.main()
