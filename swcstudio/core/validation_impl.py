"""Validation implementation moved to core.

This module contains the full validation logic formerly located in
`swcstudio.gui.validation_core`. It lives in `swcstudio.core` so both the GUI and
CLI can import a single authoritative implementation.
"""
import os
import io
import hashlib
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Tuple, Dict, Any, Iterable

import numpy as np
import morphio
from neurom.core import Morphology
from neurom.check import morphology_checks as checks

# Keep MorphIO quiet
morphio.set_maximum_warnings(0)

# --------- HUMAN-READABLE LABELS ----------
FRIENDLY_LABELS = {
    "has_all_nonzero_neurite_radii":    "All neurite radii are positive",
    "has_all_nonzero_section_lengths":  "All section lengths are positive",
    "has_all_nonzero_segment_lengths":  "All segment lengths are positive",
    "has_apical_dendrite":              "Apical dendrite present",
    "has_axon":                         "Axon present",
    "has_basal_dendrite":               "Basal dendrite present",
    "has_multifurcation":               "Contains multifurcation",
    "has_no_back_tracking":             "No geometric backtracking",
    "has_no_dangling_branch":           "No dangling branches",
    "has_no_fat_ends":                  "No oversized terminal ends",
    "has_no_flat_neurites":             "No flattened neurites",
    "has_no_jumps":                     "No large section z-axis jumps",
    "has_no_narrow_neurite_section":    "No extremely narrow sections",
    "has_no_narrow_start":              "No extremely narrow branch starts",
    "has_no_overlapping_point":         "No duplicate 3D points",
    "has_no_root_node_jumps":           "Neurite roots too far from soma",
    "has_no_single_children":           "No single-child chains",
    "has_nonzero_soma_radius":          "Soma radius is positive",
    "has_unifurcation":                 "Contains unifurcation",
    # custom:
    "has_soma":                         "Soma present",
}


def _friendly_label(name: str) -> str:
    if name in FRIENDLY_LABELS:
        return FRIENDLY_LABELS[name]
    base = name[4:] if name.startswith("has_") else name
    return base.replace("_", " ").capitalize()

# --------- PRE-RESOLVE CHECKS (avoid per-call reflection) ----------
_CHECK_FUNCS: List[Tuple[str, Any, bool]] = []
for _name in dir(checks):
    if not _name.startswith("has_"):
        continue
    _func = getattr(checks, _name)
    if callable(_func):
        co = getattr(_func, "__code__", None)
        co_vars = getattr(co, "co_varnames", ())
        _CHECK_FUNCS.append((_name, _func, "neurite_filter" in co_vars))

# Fixed-set selection:
#   - include everything EXCEPT the very slow "has_no_back_tracking"
#   - explicitly keep "has_no_overlapping_point"
_EXCLUDE = {"has_no_back_tracking"}


def _selected_checks() -> Iterable[Tuple[str, Any, bool]]:
    for n, f, nf in _CHECK_FUNCS:
        if n in _EXCLUDE:
            continue
        yield (n, f, nf)

import warnings


def _run_one_check(name, func, needs_nf, morph):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            if needs_nf:
                return name, bool(func(morph, neurite_filter=None))
            return name, bool(func(morph))
    except Exception as e:
        return name, f"ERROR: {e}"

# --------- FAST I/O HELPERS ----------
_SWCTYPE = np.dtype([
    ("id",     np.int64),
    ("type",   np.int64),
    ("x",      np.float64),
    ("y",      np.float64),
    ("z",      np.float64),
    ("radius", np.float64),
    ("parent", np.int64),
])


def _swc_rows_only(swc_text: str) -> str:
    """The lines that are SWC rows: seven or more values once any ``#`` comment is cut.

    Other lines are skipped, as ``swc_io.parse_swc_text_preserve_tokens`` skips
    them, so a stray header line (Vaa3D writes ``autoset #bkg_thresh = 1``) does
    not set the column count for the whole file.
    """
    return "\n".join(line for line in swc_text.splitlines() if len(line.split("#", 1)[0].split()) >= 7)


def _load_swc_to_array(swc_text: str) -> np.ndarray:
    """Fast parser using NumPy; reads only the lines that are SWC rows."""
    buf = io.StringIO(_swc_rows_only(swc_text))
    arr = np.genfromtxt(
        buf,
        comments="#",
        dtype=_SWCTYPE,
        invalid_raise=False,
        autostrip=True
    )
    if arr.size == 0:
        return np.array([], dtype=_SWCTYPE)
    if arr.ndim == 0:  # single-line files
        arr = arr.reshape(1)
    return arr


def _sanitize_types_inplace(arr: np.ndarray) -> None:
    """type==0 or type>7 -> 7 (in-place, vectorized)."""
    t = arr["type"]
    bad = (t == 0) | (t > 7)
    if np.any(bad):
        t[bad] = 7


def _write_array_to_tmp_swc(arr: np.ndarray) -> str:
    """Write array back to a temp .swc path (fast)."""
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".swc")
    os.close(tmp_fd)
    stacked = np.column_stack([
        arr["id"], arr["type"], arr["x"], arr["y"], arr["z"], arr["radius"], arr["parent"]
    ])
    np.savetxt(tmp_path, stacked, fmt=["%d","%d","%.10g","%.10g","%.10g","%.10g","%d"], delimiter=" ")
    return tmp_path


def _reindex_swc_array(arr: np.ndarray) -> np.ndarray:
    """Return a copy with continuous 1..N ids and remapped parent ids."""
    out = np.array(arr, copy=True)
    if out.size == 0:
        return out
    old_ids = np.asarray(out["id"], dtype=np.int64)
    new_ids = np.arange(1, len(out) + 1, dtype=np.int64)
    id_map = {int(old_id): int(new_id) for old_id, new_id in zip(old_ids.tolist(), new_ids.tolist())}
    out["id"] = new_ids
    remapped_parents = np.asarray(out["parent"], dtype=np.int64).copy()
    for i in range(len(remapped_parents)):
        parent_id = int(remapped_parents[i])
        if parent_id == -1:
            continue
        remapped_parents[i] = int(id_map.get(parent_id, -1))
    out["parent"] = remapped_parents
    return out


def _sha1(data: bytes) -> str:
    h = hashlib.sha1()
    h.update(data)
    return h.hexdigest()

# --------- IN-MEMORY CACHE (by sanitized bytes) ----------
_CACHE: Dict[str, Tuple[Dict[str, Any], List[Dict[str, Any]]]] = {}
# key -> (results_dict, rows)


def clear_cache() -> None:
    """Clear the validation results cache (e.g. after edits)."""
    _CACHE.clear()

# --------- CUSTOM ORDERING FOR TABLE ROWS ----------
# Put these first (exact order), then all others follow in their usual alpha order.
_FIRST_SIX = [
    "has_soma",
    "has_nonzero_soma_radius",
    "has_axon",
    "has_basal_dendrite",
    "has_apical_dendrite",
    "has_no_dangling_branch",
]
_PRIORITY_MAP = {name: idx for idx, name in enumerate(_FIRST_SIX)}


def _row_sort_key(code_name: str, friendly: str) -> tuple:
    """Primary: our custom priority; Secondary: friendly name for stable ordering."""
    pri = _PRIORITY_MAP.get(code_name, 1_000_000)
    return (pri, friendly.lower())

# --------- MAIN ENTRY ----------
def run_format_validation_from_text(swc_text: str):
    """
    Input:
      swc_text: raw SWC string
    Returns:
      results_dict: { check_name: bool or "ERROR: ..." }
      sanitized_swc_bytes: bytes (the exact file used for checks)
      table_rows: [ { "check": <friendly>, "status": <bool or 'ERROR: ...'> }, ... ]
    """
    # Parse + sanitize
    arr = _load_swc_to_array(swc_text)
    _sanitize_types_inplace(arr)

    # Serialize sanitized file once
    tmp_path = _write_array_to_tmp_swc(arr)
    try:
        with open(tmp_path, "rb") as f:
            sanitized_bytes = f.read()
        cache_key = _sha1(sanitized_bytes)

        # Cache hit?
        hit = _CACHE.get(cache_key)
        if hit is not None:
            results, rows = hit
            return results, sanitized_bytes, rows

        # Build morphology and run NeuroM checks
        results: Dict[str, Any] = {}
        try:
            raw = morphio.Morphology(
                tmp_path,
                options=morphio.Option.allow_unifurcated_section_change
            )
            morph = Morphology(raw)

            # Run the fixed check set in parallel
            max_workers = min(8, (os.cpu_count() or 2))
            selected = list(_selected_checks())
            with ThreadPoolExecutor(max_workers=max_workers) as ex:
                futs = [ex.submit(_run_one_check, n, f, nf, morph) for (n, f, nf) in selected]
                for fut in as_completed(futs):
                    name, value = fut.result()
                    results[name] = value

            # Custom: has_soma (quick)
            try:
                has_soma_points = hasattr(raw, "soma") and getattr(raw.soma, "points", None) is not None \
                                  and len(raw.soma.points) > 0
            except Exception:
                has_soma_points = False
            has_soma_by_type = bool(np.any(arr["type"] == 1))
            results["has_soma"] = bool(has_soma_points or has_soma_by_type)

        except Exception as morph_err:
            # MorphIO could not load the file (e.g. multiple somas).
            # Populate what we CAN determine from the raw array alone.
            results["has_soma"] = bool(np.any(arr["type"] == 1))
            # Mark all NeuroM checks we would have run as errors
            for name, _func, _nf in _selected_checks():
                if name not in results:
                    results[name] = f"ERROR: {morph_err}"

        # Custom overrides: has_axon / has_basal_dendrite / has_apical_dendrite
        results["has_axon"] = bool(np.any(arr["type"] == 2))
        results["has_basal_dendrite"] = bool(np.any(arr["type"] == 3))
        results["has_apical_dendrite"] = bool(np.any(arr["type"] == 4))

        # Custom override: has_no_dangling_branch
        roots_count = np.sum(arr["parent"] == -1)
        results["has_no_dangling_branch"] = bool(roots_count == 1)

        # Build human-readable rows with the new custom ordering
        rows_unsorted = [(code, _friendly_label(code), status) for code, status in results.items()]
        rows_unsorted.sort(key=lambda t: _row_sort_key(t[0], t[1]))  # sort by our priority, then friendly label
        rows = [{"check": friendly, "status": status} for (code, friendly, status) in rows_unsorted]

        # Save to cache and return
        _CACHE[cache_key] = (results, rows)
        return results, sanitized_bytes, rows

    finally:
        try:
            os.remove(tmp_path)
        except FileNotFoundError:
            pass


def _split_swc_by_trees(swc_text: str):
    """Split SWC text into per-tree subsets via BFS from each root.
    Non-soma trees (dangling branches) are grouped with the nearest soma
    tree by Euclidean distance, without modifying parent values.
    Returns list of (root_id, sub_swc_text, node_count) sorted by root_id ascending.
    """
    arr = _load_swc_to_array(swc_text)
    if arr.size == 0:
        return []

    ids = arr["id"]
    types = arr["type"]
    parents = arr["parent"]
    xyz = np.column_stack((arr["x"], arr["y"], arr["z"])) .astype(np.float64)

    id_to_idx = {int(ids[i]): i for i in range(len(ids))}

    # Find roots (parent < 0)
    roots = [int(ids[i]) for i in range(len(ids)) if parents[i] < 0]

    # Build children map
    children_map = {}
    for i in range(len(ids)):
        pid = int(parents[i])
        if pid >= 0:
            children_map.setdefault(pid, []).append(int(ids[i]))

    # BFS from each root to classify trees
    soma_trees = []   # [(root_id, member_set)]
    dangling_trees = []  # [(root_id, member_set)]

    for root_id in sorted(roots):
        members = set()
        queue = [root_id]
        has_soma = False
        while queue:
            nid = queue.pop(0)
            if nid in members:
                continue
            members.add(nid)
            idx = id_to_idx.get(nid)
            if idx is not None and int(types[idx]) == 1:
                has_soma = True
            for child in children_map.get(nid, []):
                queue.append(child)

        if has_soma:
            soma_trees.append((root_id, members))
        else:
            dangling_trees.append((root_id, members))

    # Group dangling trees with nearest soma tree
    if soma_trees and dangling_trees:
        soma_node_indices = [i for i in range(len(ids)) if int(types[i]) == 1]
        soma_xyz = xyz[soma_node_indices]
        soma_ids_arr = ids[soma_node_indices]

        soma_to_tree_idx = {}
        for tidx, (_, members) in enumerate(soma_trees):
            for sid in soma_ids_arr:
                if int(sid) in members:
                    soma_to_tree_idx[int(sid)] = tidx

        for _droot_id, dmembers in dangling_trees:
            droot_idx = id_to_idx[_droot_id]
            droot_xyz = xyz[droot_idx]
            dists = np.linalg.norm(soma_xyz - droot_xyz, axis=1)
            nearest_soma_idx = int(np.argmin(dists))
            nearest_soma_id = int(soma_ids_arr[nearest_soma_idx])
            target_tree_idx = soma_to_tree_idx.get(nearest_soma_id, 0)
            soma_trees[target_tree_idx][1].update(dmembers)

    tree_groups = soma_trees if soma_trees else [(rid, members) for rid, members in [(r, set()) for r in roots]]
    if not soma_trees:
        tree_groups = []
        for root_id in sorted(roots):
            members = set()
            queue = [root_id]
            while queue:
                nid = queue.pop(0)
                if nid in members:
                    continue
                members.add(nid)
                for child in children_map.get(nid, []):
                    queue.append(child)
            tree_groups.append((root_id, members))

    trees = []
    for root_id, members in tree_groups:
        sub_rows = []
        for i in range(len(ids)):
            if int(ids[i]) in members:
                sub_rows.append(arr[i])

        if not sub_rows:
            continue

        sub_arr = np.array(sub_rows, dtype=_SWCTYPE)
        tmp_path = _write_array_to_tmp_swc(sub_arr)
        try:
            with open(tmp_path, "r") as f:
                sub_text = f.read()
        finally:
            try:
                os.remove(tmp_path)
            except FileNotFoundError:
                pass

        trees.append((root_id, sub_text, len(members)))

    return trees


def _split_swc_by_soma_roots(swc_text: str):
    """Split SWC into cell files using only soma roots (type==1 and parent==-1).

    Returns list of (soma_root_id, sub_swc_text, node_count).
    Non-soma roots are attached to the nearest soma-root cell by Euclidean distance.
    If no soma root exists, returns an empty list.
    """
    arr = _load_swc_to_array(swc_text)
    if arr.size == 0:
        return []

    ids = arr["id"]
    types = arr["type"]
    parents = arr["parent"]
    xyz = np.column_stack((arr["x"], arr["y"], arr["z"])) .astype(np.float64)

    id_to_idx = {int(ids[i]): i for i in range(len(ids))}

    # Build children map once
    children_map = {}
    for i in range(len(ids)):
        pid = int(parents[i])
        if pid >= 0:
            children_map.setdefault(pid, []).append(int(ids[i]))

    # True cell roots must be soma roots only
    soma_roots = [
        int(ids[i]) for i in range(len(ids))
        if int(parents[i]) == -1 and int(types[i]) == 1
    ]
    soma_roots = sorted(set(soma_roots))
    if not soma_roots:
        return []

    # Any other root-like entries get merged into nearest soma-root cell
    other_roots = [
        int(ids[i]) for i in range(len(ids))
        if int(parents[i]) < 0 and int(ids[i]) not in soma_roots
    ]
    other_roots = sorted(set(other_roots))

    def collect_members(root_id: int) -> set[int]:
        members = set()
        queue = [root_id]
        while queue:
            nid = queue.pop(0)
            if nid in members:
                continue
            members.add(nid)
            for child in children_map.get(nid, []):
                queue.append(child)
        return members

    tree_groups = [(root_id, collect_members(root_id)) for root_id in soma_roots]

    # Attach dangling non-soma roots to nearest soma-root tree
    soma_root_xyz = {
        root_id: xyz[id_to_idx[root_id]]
        for root_id in soma_roots
        if root_id in id_to_idx
    }
    for root_id in other_roots:
        droot_xyz = xyz[id_to_idx[root_id]]
        nearest_root = min(
            soma_roots,
            key=lambda rid: np.linalg.norm(soma_root_xyz[rid] - droot_xyz),
        )
        target_idx = soma_roots.index(nearest_root)
        tree_groups[target_idx][1].update(collect_members(root_id))

    trees = []
    for root_id, members in tree_groups:
        sub_rows = [arr[i] for i in range(len(ids)) if int(ids[i]) in members]
        if not sub_rows:
            continue

        sub_arr = np.array(sub_rows, dtype=_SWCTYPE)
        tmp_path = _write_array_to_tmp_swc(sub_arr)
        try:
            with open(tmp_path, "r") as f:
                sub_text = f.read()
        finally:
            try:
                os.remove(tmp_path)
            except FileNotFoundError:
                pass

        trees.append((root_id, sub_text, len(members)))

    return trees


def run_per_tree_validation(swc_text: str):
    """
    Run validation checks on each tree separately.
    Returns:
      check_names: ordered list of (code_name, friendly_label)
      tree_results: list of (root_id, node_count, {code_name: bool|str})
    """
    trees = _split_swc_by_soma_roots(swc_text)
    if not trees:
        # Fallback keeps validation usable for malformed files with no soma root.
        trees = _split_swc_by_trees(swc_text)

    if not trees:
        return [], []

    # If single tree, just run normal validation
    all_check_names = set()
    tree_results = []

    for root_id, sub_text, node_count in trees:
        results, _bytes, _rows = run_format_validation_from_text(sub_text)
        all_check_names.update(results.keys())
        tree_results.append((root_id, node_count, results))

    # Build ordered check names list
    check_list = [(code, _friendly_label(code)) for code in all_check_names]
    check_list.sort(key=lambda t: _row_sort_key(t[0], t[1]))

    return check_list, tree_results
