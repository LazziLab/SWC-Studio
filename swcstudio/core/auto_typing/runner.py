"""Public entry points for the auto-typing engine.

SWC-Studio labels with the SWC auto-labeler: ``swcstudio.core.labeler`` is a copy
of the released labeler's code and ``labeler-<version>`` its model set, made by
``scripts/sync_labeler.py``. For each file:

* Stage 0 checks that the file is one connected cell with a soma and finite
  geometry. Before that, SWC-Studio checks the file's own node lines: every
  node id used once, and no node that is its own ancestor (a parent link to
  itself or a loop of parent links). A file that fails is not labeled.
* Stage 1 decides the cell class: 3-class (axon, basal, apical) or 2-class
  (axon, dendrite). The user may choose the class instead; Stage 1 then still
  runs, and a cell whose chosen class differs from Stage 1's prediction is
  labeled with the chosen class and flagged as a class mismatch.
* Stages 2 and 3 label each primary subtree, apply the topology rules and, for
  3-class cells, the proximal branch correction.
* Stage 4 scores the quality flag: the cell's predicted per-cell F1. A single
  file is flagged when that falls below the flag's fixed cut-off. A batch flags
  the riskiest share of each cell class (``BatchOptions.flag_budget``, 10% by
  default); a class with too few cells for that share uses the fixed cut-off.

Labeled output (a copy, or the file itself for the in-place paths of the CLI
and GUI) is the input text with only the type field of each relabeled node line
rewritten (``labeled_text``); every other byte is kept.

Public surface:

* ``run_file(path, opts, ...)`` returns ``FileResult``
* ``run_batch(folder, opts, ...)`` returns ``BatchResult``
* ``resolve_models(model_dir)`` returns the model folder and its source
* ``is_available()`` returns ``(bool, reason)``
* ``backend_status()`` returns a diagnostic dict
"""
from __future__ import annotations

import csv
import json
import math
import os
import tempfile
import zipfile
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator

from swcstudio.core.swc_io import (
    BOM,
    decode_swc_bytes,
    encode_swc_text,
    is_swc_data_file,
    rewrite_swc_fields,
    split_swc_lines,
    strip_line_bom,
    swc_data_rows_from_text,
)
from swcstudio.core.model_paths import (
    MODEL_FILES,
    ModelSelection,
    available_models,
    diagnostic_search_report,
    labeler_info,
    model_record,
    select_model_dir,
)
from swcstudio.core.reporting import (
    auto_typing_log_path_for_file,
    operation_output_dir_for_folder,
    operation_output_path_for_file,
    operation_report_path_for_file,
    operation_report_path_for_folder,
    timestamp_slug,
    write_text_report,
)

from ._pickle_compat import labeler_pickle_aliases
from .cell_class import cell_class_name, normalize_cell_class
from .types import BatchOptions, BatchResult, FileResult

FLAG_REVIEW_JSON = "flag_review.json"
FLAG_REVIEW_CSV = "flag_review.csv"


# ---------------------------------------------------------------------------
# Availability / diagnostics
# ---------------------------------------------------------------------------


def _missing_models_message(model_dir: str | os.PathLike | None) -> str:
    return "Auto-labeling cannot find its model files.\n" + diagnostic_search_report(override=model_dir)


def _package_problem() -> str | None:
    try:
        import joblib  # noqa: F401
        import pandas  # noqa: F401
        import sklearn  # noqa: F401
        import xgboost  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return (
            "Auto-labeling could not import one of its required packages "
            f"(import failed: {exc.__class__.__name__}: {exc}). "
            "Repair the active environment with "
            "`python -m pip install --upgrade --force-reinstall swcstudio`, "
            "then run `swcstudio doctor`."
        )
    return None


def is_available(*, model_dir: str | os.PathLike | None = None,
                 auto_download: bool = False) -> tuple[bool, str]:
    """Return ``(ok, reason)`` describing whether auto-labeling can run now.

    It needs a complete model folder (see :mod:`swcstudio.core.model_paths`:
    ``model_dir``, else ``SWCSTUDIO_MODEL_DIR``, else ``model_dir`` in the
    auto-typing configuration, else the default search) and the packages the
    labeler imports: joblib, scikit-learn, XGBoost and pandas.

    This is a probe: it never downloads models unless ``auto_download`` is set.
    """
    if select_model_dir(model_dir, auto_download=auto_download) is None:
        return False, _missing_models_message(model_dir)
    problem = _package_problem()
    if problem is not None:
        return False, problem
    return True, "available"


def resolve_models(model_dir: str | os.PathLike | None = None, *,
                   auto_download: bool = True) -> ModelSelection:
    """The model folder a run uses and where it came from; resolve it once per run.

    ``model_dir`` is the explicit argument; without it ``SWCSTUDIO_MODEL_DIR``,
    then ``model_dir`` in the auto-typing configuration, then the default
    search decide. Raises ``FileNotFoundError`` with the search report when no
    complete folder is found.
    """
    selection = select_model_dir(model_dir, auto_download=auto_download)
    if selection is None:
        raise FileNotFoundError(_missing_models_message(model_dir))
    return selection


def backend_status(*, model_dir: str | None = None) -> dict[str, Any]:
    """Structured status report, for ``swcstudio models status`` and the GUI."""
    info = labeler_info()
    selection = select_model_dir(model_dir, auto_download=False)
    folder = selection.folder if selection is not None else None
    files = available_models(model_dir)
    status: dict[str, Any] = {
        "labeler_version": info["version"],
        "labeler_commit": info["source_commit"],
        "model_dir": str(folder) if folder is not None else None,
        "model_source": selection.source if selection is not None else None,
        "models_ok": folder is not None,
        "search_diagnostic": diagnostic_search_report(override=model_dir),
    }
    for key in MODEL_FILES:
        status[f"{key}_path"] = str(files[key]) if files[key] is not None else None
        status[f"{key}_ok"] = files[key] is not None
    return status


# ---------------------------------------------------------------------------
# SWC parse / write / diff helpers
# ---------------------------------------------------------------------------


def read_swc_text(path: str | os.PathLike) -> str:
    """The file's text with every byte kept (see :func:`swc_io.decode_swc_bytes`)."""
    return decode_swc_bytes(Path(path).read_bytes())


def _parse_swc(path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    """``(comment_lines, node_rows)`` of the file, node lines in file order."""
    return swc_data_rows_from_text(read_swc_text(path))


def _radius_changed(old: float, new: float) -> bool:
    old, new = float(old), float(new)
    return not (old == new or (math.isnan(old) and math.isnan(new)))


def labeled_text(text: str, rows: list[dict[str, Any]], types: list[int], radii: list[float]) -> str:
    """``text`` with the labeling result written in: the type field of each node
    whose type changed, and the radius field of each node whose radius changed.

    ``rows`` are the node rows parsed from ``text`` (as ``FileResult.rows``),
    ``types`` and ``radii`` the result for each. Every other byte of ``text`` is
    kept (see :func:`swc_io.rewrite_swc_fields`). Raises ``ValueError`` when the
    rows are not the node lines of ``text``.
    """
    _headers, current = swc_data_rows_from_text(text)
    if [(r["id"], r["type"], r["parent"]) for r in current] != \
            [(int(r["id"]), int(r["type"]), int(r["parent"])) for r in rows]:
        raise ValueError("the file's node lines are not the ones that were labeled "
                         "(was it changed while labeling ran?)")
    new_types = {int(r["id"]): int(t) for r, t in zip(rows, types) if int(t) != int(r["type"])}
    new_radii = {int(r["id"]): float(rad) for r, rad in zip(rows, radii)
                 if _radius_changed(r["radius"], rad)}
    return rewrite_swc_fields(text, new_types, new_radii)


def _write_labeled_copy(path: Path, text: str, rows: list[dict[str, Any]],
                        types: list[int], radii: list[float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encode_swc_text(labeled_text(text, rows, types, radii)))


def _id_list(ids: list[int], limit: int = 10) -> str:
    shown = ", ".join(str(i) for i in ids[:limit])
    return shown + (f" and {len(ids) - limit} more" if len(ids) > limit else "")


def structural_violations(rows: list[dict[str, Any]]) -> list[str]:
    """What is wrong with the node lines themselves, before the labeler reads them.

    Labels are matched back to the file by node id, and the labeler's own
    checks do not see a node detached in a loop of parent links. So a node id
    used on more than one line, a node that is its own parent, and parent
    links that form a loop are rejected here. Returns the reasons; empty when
    the rows pass.
    """
    counts = Counter(int(r["id"]) for r in rows)
    duplicated = sorted(i for i, n in counts.items() if n > 1)
    if duplicated:
        # Parent links cannot be followed while an id names two nodes.
        noun = "node id" if len(duplicated) == 1 else "node ids"
        return [f"{noun} used on more than one line: {_id_list(duplicated)}"]
    reasons: list[str] = []
    parent_of = {int(r["id"]): int(r["parent"]) for r in rows}
    own_parent = sorted(i for i, p in parent_of.items() if p == i)
    if own_parent:
        reasons.append(f"node {_id_list(own_parent)} is its own parent" if len(own_parent) == 1
                       else f"nodes {_id_list(own_parent)} are their own parents")
    # Follow each node's parent links; a walk that comes back to a node of
    # the same walk has found a loop. Each node is walked once.
    done: set[int] = set()
    in_loop: set[int] = set()
    for start in parent_of:
        if start in done:
            continue
        position: dict[int, int] = {}
        path: list[int] = []
        node = start
        while node in parent_of and node not in done:
            if node in position:
                in_loop.update(path[position[node]:])
                break
            position[node] = len(path)
            path.append(node)
            node = parent_of[node]
        done.update(path)
    loop = sorted(in_loop - set(own_parent))
    if loop:
        reasons.append(f"parent links form a loop through nodes {_id_list(loop)}")
    return reasons


def _reject_broken_structure(name: str, rows: list[dict[str, Any]]) -> None:
    reasons = structural_violations(rows)
    if reasons:
        raise ValueError(f"{name}: QC rejected; " + "; ".join(reasons))


def _check_auto_label_eligibility(rows: list[dict[str, Any]]) -> str | None:
    """Fast pre-flight check on parsed SWC rows.

    Auto-labeling assumes the file describes one cell. A file with several
    disconnected groups of soma nodes holds several cells and needs splitting
    first. Returns ``None`` when the file is eligible, or a short reason.
    """
    if not rows:
        return "no valid SWC rows"
    soma_indices = [i for i, r in enumerate(rows) if int(r["type"]) == 1]
    if len(soma_indices) <= 1:
        return None
    id_to_idx = {int(r["id"]): i for i, r in enumerate(rows)}
    soma_set = set(soma_indices)
    parent_idx_of = [id_to_idx.get(int(r["parent"])) for r in rows]
    soma_adj: dict[int, set[int]] = {i: set() for i in soma_indices}
    for child in soma_indices:
        p = parent_idx_of[child]
        if p in soma_set:
            soma_adj[child].add(p)
            soma_adj[p].add(child)
    seen: set[int] = set()
    components = 0
    for start in soma_indices:
        if start in seen:
            continue
        stack = [start]
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(n for n in soma_adj[cur] if n not in seen)
        components += 1
    if components > 1:
        return (f"multi-soma file ({components} disconnected soma groups); "
                "split with `batch split` before auto-labeling")
    return None


def _build_change_details(
    file_name: str,
    rows: list[dict[str, Any]],
    orig_types: list[int],
    new_types: list[int],
    orig_radii: list[float],
    new_radii: list[float],
) -> list[str]:
    out: list[str] = []
    type_changes = sum(1 for old, new in zip(orig_types, new_types) if int(old) != int(new))
    radius_changes = sum(1 for old, new in zip(orig_radii, new_radii) if float(old) != float(new))
    if type_changes <= 0 and radius_changes <= 0:
        return out
    out.append(f"[{file_name}]")
    if type_changes > 0:
        out.append("type_changes:")
        for row, old_t, new_t in zip(rows, orig_types, new_types):
            if int(old_t) != int(new_t):
                out.append(f"  node_id={int(row['id'])}: old_type={int(old_t)} -> new_type={int(new_t)}")
    if radius_changes > 0:
        out.append("radius_changes:")
        for row, old_r, new_r in zip(rows, orig_radii, new_radii):
            if float(old_r) != float(new_r):
                out.append(f"  node_id={int(row['id'])}: "
                           f"old_radius={float(old_r):.10g} -> new_radius={float(new_r):.10g}")
    out.append("")
    return out


# ---------------------------------------------------------------------------
# Labeling
# ---------------------------------------------------------------------------


@dataclass
class _Labeled:
    """One file run through the labeler, before the user's label options apply."""
    # The labeler's type for each SWC row; None for a node the labeler did not
    # label (see ``_labels_for_rows``), which keeps its type.
    labels: list[int | None]
    unlabeled_ids: list[int]        # ids of those nodes, in file order
    cell_type: str                  # class used for labeling: "pyramidal" or "interneuron"
    cell_type_source: str           # "user" or "stage1"
    stage1_cell_type: str           # Stage 1's own prediction
    stage1_confidence: float
    class_mismatch: bool
    qc_result: dict[str, Any]
    notes: list[str]
    flag_result: dict[str, Any] | None


_FLAG_CACHE: dict[str, Any] = {}


def _load_flag(folder: Path) -> Any | None:
    """The folder's flag bundle, loaded once per file state; ``None`` without one."""
    path = folder / MODEL_FILES["flag"]
    try:
        st = path.stat()
        key = f"{path.resolve()}|{st.st_mtime_ns}|{st.st_size}"
    except OSError:
        key = f"{path.resolve()}|missing"
    if key not in _FLAG_CACHE:
        from swcstudio.core.labeler.stage4.single_seed_flag import load_flag  # noqa: PLC0415
        with labeler_pickle_aliases():
            _FLAG_CACHE[key] = load_flag(folder)
    return _FLAG_CACHE[key]


def _score_flag(nodes: list, result: Any, folder: Path, labeled_class: str,
                stage1: Any, mismatch: bool) -> dict[str, Any]:
    """The per-file flag record. ``flagged`` here is the single-file decision."""
    base: dict[str, Any] = {
        "enabled": True, "cell_type": labeled_class, "stage1_cell_type": stage1.cell_type,
        "stage1_confidence": float(stage1.confidence), "suspicion": None, "predicted_f1": None,
        "threshold": None, "flagged": False, "flagged_by_threshold": False, "reason": None,
    }
    if mismatch:
        # The flag reads Stage 1's prediction among its inputs; for a cell whose
        # chosen class Stage 1 disputes it was never trained or measured, so the
        # disagreement itself is the reason to review the cell.
        base.update(flagged=True, reason="class_mismatch")
        return base
    flag = _load_flag(folder)
    if flag is None:
        base.update(enabled=False, reason="no_flag_model")
        return base
    from swcstudio.core.labeler.stage4.single_seed_flag import score_flag  # noqa: PLC0415
    with labeler_pickle_aliases():
        scored = score_flag(nodes, result, flag)
    if scored.get("out_of_scope"):
        base.update(enabled=False, reason="no_flag_for_class")
        return base
    flagged = bool(scored.get("flagged"))
    # The flag regresses per-cell F1 and can overshoot [0, 1] slightly; the
    # suspicion score is clipped the same way.
    predicted_f1 = min(max(float(scored["predicted_f1"]), 0.0), 1.0)
    base.update(suspicion=float(scored["suspicion"]), predicted_f1=predicted_f1,
                threshold=float(scored["threshold"]), flagged=flagged, flagged_by_threshold=flagged,
                reason="below_cutoff" if flagged else None)
    return base


def _soma_ids_before_merge(rows: list[dict[str, Any]]) -> set[int]:
    """The nodes the labeler's parse takes as soma before merging the soma.

    That is the nodes typed soma; in a file with no node typed soma and a
    single root, the root and the points around it that draw the soma
    (``parse_swc_for_labeling``'s own rule, applied to the same rows).
    """
    soma = {int(r["id"]) for r in rows if int(r["type"]) == 1}
    if soma or not rows:
        return soma
    roots = [r for r in rows if int(r["parent"]) == -1]
    if len(roots) != 1:
        return soma
    from swcstudio.core.labeler.features import SWCNode  # noqa: PLC0415
    from swcstudio.core.labeler.infer_pipeline import _soma_points_by_shape  # noqa: PLC0415

    raw = [SWCNode(int(r["id"]), int(r["type"]), float(r["x"]), float(r["y"]), float(r["z"]),
                   float(r["radius"]), int(r["parent"])) for r in rows]
    root = raw[rows.index(roots[0])]
    return {root.id} | {raw[i].id for i in _soma_points_by_shape(raw, root)}


def _labels_for_rows(rows: list[dict[str, Any]], label_by_id: dict[int, int]
                     ) -> tuple[list[int | None], list[int]]:
    """The label of each row, and the ids of the rows left unlabeled.

    The labeler's parse merges the points of a soma into one node, so a soma
    point it merged away has no label of its own and is labeled soma, as the
    labeler's writer does. The same parse also folds into the soma a branch
    leaving it that holds no standard type at all (type 0 or a custom type
    throughout): such a node was not soma in the input and is not written as
    soma; it gets ``None`` and keeps its type.
    """
    soma = _soma_ids_before_merge(rows)
    labels: list[int | None] = []
    unlabeled: list[int] = []
    for r in rows:
        rid = int(r["id"])
        if rid in label_by_id:
            labels.append(label_by_id[rid])
        elif rid in soma:
            labels.append(1)
        else:
            labels.append(None)
            unlabeled.append(rid)
    return labels, unlabeled


def unlabeled_warning(count: int) -> list[str]:
    """The warning for nodes the labeler left unlabeled, or nothing."""
    if count <= 0:
        return []
    return [f"{count} node{'s' if count != 1 else ''} on a branch with no standard type "
            f"{'were' if count != 1 else 'was'} left unlabeled"]


@contextmanager
def _labeler_input(path: Path, text: str) -> Iterator[Path]:
    """The file the labeler should parse for ``path``, whose text is ``text``.

    The labeler's parser does not drop a byte order mark, so a node line that
    starts with one would be lost (a soma written first: "no soma node").
    Such a file is handed to the labeler as a temporary copy without the
    marks; any other file is parsed where it is.
    """
    if BOM not in text:
        yield path
        return
    cleaned = "".join(strip_line_bom(content) + brk for content, brk in split_swc_lines(text))
    fd, tmp = tempfile.mkstemp(prefix="swcstudio-label-", suffix=".swc")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(encode_swc_text(cleaned))
        yield Path(tmp)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _label_file(path: Path, rows: list[dict[str, Any]], opts: BatchOptions, folder: Path,
                text: str | None = None) -> _Labeled:
    """Run the labeler on ``path`` and match its labels to the parsed ``rows``.

    The labeler parses the file itself; that parse merges a soma drawn with
    several points into one node and keeps every other node's id, so labels are
    matched to rows by id and the merged soma points are labeled soma, exactly
    as the labeler writes a labeled file. ``rows`` must have passed
    :func:`structural_violations`.
    """
    from swcstudio.core.labeler.infer_pipeline import parse_swc_for_labeling, run_pipeline_on_nodes  # noqa: PLC0415
    from swcstudio.core.labeler.qc import qc_violations  # noqa: PLC0415
    from swcstudio.core.labeler.stage1.cell_type_detector import detect_cell_type_from_nodes  # noqa: PLC0415

    s1_path = folder / MODEL_FILES["stage1"]
    s2_path = folder / MODEL_FILES["stage2"]
    with _labeler_input(path, read_swc_text(path) if text is None else text) as source:
        nodes, notes = parse_swc_for_labeling(source)
    reasons = qc_violations(nodes)
    qc_result = {"passed": not reasons, "reasons": list(reasons), "n_nodes": len(nodes)}
    if reasons:
        raise ValueError(f"{path.name}: QC rejected; " + "; ".join(reasons))

    chosen = normalize_cell_class(getattr(opts, "cell_type", None))
    with labeler_pickle_aliases():
        if chosen is None:
            result = run_pipeline_on_nodes(nodes, str(path), s1_path, s2_path)
            stage1 = result.stage1
            mismatch = False
        else:
            stage1 = detect_cell_type_from_nodes(nodes, s1_path)
            mismatch = stage1.cell_type != chosen
            # When Stage 1 agrees, the run is the labeler's own, Stage 1 included,
            # so the flag sees exactly the inputs it was trained and measured on.
            result = run_pipeline_on_nodes(nodes, str(path), s1_path, s2_path,
                                           override_cell_type=chosen if mismatch else None)
    labeled_class = chosen if chosen is not None else result.stage1.cell_type

    label_by_id = {int(nd.id): int(lbl) for nd, lbl in zip(nodes, result.node_labels)}
    if len(label_by_id) != len(nodes):
        raise ValueError(f"{path.name}: node ids are not unique, so labels cannot be matched to rows")
    row_ids = [int(r["id"]) for r in rows]
    if len(set(row_ids)) != len(row_ids) or not set(label_by_id) <= set(row_ids):
        raise ValueError(f"{path.name}: the file's node rows do not match the labeled parse")
    labels, unlabeled_ids = _labels_for_rows(rows, label_by_id)

    flag_result = None
    if bool(getattr(opts, "flag_enabled", True)):
        flag_result = _score_flag(nodes, result, folder, labeled_class, stage1, mismatch)

    return _Labeled(
        labels=labels, unlabeled_ids=unlabeled_ids, cell_type=labeled_class,
        cell_type_source="user" if chosen is not None else "stage1",
        stage1_cell_type=stage1.cell_type, stage1_confidence=float(stage1.confidence),
        class_mismatch=mismatch, qc_result=qc_result, notes=list(notes), flag_result=flag_result,
    )


def _apply_options(rows: list[dict[str, Any]], labels: list[int | None], opts: BatchOptions
                   ) -> tuple[list[int], list[float], int, int]:
    """The user's label options and radius cleanup on top of the labeler's labels.

    ``opts.soma`` / ``axon`` / ``basal`` / ``apic`` choose which predicted labels
    are written; a switched-off label, or no label (``None``), keeps the node's
    original type.
    ``opts.rad`` copies the parent's radius onto nodes whose radius is not positive.
    """
    orig_types = [int(r["type"]) for r in rows]
    enabled = {t for t, on in ((2, opts.axon), (3, opts.basal), (4, opts.apic)) if on}
    final_types: list[int] = []
    for old_t, new_t in zip(orig_types, labels):
        if new_t is None:
            final_types.append(old_t)
        elif new_t == 1:
            final_types.append(1 if opts.soma else old_t)
        elif new_t in (2, 3, 4) and new_t in enabled:
            final_types.append(new_t)
        else:
            final_types.append(old_t)

    radii_orig = [float(r["radius"]) for r in rows]
    radii = list(radii_orig)
    if opts.rad:
        id_to_idx = {int(r["id"]): i for i, r in enumerate(rows)}
        children: dict[int, list[int]] = {}
        roots = []
        for i, r in enumerate(rows):
            p = id_to_idx.get(int(r["parent"]))
            if p is None:
                roots.append(i)
            else:
                children.setdefault(p, []).append(i)
        order, seen, queue = [], set(), list(roots)
        while queue:
            idx = queue.pop(0)
            if idx in seen:
                continue
            seen.add(idx)
            order.append(idx)
            queue.extend(j for j in children.get(idx, []) if j not in seen)
        for idx in order:
            pidx = id_to_idx.get(int(rows[idx]["parent"]))
            if pidx is not None and radii[idx] <= 0 and radii[pidx] > 0:
                radii[idx] = radii[pidx]

    type_changes = sum(1 for old, new in zip(orig_types, final_types) if old != new)
    # NaN != NaN: an unchanged NaN radius is not a change.
    radius_changes = sum(1 for old, new in zip(radii_orig, radii) if _radius_changed(old, new))
    return final_types, radii, type_changes, radius_changes


def _out_counts(types: list[int]) -> dict[int, int]:
    return {t: sum(1 for x in types if int(x) == t) for t in (1, 2, 3, 4)}


def _flag_summary(flag_result: dict[str, Any] | None) -> str:
    if not flag_result or not flag_result.get("enabled", True) and not flag_result.get("flagged"):
        return "flag=off" if not flag_result else f"flag=not scored ({flag_result.get('reason')})"
    f1 = flag_result.get("predicted_f1")
    f1_txt = f", predicted_f1={f1:.3f}" if isinstance(f1, float) else ""
    reason = flag_result.get("reason")
    return f"flag={bool(flag_result.get('flagged'))}{f1_txt}" + (f" ({reason})" if reason else "")


# ---------------------------------------------------------------------------
# Flag review records (batch)
# ---------------------------------------------------------------------------


def _type_count(counts: dict[Any, Any], t: int) -> int:
    return int(counts.get(t, counts.get(str(t), 0)) or 0)


def label_warnings(cell_type: str | None, out_type_counts: dict[int, int] | None,
                   input_type_counts: dict[int, int] | None = None) -> list[str]:
    """The compartments a labeled cell of its class should have but has no node of.

    The labeler gives each primary subtree one label and does not force an
    axon, so a cell can come back without one. Saying so in the batch results
    spares reviewers from finding it by eye. When the input was labeled
    (``input_type_counts`` has an axon or dendrite node) and has no node of
    that compartment either, the warning says so: the tracing may simply not
    include it.
    """
    if out_type_counts is None:
        return []
    if cell_type == "pyramidal":
        expected = {2: ("axon", (2,)), 3: ("basal dendrite", (3,)), 4: ("apical dendrite", (4,))}
    else:
        # A 2-class dendrite is written as type 3; an input may draw it as 3 or 4.
        expected = {2: ("axon", (2,)), 3: ("dendrite", (3, 4))}
    inputs = dict(input_type_counts or {})
    input_labeled = any(_type_count(inputs, t) for t in (2, 3, 4))
    out: list[str] = []
    for t, (name, input_types) in expected.items():
        if _type_count(out_type_counts, t):
            continue
        text = f"no {name} labeled"
        if input_labeled and not any(_type_count(inputs, it) for it in input_types):
            text += " (none in the input either)"
        out.append(text)
    return out


def _warning_text(record: dict[str, Any]) -> str:
    warnings = list(record.get("warnings") or [])
    return f", WARNING: {'; '.join(warnings)}" if warnings else ""


def batch_flag_text(record: dict[str, Any], *, flag_enabled: bool,
                    flag_result: dict[str, Any] | None = None) -> str:
    """The flag part of a batch's per-file line, from the record's final decision.

    Call it after :func:`apply_flag_budget`, which decides ``flagged``.
    """
    if not flag_enabled:
        return "flag=off"
    if record.get("suspicion") is None and not record.get("class_mismatch"):
        reason = (flag_result or {}).get("reason")
        return f"flag=not scored ({reason})" if reason else "flag=not scored"
    f1 = record.get("predicted_f1")
    f1_txt = f", predicted_f1={float(f1):.3f}" if f1 is not None else ""
    reason = record.get("flag_reason")
    return f"flag={bool(record.get('flagged'))}{f1_txt}" + (f" ({reason})" if reason else "")


def batch_file_line(record: dict[str, Any], *, radius_changes: int, out_type_counts: dict[int, int] | None,
                    flag_enabled: bool, flag_result: dict[str, Any] | None = None) -> str:
    """One labeled file's line in a batch report, after the flag budget is applied."""
    counts = dict(out_type_counts or {})
    return (
        f"{record['file']}: nodes={int(record.get('nodes') or 0)}, "
        f"type_changes={int(record.get('type_changes') or 0)}, radius_changes={int(radius_changes)}, "
        f"cell_class={cell_class_name(record.get('cell_type'))} ({record.get('cell_class_source') or 'auto'}), "
        f"{batch_flag_text(record, flag_enabled=flag_enabled, flag_result=flag_result)}, "
        "out_types(soma/axon/basal/apic)="
        f"{_type_count(counts, 1)}/{_type_count(counts, 2)}/{_type_count(counts, 3)}/{_type_count(counts, 4)}"
        f"{_warning_text(record)}"
    )


def batch_report_text(
    *,
    folder: str | os.PathLike,
    files_total: int,
    files_processed: int,
    failed_files: list[tuple[str, str]],
    files_with_warnings: int,
    per_file: list[str],
    files_flagged: int | None = None,
    flag_review_path: str | None = None,
    model_record: dict[str, Any] | None = None,
    totals: dict[str, int] | None = None,
    out_dir: str | None = None,
    zip_path: str | None = None,
    change_details: list[str] | None = None,
) -> str:
    """The text report of an auto-labeling batch; the GUI, the CLI and
    :func:`run_batch` write the same one. ``failed_files`` are ``(file, error)``."""
    qc_failed = sum(1 for _name, error in failed_files if "QC rejected" in str(error))
    lines = [
        "Auto-labeling batch report",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        f"Folder: {folder}",
    ]
    if model_record:
        lines.append(f"Models: {model_record.get('model_dir')} ({model_record.get('model_source')}), "
                     f"labeler {model_record.get('model_version')}, "
                     f"model set sha256 {model_record.get('model_sha')}")
    if out_dir:
        lines.append(f"Labeled copies: {out_dir}")
    if zip_path:
        lines.append(f"Zip: {zip_path}")
    lines += [
        f"SWC files: {int(files_total)}",
        f"Labeled: {int(files_processed)}",
        f"Labeled with warnings: {int(files_with_warnings)}",
        f"Not labeled: {len(failed_files)} ({qc_failed} failed the structural checks, "
        f"{len(failed_files) - qc_failed} errors)",
    ]
    if totals:
        lines.append(f"Nodes: {int(totals.get('nodes', 0))}, type changes: {int(totals.get('type_changes', 0))}, "
                     f"radius changes: {int(totals.get('radius_changes', 0))}")
    if files_flagged is not None:
        lines.append(f"Flagged for review: {int(files_flagged)}"
                     + (f" (flag review: {flag_review_path})" if flag_review_path else ""))
    else:
        lines.append("Flagged for review: quality flag off")
    lines += ["", "Per file:", *(per_file or ["(none)"]), "", "Not labeled:",
              *([f"{name}: {error}" for name, error in failed_files] or ["(none)"])]
    if change_details:
        lines += ["", "Node changes:", *change_details]
    return "\n".join(lines).rstrip() + "\n"


def flag_record(path: Path, out_path: Path | None, labeled: Any | None,
                nodes_total: int, type_changes: int, qc_reasons: list[str] | None = None,
                error: str | None = None, out_type_counts: dict[int, int] | None = None) -> dict[str, Any]:
    """One row of a batch's flag review.

    ``labeled`` is a :class:`FileResult` (or the runner's internal equivalent) for
    a labeled file, or ``None`` for a file that was not labeled, with
    ``qc_reasons`` or ``error`` saying why. ``out_type_counts`` (node count per
    output type) defaults to ``labeled.out_type_counts`` and feeds ``warnings``.
    """
    rec: dict[str, Any] = {
        "file": path.name, "input_path": str(path),
        "output_path": str(out_path) if out_path is not None else None,
        "labeled": labeled is not None, "nodes": nodes_total, "type_changes": type_changes,
        "qc_passed": qc_reasons is None and error is None, "qc_reasons": list(qc_reasons or []),
        "error": error, "review_status": "unreviewed",
        "cell_type": None, "cell_class": None, "cell_class_source": None,
        "stage1_cell_type": None, "stage1_confidence": None, "class_mismatch": False,
        "predicted_f1": None, "suspicion": None, "threshold": None,
        "flagged_by_threshold": False, "flagged": False, "flag_reason": None,
        "rank_in_class": None, "class_size": None, "notes": [], "warnings": [],
    }
    if labeled is not None:
        fr = getattr(labeled, "flag_result", None) or {}
        rec.update(
            cell_type=labeled.cell_type, cell_class=cell_class_name(labeled.cell_type),
            cell_class_source="user" if labeled.cell_type_source == "user" else "auto",
            stage1_cell_type=labeled.stage1_cell_type, stage1_confidence=labeled.stage1_confidence,
            class_mismatch=bool(labeled.class_mismatch), predicted_f1=fr.get("predicted_f1"),
            suspicion=fr.get("suspicion"), threshold=fr.get("threshold"),
            flagged_by_threshold=bool(fr.get("flagged_by_threshold")),
            notes=list(getattr(labeled, "notes", []) or []),
        )
        counts = out_type_counts if out_type_counts is not None else getattr(labeled, "out_type_counts", None)
        rec["warnings"] = (label_warnings(labeled.cell_type, counts, getattr(labeled, "input_type_counts", None))
                           + unlabeled_warning(int(getattr(labeled, "unlabeled_nodes", 0) or 0)))
    return rec


def apply_flag_budget(records: list[dict[str, Any]], budget: float) -> None:
    """Decide ``flagged`` for each labeled record of a batch, in place.

    Each cell class flags its riskiest ``budget`` share, ranked by suspicion and
    rounded to the nearest cell, as the labeler's flag was evaluated. A class
    with fewer than ``1 / budget`` cells has no such share, so its cells are
    flagged by the flag's fixed cut-off instead. A class mismatch is always flagged.
    """
    budget = min(max(float(budget), 0.0), 1.0)
    groups: dict[str, list[dict[str, Any]]] = {}
    for rec in records:
        rec["rank_in_class"] = None
        rec["class_size"] = None
        if not rec.get("labeled"):
            rec["flagged"], rec["flag_reason"] = False, None
        elif rec.get("class_mismatch"):
            rec["flagged"], rec["flag_reason"] = True, "class_mismatch"
        elif rec.get("suspicion") is None:
            rec["flagged"], rec["flag_reason"] = False, None
        else:
            groups.setdefault(str(rec["cell_type"]), []).append(rec)
    for group in groups.values():
        ranked = sorted(group, key=lambda r: float(r["suspicion"]), reverse=True)
        n = len(ranked)
        use_share = budget > 0 and n >= math.ceil(1.0 / budget)
        k = int(math.floor(budget * n + 0.5)) if use_share else 0
        for i, rec in enumerate(ranked):
            rec["rank_in_class"], rec["class_size"] = i + 1, n
            if use_share:
                rec["flagged"] = i < k
                rec["flag_reason"] = "riskiest_share" if i < k else None
            else:
                rec["flagged"] = bool(rec.get("flagged_by_threshold"))
                rec["flag_reason"] = "below_cutoff" if rec["flagged"] else None


_CSV_FIELDS = ("file", "flagged", "flag_reason", "warnings", "cell_class", "cell_class_source", "stage1_cell_type",
               "stage1_confidence", "class_mismatch", "predicted_f1", "suspicion", "rank_in_class",
               "class_size", "nodes", "type_changes", "labeled", "qc_passed", "qc_reasons", "error",
               "review_status", "input_path", "output_path")


def write_flag_review(json_path: Path, records: list[dict[str, Any]], *, budget: float,
                      folder: str, timestamp: str, labeler_version: str | None = None) -> Path:
    """Write a batch's flag review: ``json_path`` (read by the Flag Review window)
    and a CSV copy beside it with the same name. ``labeler_version`` defaults to
    the labeler SWC-Studio runs; a review saved again keeps the one it was made with."""
    payload = {
        "kind": "swcstudio_flag_review", "version": 1, "folder": folder, "timestamp": timestamp,
        "labeler_version": labeler_version or labeler_info()["version"], "flag_budget": budget,
        "records": records,
    }
    json_path = Path(json_path)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    with json_path.with_suffix(".csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for rec in records:
            row = dict(rec)
            row["qc_reasons"] = "; ".join(rec.get("qc_reasons") or [])
            row["warnings"] = "; ".join(rec.get("warnings") or [])
            writer.writerow(row)
    return json_path


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _run_one(in_path: Path, text: str, opts: BatchOptions, selection: ModelSelection,
             record: dict[str, Any]) -> FileResult:
    """Label one file's ``text``; nothing is written."""
    headers, rows = swc_data_rows_from_text(text)
    _reject_broken_structure(in_path.name, rows)
    ineligible = _check_auto_label_eligibility(rows)
    if ineligible is not None:
        raise ValueError(f"{in_path.name}: {ineligible}")
    labeled = _label_file(in_path, rows, opts, selection.folder, text)

    orig_types = [int(r["type"]) for r in rows]
    orig_radii = [float(r["radius"]) for r in rows]
    types, radii, type_changes, radius_changes = _apply_options(rows, labeled.labels, opts)
    out_counts = _out_counts(types)
    input_counts = _out_counts(orig_types)
    warnings = (label_warnings(labeled.cell_type, out_counts, input_counts)
                + unlabeled_warning(len(labeled.unlabeled_ids)))
    return FileResult(
        input_file=str(in_path),
        output_file=None,
        nodes_total=len(rows),
        type_changes=type_changes,
        radius_changes=radius_changes,
        out_type_counts=out_counts,
        cell_type=labeled.cell_type,
        cell_type_source=labeled.cell_type_source,
        stage1_confidence=labeled.stage1_confidence,
        qc_result=labeled.qc_result,
        flag_result=labeled.flag_result,
        failures=[],
        change_details=_build_change_details(in_path.name, rows, orig_types, types, orig_radii, radii),
        log_path=None,
        headers=headers,
        rows=rows,
        types=types,
        radii=radii,
        stage1_cell_type=labeled.stage1_cell_type,
        class_mismatch=labeled.class_mismatch,
        notes=labeled.notes,
        labeler_version=labeler_info()["version"],
        model_record=dict(record),
        warnings=warnings,
        unlabeled_nodes=len(labeled.unlabeled_ids),
        input_type_counts=input_counts,
    )


def _single_file_report_text(res: FileResult) -> str:
    counts = res.out_type_counts
    lines = [
        "Auto-labeling report",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        f"Input: {res.input_file}",
        f"Output: {res.output_file or '(not written)'}",
    ]
    rec = res.model_record or {}
    if rec:
        lines.append(f"Models: {rec.get('model_dir')} ({rec.get('model_source')}), labeler "
                     f"{rec.get('model_version')}, model set sha256 {rec.get('model_sha')}")
    lines.append(
        f"{Path(res.input_file).name}: nodes={res.nodes_total}, type_changes={res.type_changes}, "
        f"radius_changes={res.radius_changes}, cell_class={cell_class_name(res.cell_type)} "
        f"({'user' if res.cell_type_source == 'user' else 'auto'}), {_flag_summary(res.flag_result)}, "
        f"out_types(soma/axon/basal/apic)={counts[1]}/{counts[2]}/{counts[3]}/{counts[4]}"
        + (f", WARNING: {'; '.join(res.warnings)}" if res.warnings else ""))
    if res.notes:
        lines += ["", "Notes:", *res.notes]
    if res.change_details:
        lines += ["", "Node changes:", *res.change_details]
    return "\n".join(lines).rstrip() + "\n"


def run_file(
    file_path: str,
    opts: BatchOptions,
    *,
    output_path: str | None = None,
    write_output: bool = True,
    write_log: bool = True,
    model_dir: str | None = None,
    models: ModelSelection | None = None,
) -> FileResult:
    """Run the auto-labeler on one SWC file.

    ``models`` is a folder already resolved with :func:`resolve_models`;
    without it, ``model_dir`` and the usual search decide. The input file is
    not changed. With ``write_output``, a labeled copy is written: the input
    with only the relabeled type fields rewritten.
    """
    selection = models if models is not None else resolve_models(model_dir)
    record = model_record(selection.folder, selection.source)
    in_path = Path(file_path)
    text = read_swc_text(in_path)
    res = _run_one(in_path, text, opts, selection, record)

    run_timestamp = timestamp_slug()
    if write_output:
        out_path = (Path(output_path) if output_path
                    else operation_output_path_for_file(in_path, "auto_typing", timestamp=run_timestamp))
        _write_labeled_copy(out_path, text, res.rows, res.types, res.radii)
        res.output_file = str(out_path)
    if write_log:
        log_target = (auto_typing_log_path_for_file(in_path) if output_path
                      else operation_report_path_for_file(in_path, "auto_typing", timestamp=run_timestamp))
        res.log_path = write_text_report(log_target, _single_file_report_text(res))
    return res


def run_batch(
    folder: str,
    opts: BatchOptions,
    *,
    model_dir: str | None = None,
    models: ModelSelection | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> BatchResult:
    """Run the auto-labeler on every ``.swc`` file in ``folder``.

    Labeled copies go to a new output folder created inside ``folder``
    (``<folder>_batch_auto_typing_<timestamp>``), together with a text report and
    the flag review (``flag_review.json`` / ``.csv``); the source files are not
    changed. Each copy is its source with only the relabeled type fields
    rewritten.
    ``progress_callback(index, total, current_filename)`` is invoked once per
    file before processing it.
    """
    selection = models if models is not None else resolve_models(model_dir)
    record = model_record(selection.folder, selection.source)
    in_dir = Path(folder)
    swc_files = sorted(p for p in in_dir.iterdir() if is_swc_data_file(p))
    run_timestamp = timestamp_slug()
    out_dir = operation_output_dir_for_folder(in_dir, "batch_auto_typing", timestamp=run_timestamp)
    flag_enabled = bool(getattr(opts, "flag_enabled", True))

    failed_files: list[tuple[str, str]] = []
    change_details: list[str] = []
    labeled_items: list[tuple[FileResult, dict[str, Any]]] = []
    unlabeled_records: list[dict[str, Any]] = []
    totals = {"nodes": 0, "type_changes": 0, "radius_changes": 0}

    for idx, swc_path in enumerate(swc_files):
        if progress_callback is not None:
            progress_callback(idx, len(swc_files), swc_path.name)
        try:
            text = read_swc_text(swc_path)
            res = _run_one(swc_path, text, opts, selection, record)
            out_path = operation_output_path_for_file(swc_path, "batch_auto_typing", output_dir=out_dir,
                                                      timestamp=run_timestamp)
            _write_labeled_copy(out_path, text, res.rows, res.types, res.radii)
            res.output_file = str(out_path)
        except Exception as exc:  # noqa: BLE001 - one bad file does not stop the batch
            error = str(exc)
            prefix = f"{swc_path.name}: "
            error = error[len(prefix):] if error.startswith(prefix) else error
            failed_files.append((swc_path.name, error))
            qc = error.split("QC rejected; ", 1)[1].split("; ") if "QC rejected; " in error else None
            unlabeled_records.append(flag_record(swc_path, None, None, 0, 0, qc_reasons=qc,
                                                 error=None if qc else error))
            continue
        totals["nodes"] += res.nodes_total
        totals["type_changes"] += res.type_changes
        totals["radius_changes"] += res.radius_changes
        labeled_items.append((res, flag_record(swc_path, out_path, res, res.nodes_total, res.type_changes)))
        change_details.extend(res.change_details)

    records = [rec for _res, rec in labeled_items] + unlabeled_records
    budget = float(getattr(opts, "flag_budget", 0.10))
    flag_review_path: str | None = None
    files_flagged: int | None = None
    if flag_enabled:
        apply_flag_budget(records, budget)
        files_flagged = sum(1 for r in records if r.get("flagged"))
        flag_review_path = str(write_flag_review(out_dir / FLAG_REVIEW_JSON, records, budget=budget,
                                                 folder=str(in_dir), timestamp=run_timestamp))
    # Built after the budget, so each line says the file's final flag.
    per_file = [batch_file_line(rec, radius_changes=res.radius_changes, out_type_counts=res.out_type_counts,
                                flag_enabled=flag_enabled, flag_result=res.flag_result)
                for res, rec in labeled_items]
    failures = [f"{name}: {error}" for name, error in failed_files]
    files_qc_failed = sum(1 for _name, error in failed_files if "QC rejected" in error)
    files_with_warnings = sum(1 for _res, rec in labeled_items if rec.get("warnings"))

    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path: str | None = None
    if opts.zip_output and labeled_items:
        zip_target = in_dir / f"{out_dir.name}.zip"
        with zipfile.ZipFile(zip_target, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(out_dir.glob("*.swc")):
                zf.write(f, arcname=f"{out_dir.name}/{f.name}")
        zip_path = str(zip_target)

    log_path = write_text_report(
        operation_report_path_for_folder(in_dir, "batch_auto_typing", output_dir=out_dir,
                                         timestamp=run_timestamp),
        batch_report_text(folder=in_dir, files_total=len(swc_files), files_processed=len(labeled_items),
                          failed_files=failed_files, files_with_warnings=files_with_warnings,
                          per_file=per_file, files_flagged=files_flagged, flag_review_path=flag_review_path,
                          model_record=record, totals=totals, out_dir=str(out_dir), zip_path=zip_path,
                          change_details=change_details),
    )

    return BatchResult(
        folder=str(in_dir), out_dir=str(out_dir), zip_path=zip_path,
        files_total=len(swc_files), files_processed=len(labeled_items), files_failed=len(failures),
        files_qc_failed=files_qc_failed, files_with_warnings=files_with_warnings,
        total_nodes=totals["nodes"], total_type_changes=totals["type_changes"],
        total_radius_changes=totals["radius_changes"],
        files_flagged=int(files_flagged or 0), failures=failures, per_file=per_file, log_path=log_path,
        flag_records=records if flag_enabled else None,
        flag_review_path=flag_review_path,
        flag_budget=budget if flag_enabled else None,
        model_record=dict(record),
    )


__all__ = [
    "is_available",
    "resolve_models",
    "backend_status",
    "run_file",
    "run_batch",
    "labeled_text",
    "read_swc_text",
    "structural_violations",
    "apply_flag_budget",
    "flag_record",
    "label_warnings",
    "unlabeled_warning",
    "batch_flag_text",
    "batch_file_line",
    "batch_report_text",
    "write_flag_review",
    "FLAG_REVIEW_JSON",
    "FLAG_REVIEW_CSV",
]
