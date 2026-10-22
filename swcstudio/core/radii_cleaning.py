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
    # Plausible radius range (um) per node type. Fixed ranges, unlike
    # percentiles of the file's own radii, still hold when most of a file's
    # radii are wrong. A radius above the maximum is always flagged; one
    # below the minimum only when small_radius_zero_only is off, because
    # tiny values such as 0.004 are common placeholders for "unknown".
    # Only radii inside the range serve as reference or replacement values.
    "sanity_bounds": {
        "default": {"min_radius": 0.05, "max_radius": 10.0},
        "per_type": {
            "2": {"enabled": True, "min_radius": 0.05, "max_radius": 5.0},
            "3": {"enabled": True, "min_radius": 0.05, "max_radius": 8.0},
            "4": {"enabled": True, "min_radius": 0.05, "max_radius": 10.0},
        },
    },
    # Only a radius more than max_ratio times thicker or thinner than the
    # median of its plausible neighbours (both sides) counts as an outlier.
    "local_outlier": {
        "enabled": True,
        "window_nodes": 5,
        "max_ratio": 3.0,
    },
    # Cap each dendrite node at its parent's radius x (1 + slack). Off by
    # default: real dendrites do not taper monotonically, so the cap changed
    # correct radii, and one thin node thinned everything distal to it.
    "taper": {
        "enabled": False,
        "slack": 0.05,
    },
    # Raise axon radii below min_radius. Off by default: the axon's plausible
    # range already sets its minimum, and many real axons are thinner than
    # 0.12 um, so a floor changed most nodes of otherwise clean cells.
    "axon_floor": {
        "enabled": False,
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


def _plausible(v: float, lo: float, hi: float) -> bool:
    return _is_valid_radius(v) and lo <= float(v) <= hi


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


def _resolve_default_bounds(cfg: dict[str, Any]) -> dict[str, float]:
    """The plausible range for types without their own entry.

    Configs written before fixed ranges used ``global.lower_abs`` and
    ``global.upper_abs``; those still count as the range's ends.
    """
    sanity_bounds = cfg.get("sanity_bounds", {})
    sanity_bounds = sanity_bounds if isinstance(sanity_bounds, dict) else {}
    default_cfg = sanity_bounds.get("default", {})
    default_cfg = dict(default_cfg) if isinstance(default_cfg, dict) else {}
    legacy = sanity_bounds.get("global", {})
    legacy = dict(legacy) if isinstance(legacy, dict) else {}
    legacy_abs = dict(cfg.get("global_absolute_bounds", {}))
    return {
        "min_radius": float(legacy.get("lower_abs", legacy_abs.get("min", default_cfg.get("min_radius", 0.05)))),
        "max_radius": float(legacy.get("upper_abs", legacy_abs.get("max", default_cfg.get("max_radius", 10.0)))),
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
    types: np.ndarray,
    cfg: dict[str, Any],
) -> dict[int, tuple[bool, float, float]]:
    """``(enabled, min, max)`` plausible radius per node type in the file."""
    default = _resolve_default_bounds(cfg)
    preserve_soma = bool(cfg.get("preserve_soma", True))
    per_type_cfg = _resolve_type_thresholds(cfg)

    bounds: dict[int, tuple[bool, float, float]] = {}
    for t in sorted({int(v) for v in types.tolist()}):
        if preserve_soma and t == 1:
            bounds[t] = (False, 0.0, float("inf"))
            continue
        t_cfg = dict(per_type_cfg.get(int(t), {}))
        enabled = bool(t_cfg.get("enabled", True))
        # min_radius/max_radius. Configs from before fixed ranges said
        # lower_abs/upper_abs; the defaults never set those, so when present
        # they are the user's choice and win over the default range.
        lo = float(t_cfg.get("lower_abs", t_cfg.get("min_abs", t_cfg.get("min_radius", default["min_radius"]))))
        hi = float(t_cfg.get("upper_abs", t_cfg.get("max_abs", t_cfg.get("max_radius", default["max_radius"]))))
        if hi < lo:
            lo, hi = hi, lo
        bounds[t] = (enabled, lo, hi)
    return bounds


def _type_range(t: int, bounds: dict[int, tuple[bool, float, float]]) -> tuple[float, float]:
    _enabled, lo, hi = bounds.get(int(t), (True, 0.0, float("inf")))
    return float(lo), float(hi)


def _nearest_valid_parent(
    i: int,
    parent_idx: np.ndarray,
    radii: np.ndarray,
    lo: float = 0.0,
    hi: float = float("inf"),
) -> float | None:
    p = int(parent_idx[i])
    while p >= 0:
        val = float(radii[p])
        if _plausible(val, lo, hi):
            return val
        p = int(parent_idx[p])
    return None


def _nearest_valid_children(
    i: int,
    children: list[list[int]],
    radii: np.ndarray,
    *,
    max_depth: int = 6,
    lo: float = 0.0,
    hi: float = float("inf"),
) -> list[float]:
    out: list[float] = []
    queue: list[tuple[int, int]] = [(child, 1) for child in children[i]]
    best_depth = 0
    while queue:
        idx, depth = queue.pop(0)
        if depth > max_depth:
            continue
        val = float(radii[idx])
        if _plausible(val, lo, hi):
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
    lo: float = 0.0,
    hi: float = float("inf"),
) -> float:
    vals: list[float] = []
    parent_val = _nearest_valid_parent(i, parent_idx, radii, lo, hi)
    if parent_val is not None:
        vals.append(parent_val)
    child_vals = _nearest_valid_children(i, children, radii, lo=lo, hi=hi)
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
    # A radius this many times thicker or thinner than its neighbours is an
    # outlier. On expert-traced cells 99% of radii lie within x1.9 of their
    # neighbours and 0.5% beyond x3, so x3 flags errors, not natural variation.
    max_ratio = max(1.0, float(local_cfg.get("max_ratio", 3.0)))

    # Single-node paths too: they have no window, but the range check and
    # the non-positive repair still apply.
    for path in paths:
        for j, idx in enumerate(path):
            if preserve_soma and int(types[idx]) == 1:
                continue
            cur = float(radii[idx])
            plo, phi = _type_range(int(types[idx]), bounds)
            left_vals = [float(radii[n]) for n in path[max(0, j - half) : j]
                         if _plausible(float(radii[n]), plo, phi)]
            right_vals = [float(radii[n]) for n in path[j + 1 : min(len(path), j + half + 1)]
                          if _plausible(float(radii[n]), plo, phi)]
            win_vals = left_vals + right_vals
            local_median = _small_median(win_vals)

            flagged: list[str] = []
            if not math.isfinite(cur):
                flagged.append("non_finite")
            elif cur <= 0.0:
                flagged.append("non_positive")
            elif local_median is not None and left_vals and right_vals:
                # Judged against plausible neighbours on both sides, so the end
                # of a path or one bad neighbour cannot make a node look wrong.
                ratio = cur / max(local_median, 1e-9)
                if ratio > max_ratio or ratio < 1.0 / max_ratio:
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
                    plo,
                    phi,
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
    use_taper = bool(taper_cfg.get("enabled", False))
    axon_floor_cfg = dict(cfg.get("axon_floor", {}))
    use_axon_floor = bool(axon_floor_cfg.get("enabled", False))
    axon_floor = float(axon_floor_cfg.get("min_radius", 0.12))
    if not (use_taper or use_axon_floor):
        return

    preserve_soma = bool(cfg.get("preserve_soma", True))
    zero_only_small = bool(cfg.get("small_radius_zero_only", True))
    replacement_cfg = dict(cfg.get("replacement", {}))
    clamp_min = float(replacement_cfg.get("clamp_min", 0.05))
    clamp_max = float(replacement_cfg.get("clamp_max", 30.0))
    slack = max(0.0, float(taper_cfg.get("slack", 0.05)))

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
            changed = False
            if not _is_valid_radius(cur):
                _record_reason(reasons_by_idx, idx, "non_positive" if math.isfinite(cur) else "non_finite")
                cur = clamp_min
                changed = True
            plo, phi = _type_range(int(types[idx]), bounds)
            # A placeholder (e.g. 0.004) or implausibly thick parent says
            # nothing about how thick its child may be.
            if use_taper and int(types[idx]) != 2 and _plausible(prev_radius, plo, phi):
                max_allowed = prev_radius * (1.0 + slack)
                if cur > max_allowed:
                    cur = max_allowed
                    changed = True
                    _record_reason(reasons_by_idx, idx, reason_name)
            if use_axon_floor and int(types[idx]) == 2 and cur < axon_floor:
                cur = axon_floor
                changed = True
                _record_reason(reasons_by_idx, idx, "axon_floor")
            # Only a value this pass changed is bounded; clamping every node
            # would silently raise small radii (e.g. 0.035) to clamp_min.
            if changed:
                radii[idx] = float(_apply_bounds(
                    cur,
                    int(types[idx]),
                    bounds,
                    zero_only_small=zero_only_small,
                    clamp_min=clamp_min,
                    clamp_max=clamp_max,
                ))


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
    use_axon_floor = bool(axon_floor_cfg.get("enabled", False))
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
            plo, phi = _type_range(int(types[idx]), bounds)
            keep = [k for k, n in enumerate(win_nodes) if _plausible(float(radii[n]), plo, phi)]
            if len(keep) <= polyorder:
                continue
            win_x = dist[lo:hi][keep]
            win_y = np.asarray([float(radii[win_nodes[k]]) for k in keep], dtype=float)
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

    bounds = _compute_type_bounds(types, cfg)
    replacement_cfg = dict(cfg.get("replacement", {}))
    clamp_min = float(replacement_cfg.get("clamp_min", 0.05))
    clamp_max = float(replacement_cfg.get("clamp_max", 30.0))

    # Fallback values come from plausible radii only; a type with none gets
    # its own median pulled into its range.
    plausible_mask = np.zeros(len(radii), dtype=bool)
    type_medians: dict[int, float] = {}
    for t in sorted({int(v) for v in types.tolist()}):
        plo, phi = _type_range(int(t), bounds)
        of_type = types == int(t)
        ok = of_type & np.isfinite(radii) & (radii >= plo) & (radii <= phi) & (radii > 0.0)
        plausible_mask |= ok
        if ok.any():
            type_medians[int(t)] = float(np.median(radii[ok]))
        else:
            valid = radii[of_type & np.isfinite(radii) & (radii > 0.0)]
            if valid.size:
                type_medians[int(t)] = _clamp(float(np.median(valid)), max(plo, clamp_min), min(phi, clamp_max))
    plausible_all = radii[plausible_mask]
    global_median = float(np.median(plausible_all)) if plausible_all.size else max(0.05, clamp_min)

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
