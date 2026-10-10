"""Shared validation engine."""

from __future__ import annotations

import io
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

import morphio
import numpy as np
from neurom.core import Morphology

from swcstudio.core.config import merge_config
from swcstudio.core.validation_catalog import CHECK_ORDER, display_label_for_result
from swcstudio.core.validation_registry import get_check
from swcstudio.core.validation_results import CheckResult, PreCheckItem, ValidationReport


_SWCTYPE = np.dtype(
    [
        ("id", np.int64),
        ("type", np.int64),
        ("x", np.float64),
        ("y", np.float64),
        ("z", np.float64),
        ("radius", np.float64),
        ("parent", np.int64),
    ]
)

_CFG_DIR = Path(__file__).resolve().parents[1] / "tools" / "validation" / "configs"
_ANSI_RE = re.compile(r"\x1B\[[0-9;]*[A-Za-z]")


class ValidationContext:
    def __init__(self, swc_text: str):
        self.original_swc_text = swc_text
        self.original_arr = _load_swc_to_array(swc_text)
        prepared = build_validation_working_copy_from_array(self.original_arr)
        self.arr = prepared["array"]
        self.swc_text = prepared["swc_text"]
        self.soma_consolidation = dict(prepared.get("soma_consolidation", {}))
        self._morph: Morphology | None = None
        self._morph_error: str | None = None
        self._raw = None

    @property
    def ids(self) -> np.ndarray:
        return self.arr["id"] if self.arr.size else np.array([], dtype=np.int64)

    @property
    def types(self) -> np.ndarray:
        return self.arr["type"] if self.arr.size else np.array([], dtype=np.int64)

    @property
    def parents(self) -> np.ndarray:
        return self.arr["parent"] if self.arr.size else np.array([], dtype=np.int64)

    @property
    def xyz(self) -> np.ndarray:
        if self.arr.size == 0:
            return np.empty((0, 3), dtype=np.float64)
        return np.column_stack((self.arr["x"], self.arr["y"], self.arr["z"])).astype(np.float64)

    @property
    def radii(self) -> np.ndarray:
        return self.arr["radius"] if self.arr.size else np.array([], dtype=np.float64)

    def id_to_index(self) -> dict[int, int]:
        return {int(self.ids[i]): i for i in range(len(self.ids))}

    def children_map(self) -> dict[int, list[int]]:
        cmap: dict[int, list[int]] = {}
        for i in range(len(self.ids)):
            pid = int(self.parents[i])
            if pid >= 0:
                cmap.setdefault(pid, []).append(int(self.ids[i]))
        return cmap

    def get_morphology(self) -> Morphology | None:
        if self._morph is not None:
            return self._morph
        if self._morph_error is not None:
            return None

        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".swc")
        os.close(tmp_fd)
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(self.swc_text)
            raw = morphio.Morphology(
                tmp_path,
                options=morphio.Option.allow_unifurcated_section_change,
            )
            self._raw = raw
            self._morph = Morphology(raw)
            return self._morph
        except Exception as e:  # noqa: BLE001
            self._morph_error = _strip_ansi(str(e))
            return None
        finally:
            try:
                os.remove(tmp_path)
            except FileNotFoundError:
                pass

    @property
    def morphology_error(self) -> str | None:
        return self._morph_error


def _load_swc_to_array(swc_text: str) -> np.ndarray:
    buf = io.StringIO(swc_text)
    arr = np.genfromtxt(
        buf,
        comments="#",
        dtype=_SWCTYPE,
        invalid_raise=False,
        autostrip=True,
    )
    if arr.size == 0:
        return np.array([], dtype=_SWCTYPE)
    if arr.ndim == 0:
        arr = arr.reshape(1)
    return arr


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text or "")


def _array_to_swc_text(arr: np.ndarray) -> str:
    if arr.size == 0:
        return "# id type x y z radius parent\n"
    buf = io.StringIO()
    stacked = np.column_stack(
        [
            arr["id"],
            arr["type"],
            arr["x"],
            arr["y"],
            arr["z"],
            arr["radius"],
            arr["parent"],
        ]
    )
    np.savetxt(
        buf,
        stacked,
        fmt=["%d", "%d", "%.10g", "%.10g", "%.10g", "%.10g", "%d"],
        delimiter=" ",
    )
    return "# id type x y z radius parent\n" + buf.getvalue()


def consolidate_complex_somas_array(arr: np.ndarray) -> dict[str, Any]:
    """Collapse connected soma groups without renumbering surviving node IDs.

    Each connected type-1 soma component is reduced to one anchor soma node with
    updated centroid/radius. Non-anchor soma nodes are removed, and any child that
    pointed to a removed soma node is rewired to the surviving anchor ID. Surviving
    node IDs are preserved; no global reindexing is performed here.
    """
    out = np.array(arr, copy=True)
    if out.size == 0:
        return {
            "array": out,
            "soma_count_before": 0,
            "soma_count_after": 0,
            "group_count": 0,
            "groups": [],
            "complex_groups": [],
            "anchor_map": {},
            "changed": False,
        }

    ids = np.asarray(out["id"], dtype=np.int64)
    types = np.asarray(out["type"], dtype=np.int64)
    parents = np.asarray(out["parent"], dtype=np.int64)
    xyz = np.column_stack((out["x"], out["y"], out["z"])).astype(np.float64)
    radii = np.asarray(out["radius"], dtype=np.float64)

    soma_idx = np.flatnonzero(types == 1)
    if soma_idx.size == 0:
        return {
            "array": out,
            "soma_count_before": 0,
            "soma_count_after": 0,
            "group_count": 0,
            "groups": [],
            "complex_groups": [],
            "anchor_map": {},
            "changed": False,
        }

    id_to_index = {int(ids[i]): int(i) for i in range(len(ids))}
    children: list[list[int]] = [[] for _ in range(len(ids))]
    for i, pid in enumerate(parents):
        pidx = id_to_index.get(int(pid))
        if pidx is not None:
            children[pidx].append(i)

    soma_index_set = {int(i) for i in soma_idx.tolist()}
    visited: set[int] = set()
    groups: list[list[int]] = []
    for start in soma_idx.tolist():
        start_i = int(start)
        if start_i in visited:
            continue
        stack = [start_i]
        component: list[int] = []
        visited.add(start_i)
        while stack:
            idx = stack.pop()
            component.append(idx)
            parent_idx = id_to_index.get(int(parents[idx]))
            if parent_idx is not None and parent_idx in soma_index_set and parent_idx not in visited:
                visited.add(parent_idx)
                stack.append(parent_idx)
            for child_idx in children[idx]:
                if child_idx in soma_index_set and child_idx not in visited:
                    visited.add(child_idx)
                    stack.append(child_idx)
        groups.append(sorted(component))

    keep_mask = np.ones(len(out), dtype=bool)
    anchor_map: dict[int, int] = {}
    group_infos: list[dict[str, Any]] = []

    for group in groups:
        group_ids = [int(ids[i]) for i in group]
        anchor_idx = next((i for i in group if int(parents[i]) == -1), group[0])
        anchor_id = int(ids[anchor_idx])
        group_xyz = xyz[group]
        centroid = np.mean(group_xyz, axis=0) if len(group) else np.zeros(3, dtype=np.float64)
        distances = np.linalg.norm(group_xyz - centroid, axis=1) if len(group) else np.zeros(0, dtype=np.float64)
        if distances.size:
            furthest_pos = int(np.argmax(distances))
            furthest_idx = group[furthest_pos]
            mega_radius = float(distances[furthest_pos] + max(float(radii[furthest_idx]), 0.0))
        else:
            furthest_idx = anchor_idx
            mega_radius = float(max(float(radii[anchor_idx]), 0.0))

        out["type"][anchor_idx] = 1
        out["x"][anchor_idx] = float(centroid[0])
        out["y"][anchor_idx] = float(centroid[1])
        out["z"][anchor_idx] = float(centroid[2])
        out["radius"][anchor_idx] = float(mega_radius)
        out["parent"][anchor_idx] = -1

        for idx in group:
            anchor_map[int(ids[idx])] = anchor_id
            if idx != anchor_idx:
                keep_mask[idx] = False

        group_infos.append(
            {
                "anchor_id": anchor_id,
                "node_ids": group_ids,
                "group_size": len(group),
                "centroid": [float(centroid[0]), float(centroid[1]), float(centroid[2])],
                "radius": float(mega_radius),
                "furthest_node_id": int(ids[furthest_idx]),
            }
        )

    for i in range(len(out)):
        if not keep_mask[i]:
            continue
        if int(out["type"][i]) == 1:
            continue
        parent_id = int(out["parent"][i])
        if parent_id in anchor_map:
            out["parent"][i] = int(anchor_map[parent_id])

    final_arr = np.array(out[keep_mask], copy=True)
    # Compatibility field retained for callers that expect a mapping payload.
    # Soma consolidation preserves surviving IDs, so there is intentionally no
    # automatic remap here.
    reindex_map: dict[int, int] = {}
    complex_groups = [group for group in group_infos if int(group.get("group_size", 0)) > 1]
    return {
        "array": final_arr,
        "soma_count_before": int(soma_idx.size),
        "soma_count_after": int(np.sum(final_arr["type"] == 1)),
        "group_count": len(group_infos),
        "groups": group_infos,
        "complex_groups": complex_groups,
        "anchor_map": anchor_map,
        "reindex_map": reindex_map,
        "changed": bool(complex_groups),
    }


def build_validation_working_copy_from_array(arr: np.ndarray) -> dict[str, Any]:
    working_arr = np.array(arr, copy=True)
    soma_consolidation = consolidate_complex_somas_array(working_arr)
    final_arr = np.array(soma_consolidation.get("array", working_arr), copy=True)
    return {
        "array": final_arr,
        "swc_text": _array_to_swc_text(final_arr),
        "soma_consolidation": soma_consolidation,
    }


def _ensure_builtin_checks_registered() -> None:
    # Local import keeps startup cost low and avoids circular imports.
    from swcstudio.core.validation_checks.native_checks import register_native_checks
    from swcstudio.core.validation_checks.neuron_morphology_checks import (
        register_neuron_morphology_checks,
    )

    register_native_checks()
    register_neuron_morphology_checks()


def load_validation_config(profile: str = "default", overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    # Single validation profile: always use default.json.
    _ = profile
    p = _CFG_DIR / "default.json"
    if p.exists():
        base = json.loads(p.read_text(encoding="utf-8"))
    else:
        base = {"checks": {}}
    merged = merge_config(base, overrides)
    merged["profile"] = "default"
    return merged


def build_precheck_summary(config: dict[str, Any]) -> list[PreCheckItem]:
    _ensure_builtin_checks_registered()
    checks_cfg = config.get("checks", {})
    out: list[PreCheckItem] = []
    ordered_keys = sorted(checks_cfg.keys(), key=lambda key: (CHECK_ORDER.get(str(key), 10_000), str(key)))
    for key in ordered_keys:
        rule = checks_cfg.get(key, {})
        if not bool(rule.get("enabled", True)):
            continue
        spec = get_check(key)
        if spec is None:
            out.append(
                PreCheckItem(
                    key=key,
                    label=key,
                    source="missing",
                    severity=str(rule.get("severity", "error")),
                    params=dict(rule.get("params", {})),
                    enabled=True,
                )
            )
            continue
        out.append(
            PreCheckItem(
                key=spec.key,
                label=spec.label,
                source=spec.source,
                severity=str(rule.get("severity", "error")),
                params=dict(rule.get("params", {})),
                enabled=True,
            )
        )
    return out


def run_validation_text(
    swc_text: str,
    *,
    profile: str = "default",
    config_overrides: dict[str, Any] | None = None,
) -> ValidationReport:
    _ensure_builtin_checks_registered()

    cfg = load_validation_config(profile=profile, overrides=config_overrides)
    precheck = build_precheck_summary(cfg)
    ctx = ValidationContext(swc_text)

    results: list[CheckResult] = []
    for item in precheck:
        spec = get_check(item.key)
        if spec is None:
            results.append(
                CheckResult.from_pass_fail(
                    key=item.key,
                    label=item.label,
                    passed=False,
                    severity=item.severity,
                    message="Check is enabled in config but not registered.",
                    source=item.source,
                    params_used=item.params,
                    thresholds_used=item.params,
                    error=True,
                )
            )
            continue

        try:
            result = spec.runner(ctx, item.params)
            result.key = item.key
            result.label = item.label
            result.source = spec.source
            result.severity = item.severity
            merged_params = dict(result.params_used or {})
            merged_params.update(dict(item.params))
            result.params_used = merged_params
            merged_thresholds = dict(result.thresholds_used or {})
            if not merged_thresholds:
                merged_thresholds = dict(merged_params)
            else:
                merged_thresholds.update(dict(item.params))
            result.thresholds_used = merged_thresholds
            result.message = _strip_ansi(str(result.message))
            if result.passed:
                result.status = "pass"
            elif item.severity.lower() == "warning":
                result.status = "warning"
            elif item.severity.lower() == "info":
                result.status = "info"
            else:
                result.status = "fail"
            result.label = display_label_for_result(item.key, bool(result.passed), item.label)
            results.append(result)
            # A complex soma is only a warning: every check already runs on
            # the working copy with each soma group merged (ValidationContext),
            # so keep going. Several separate somas mean several cells, and
            # checks over the combined file would mislead, so stop there.
            if item.key == "multiple_somas" and not bool(result.passed):
                break
        except Exception as e:  # noqa: BLE001
            results.append(
                CheckResult.from_pass_fail(
                    key=item.key,
                    label=item.label,
                    passed=False,
                    severity=item.severity,
                    message=f"Check raised exception: {e}",
                    source=spec.source,
                    params_used=item.params,
                    thresholds_used=item.params,
                    error=True,
                )
            )
            if item.key in {"valid_soma_format", "multiple_somas"}:
                break

    return ValidationReport(profile="default", precheck=precheck, results=results)
