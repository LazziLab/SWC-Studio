"""Shared radii-cleaning logic used by CLI + GUI features."""

from __future__ import annotations

import math
import re
from typing import Any

import numpy as np
import pandas as pd

TYPE_NAMES = {
    0: "undefined",
    1: "soma",
    2: "axon",
    3: "basal dendrite",
    4: "apical dendrite",
}

TYPE_ALIASES: dict[int, set[str]] = {
    0: {"0", "undefined", "unknown"},
    1: {"1", "soma"},
    2: {"2", "axon"},
    3: {"3", "basal", "basal dendrite", "dendrite", "basal_dendrite"},
    4: {"4", "apical", "apical dendrite", "apical_dendrite"},
}

DEFAULT_RULES: dict[str, Any] = {
    "preserve_soma": True,
    "small_radius_zero_only": True,
    "sanity_bounds": {
        "global": {
            "lower_percentile": 1.0,
            "upper_percentile": 99.5,
            "lower_abs": 0.05,
            "upper_abs": 30.0,
        },
        "per_type": {
            "2": {"enabled": True, "lower_percentile": 1.0, "upper_percentile": 99.5, "lower_abs": 0.05, "upper_abs": 30.0},
            "3": {"enabled": True, "lower_percentile": 1.0, "upper_percentile": 99.5, "lower_abs": 0.05, "upper_abs": 30.0},
            "4": {"enabled": True, "lower_percentile": 1.0, "upper_percentile": 99.5, "lower_abs": 0.05, "upper_abs": 30.0},
        },
    },
    "local_outlier": {
        "enabled": True,
        "window_nodes": 5,
        "max_percent_deviation": 0.5,
    },
    "taper": {
        "enabled": True,
        "slack": 0.05,
    },
    "axon_floor": {
        "enabled": True,
        "min_radius": 0.12,
    },
    "savgol": {
        "enabled": True,
        "window_nodes": 7,
        "polyorder": 2,
        "gaussian_sigma_fraction": 0.5,
    },
    "fixed_point": {
        "enabled": True,
        "max_passes": 32,
        "min_effective_delta": 0.005,
    },
    "replacement": {
        "clamp_min": 0.05,
        "clamp_max": 30.0,
    },
}


def _is_valid_radius(v: float) -> bool:
    return isinstance(v, (int, float, np.floating)) and math.isfinite(float(v)) and float(v) > 0.0


def _same_radius(old: float, new: float, tol: float) -> bool:
    # NaN != NaN and inf - inf is NaN, so compare those before subtracting.
    if math.isnan(old) and math.isnan(new):
        return True
    return old == new or abs(old - new) <= tol


def _clamp(v: float, lo: float, hi: float) -> float:
    if hi < lo:
        lo, hi = hi, lo
    return max(lo, min(hi, v))


def _small_median(values: list[float]) -> float | None:
    if not values:
        return None
    vals = sorted(float(v) for v in values)
    n = len(vals)
    mid = n // 2
    if n % 2:
        return vals[mid]
    return 0.5 * (vals[mid - 1] + vals[mid])


def _deep_merge(base: dict[str, Any], overrides: dict[str, Any] | None) -> dict[str, Any]:
    out = dict(base)
    if not isinstance(overrides, dict):
        return out
    for k, v in overrides.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(dict(out[k]), v)
        else:
            out[k] = v
    return out


def _norm_type_text(v: Any) -> str:
    txt = str(v or "").strip().lower()
    txt = txt.replace("_", " ")
    txt = re.sub(r"\s+", " ", txt)
    return txt


def _resolve_type_id(v: Any) -> int | None:
    if isinstance(v, (int, np.integer)):
        return int(v)
    txt = _norm_type_text(v)
    if not txt:
        return None
    if txt.lstrip("-").isdigit():
        return int(txt)
    m = re.search(r"\(([-]?\d+)\)\s*$", txt)
    if m:
        return int(m.group(1))
    for tid, aliases in TYPE_ALIASES.items():
        if txt in aliases:
            return int(tid)
    return None


def _resolve_type_thresholds(cfg: dict[str, Any]) -> dict[int, dict[str, Any]]:
    sanity_bounds = cfg.get("sanity_bounds", {})
    raw = {}
    if isinstance(sanity_bounds, dict):
        raw = sanity_bounds.get("per_type", {})
    if not isinstance(raw, dict) or not raw:
        raw = cfg.get("type_thresholds", {})
    if not isinstance(raw, dict):
        return {}
    out: dict[int, dict[str, Any]] = {}
    for key, value in raw.items():
        if not isinstance(value, dict):
            continue
        tid = _resolve_type_id(key)
        if tid is None and "type_id" in value:
            tid = _resolve_type_id(value.get("type_id"))
        if tid is None:
            continue
        out[int(tid)] = dict(value)
    return out


def _resolve_global_sanity_bounds(cfg: dict[str, Any]) -> dict[str, float]:
    sanity_bounds = cfg.get("sanity_bounds", {})
    global_cfg = {}
    if isinstance(sanity_bounds, dict):
        global_cfg = sanity_bounds.get("global", {})
    global_cfg = dict(global_cfg) if isinstance(global_cfg, dict) else {}

    legacy_pct = dict(cfg.get("global_percentile_bounds", {}))
    legacy_abs = dict(cfg.get("global_absolute_bounds", {}))
    return {
        "lower_percentile": float(global_cfg.get("lower_percentile", legacy_pct.get("min", 1.0))),
        "upper_percentile": float(global_cfg.get("upper_percentile", legacy_pct.get("max", 99.5))),
        "lower_abs": float(global_cfg.get("lower_abs", legacy_abs.get("min", 0.05))),
        "upper_abs": float(global_cfg.get("upper_abs", legacy_abs.get("max", 30.0))),
    }


def _build_topology(ids: np.ndarray, parents: np.ndarray) -> tuple[np.ndarray, list[list[int]]]:
    id_to_idx = {int(ids[i]): int(i) for i in range(len(ids))}
    parent_idx = np.full(len(ids), -1, dtype=int)
    children: list[list[int]] = [[] for _ in range(len(ids))]
    for i, pid in enumerate(parents):
        pidx = id_to_idx.get(int(pid))
        if pidx is not None:
            parent_idx[i] = pidx
            children[pidx].append(i)
    for row in children:
        row.sort()
    return parent_idx, children


def _depths_from_roots(parent_idx: np.ndarray, children: list[list[int]]) -> np.ndarray:
    n = len(parent_idx)
    depths = np.full(n, -1, dtype=int)
    roots = [i for i in range(n) if int(parent_idx[i]) < 0]
    queue = list(roots)
    for root in roots:
        depths[root] = 0
    while queue:
        idx = queue.pop(0)
        for child in children[idx]:
            if depths[child] >= 0:
                continue
            depths[child] = depths[idx] + 1
            queue.append(child)
    depths[depths < 0] = 0
    return depths


def _segment_paths(parent_idx: np.ndarray, children: list[list[int]]) -> list[list[int]]:
    paths: list[list[int]] = []
    seen_edges: set[tuple[int, int]] = set()
    roots = [i for i in range(len(parent_idx)) if int(parent_idx[i]) < 0]

    def add_path(anchor: int, child: int) -> None:
        path = [anchor, child]
        prev = anchor
        cur = child
        seen_edges.add((anchor, child))
        while len(children[cur]) == 1:
            nxt = children[cur][0]
            if (cur, nxt) in seen_edges:
                break
            path.append(nxt)
            seen_edges.add((cur, nxt))
            prev, cur = cur, nxt
        paths.append(path)
        for nxt in children[cur]:
            if (cur, nxt) not in seen_edges:
                add_path(cur, nxt)

    for root in roots:
        if not children[root]:
            paths.append([root])
            continue
        for child in children[root]:
            if (root, child) not in seen_edges:
                add_path(root, child)

    for parent, row in enumerate(children):
        for child in row:
            if (parent, child) not in seen_edges:
                add_path(parent, child)
    return paths


def _path_distances(nodes: list[int], coords: np.ndarray, parent_idx: np.ndarray) -> np.ndarray:
    d = np.zeros(len(nodes), dtype=float)
    for j in range(1, len(nodes)):
        idx = nodes[j]
        pidx = nodes[j - 1]
        if int(parent_idx[idx]) != int(pidx):
            pidx = int(parent_idx[idx]) if int(parent_idx[idx]) >= 0 else pidx
        seg = coords[idx] - coords[pidx]
        d[j] = d[j - 1] + float(np.linalg.norm(seg))
    return d


def _prepare_static_radii_context(df: pd.DataFrame) -> dict[str, Any]:
    ids = np.asarray(df["id"], dtype=int)
    types = np.asarray(df["type"], dtype=int) if "type" in df.columns else np.zeros(len(df), dtype=int)
    parents = np.asarray(df["parent"], dtype=int)
    coords = np.asarray(df.loc[:, ["x", "y", "z"]], dtype=float)
    parent_idx, children = _build_topology(ids, parents)
    paths = _segment_paths(parent_idx, children)
    path_distances = [_path_distances(path, coords, parent_idx) for path in paths]
    return {
        "ids": ids,
        "types": types,
        "parents": parents,
        "coords": coords,
        "parent_idx": parent_idx,
        "children": children,
        "paths": paths,
        "path_distances": path_distances,
    }


def radii_stats_by_type(df: pd.DataFrame, *, bins: int = 12) -> dict[str, Any]:
    """Compute per-type distribution stats + histogram bins for radii."""
    if df.empty or "type" not in df.columns or "radius" not in df.columns:
        return {"type_stats": {}}

    types = np.asarray(df["type"], dtype=int)
    radii = np.asarray(df["radius"], dtype=float)
    type_stats: dict[str, Any] = {}
    for t in sorted({int(v) for v in types.tolist()}):
        mask_t = types == int(t)
        all_vals = radii[mask_t]
        valid = all_vals[np.isfinite(all_vals) & (all_vals > 0.0)]
        row: dict[str, Any] = {
            "type_id": int(t),
            "type_name": TYPE_NAMES.get(int(t), f"type_{int(t)}"),
            "count_total": int(mask_t.sum()),
            "count_valid_positive": int(valid.size),
        }
        if valid.size > 0:
            q1 = float(np.percentile(valid, 25))
            med = float(np.percentile(valid, 50))
            q3 = float(np.percentile(valid, 75))
            vmin = float(np.min(valid))
            vmax = float(np.max(valid))
            mean = float(np.mean(valid))
            b = max(4, int(bins))
            if vmin == vmax:
                edges = [vmin, vmax]
                counts = [int(valid.size)]
            else:
                counts_np, edges_np = np.histogram(valid, bins=b, range=(vmin, vmax))
                counts = [int(x) for x in counts_np.tolist()]
                edges = [float(x) for x in edges_np.tolist()]
            row.update(
                {
                    "mean": mean,
                    "median": med,
                    "q1": q1,
                    "q3": q3,
                    "min": vmin,
                    "max": vmax,
                    "hist_counts": counts,
                    "hist_edges": edges,
                }
            )
        else:
            row.update(
                {
                    "mean": None,
                    "median": None,
                    "q1": None,
                    "q3": None,
                    "min": None,
                    "max": None,
                    "hist_counts": [],
                    "hist_edges": [],
                }
            )
        type_stats[str(int(t))] = row
    return {"type_stats": type_stats}


def _compute_type_bounds(
    radii: np.ndarray,
    types: np.ndarray,
    cfg: dict[str, Any],
) -> dict[int, tuple[bool, float, float]]:
    global_bounds = _resolve_global_sanity_bounds(cfg)
    zero_only_small = bool(cfg.get("small_radius_zero_only", True))
    preserve_soma = bool(cfg.get("preserve_soma", True))
    per_type_cfg = _resolve_type_thresholds(cfg)

    bounds: dict[int, tuple[bool, float, float]] = {}
    for t in sorted({int(v) for v in types.tolist()}):
        if preserve_soma and t == 1:
            bounds[t] = (False, 0.0, float("inf"))
            continue
        t_cfg = dict(per_type_cfg.get(int(t), {}))
        enabled = bool(t_cfg.get("enabled", True))
        lower_pct = float(t_cfg.get("lower_percentile", t_cfg.get("min_percentile", global_bounds["lower_percentile"])))
        upper_pct = float(t_cfg.get("upper_percentile", t_cfg.get("max_percentile", global_bounds["upper_percentile"])))
        lower_abs = float(t_cfg.get("lower_abs", t_cfg.get("min_abs", global_bounds["lower_abs"])))
        upper_abs = float(t_cfg.get("upper_abs", t_cfg.get("max_abs", global_bounds["upper_abs"])))

        vals = radii[(types == int(t)) & np.isfinite(radii) & (radii > 0.0)]
        if vals.size > 0:
            pct_lo = float(np.percentile(vals, lower_pct))
            pct_hi = float(np.percentile(vals, upper_pct))
            lo = max(lower_abs, pct_lo)
            hi = min(upper_abs, pct_hi)
        else:
            lo = lower_abs
            hi = upper_abs
        if zero_only_small:
            lo = 0.0
        if hi < lo:
            lo, hi = hi, lo
        bounds[t] = (enabled, float(lo), float(hi))
    return bounds


def _nearest_valid_parent(i: int, parent_idx: np.ndarray, radii: np.ndarray) -> float | None:
    p = int(parent_idx[i])
    while p >= 0:
        val = float(radii[p])
        if _is_valid_radius(val):
            return val
        p = int(parent_idx[p])
    return None


def _nearest_valid_children(i: int, children: list[list[int]], radii: np.ndarray, *, max_depth: int = 6) -> list[float]:
    out: list[float] = []
    queue: list[tuple[int, int]] = [(child, 1) for child in children[i]]
    best_depth = 0
    while queue:
        idx, depth = queue.pop(0)
        if depth > max_depth:
            continue
        val = float(radii[idx])
        if _is_valid_radius(val):
            out.append(val)
            best_depth = depth
            continue
        if best_depth and depth >= best_depth:
            continue
        for child in children[idx]:
            queue.append((child, depth + 1))
    return out


def _fallback_radius(
    i: int,
    radii: np.ndarray,
    types: np.ndarray,
    parent_idx: np.ndarray,
    children: list[list[int]],
    type_medians: dict[int, float],
    global_median: float,
    clamp_min: float,
    clamp_max: float,
) -> float:
    vals: list[float] = []
    parent_val = _nearest_valid_parent(i, parent_idx, radii)
    if parent_val is not None:
        vals.append(parent_val)
    child_vals = _nearest_valid_children(i, children, radii)
    if child_vals:
        vals.append(float(np.mean(child_vals)))
    if vals:
        return _clamp(float(np.mean(vals)), clamp_min, clamp_max)
    return _clamp(float(type_medians.get(int(types[i]), global_median)), clamp_min, clamp_max)


def _apply_bounds(
    value: float,
    t: int,
    bounds: dict[int, tuple[bool, float, float]],
    *,
    zero_only_small: bool,
    clamp_min: float,
    clamp_max: float,
) -> float:
    out = _clamp(float(value), clamp_min, clamp_max)
    enabled, lo, hi = bounds.get(int(t), (True, 0.0, float("inf")))
    if enabled:
        if not zero_only_small:
            out = max(out, float(lo))
        out = min(out, float(hi))
    return out


def _record_reason(reasons_by_idx: dict[int, set[str]], idx: int, *reasons: str) -> None:
    bucket = reasons_by_idx.setdefault(int(idx), set())
    for reason in reasons:
        if reason:
            bucket.add(reason)


def _local_median_pass(
    paths: list[list[int]],
    radii: np.ndarray,
    types: np.ndarray,
    parent_idx: np.ndarray,
    children: list[list[int]],
    bounds: dict[int, tuple[bool, float, float]],
    cfg: dict[str, Any],
    type_medians: dict[int, float],
    global_median: float,
    reasons_by_idx: dict[int, set[str]],
) -> None:
    local_cfg = dict(cfg.get("local_outlier", {}))
    if not bool(local_cfg.get("enabled", True)):
        return

    preserve_soma = bool(cfg.get("preserve_soma", True))
    zero_only_small = bool(cfg.get("small_radius_zero_only", True))
    replacement_cfg = dict(cfg.get("replacement", {}))
    clamp_min = float(replacement_cfg.get("clamp_min", 0.05))
    clamp_max = float(replacement_cfg.get("clamp_max", 30.0))
    window_nodes = max(3, int(local_cfg.get("window_nodes", 5)))
    if window_nodes % 2 == 0:
        window_nodes += 1
    half = window_nodes // 2
    max_percent_deviation = float(local_cfg.get("max_percent_deviation", 0.5))

    for path in paths:
        if len(path) <= 1:
            continue
        for j, idx in enumerate(path):
            if preserve_soma and int(types[idx]) == 1:
                continue
            cur = float(radii[idx])
            left_nodes = path[max(0, j - half) : j]
            right_nodes = path[j + 1 : min(len(path), j + half + 1)]
            win_vals = [
                float(radii[n])
                for n in (left_nodes + right_nodes)
                if _is_valid_radius(float(radii[n]))
            ]
            local_median = _small_median(win_vals)

            flagged: list[str] = []
            if not math.isfinite(cur):
                flagged.append("non_finite")
            elif cur <= 0.0:
                flagged.append("non_positive")
            elif local_median is not None and left_nodes and right_nodes and len(win_vals) >= 3:
                denom = max(local_median, 1e-9)
                deviation = abs(cur - local_median) / denom
                if deviation > max_percent_deviation:
                    flagged.append("local_outlier")

            enabled, lo, hi = bounds.get(int(types[idx]), (True, 0.0, float("inf")))
            if math.isfinite(cur) and cur > 0.0 and enabled:
                if not zero_only_small and cur < lo:
                    flagged.append("below_type_min")
                if cur > hi:
                    flagged.append("above_type_max")

            if not flagged:
                continue

            replacement = local_median
            if replacement is None or not _is_valid_radius(replacement):
                replacement = _fallback_radius(
                    idx,
                    radii,
                    types,
                    parent_idx,
                    children,
                    type_medians,
                    global_median,
                    clamp_min,
                    clamp_max,
                )
            replacement = _apply_bounds(
                replacement,
                int(types[idx]),
                bounds,
                zero_only_small=zero_only_small,
                clamp_min=clamp_min,
                clamp_max=clamp_max,
            )
            if not _is_valid_radius(replacement):
                replacement = max(clamp_min, float(type_medians.get(int(types[idx]), global_median)))
            radii[idx] = float(replacement)
            _record_reason(reasons_by_idx, idx, *flagged)


def _taper_pass(
    paths: list[list[int]],
    radii: np.ndarray,
    types: np.ndarray,
    bounds: dict[int, tuple[bool, float, float]],
    cfg: dict[str, Any],
    reasons_by_idx: dict[int, set[str]],
    *,
    reason_name: str = "taper_cap",
) -> None:
    taper_cfg = dict(cfg.get("taper", {}))
    if not bool(taper_cfg.get("enabled", True)):
        return

    preserve_soma = bool(cfg.get("preserve_soma", True))
    zero_only_small = bool(cfg.get("small_radius_zero_only", True))
    replacement_cfg = dict(cfg.get("replacement", {}))
    clamp_min = float(replacement_cfg.get("clamp_min", 0.05))
    clamp_max = float(replacement_cfg.get("clamp_max", 30.0))
    slack = max(0.0, float(taper_cfg.get("slack", 0.05)))

    axon_floor_cfg = dict(cfg.get("axon_floor", {}))
    use_axon_floor = bool(axon_floor_cfg.get("enabled", True))
    axon_floor = float(axon_floor_cfg.get("min_radius", 0.12))

    for path in paths:
        if len(path) <= 1:
            continue
        for j in range(1, len(path)):
            idx = path[j]
            prev = path[j - 1]
            if preserve_soma and int(types[idx]) == 1:
                continue
            cur = float(radii[idx])
            prev_radius = float(radii[prev])
            if not _is_valid_radius(cur):
                cur = clamp_min
            changed = False
            if int(types[idx]) != 2 and _is_valid_radius(prev_radius):
                max_allowed = prev_radius * (1.0 + slack)
                if cur > max_allowed:
                    cur = max_allowed
                    changed = True
                    _record_reason(reasons_by_idx, idx, reason_name)
            if use_axon_floor and int(types[idx]) == 2 and cur < axon_floor:
                cur = axon_floor
                changed = True
                _record_reason(reasons_by_idx, idx, "axon_floor")
            cur = _apply_bounds(
                cur,
                int(types[idx]),
                bounds,
                zero_only_small=zero_only_small,
                clamp_min=clamp_min,
                clamp_max=clamp_max,
            )
            if float(cur) != float(radii[idx]) or changed:
                radii[idx] = float(cur)


def _weighted_poly_smooth(x: np.ndarray, y: np.ndarray, center_x: float, polyorder: int, sigma_fraction: float) -> float:
    if len(x) <= 1:
        return float(y[0]) if len(y) else 0.0
    deg = min(max(1, int(polyorder)), len(x) - 1)
    x_local = np.asarray(x, dtype=float) - float(center_x)
    y_local = np.asarray(y, dtype=float)
    sigma = max(1e-6, float(max(np.ptp(x_local), 1.0) * max(0.05, float(sigma_fraction))))
    weights = np.exp(-0.5 * np.square(x_local / sigma))
    try:
        coeffs = np.polynomial.polynomial.polyfit(x_local, y_local, deg, w=weights)
        return float(np.polynomial.polynomial.polyval(0.0, coeffs))
    except Exception:
        return float(np.median(y_local))


def _savgol_pass(
    paths: list[list[int]],
    path_distances: list[np.ndarray] | None,
    radii: np.ndarray,
    types: np.ndarray,
    coords: np.ndarray,
    parent_idx: np.ndarray,
    bounds: dict[int, tuple[bool, float, float]],
    cfg: dict[str, Any],
    reasons_by_idx: dict[int, set[str]],
) -> None:
    smooth_cfg = dict(cfg.get("savgol", {}))
    if not bool(smooth_cfg.get("enabled", True)):
        return

    preserve_soma = bool(cfg.get("preserve_soma", True))
    zero_only_small = bool(cfg.get("small_radius_zero_only", True))
    replacement_cfg = dict(cfg.get("replacement", {}))
    clamp_min = float(replacement_cfg.get("clamp_min", 0.05))
    clamp_max = float(replacement_cfg.get("clamp_max", 30.0))
    window_nodes = max(5, int(smooth_cfg.get("window_nodes", 7)))
    if window_nodes % 2 == 0:
        window_nodes += 1
    half = window_nodes // 2
    polyorder = max(1, int(smooth_cfg.get("polyorder", 2)))
    sigma_fraction = float(smooth_cfg.get("gaussian_sigma_fraction", 0.5))

    axon_floor_cfg = dict(cfg.get("axon_floor", {}))
    use_axon_floor = bool(axon_floor_cfg.get("enabled", True))
    axon_floor = float(axon_floor_cfg.get("min_radius", 0.12))

    updated = np.array(radii, dtype=float, copy=True)
    for path_idx, path in enumerate(paths):
        if len(path) <= 2:
            continue
        if path_distances is not None and path_idx < len(path_distances):
            dist = np.asarray(path_distances[path_idx], dtype=float)
        else:
            dist = _path_distances(path, coords, parent_idx)
        for j, idx in enumerate(path):
            if preserve_soma and int(types[idx]) == 1:
                continue
            if int(idx) not in reasons_by_idx:
                continue
            lo = max(0, j - half)
            hi = min(len(path), j + half + 1)
            win_nodes = path[lo:hi]
            if len(win_nodes) <= polyorder:
                continue
            win_x = dist[lo:hi]
            win_y = np.asarray([float(radii[n]) for n in win_nodes], dtype=float)
            new_radius = _weighted_poly_smooth(
                win_x,
                win_y,
                float(dist[j]),
                polyorder,
                sigma_fraction,
            )
            new_radius = _clamp(new_radius, float(np.min(win_y)), float(np.max(win_y)))
            if use_axon_floor and int(types[idx]) == 2 and new_radius < axon_floor:
                new_radius = axon_floor
            new_radius = _apply_bounds(
                new_radius,
                int(types[idx]),
                bounds,
                zero_only_small=zero_only_small,
                clamp_min=clamp_min,
                clamp_max=clamp_max,
            )
            if not _is_valid_radius(new_radius):
                continue
            if abs(new_radius - float(radii[idx])) > 1e-9:
                updated[idx] = float(new_radius)
                _record_reason(reasons_by_idx, idx, "savitzky_golay")
    radii[:] = updated


def _clean_radii_single_pass(
    df: pd.DataFrame,
    cfg: dict[str, Any],
    *,
    static: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out = df.copy()
    if out.empty or "id" not in out.columns or "radius" not in out.columns or "parent" not in out.columns:
        return {"dataframe": out, "total_changes": 0, "change_details": [], "stats_by_type": {"type_stats": {}}}

    static_ok = False
    if isinstance(static, dict):
        cached_ids = np.asarray(static.get("ids", []), dtype=int)
        if len(cached_ids) == len(out):
            try:
                static_ok = np.array_equal(np.asarray(out["id"], dtype=int), cached_ids)
            except Exception:
                static_ok = False

    if static_ok:
        ids = np.asarray(static["ids"], dtype=int)
        types = np.asarray(static["types"], dtype=int)
        parents = np.asarray(static["parents"], dtype=int)
        coords = np.asarray(static["coords"], dtype=float)
        parent_idx = np.asarray(static["parent_idx"], dtype=int)
        children = static["children"]
        paths = static["paths"]
        path_distances = static.get("path_distances")
    else:
        ids = np.asarray(out["id"], dtype=int)
        types = np.asarray(out["type"], dtype=int) if "type" in out.columns else np.zeros(len(out), dtype=int)
        parents = np.asarray(out["parent"], dtype=int)
        coords = np.asarray(out.loc[:, ["x", "y", "z"]], dtype=float)
        parent_idx, children = _build_topology(ids, parents)
        paths = _segment_paths(parent_idx, children)
        path_distances = [_path_distances(path, coords, parent_idx) for path in paths]

    radii = np.array(out["radius"], dtype=float, copy=True)
    original = np.array(radii, dtype=float, copy=True)

    bounds = _compute_type_bounds(radii, types, cfg)
    replacement_cfg = dict(cfg.get("replacement", {}))
    clamp_min = float(replacement_cfg.get("clamp_min", 0.05))
    clamp_max = float(replacement_cfg.get("clamp_max", 30.0))

    valid_global = radii[np.isfinite(radii) & (radii > 0.0)]
    global_median = float(np.median(valid_global)) if valid_global.size else max(0.05, clamp_min)
    type_medians: dict[int, float] = {}
    for t in sorted({int(v) for v in types.tolist()}):
        vals = radii[(types == int(t)) & np.isfinite(radii) & (radii > 0.0)]
        if vals.size > 0:
            type_medians[int(t)] = float(np.median(vals))

    reasons_by_idx: dict[int, set[str]] = {}

    _local_median_pass(
        paths,
        radii,
        types,
        parent_idx,
        children,
        bounds,
        cfg,
        type_medians,
        global_median,
        reasons_by_idx,
    )
    _taper_pass(paths, radii, types, bounds, cfg, reasons_by_idx, reason_name="taper_cap")
    _savgol_pass(paths, path_distances, radii, types, coords, parent_idx, bounds, cfg, reasons_by_idx)
    _taper_pass(paths, radii, types, bounds, cfg, reasons_by_idx, reason_name="post_smooth_taper_cap")

    preserve_soma = bool(cfg.get("preserve_soma", True))
    if preserve_soma:
        soma_mask = types == 1
        radii[soma_mask] = original[soma_mask]

    out["radius"] = radii

    change_details: list[dict[str, Any]] = []
    for i in range(len(radii)):
        old_r = float(original[i])
        new_r = float(radii[i])
        if _same_radius(old_r, new_r, 1e-12):
            continue
        change_details.append(
            {
                "node_id": int(ids[i]),
                "old_radius": old_r,
                "new_radius": new_r,
                "reasons": sorted(reasons_by_idx.get(i, set())),
            }
        )
    change_details.sort(key=lambda row: int(row.get("node_id", -1)))

    return {
        "dataframe": out,
        "total_changes": len(change_details),
        "change_details": change_details,
        "stats_by_type": radii_stats_by_type(out),
    }


def clean_radii_dataframe(df: pd.DataFrame, *, rules: dict[str, Any] | None = None) -> dict[str, Any]:
    """Clean abnormal radii and return structured change details."""
    cfg = _deep_merge(DEFAULT_RULES, rules)
    cfg["preserve_soma"] = True
    if not isinstance(df, pd.DataFrame) or df.empty:
        out = df.copy() if isinstance(df, pd.DataFrame) else pd.DataFrame()
        return {"dataframe": out, "total_changes": 0, "change_details": [], "stats_by_type": {"type_stats": {}}, "passes": 0}

    fixed_cfg = dict(cfg.get("fixed_point", {}))
    fixed_enabled = bool(fixed_cfg.get("enabled", True))
    max_passes = max(1, int(fixed_cfg.get("max_passes", 20))) if fixed_enabled else 1
    min_effective_delta = max(0.0, float(fixed_cfg.get("min_effective_delta", 0.005)))

    current = df.copy()
    original = df.copy()
    reasons_by_id: dict[int, set[str]] = {}
    passes = 0
    static = _prepare_static_radii_context(current)

    def _visible_change_count(step_result: dict[str, Any]) -> int:
        count = 0
        for row in list(step_result.get("change_details", []) or []):
            try:
                if abs(float(row.get("new_radius", 0.0)) - float(row.get("old_radius", 0.0))) > max(1e-12, min_effective_delta):
                    count += 1
            except Exception:
                continue
        return count

    for _ in range(max_passes):
        passes += 1
        step = _clean_radii_single_pass(current, cfg, static=static)
        for row in list(step.get("change_details", []) or []):
            node_id = int(row.get("node_id", -1))
            if node_id < 0:
                continue
            reasons_by_id.setdefault(node_id, set()).update(str(v) for v in list(row.get("reasons", []) or []))
        next_df = step.get("dataframe")
        if not isinstance(next_df, pd.DataFrame) or next_df.empty:
            current = next_df if isinstance(next_df, pd.DataFrame) else current
            break
        if int(step.get("total_changes", 0)) <= 0:
            current = next_df
            break
        if np.array_equal(
            np.asarray(current["radius"], dtype=float),
            np.asarray(next_df["radius"], dtype=float),
        ):
            current = next_df
            break
        current = next_df
        if _visible_change_count(step) <= 0:
            break

    out = current.copy()
    old_lookup = {
        int(row["id"]): float(row["radius"])
        for _, row in original.loc[:, ["id", "radius"]].iterrows()
    }
    new_lookup = {
        int(row["id"]): float(row["radius"])
        for _, row in out.loc[:, ["id", "radius"]].iterrows()
    }
    change_details: list[dict[str, Any]] = []
    for node_id in sorted(set(old_lookup).intersection(new_lookup)):
        old_radius = float(old_lookup[node_id])
        new_radius = float(new_lookup[node_id])
        if _same_radius(old_radius, new_radius, max(1e-12, min_effective_delta)):
            continue
        change_details.append(
            {
                "node_id": int(node_id),
                "old_radius": old_radius,
                "new_radius": new_radius,
                "reasons": sorted(reasons_by_id.get(int(node_id), set())),
            }
        )

    return {
        "dataframe": out,
        "total_changes": len(change_details),
        "change_details": change_details,
        "stats_by_type": radii_stats_by_type(out),
        "passes": passes,
    }
