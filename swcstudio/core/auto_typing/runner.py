"""Public entry points for the auto-typing engine.

SWC-Studio labels with the SWC auto-labeler: ``swcstudio.core.labeler`` is a copy
of the released labeler's code and ``labeler-<version>`` its model set, made by
``scripts/sync_labeler.py``. For each file:

* Stage 0 checks that the file is one connected cell with a soma and finite
  geometry. A file that fails is not labeled.
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

Public surface:

* ``run_file(path, opts, ...)`` returns ``FileResult``
* ``run_batch(folder, opts, ...)`` returns ``BatchResult``
* ``is_available()`` returns ``(bool, reason)``
* ``backend_status()`` returns a diagnostic dict
"""
from __future__ import annotations

import csv
import json
import math
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from swcstudio.core.model_paths import (
    MODEL_FILES,
    available_models,
    diagnostic_search_report,
    labeler_info,
    resolve_model_dir,
)
from swcstudio.core.reporting import (
    auto_typing_log_path_for_file,
    format_auto_typing_report_text,
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


def is_available(*, model_dir: str | None = None) -> tuple[bool, str]:
    """Return ``(ok, reason)`` describing whether auto-labeling can run now.

    It needs a complete model folder (see :mod:`swcstudio.core.model_paths`) and
    the packages the labeler imports: joblib, scikit-learn, XGBoost and pandas.
    """
    if resolve_model_dir(model_dir) is None:
        return False, ("Auto-labeling cannot find its model files.\n"
                       + diagnostic_search_report(override=model_dir))
    try:
        import joblib  # noqa: F401
        import pandas  # noqa: F401
        import sklearn  # noqa: F401
        import xgboost  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return False, (
            "Auto-labeling could not import one of its required packages "
            f"(import failed: {exc.__class__.__name__}: {exc}). "
            "Repair the active environment with "
            "`python -m pip install --upgrade --force-reinstall swcstudio`, "
            "then run `swcstudio doctor`."
        )
    return True, "available"


def backend_status(*, model_dir: str | None = None) -> dict[str, Any]:
    """Structured status report, for ``swcstudio models status`` and the GUI."""
    info = labeler_info()
    folder = resolve_model_dir(model_dir, auto_download=False)
    files = available_models(model_dir)
    status: dict[str, Any] = {
        "labeler_version": info["version"],
        "labeler_commit": info["source_commit"],
        "model_dir": str(folder) if folder is not None else None,
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


def _parse_swc(path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    headers: list[str] = []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            s = line.strip()
            if not s:
                continue
            if s.startswith("#"):
                headers.append(line.rstrip("\n"))
                continue
            parts = s.split()
            if len(parts) < 7:
                continue
            try:
                rid = int(float(parts[0]))
                rtype = int(float(parts[1]))
                x = float(parts[2])
                y = float(parts[3])
                z = float(parts[4])
                radius = float(parts[5])
                parent = int(float(parts[6]))
            except Exception:
                continue
            rows.append({"id": rid, "type": rtype, "x": x, "y": y, "z": z,
                         "radius": radius, "parent": parent})
    return headers, rows


def _write_swc(
    path: Path,
    headers: list[str],
    rows: list[dict[str, Any]],
    types: list[int],
    radii: list[float],
) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for h in headers:
            fh.write(f"{h}\n")
        for i, row in enumerate(rows):
            fh.write(
                f"{int(row['id'])} {int(types[i])} "
                f"{float(row['x']):.10g} {float(row['y']):.10g} {float(row['z']):.10g} "
                f"{float(radii[i]):.10g} {int(row['parent'])}\n"
            )


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
    labels: list[int]               # the labeler's type for each SWC row
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


def _label_file(path: Path, rows: list[dict[str, Any]], opts: BatchOptions, folder: Path) -> _Labeled:
    """Run the labeler on ``path`` and match its labels to the parsed ``rows``.

    The labeler parses the file itself; that parse merges a soma drawn with
    several points into one node and keeps every other node's id, so labels are
    matched to rows by id and the merged soma points are labeled soma, exactly
    as the labeler writes a labeled file.
    """
    from swcstudio.core.labeler.infer_pipeline import parse_swc_for_labeling, run_pipeline_on_nodes  # noqa: PLC0415
    from swcstudio.core.labeler.qc import qc_violations  # noqa: PLC0415
    from swcstudio.core.labeler.stage1.cell_type_detector import detect_cell_type_from_nodes  # noqa: PLC0415

    s1_path = folder / MODEL_FILES["stage1"]
    s2_path = folder / MODEL_FILES["stage2"]
    nodes, notes = parse_swc_for_labeling(path)
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
    if not set(label_by_id) <= set(row_ids):
        raise ValueError(f"{path.name}: the file's node rows do not match the labeled parse")
    labels = [label_by_id.get(rid, 1) for rid in row_ids]

    flag_result = None
    if bool(getattr(opts, "flag_enabled", True)):
        flag_result = _score_flag(nodes, result, folder, labeled_class, stage1, mismatch)

    return _Labeled(
        labels=labels, cell_type=labeled_class,
        cell_type_source="user" if chosen is not None else "stage1",
        stage1_cell_type=stage1.cell_type, stage1_confidence=float(stage1.confidence),
        class_mismatch=mismatch, qc_result=qc_result, notes=list(notes), flag_result=flag_result,
    )


def _apply_options(rows: list[dict[str, Any]], labels: list[int], opts: BatchOptions
                   ) -> tuple[list[int], list[float], int, int]:
    """The user's label options and radius cleanup on top of the labeler's labels.

    ``opts.soma`` / ``axon`` / ``basal`` / ``apic`` choose which predicted labels
    are written; a switched-off label keeps the node's original type.
    ``opts.rad`` copies the parent's radius onto nodes whose radius is not positive.
    """
    orig_types = [int(r["type"]) for r in rows]
    enabled = {t for t, on in ((2, opts.axon), (3, opts.basal), (4, opts.apic)) if on}
    final_types: list[int] = []
    for old_t, new_t in zip(orig_types, labels):
        if new_t == 1:
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
    radius_changes = sum(1 for old, new in zip(radii_orig, radii) if old != new)
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


def flag_record(path: Path, out_path: Path | None, labeled: Any | None,
                nodes_total: int, type_changes: int, qc_reasons: list[str] | None = None,
                error: str | None = None) -> dict[str, Any]:
    """One row of a batch's flag review.

    ``labeled`` is a :class:`FileResult` (or the runner's internal equivalent) for
    a labeled file, or ``None`` for a file that was not labeled, with
    ``qc_reasons`` or ``error`` saying why.
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
        "rank_in_class": None, "class_size": None, "notes": [],
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


_CSV_FIELDS = ("file", "flagged", "flag_reason", "cell_class", "cell_class_source", "stage1_cell_type",
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
            writer.writerow(row)
    return json_path


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _model_folder(model_dir: str | None) -> Path:
    ok, reason = is_available(model_dir=model_dir)
    if not ok:
        raise FileNotFoundError(reason)
    folder = resolve_model_dir(model_dir)
    assert folder is not None
    return folder


def run_file(
    file_path: str,
    opts: BatchOptions,
    *,
    output_path: str | None = None,
    write_output: bool = True,
    write_log: bool = True,
    model_dir: str | None = None,
) -> FileResult:
    """Run the auto-labeler on one SWC file."""
    folder = _model_folder(model_dir)
    in_path = Path(file_path)
    headers, rows = _parse_swc(in_path)
    ineligible = _check_auto_label_eligibility(rows)
    if ineligible is not None:
        raise ValueError(f"{in_path.name}: {ineligible}")
    labeled = _label_file(in_path, rows, opts, folder)

    orig_types = [int(r["type"]) for r in rows]
    orig_radii = [float(r["radius"]) for r in rows]
    types, radii, type_changes, radius_changes = _apply_options(rows, labeled.labels, opts)

    out_path: Path | None = None
    run_timestamp = timestamp_slug()
    if write_output:
        out_path = (Path(output_path) if output_path
                    else operation_output_path_for_file(in_path, "auto_typing", timestamp=run_timestamp))
        _write_swc(out_path, headers, rows, types, radii)

    out_counts = _out_counts(types)
    change_details = _build_change_details(in_path.name, rows, orig_types, types, orig_radii, radii)
    flagged = bool(labeled.flag_result and labeled.flag_result.get("flagged"))

    log_path: str | None = None
    if write_log:
        log_target = (auto_typing_log_path_for_file(in_path) if output_path
                      else operation_report_path_for_file(in_path, "auto_typing", timestamp=run_timestamp))
        payload = {
            "folder": str(in_path.parent),
            "out_dir": str(out_path.parent if out_path is not None else in_path.parent),
            "zip_path": None, "files_total": 1, "files_processed": 1, "files_failed": 0,
            "files_qc_failed": 0, "total_nodes": len(rows), "total_type_changes": type_changes,
            "total_radius_changes": radius_changes, "files_flagged": int(flagged), "failures": [],
            "per_file": [
                f"{in_path.name}: nodes={len(rows)}, type_changes={type_changes}, "
                f"radius_changes={radius_changes}, cell_class={cell_class_name(labeled.cell_type)} "
                f"({'user' if labeled.cell_type_source == 'user' else 'auto'}), "
                f"{_flag_summary(labeled.flag_result)}, "
                f"out_types(soma/axon/basal/apic)={out_counts[1]}/{out_counts[2]}/"
                f"{out_counts[3]}/{out_counts[4]}"
            ],
            "change_details": change_details,
        }
        log_path = write_text_report(log_target, format_auto_typing_report_text(payload))

    return FileResult(
        input_file=str(in_path),
        output_file=str(out_path) if out_path is not None else None,
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
        change_details=change_details,
        log_path=log_path,
        headers=headers,
        rows=rows,
        types=types,
        radii=radii,
        stage1_cell_type=labeled.stage1_cell_type,
        class_mismatch=labeled.class_mismatch,
        notes=labeled.notes,
        labeler_version=labeler_info()["version"],
    )


def run_batch(
    folder: str,
    opts: BatchOptions,
    *,
    model_dir: str | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> BatchResult:
    """Run the auto-labeler on every ``.swc`` file in ``folder``.

    Labeled copies go to a new output folder created inside ``folder``
    (``<folder>_batch_auto_typing_<timestamp>``), together with a text report and
    the flag review (``flag_review.json`` / ``.csv``); the source files are not
    changed.
    ``progress_callback(index, total, current_filename)`` is invoked once per
    file before processing it.
    """
    model_folder = _model_folder(model_dir)
    in_dir = Path(folder)
    swc_files = sorted(p for p in in_dir.iterdir() if p.is_file() and p.suffix.lower() == ".swc")
    run_timestamp = timestamp_slug()
    out_dir = operation_output_dir_for_folder(in_dir, "batch_auto_typing", timestamp=run_timestamp)

    failures: list[str] = []
    per_file: list[str] = []
    change_details: list[str] = []
    records: list[dict[str, Any]] = []
    processed = total_nodes = total_type_changes = total_radius_changes = files_qc_failed = 0

    for idx, swc_path in enumerate(swc_files):
        if progress_callback is not None:
            progress_callback(idx, len(swc_files), swc_path.name)
        try:
            headers, rows = _parse_swc(swc_path)
            ineligible = _check_auto_label_eligibility(rows)
            if ineligible is not None:
                failures.append(f"{swc_path.name}: skipped — {ineligible}")
                records.append(flag_record(swc_path, None, None, len(rows), 0, error=ineligible))
                continue
            try:
                labeled = _label_file(swc_path, rows, opts, model_folder)
            except ValueError as exc:
                if "QC rejected" not in str(exc):
                    raise
                files_qc_failed += 1
                reasons = str(exc).split("QC rejected; ", 1)[-1].split("; ")
                per_file.append(f"{swc_path.name}: QC rejected; skipped auto-labeling; "
                                f"reasons={'; '.join(reasons)}")
                records.append(flag_record(swc_path, None, None, len(rows), 0, qc_reasons=reasons))
                continue

            orig_types = [int(r["type"]) for r in rows]
            orig_radii = [float(r["radius"]) for r in rows]
            types, radii, type_changes, radius_changes = _apply_options(rows, labeled.labels, opts)
            out_path = operation_output_path_for_file(swc_path, "batch_auto_typing", output_dir=out_dir,
                                                      timestamp=run_timestamp)
            _write_swc(out_path, headers, rows, types, radii)

            processed += 1
            total_nodes += len(rows)
            total_type_changes += type_changes
            total_radius_changes += radius_changes
            records.append(flag_record(swc_path, out_path, labeled, len(rows), type_changes))
            out_counts = _out_counts(types)
            per_file.append(
                f"{swc_path.name}: nodes={len(rows)}, type_changes={type_changes}, "
                f"radius_changes={radius_changes}, cell_class={cell_class_name(labeled.cell_type)} "
                f"({'user' if labeled.cell_type_source == 'user' else 'auto'}), "
                f"{_flag_summary(labeled.flag_result)}, "
                f"out_types(soma/axon/basal/apic)={out_counts[1]}/{out_counts[2]}/"
                f"{out_counts[3]}/{out_counts[4]}"
            )
            change_details.extend(_build_change_details(swc_path.name, rows, orig_types, types,
                                                        orig_radii, radii))
        except Exception as e:  # noqa: BLE001
            failures.append(f"{swc_path.name}: {e}")
            records.append(flag_record(swc_path, None, None, 0, 0, error=str(e)))

    budget = float(getattr(opts, "flag_budget", 0.10))
    flag_review_path: str | None = None
    files_flagged = 0
    if bool(getattr(opts, "flag_enabled", True)):
        apply_flag_budget(records, budget)
        files_flagged = sum(1 for r in records if r.get("flagged"))
        flag_review_path = str(write_flag_review(out_dir / FLAG_REVIEW_JSON, records, budget=budget,
                                                 folder=str(in_dir), timestamp=run_timestamp))

    zip_path: str | None = None
    if opts.zip_output and processed > 0:
        zip_target = in_dir / f"{out_dir.name}.zip"
        with zipfile.ZipFile(zip_target, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(out_dir.glob("*.swc")):
                zf.write(f, arcname=f"{out_dir.name}/{f.name}")
        zip_path = str(zip_target)

    payload = {
        "folder": str(in_dir), "out_dir": str(out_dir), "zip_path": zip_path,
        "files_total": len(swc_files), "files_processed": processed, "files_failed": len(failures),
        "files_qc_failed": files_qc_failed, "total_nodes": total_nodes,
        "total_type_changes": total_type_changes, "total_radius_changes": total_radius_changes,
        "files_flagged": files_flagged, "failures": failures, "per_file": per_file,
        "change_details": change_details,
    }
    log_path = write_text_report(
        operation_report_path_for_folder(in_dir, "batch_auto_typing", output_dir=out_dir,
                                         timestamp=run_timestamp),
        format_auto_typing_report_text(payload),
    )

    return BatchResult(
        folder=str(in_dir), out_dir=str(out_dir), zip_path=zip_path,
        files_total=len(swc_files), files_processed=processed, files_failed=len(failures),
        files_qc_failed=files_qc_failed, total_nodes=total_nodes,
        total_type_changes=total_type_changes, total_radius_changes=total_radius_changes,
        files_flagged=files_flagged, failures=failures, per_file=per_file, log_path=log_path,
        flag_records=records if bool(getattr(opts, "flag_enabled", True)) else None,
        flag_review_path=flag_review_path,
        flag_budget=budget if bool(getattr(opts, "flag_enabled", True)) else None,
    )


__all__ = [
    "is_available",
    "backend_status",
    "run_file",
    "run_batch",
    "apply_flag_budget",
    "flag_record",
    "write_flag_review",
    "FLAG_REVIEW_JSON",
    "FLAG_REVIEW_CSV",
]
