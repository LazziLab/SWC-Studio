"""Native validation checks."""

from __future__ import annotations

from typing import Any

import numpy as np

from swcstudio.core.custom_types import load_custom_type_definitions
from swcstudio.core.geometry_editing import format_id_list, nodes_cut_off_by_cycles, parent_cycles
from swcstudio.core.validation_registry import register_check
from swcstudio.core.validation_results import CheckResult


_REGISTERED = False


def _original_ids_parents(ctx) -> tuple[np.ndarray, np.ndarray]:
    """The file's own node IDs and parent IDs, before soma groups are merged.

    The working copy (``ctx.ids``) drops the IDs of merged soma nodes and
    re-roots each soma, so checks about IDs, ordering and parent links read
    the original rows instead.
    """
    arr = getattr(ctx, "original_arr", None)
    if arr is None:
        return np.asarray(ctx.ids, dtype=np.int64), np.asarray(ctx.parents, dtype=np.int64)
    if arr.size == 0:
        empty = np.array([], dtype=np.int64)
        return empty, empty
    return np.asarray(arr["id"], dtype=np.int64), np.asarray(arr["parent"], dtype=np.int64)


def _native_cache(ctx) -> dict[str, Any]:
    cache = getattr(ctx, "_native_cache", None)
    if cache is None:
        cache = {}
        setattr(ctx, "_native_cache", cache)
    return cache


def _segment_length_stats(ctx) -> dict[str, Any]:
    cache = _native_cache(ctx)
    key = "segment_length_stats"
    if key in cache:
        return cache[key]

    ids = np.asarray(ctx.ids, dtype=np.int64)
    parents = np.asarray(ctx.parents, dtype=np.int64)
    xyz = np.asarray(ctx.xyz, dtype=np.float64)

    child_mask = parents >= 0
    child_idx = np.flatnonzero(child_mask)
    if child_idx.size == 0:
        out = {"segment_count": 0, "invalid_ids": []}
        cache[key] = out
        return out

    parent_ids = parents[child_idx]
    sort_idx = np.argsort(ids)
    ids_sorted = ids[sort_idx]
    pos = np.searchsorted(ids_sorted, parent_ids)
    # A parent id above every node id gives pos == size; clamp before
    # indexing, since & does not short-circuit.
    found = ids_sorted[np.minimum(pos, ids_sorted.size - 1)] == parent_ids
    valid = (pos < ids_sorted.size) & found
    child_idx = child_idx[valid]
    if child_idx.size == 0:
        out = {"segment_count": 0, "invalid_ids": []}
        cache[key] = out
        return out

    parent_idx = sort_idx[pos[valid]]
    lengths = np.linalg.norm(xyz[child_idx] - xyz[parent_idx], axis=1)
    bad_mask = (~np.isfinite(lengths)) | (lengths <= 0.0)
    bad_ids = ids[child_idx[bad_mask]].astype(np.int64).tolist()
    out = {
        "segment_count": int(lengths.size),
        "invalid_ids": bad_ids,
    }
    cache[key] = out
    return out


def _check_has_soma(ctx, params: dict[str, Any]) -> CheckResult:
    _ = params
    ids = np.asarray(ctx.ids, dtype=np.int64)
    soma_mask = np.asarray(ctx.types, dtype=np.int64) == 1
    soma_ids = ids[soma_mask].astype(np.int64).tolist()
    passed = len(soma_ids) > 0
    msg = "Soma present." if passed else "No soma node found."
    return CheckResult.from_pass_fail(
        key="has_soma",
        label="Soma present",
        passed=passed,
        severity="warning",
        message=msg,
        source="native",
        failing_node_ids=[] if passed else list(soma_ids),
        metrics={"soma_count": len(soma_ids)},
    )


def _check_valid_soma_format(ctx, params: dict[str, Any]) -> CheckResult:
    _ = params
    meta = dict(getattr(ctx, "soma_consolidation", {}) or {})
    complex_groups = list(meta.get("complex_groups", []) or [])
    bad_ids: list[int] = []
    for group in complex_groups:
        for node_id in group.get("node_ids", []) or []:
            try:
                bad_ids.append(int(node_id))
            except Exception:
                continue
    passed = len(complex_groups) == 0
    if passed:
        msg = "Soma format is a single-node representation or already simple."
    else:
        msg = f"Found {len(complex_groups)} connected multi-node soma group(s)."
    return CheckResult.from_pass_fail(
        key="valid_soma_format",
        label="Soma format is simple",
        passed=passed,
        severity="warning",
        message=msg,
        source="native",
        failing_node_ids=bad_ids,
        metrics={
            "complex_soma_group_count": len(complex_groups),
            "complex_soma_node_count": len(bad_ids),
            "soma_count_before": int(meta.get("soma_count_before", 0)),
            "soma_count_after": int(meta.get("soma_count_after", 0)),
            "complex_groups": complex_groups,
        },
    )


def _check_multiple_somas(ctx, params: dict[str, Any]) -> CheckResult:
    _ = params
    ids = np.asarray(ctx.ids, dtype=np.int64)
    soma_mask = np.asarray(ctx.types, dtype=np.int64) == 1
    soma_ids = ids[soma_mask].astype(np.int64).tolist()
    passed = len(soma_ids) <= 1
    if passed:
        msg = "At most one connected soma remains after soma consolidation."
    else:
        msg = (
            f"Found {len(soma_ids)} disconnected soma groups after soma consolidation. "
            "This likely means multiple cells are present in the same SWC file."
        )
    return CheckResult.from_pass_fail(
        key="multiple_somas",
        label="Only one connected soma group remains",
        passed=passed,
        severity="error",
        message=msg,
        source="native",
        failing_node_ids=[] if passed else soma_ids,
        metrics={
            "multiple_soma_count": len(soma_ids),
            "can_split_trees": bool(not passed),
            "soma_ids_after_consolidation": soma_ids,
        },
    )


def _check_has_axon(ctx, params: dict[str, Any]) -> CheckResult:
    _ = params
    count = int(np.sum(ctx.types == 2)) if len(ctx.types) else 0
    passed = count > 0
    return CheckResult.from_pass_fail(
        key="has_axon",
        label="Axon present",
        passed=passed,
        severity="warning",
        message="Axon present." if passed else "No axon node found.",
        source="native",
        metrics={"axon_node_count": count},
    )


def _check_has_basal_dendrite(ctx, params: dict[str, Any]) -> CheckResult:
    _ = params
    count = int(np.sum(ctx.types == 3)) if len(ctx.types) else 0
    passed = count > 0
    return CheckResult.from_pass_fail(
        key="has_basal_dendrite",
        label="Basal dendrite present",
        passed=passed,
        severity="warning",
        message="Basal dendrite present." if passed else "No basal dendrite node found.",
        source="native",
        metrics={"basal_node_count": count},
    )


def _check_has_apical_dendrite(ctx, params: dict[str, Any]) -> CheckResult:
    _ = params
    count = int(np.sum(ctx.types == 4)) if len(ctx.types) else 0
    passed = count > 0
    return CheckResult.from_pass_fail(
        key="has_apical_dendrite",
        label="Apical dendrite present",
        passed=passed,
        severity="warning",
        message="Apical dendrite present." if passed else "No apical dendrite node found.",
        source="native",
        metrics={"apical_node_count": count},
    )


def _check_no_invalid_negative_types(ctx, params: dict[str, Any]) -> CheckResult:
    _ = params
    ids = np.asarray(ctx.ids, dtype=np.int64)
    types = np.asarray(ctx.types, dtype=np.int64)
    bad_mask = types < 0
    bad_ids = ids[bad_mask].astype(np.int64).tolist()
    bad_types = sorted({int(v) for v in types[bad_mask].astype(np.int64).tolist()})
    passed = len(bad_ids) == 0
    msg = (
        "No invalid negative node types."
        if passed
        else f"Found {len(bad_ids)} node(s) with invalid type values below 0: {', '.join(str(v) for v in bad_types)}."
    )
    return CheckResult.from_pass_fail(
        key="no_invalid_negative_types",
        label="No invalid negative node types",
        passed=passed,
        severity="error",
        message=msg,
        source="native",
        failing_node_ids=bad_ids,
        metrics={
            "invalid_negative_type_count": len(bad_ids),
            "invalid_negative_types": bad_types,
        },
    )


def _check_custom_types_defined(ctx, params: dict[str, Any]) -> CheckResult:
    _ = params
    ids = np.asarray(ctx.ids, dtype=np.int64)
    types = np.asarray(ctx.types, dtype=np.int64)
    custom_mask = types >= 5
    custom_type_ids = sorted({int(v) for v in types[custom_mask].astype(np.int64).tolist()})
    definitions = load_custom_type_definitions()
    missing_defs: list[dict[str, Any]] = []
    bad_ids: list[int] = []
    for type_id in custom_type_ids:
        definition = definitions.get(int(type_id)) or {}
        name = str(definition.get("name", "")).strip()
        color = str(definition.get("color", "")).strip()
        if name and color:
            continue
        node_ids = ids[types == type_id].astype(np.int64).tolist()
        bad_ids.extend(node_ids)
        missing_defs.append(
            {
                "type_id": int(type_id),
                "node_count": len(node_ids),
                "node_ids_sample": node_ids[:25],
            }
        )
    passed = len(missing_defs) == 0
    msg = (
        "All custom node types are defined."
        if passed
        else f"Found {len(missing_defs)} custom type ID(s) >= 5 without a defined name/color."
    )
    return CheckResult.from_pass_fail(
        key="custom_types_defined",
        label="Custom node types are defined",
        passed=passed,
        severity="warning",
        message=msg,
        source="native",
        failing_node_ids=bad_ids,
        metrics={
            "custom_type_count": len(custom_type_ids),
            "custom_type_ids": custom_type_ids,
            "undefined_custom_types": missing_defs,
        },
    )


def _check_all_neurite_radii_nonzero(ctx, params: dict[str, Any]) -> CheckResult:
    _ = params
    ids = np.asarray(ctx.ids, dtype=np.int64)
    types = np.asarray(ctx.types, dtype=np.int64)
    radii = np.asarray(ctx.radii, dtype=np.float64)
    bad_mask = (types != 1) & ((~np.isfinite(radii)) | (radii <= 0.0))
    bad_ids = ids[bad_mask].astype(np.int64).tolist()
    passed = len(bad_ids) == 0
    msg = "All neurite radii are positive." if passed else f"Found {len(bad_ids)} neurite nodes with non-positive/NaN radius."
    return CheckResult.from_pass_fail(
        key="all_neurite_radii_nonzero",
        label="All neurite radii are positive",
        passed=passed,
        severity="error",
        message=msg,
        source="native",
        failing_node_ids=bad_ids,
        metrics={"invalid_radius_count": len(bad_ids)},
    )


def _check_all_segment_lengths_nonzero(ctx, params: dict[str, Any]) -> CheckResult:
    _ = params
    stats = _segment_length_stats(ctx)
    bad_ids = list(stats["invalid_ids"])
    passed = len(bad_ids) == 0
    msg = "All segment lengths are positive." if passed else f"Found {len(bad_ids)} zero-length/invalid segments."
    return CheckResult.from_pass_fail(
        key="all_segment_lengths_nonzero",
        label="All segment lengths are positive",
        passed=passed,
        severity="error",
        message=msg,
        source="native",
        failing_node_ids=bad_ids,
        failing_section_ids=bad_ids,
        metrics={"segment_count": int(stats["segment_count"]), "invalid_segment_count": len(bad_ids)},
    )


def _check_all_section_lengths_nonzero(ctx, params: dict[str, Any]) -> CheckResult:
    _ = params
    stats = _segment_length_stats(ctx)
    bad_ids = list(stats["invalid_ids"])
    passed = len(bad_ids) == 0
    msg = (
        "All section lengths are positive."
        if passed
        else "Detected section(s) with non-positive total length (segment-based approximation)."
    )
    return CheckResult.from_pass_fail(
        key="all_section_lengths_nonzero",
        label="All section lengths are positive",
        passed=passed,
        severity="error",
        message=msg,
        source="native",
        failing_node_ids=bad_ids,
        failing_section_ids=bad_ids,
        metrics={"section_count_approx": int(stats["segment_count"]), "invalid_section_count": len(bad_ids)},
    )


def _check_no_dangling_branches(ctx, params: dict[str, Any]) -> CheckResult:
    _ = params
    ids = np.asarray(ctx.ids, dtype=np.int64)
    types = np.asarray(ctx.types, dtype=np.int64)
    parents = np.asarray(ctx.parents, dtype=np.int64)
    bad_mask = (types != 1) & (parents == -1)
    bad_ids = ids[bad_mask].astype(np.int64).tolist()
    passed = len(bad_ids) == 0
    msg = (
        "No dangling branches."
        if passed
        else f"Found {len(bad_ids)} non-soma nodes whose parent is -1."
    )
    return CheckResult.from_pass_fail(
        key="no_dangling_branches",
        label="No dangling branches",
        passed=passed,
        severity="error",
        message=msg,
        source="native",
        failing_node_ids=bad_ids,
        metrics={"dangling_branch_count": len(bad_ids)},
    )


def _check_no_duplicate_node_ids(ctx, params: dict[str, Any]) -> CheckResult:
    _ = params
    ids, _parents = _original_ids_parents(ctx)
    unique_ids, counts = np.unique(ids, return_counts=True)
    dup_mask = counts > 1
    dup_ids = unique_ids[dup_mask].astype(np.int64).tolist()
    dup_counts = counts[dup_mask].astype(np.int64).tolist()
    dup_row_count = int(sum(dup_counts))
    passed = len(dup_ids) == 0
    msg = (
        "Every node ID is on one row."
        if passed
        else (
            f"Found {len(dup_ids)} node ID(s) used on more than one row "
            f"({dup_row_count} rows in all): {format_id_list(dup_ids)}."
        )
    )
    return CheckResult.from_pass_fail(
        key="no_duplicate_node_ids",
        label="Node IDs are unique",
        passed=passed,
        severity="error",
        message=msg,
        source="native",
        failing_node_ids=dup_ids,
        metrics={
            "duplicate_id_count": len(dup_ids),
            "duplicate_row_count": dup_row_count,
            "duplicate_ids_sample": [
                {"id": int(node_id), "row_count": int(count)}
                for node_id, count in zip(dup_ids[:20], dup_counts[:20])
            ],
        },
    )


def _check_no_parent_cycles(ctx, params: dict[str, Any]) -> CheckResult:
    _ = params
    ids, parents = _original_ids_parents(ctx)
    id_list = ids.astype(np.int64).tolist()
    parent_list = parents.astype(np.int64).tolist()
    # Self loops are reported by no_self_loop.
    cycles = parent_cycles(id_list, parent_list, include_self_loops=False)
    on_cycle = sorted({int(v) for cycle in cycles for v in cycle})
    cut_off = nodes_cut_off_by_cycles(id_list, parent_list, cycles)
    passed = len(cycles) == 0
    msg = (
        "Every parent chain reaches a root."
        if passed
        else (
            f"Found {len(cycles)} parent cycle(s) through {len(on_cycle)} node(s): "
            f"{format_id_list(on_cycle)}. Including the nodes below them, "
            f"{len(cut_off)} node(s) never reach a root."
        )
    )
    return CheckResult.from_pass_fail(
        key="no_parent_cycles",
        label="No parent cycles",
        passed=passed,
        severity="error",
        message=msg,
        source="native",
        failing_node_ids=on_cycle,
        metrics={
            "cycle_count": len(cycles),
            "cycle_node_count": len(on_cycle),
            "cut_off_node_count": len(cut_off),
            "cycles_sample": [cycle[:50] for cycle in cycles[:10]],
        },
    )


def _check_no_self_loop(ctx, params: dict[str, Any]) -> CheckResult:
    _ = params
    # Soma consolidation re-roots each soma, which would hide a soma that is
    # its own parent, so read the file's own parent IDs.
    ids, parents = _original_ids_parents(ctx)
    bad_mask = parents == ids
    bad_ids = ids[bad_mask].astype(np.int64).tolist()
    passed = len(bad_ids) == 0
    msg = "No self loops." if passed else f"Found {len(bad_ids)} node(s) whose parent ID equals their own ID."
    return CheckResult.from_pass_fail(
        key="no_self_loop",
        label="No self loops",
        passed=passed,
        severity="error",
        message=msg,
        source="native",
        failing_node_ids=bad_ids,
        metrics={"self_loop_count": len(bad_ids)},
    )


def _check_parent_id_less_than_child_id(ctx, params: dict[str, Any]) -> CheckResult:
    _ = params
    ids, parents = _original_ids_parents(ctx)
    valid_ids = {int(v) for v in ids.astype(np.int64).tolist()}
    valid_parent_mask = (parents >= 0) & np.isin(parents, list(valid_ids), assume_unique=False)
    bad_mask = valid_parent_mask & (parents >= ids)
    bad_ids = ids[bad_mask].astype(np.int64).tolist()
    passed = len(bad_ids) == 0
    msg = (
        "All parent IDs are less than their child IDs."
        if passed
        else f"Found {len(bad_ids)} node(s) where parent ID is not less than child ID."
    )
    return CheckResult.from_pass_fail(
        key="parent_id_less_than_child_id",
        label="Parent ID is less than child ID",
        passed=passed,
        severity="warning",
        message=msg,
        source="native",
        failing_node_ids=bad_ids,
        metrics={"id_order_violation_count": len(bad_ids)},
    )


def _check_no_node_id_gaps(ctx, params: dict[str, Any]) -> CheckResult:
    _ = params
    # Soma consolidation deletes the merged soma nodes' IDs, which would read
    # as gaps; the file's own IDs decide.
    ids, _parents = _original_ids_parents(ctx)
    if ids.size <= 1:
        return CheckResult.from_pass_fail(
            key="no_node_id_gaps",
            label="Node IDs are continuous",
            passed=True,
            severity="warning",
            message="Node IDs are continuous.",
            source="native",
            metrics={"gap_count": 0, "missing_id_count": 0},
        )

    sorted_ids = np.sort(np.unique(ids))
    diffs = np.diff(sorted_ids)
    gap_idx = np.flatnonzero(diffs > 1)
    failing_node_ids = sorted_ids[gap_idx + 1].astype(np.int64).tolist() if gap_idx.size else []
    missing_id_count = int(np.sum(diffs[gap_idx] - 1)) if gap_idx.size else 0
    gap_samples: list[dict[str, Any]] = []
    for idx in gap_idx[:10].tolist():
        prev_id = int(sorted_ids[int(idx)])
        next_id = int(sorted_ids[int(idx) + 1])
        gap_samples.append(
            {
                "after_id": prev_id,
                "before_id": next_id,
                "missing_count": int(next_id - prev_id - 1),
            }
        )
    passed = gap_idx.size == 0
    msg = (
        "Node IDs are continuous."
        if passed
        else f"Found {int(gap_idx.size)} node ID gap(s) with {missing_id_count} missing integer ID value(s)."
    )
    return CheckResult.from_pass_fail(
        key="no_node_id_gaps",
        label="Node IDs are continuous",
        passed=passed,
        severity="warning",
        message=msg,
        source="native",
        failing_node_ids=failing_node_ids,
        metrics={
            "gap_count": int(gap_idx.size),
            "missing_id_count": missing_id_count,
            "min_id": int(sorted_ids[0]),
            "max_id": int(sorted_ids[-1]),
            "expected_continuous_count": int(sorted_ids[-1] - sorted_ids[0] + 1),
            "observed_unique_count": int(sorted_ids.size),
            "gap_samples": gap_samples,
        },
    )


def _check_no_extreme_spatial_jump(ctx, params: dict[str, Any]) -> CheckResult:
    min_jump_um = float(params.get("min_jump_um", 200.0))
    median_ratio = float(params.get("median_ratio", 10.0))
    mad_scale = float(params.get("mad_scale", 12.0))
    mad_floor_um = float(params.get("mad_floor_um", 1.0))

    ids = np.asarray(ctx.ids, dtype=np.int64)
    parents = np.asarray(ctx.parents, dtype=np.int64)
    xyz = np.asarray(ctx.xyz, dtype=np.float64)
    id_to_index = {int(ids[i]): int(i) for i in range(len(ids))}

    child_mask = parents >= 0
    child_idx = np.flatnonzero(child_mask)
    if child_idx.size == 0:
        return CheckResult.from_pass_fail(
            key="no_extreme_spatial_jump",
            label="No extreme spatial jumps",
            passed=True,
            severity="warning",
            message="No extreme spatial jumps.",
            source="native",
            params_used={
                "min_jump_um": min_jump_um,
                "median_ratio": median_ratio,
                "mad_scale": mad_scale,
                "mad_floor_um": mad_floor_um,
            },
            thresholds_used={
                "min_jump_um": min_jump_um,
                "median_ratio": median_ratio,
                "mad_scale": mad_scale,
                "mad_floor_um": mad_floor_um,
            },
            metrics={"segment_count": 0, "extreme_jump_count": 0, "jump_threshold_um": float(min_jump_um)},
        )

    valid_pairs: list[tuple[int, int]] = []
    for idx in child_idx.tolist():
        pidx = id_to_index.get(int(parents[idx]))
        if pidx is None:
            continue
        valid_pairs.append((int(idx), int(pidx)))

    if not valid_pairs:
        return CheckResult.from_pass_fail(
            key="no_extreme_spatial_jump",
            label="No extreme spatial jumps",
            passed=True,
            severity="warning",
            message="No extreme spatial jumps.",
            source="native",
            params_used={
                "min_jump_um": min_jump_um,
                "median_ratio": median_ratio,
                "mad_scale": mad_scale,
                "mad_floor_um": mad_floor_um,
            },
            thresholds_used={
                "min_jump_um": min_jump_um,
                "median_ratio": median_ratio,
                "mad_scale": mad_scale,
                "mad_floor_um": mad_floor_um,
            },
            metrics={"segment_count": 0, "extreme_jump_count": 0, "jump_threshold_um": float(min_jump_um)},
        )

    child_indices = np.asarray([pair[0] for pair in valid_pairs], dtype=np.int64)
    parent_indices = np.asarray([pair[1] for pair in valid_pairs], dtype=np.int64)
    lengths = np.linalg.norm(xyz[child_indices] - xyz[parent_indices], axis=1)
    finite_mask = np.isfinite(lengths)
    if not bool(np.any(finite_mask)):
        return CheckResult.from_pass_fail(
            key="no_extreme_spatial_jump",
            label="No extreme spatial jumps",
            passed=True,
            severity="warning",
            message="No extreme spatial jumps.",
            source="native",
            params_used={
                "min_jump_um": min_jump_um,
                "median_ratio": median_ratio,
                "mad_scale": mad_scale,
                "mad_floor_um": mad_floor_um,
            },
            thresholds_used={
                "min_jump_um": min_jump_um,
                "median_ratio": median_ratio,
                "mad_scale": mad_scale,
                "mad_floor_um": mad_floor_um,
            },
            metrics={"segment_count": 0, "extreme_jump_count": 0, "jump_threshold_um": float(min_jump_um)},
        )

    child_indices = child_indices[finite_mask]
    parent_indices = parent_indices[finite_mask]
    lengths = lengths[finite_mask]

    median_len = float(np.median(lengths)) if lengths.size else 0.0
    mad = float(np.median(np.abs(lengths - median_len))) if lengths.size else 0.0
    robust_threshold = median_len + mad_scale * max(mad, mad_floor_um)
    ratio_threshold = median_len * median_ratio
    jump_threshold = float(max(min_jump_um, ratio_threshold, robust_threshold))

    bad_mask = lengths > jump_threshold
    bad_child_indices = child_indices[bad_mask]
    bad_ids = ids[bad_child_indices].astype(np.int64).tolist()
    sample_segments: list[dict[str, Any]] = []
    for child_idx_val, parent_idx_val, seg_len in zip(
        child_indices[bad_mask][:20].tolist(),
        parent_indices[bad_mask][:20].tolist(),
        lengths[bad_mask][:20].tolist(),
    ):
        sample_segments.append(
            {
                "child_id": int(ids[int(child_idx_val)]),
                "parent_id": int(ids[int(parent_idx_val)]),
                "segment_length_um": float(seg_len),
            }
        )

    passed = len(bad_ids) == 0
    msg = (
        "No extreme spatial jumps."
        if passed
        else f"Found {len(bad_ids)} parent-child segment(s) with extreme spatial jump > {jump_threshold:.5g} um."
    )
    params_used = {
        "min_jump_um": min_jump_um,
        "median_ratio": median_ratio,
        "mad_scale": mad_scale,
        "mad_floor_um": mad_floor_um,
    }
    return CheckResult.from_pass_fail(
        key="no_extreme_spatial_jump",
        label="No extreme spatial jumps",
        passed=passed,
        severity="warning",
        message=msg,
        source="native",
        params_used=params_used,
        thresholds_used={**params_used, "jump_threshold_um": jump_threshold},
        failing_node_ids=bad_ids,
        failing_section_ids=bad_ids,
        metrics={
            "segment_count": int(lengths.size),
            "extreme_jump_count": len(bad_ids),
            "median_segment_length_um": median_len,
            "mad_segment_length_um": mad,
            "jump_threshold_um": jump_threshold,
            "max_segment_length_um": float(np.max(lengths)) if lengths.size else 0.0,
            "sample_segments": sample_segments,
        },
    )


def _check_no_duplicate_3d_points(ctx, params: dict[str, Any]) -> CheckResult:
    _ = params
    ids = np.asarray(ctx.ids, dtype=np.int64)
    xyz = np.ascontiguousarray(np.asarray(ctx.xyz, dtype=np.float64))
    dup_ids: list[int] = []
    group_count = 0
    sample_groups: list[dict[str, Any]] = []
    if xyz.shape[0] > 0:
        # Duplicate means the full 3D coordinate tuple matches exactly.
        # Sharing only x/y, x/z, or y/z does not count as a duplicate point.
        row_view = xyz.view(np.dtype((np.void, xyz.dtype.itemsize * xyz.shape[1]))).ravel()
        _, inverse, counts = np.unique(row_view, return_inverse=True, return_counts=True)
        repeated_group_labels = np.flatnonzero(counts > 1)
        group_count = int(repeated_group_labels.size)
        if group_count > 0:
            repeated_mask = counts[inverse] > 1
            dup_ids = ids[repeated_mask].astype(np.int64).tolist()
            for g in repeated_group_labels[:10]:
                idx = np.flatnonzero(inverse == g)
                grp_ids = ids[idx].astype(np.int64).tolist()
                sample_groups.append(
                    {
                        "ids": grp_ids,
                        "xyz": [
                            float(xyz[idx[0], 0]),
                            float(xyz[idx[0], 1]),
                            float(xyz[idx[0], 2]),
                        ],
                    }
                )
    passed = group_count == 0
    msg = (
        "No duplicate 3D points."
        if passed
        else f"Found {len(dup_ids)} duplicate nodes across {group_count} coordinate groups."
    )
    return CheckResult.from_pass_fail(
        key="no_duplicate_3d_points",
        label="No duplicate 3D points",
        passed=passed,
        severity="error",
        message=msg,
        source="native",
        failing_node_ids=dup_ids,
        metrics={
            "duplicate_point_count": len(dup_ids),
            "duplicate_group_count": group_count,
            "duplicate_groups_sample": sample_groups,
        },
    )


def _check_radius_upper_bound(ctx, params: dict[str, Any]) -> CheckResult:
    max_radius = float(params.get("max_radius", 20.0))
    ids = np.asarray(ctx.ids, dtype=np.int64)
    radii = np.asarray(ctx.radii, dtype=np.float64)
    bad_ids = ids[radii > max_radius].astype(np.int64).tolist()
    passed = len(bad_ids) == 0
    msg = (
        f"All radii are <= {max_radius:g}."
        if passed
        else f"Found {len(bad_ids)} nodes with radius > {max_radius:g}."
    )
    return CheckResult.from_pass_fail(
        key="radius_upper_bound",
        label="Radius upper bound",
        passed=passed,
        severity="warning",
        message=msg,
        source="native",
        failing_node_ids=bad_ids,
        params_used={"max_radius": max_radius},
        metrics={"max_radius_observed": float(np.max(radii)) if radii.size else 0.0},
    )


def register_native_checks() -> None:
    global _REGISTERED
    if _REGISTERED:
        return

    register_check(
        key="no_duplicate_node_ids",
        label="Node IDs are unique",
        source="native",
        runner=_check_no_duplicate_node_ids,
    )
    register_check(
        key="no_parent_cycles",
        label="No parent cycles",
        source="native",
        runner=_check_no_parent_cycles,
    )
    register_check(
        key="valid_soma_format",
        label="Soma format is simple",
        source="native",
        runner=_check_valid_soma_format,
    )
    register_check(
        key="multiple_somas",
        label="Only one connected soma group remains",
        source="native",
        runner=_check_multiple_somas,
    )
    register_check(key="has_soma", label="Soma present", source="native", runner=_check_has_soma)
    register_check(key="has_axon", label="Axon present", source="native", runner=_check_has_axon)
    register_check(
        key="has_basal_dendrite",
        label="Basal dendrite present",
        source="native",
        runner=_check_has_basal_dendrite,
    )
    register_check(
        key="has_apical_dendrite",
        label="Apical dendrite present",
        source="native",
        runner=_check_has_apical_dendrite,
    )
    register_check(
        key="no_invalid_negative_types",
        label="No invalid negative node types",
        source="native",
        runner=_check_no_invalid_negative_types,
    )
    register_check(
        key="custom_types_defined",
        label="Custom node types are defined",
        source="native",
        runner=_check_custom_types_defined,
    )
    register_check(
        key="all_neurite_radii_nonzero",
        label="All neurite radii are positive",
        source="native",
        runner=_check_all_neurite_radii_nonzero,
    )
    register_check(
        key="all_section_lengths_nonzero",
        label="All section lengths are positive",
        source="native",
        runner=_check_all_section_lengths_nonzero,
    )
    register_check(
        key="all_segment_lengths_nonzero",
        label="All segment lengths are positive",
        source="native",
        runner=_check_all_segment_lengths_nonzero,
    )
    register_check(
        key="no_dangling_branches",
        label="No dangling branches",
        source="native",
        runner=_check_no_dangling_branches,
    )
    register_check(
        key="no_self_loop",
        label="No self loops",
        source="native",
        runner=_check_no_self_loop,
    )
    register_check(
        key="parent_id_less_than_child_id",
        label="Parent ID is less than child ID",
        source="native",
        runner=_check_parent_id_less_than_child_id,
    )
    register_check(
        key="no_node_id_gaps",
        label="Node IDs are continuous",
        source="native",
        runner=_check_no_node_id_gaps,
    )
    register_check(
        key="no_extreme_spatial_jump",
        label="No extreme spatial jumps",
        source="native",
        runner=_check_no_extreme_spatial_jump,
    )
    register_check(
        key="no_duplicate_3d_points",
        label="No duplicate 3D points",
        source="native",
        runner=_check_no_duplicate_3d_points,
    )
    register_check(
        key="radius_upper_bound",
        label="Radius upper bound",
        source="native",
        runner=_check_radius_upper_bound,
    )
    _REGISTERED = True
