"""Wrapped neuron_morphology (NeuroM) checks."""

from __future__ import annotations

import inspect
from typing import Any, Callable

import numpy as np

from neurom.check import CheckResult as NeuroMCheckResult
from neurom.check import morphology_checks as nm_checks
from neurom.core.dataformat import COLS

from swcstudio.core.validation_registry import register_check
from swcstudio.core.validation_results import CheckResult


_REGISTERED = False
_POINT_KEY_DECIMALS = 3
_POINT_MATCH_TOL = 1e-2


def _point_key(point_xyz: np.ndarray | list[float] | tuple[float, ...]) -> tuple[float, float, float]:
    point = np.asarray(point_xyz, dtype=np.float64).reshape(-1)
    return tuple(round(float(v), _POINT_KEY_DECIMALS) for v in point[:3])


def _xyz_lookup(ctx) -> dict[tuple[float, float, float], list[int]]:
    lookup = getattr(ctx, "_neurom_xyz_lookup", None)
    if lookup is not None:
        return lookup

    lookup = {}
    xyz = ctx.xyz
    ids = ctx.ids
    for idx in range(len(ids)):
        lookup.setdefault(_point_key(xyz[idx]), []).append(int(ids[idx]))
    ctx._neurom_xyz_lookup = lookup
    return lookup


def _point_to_node_ids(ctx, point_xyz: np.ndarray | list[float] | tuple[float, ...]) -> list[int]:
    point = np.asarray(point_xyz, dtype=np.float64).reshape(-1)
    if point.size < 3:
        return []

    lookup = _xyz_lookup(ctx)
    matched = list(lookup.get(_point_key(point[:3]), []))
    if matched:
        return sorted({int(v) for v in matched})

    xyz = ctx.xyz
    if xyz.size == 0:
        return []
    distances = np.linalg.norm(xyz - point[:3], axis=1)
    if distances.size == 0:
        return []
    min_distance = float(np.min(distances))
    if not np.isfinite(min_distance) or min_distance > _POINT_MATCH_TOL:
        return []
    near = np.flatnonzero(distances <= min_distance + 1e-12)
    return sorted({int(ctx.ids[i]) for i in near.tolist()})


def _section_points(ctx, section_id: int) -> np.ndarray:
    morph = ctx.get_morphology()
    if morph is None:
        return np.empty((0, 3), dtype=np.float64)
    try:
        section = morph.section(int(section_id))
    except Exception:  # noqa: BLE001
        return np.empty((0, 3), dtype=np.float64)

    points = np.asarray(section.points, dtype=np.float64)
    if points.ndim != 2 or points.shape[0] == 0:
        return np.empty((0, 3), dtype=np.float64)
    return points[:, :3]


def _section_to_node_ids(ctx, section_id: int) -> list[int]:
    node_ids: set[int] = set()
    for point in _section_points(ctx, int(section_id)):
        node_ids.update(_point_to_node_ids(ctx, point))
    return sorted(node_ids)


def _extract_points(value: Any) -> list[np.ndarray]:
    if value is None:
        return []
    arr = np.asarray(value, dtype=np.float64)
    if arr.ndim == 1:
        return [arr]
    if arr.ndim == 2:
        return [row for row in arr]
    return []


def _localize_neurom_info(ctx, info: Any) -> tuple[list[int], list[int], dict[str, Any]]:
    node_ids: set[int] = set()
    section_ids: set[int] = set()
    unresolved_entries: list[str] = []
    entries = list(info) if isinstance(info, (list, tuple, set)) else ([] if info is None else [info])

    for entry in entries:
        if isinstance(entry, (int, np.integer)):
            section_id = int(entry)
            section_ids.add(section_id)
            node_ids.update(_section_to_node_ids(ctx, section_id))
            continue

        if not isinstance(entry, tuple) or not entry:
            unresolved_entries.append(type(entry).__name__)
            continue

        primary = entry[0]
        raw_points = entry[1] if len(entry) > 1 else None
        localized_from_points: set[int] = set()
        for point in _extract_points(raw_points):
            localized_from_points.update(_point_to_node_ids(ctx, point[:3]))

        if isinstance(primary, (int, np.integer)):
            primary_id = int(primary)
            if localized_from_points and primary_id in localized_from_points:
                node_ids.add(primary_id)
            else:
                section_ids.add(primary_id)
            node_ids.update(localized_from_points)
            if not localized_from_points and primary_id in section_ids:
                node_ids.update(_section_to_node_ids(ctx, primary_id))
            continue

        if isinstance(primary, tuple) and primary:
            section_head = primary[0]
            if isinstance(section_head, (int, np.integer)):
                section_id = int(section_head)
                section_ids.add(section_id)
                if localized_from_points:
                    node_ids.update(localized_from_points)
                else:
                    node_ids.update(_section_to_node_ids(ctx, section_id))
                continue

        if localized_from_points:
            node_ids.update(localized_from_points)
        else:
            unresolved_entries.append(type(primary).__name__)

    metrics = {
        "neurom_bad_entry_count": len(entries),
        "localized_node_count": len(node_ids),
        "localized_section_count": len(section_ids),
    }
    if unresolved_entries:
        metrics["unresolved_neurom_entries"] = unresolved_entries
    return sorted(node_ids), sorted(section_ids), metrics


def _call_neurom_check(func: Callable, morph, params: dict[str, Any]) -> Any:
    signature = inspect.signature(func)
    kwargs: dict[str, Any] = {}
    for name in signature.parameters:
        if name in {"morph", "neuron"}:
            continue
        if name in params:
            kwargs[name] = params[name]
        elif name == "neurite_filter":
            kwargs[name] = None
    return func(morph, **kwargs)


def _run_neurom_bool_check(
    *,
    key: str,
    label: str,
    func: Callable,
    ctx,
    params: dict[str, Any],
) -> CheckResult:
    morph = ctx.get_morphology()
    if morph is None:
        return CheckResult.from_pass_fail(
            key=key,
            label=label,
            passed=False,
            severity="error",
            message=f"Unable to build morphology for NeuroM check: {ctx.morphology_error}",
            source="neuron_morphology",
            error=True,
        )

    try:
        raw_result = _call_neurom_check(func, morph, params)
        passed = bool(raw_result)
        info = getattr(raw_result, "info", None)
        failing_node_ids, failing_section_ids, localization_metrics = _localize_neurom_info(ctx, info)
        msg = f"{label}: {'pass' if passed else 'fail'}."
        if not passed and (failing_node_ids or failing_section_ids):
            localized_parts: list[str] = []
            if failing_node_ids:
                localized_parts.append(f"{len(failing_node_ids)} node(s)")
            if failing_section_ids:
                localized_parts.append(f"{len(failing_section_ids)} section(s)")
            msg += f" Localized to {' and '.join(localized_parts)}."
        return CheckResult.from_pass_fail(
            key=key,
            label=label,
            passed=passed,
            severity="error",
            message=msg,
            source="neuron_morphology",
            params_used=dict(params),
            thresholds_used=dict(params),
            failing_node_ids=failing_node_ids,
            failing_section_ids=failing_section_ids,
            metrics=localization_metrics,
        )
    except Exception as e:  # noqa: BLE001
        return CheckResult.from_pass_fail(
            key=key,
            label=label,
            passed=False,
            severity="error",
            message=f"NeuroM check error: {e}",
            source="neuron_morphology",
            params_used=dict(params),
            thresholds_used=dict(params),
            error=True,
        )


def has_no_narrow_start(morph, frac=0.9):
    """NeuroM's ``has_no_narrow_start``, for neurites that branch at once.

    A neurite fails when its first point's radius is below ``frac`` times
    the radius of the point after it. NeuroM 4.0.5 reads that point as
    ``root_node.points[1]``, which does not exist when the neurite's first
    node is already a branch point (its root section holds one point), and
    the whole check then errors. Here the point after it is the next point
    on each child section instead, and the neurite fails if its first point
    is narrow against any of them. A neurite of one node has nothing to
    compare and passes. Bad entries match NeuroM's:
    ``(root section id, the next point as a 1x4 array)``.
    """
    bad = []
    for neurite in morph.neurites:
        root = neurite.root_node
        points = np.asarray(root.points)
        if points.shape[0] == 0:
            continue
        first_radius = points[0][COLS.R]
        if points.shape[0] >= 2:
            next_points = [points[1]]
        else:
            next_points = []
            for child in root.children:
                child_points = np.asarray(child.points)
                # MorphIO starts a child section with a copy of the branch
                # point; the next real point follows it.
                start = 1 if child_points.shape[0] and np.allclose(child_points[0][:3], points[0][:3]) else 0
                if child_points.shape[0] > start:
                    next_points.append(child_points[start])
        for next_point in next_points:
            if first_radius < frac * next_point[COLS.R]:
                bad.append((root.id, np.asarray(next_point)[np.newaxis, :]))
                break
    return NeuroMCheckResult(len(bad) == 0, bad)


# Checks evaluated here instead of by NeuroM, because NeuroM's version errors
# on common real files.
_LOCAL_CHECK_FUNCS: dict[str, Callable] = {
    "has_no_narrow_start": has_no_narrow_start,
}


def _wrapper(key: str, label: str, nm_name: str):
    func = _LOCAL_CHECK_FUNCS.get(nm_name) or getattr(nm_checks, nm_name)

    def _run(ctx, params: dict[str, Any]) -> CheckResult:
        return _run_neurom_bool_check(key=key, label=label, func=func, ctx=ctx, params=params)

    return _run


def register_neuron_morphology_checks() -> None:
    global _REGISTERED
    if _REGISTERED:
        return

    mapping = {
        "has_multifurcation": ("Contains multifurcation", "has_multifurcation"),
        "no_back_tracking": ("No geometric backtracking", "has_no_back_tracking"),
        "no_fat_terminal_ends": ("No oversized terminal ends", "has_no_fat_ends"),
        "no_flat_neurites": ("No flattened neurites", "has_no_flat_neurites"),
        "no_section_index_jumps": ("No large section z-axis jumps", "has_no_jumps"),
        "no_ultranarrow_sections": ("No extremely narrow sections", "has_no_narrow_neurite_section"),
        "no_ultranarrow_starts": ("No extremely narrow branch starts", "has_no_narrow_start"),
        "no_root_index_jumps": ("Neurite roots too far from soma", "has_no_root_node_jumps"),
        "no_single_child_chains": ("No single-child chains", "has_no_single_children"),
        "soma_radius_nonzero": ("Soma radius is positive", "has_nonzero_soma_radius"),
        "has_unifurcation": ("Contains unifurcation", "has_unifurcation"),
    }

    for key, (label, nm_name) in mapping.items():
        register_check(
            key=key,
            label=label,
            source="neuron_morphology",
            runner=_wrapper(key, label, nm_name),
        )

    _REGISTERED = True
