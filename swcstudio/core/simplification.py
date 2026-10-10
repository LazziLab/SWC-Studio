"""Core morphology simplification algorithms."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd


def _point_line_dist(p: np.ndarray, a: np.ndarray, b: np.ndarray) -> float:
    ab = b - a
    denom = float(np.dot(ab, ab))
    if denom <= 1e-12:
        return float(np.linalg.norm(p - a))
    t = float(np.dot(p - a, ab) / denom)
    t = max(0.0, min(1.0, t))
    proj = a + t * ab
    return float(np.linalg.norm(p - proj))


def _rdp_indices(points: np.ndarray, epsilon: float) -> list[int]:
    n = int(points.shape[0])
    if n <= 2:
        return [0, max(0, n - 1)]

    start = points[0]
    end = points[-1]

    max_dist = -1.0
    max_idx = -1
    for i in range(1, n - 1):
        d = _point_line_dist(points[i], start, end)
        if d > max_dist:
            max_dist = d
            max_idx = i

    if max_dist > float(epsilon) and max_idx > 0:
        left = _rdp_indices(points[: max_idx + 1], epsilon)
        right = _rdp_indices(points[max_idx:], epsilon)
        return left[:-1] + [max_idx + r for r in right]

    return [0, n - 1]


def _build_graph(df: pd.DataFrame) -> tuple[dict[int, int], list[list[int]], np.ndarray, np.ndarray]:
    ids = df["id"].to_numpy(dtype=int)
    parents = df["parent"].to_numpy(dtype=int)
    id_to_idx = {int(ids[i]): int(i) for i in range(len(ids))}
    children: list[list[int]] = [[] for _ in range(len(ids))]
    for i, pid in enumerate(parents):
        pidx = id_to_idx.get(int(pid))
        if pidx is not None:
            children[pidx].append(i)
    child_counts = np.asarray([len(c) for c in children], dtype=int)
    return id_to_idx, children, ids, child_counts


def _extract_anchor_paths(children: list[list[int]], anchors: set[int]) -> list[list[int]]:
    paths: list[list[int]] = []
    for start in sorted(anchors):
        for c in children[start]:
            path = [start, c]
            cur = c
            while cur not in anchors:
                nxts = children[cur]
                if len(nxts) != 1:
                    break
                nxt = nxts[0]
                path.append(nxt)
                cur = nxt
            paths.append(path)
    return paths


def _nearest_kept_parent(
    idx: int,
    parents: np.ndarray,
    ids: np.ndarray,
    id_to_idx: dict[int, int],
    keep_mask: np.ndarray,
) -> int:
    cur_pid = int(parents[idx])
    while cur_pid != -1:
        pidx = id_to_idx.get(cur_pid)
        if pidx is None:
            return -1
        if bool(keep_mask[pidx]):
            return int(ids[pidx])
        cur_pid = int(parents[pidx])
    return -1


def simplify_morphology_dataframe(df: pd.DataFrame, config: dict[str, Any]) -> dict[str, Any]:
    """Simplify an SWC dataframe while preserving key topology anchors."""
    out_df = df.copy()
    if out_df.empty:
        return {
            "dataframe": out_df,
            "kept_node_ids": [],
            "removed_node_ids": [],
            "original_node_count": 0,
            "new_node_count": 0,
            "reduction_percent": 0.0,
            "params_used": {
                "epsilon": float(config.get("thresholds", {}).get("epsilon", 2.0)),
                "radius_tolerance": float(config.get("thresholds", {}).get("radius_tolerance", 0.5)),
                "keep_tips": bool(config.get("flags", {}).get("keep_tips", True)),
                "keep_bifurcations": bool(config.get("flags", {}).get("keep_bifurcations", True)),
                "keep_soma": bool(config.get("flags", {}).get("keep_soma", True)),
            },
            "protected_counts": {"roots": 0, "tips": 0, "bifurcations": 0, "soma": 0,
                                 "type_boundaries": 0, "radius_sensitive": 0},
        }

    thresholds = dict(config.get("thresholds", {}))
    flags = dict(config.get("flags", {}))

    epsilon = max(0.0, float(thresholds.get("epsilon", 2.0)))
    radius_tol = max(0.0, float(thresholds.get("radius_tolerance", 0.5)))
    keep_tips = bool(flags.get("keep_tips", True))
    keep_bifs = bool(flags.get("keep_bifurcations", True))
    keep_roots = bool(flags.get("keep_roots", True))
    keep_soma = bool(flags.get("keep_soma", True))

    id_to_idx, children, ids, child_counts = _build_graph(out_df)
    parents = out_df["parent"].to_numpy(dtype=int)
    xyz = out_df[["x", "y", "z"]].to_numpy(dtype=float)
    radii = out_df["radius"].to_numpy(dtype=float)

    root_idx = {i for i in range(len(out_df)) if int(parents[i]) == -1}
    tip_idx = {i for i in range(len(out_df)) if int(child_counts[i]) == 0}
    bif_idx = {i for i in range(len(out_df)) if int(child_counts[i]) > 1}
    types = out_df["type"].to_numpy(dtype=int)
    # A soma outline is shape, not a path to straighten; and a node whose
    # type differs from its parent's marks where a compartment starts, so
    # removing it would move the axon or apical start.
    soma_idx = {i for i in range(len(out_df)) if int(types[i]) == 1}
    boundary_idx = {
        i for i in range(len(out_df))
        if int(parents[i]) in id_to_idx and int(types[id_to_idx[int(parents[i])]]) != int(types[i])
    }

    protected: set[int] = set(root_idx if keep_roots else [])
    if keep_tips:
        protected.update(tip_idx)
    if keep_bifs:
        protected.update(bif_idx)
    if keep_soma:
        protected.update(soma_idx)
    protected.update(boundary_idx)

    anchors: set[int] = set(root_idx)
    anchors.update(i for i in range(len(out_df)) if int(child_counts[i]) != 1)
    anchors.update(boundary_idx)
    if keep_soma:
        anchors.update(soma_idx)

    paths = _extract_anchor_paths(children, anchors)

    rdp_keep: set[int] = set()
    radius_sensitive: set[int] = set()

    for path in paths:
        if len(path) < 3:
            continue

        pts = xyz[path, :]
        keep_local = set(_rdp_indices(pts, epsilon))
        for li in keep_local:
            if li <= 0 or li >= len(path) - 1:
                continue
            rdp_keep.add(path[li])

        valid_rs = [float(radii[i]) for i in path if math.isfinite(float(radii[i])) and float(radii[i]) > 0]
        if not valid_rs:
            continue
        seg_mean = float(np.mean(valid_rs))
        if seg_mean <= 0:
            continue

        for i in path[1:-1]:
            rv = float(radii[i])
            if not math.isfinite(rv) or rv <= 0:
                radius_sensitive.add(i)
                continue
            dev = abs(rv - seg_mean) / seg_mean
            if dev > radius_tol:
                radius_sensitive.add(i)

    keep_idx = set(protected)
    keep_idx.update(rdp_keep)
    keep_idx.update(radius_sensitive)
    keep_idx.update(root_idx)

    if not keep_idx:
        keep_idx.add(0)

    keep_indices = sorted(keep_idx)
    keep_mask = np.zeros(len(out_df), dtype=bool)
    keep_mask[keep_indices] = True

    new_parents: list[int] = []
    for idx in keep_indices:
        if int(parents[idx]) == -1:
            new_parents.append(-1)
            continue
        new_parents.append(_nearest_kept_parent(idx, parents, ids, id_to_idx, keep_mask))

    simplified = out_df.iloc[keep_indices].copy().reset_index(drop=True)
    simplified["parent"] = np.asarray(new_parents, dtype=int)
    if "parent_str" in simplified.columns:
        for i in range(len(simplified)):
            simplified.at[simplified.index[i], "parent_str"] = str(int(simplified.iloc[i]["parent"]))

    kept_ids = [int(ids[i]) for i in keep_indices]
    kept_id_set = set(kept_ids)
    removed_ids = [int(v) for v in ids.tolist() if int(v) not in kept_id_set]

    original_count = int(len(out_df))
    new_count = int(len(simplified))
    reduction = 0.0 if original_count <= 0 else max(0.0, 100.0 * (original_count - new_count) / original_count)

    return {
        "dataframe": simplified,
        "kept_node_ids": kept_ids,
        "removed_node_ids": removed_ids,
        "original_node_count": original_count,
        "new_node_count": new_count,
        "reduction_percent": reduction,
        "params_used": {
            "epsilon": epsilon,
            "radius_tolerance": radius_tol,
            "keep_tips": keep_tips,
            "keep_bifurcations": keep_bifs,
            "keep_roots": keep_roots,
            "keep_soma": keep_soma,
        },
        "protected_counts": {
            "roots": len(root_idx),
            "tips": len(tip_idx) if keep_tips else 0,
            "bifurcations": len(bif_idx) if keep_bifs else 0,
            "soma": len(soma_idx) if keep_soma else 0,
            "type_boundaries": len(boundary_idx),
            "radius_sensitive": len(radius_sensitive),
        },
    }
