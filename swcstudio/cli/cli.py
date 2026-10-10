"""swcstudio CLI.

The CLI is a thin interface layer over the shared tool/feature library API.
No algorithmic logic should live here.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from swcstudio.core.auto_typing import BatchOptions
from swcstudio.core.config import merge_config
from swcstudio.core.swc_io import (
    is_swc_data_file,
    parse_swc_text_preserve_tokens,
    write_swc_to_bytes_preserve_tokens,
)
from swcstudio.core.issues import build_issue_list
from swcstudio.plugins import (
    autoload_plugins_from_environment,
    list_all_feature_methods,
    list_feature_methods,
    list_plugins,
    load_plugin_module,
)
from swcstudio.tools.batch_processing.features.batch_validation import validate_folder
from swcstudio.tools.batch_processing.features.index_clean import run_folder as batch_index_clean_folder
from swcstudio.tools.batch_processing.features.radii_cleaning import clean_path as batch_clean_radii_path
from swcstudio.tools.batch_processing.features.simplification import run_folder as batch_simplify_folder
from swcstudio.tools.batch_processing.features.swc_splitter import split_folder
from swcstudio.tools.geometry_editing.features.operations import (
    delete_node as geometry_delete_node,
    delete_subtree as geometry_delete_subtree,
    disconnect_branch as geometry_disconnect_branch,
    insert_node_between as geometry_insert_node_between,
    label_for_type,
    move_node_absolute as geometry_move_node_absolute,
    move_subtree_absolute as geometry_move_subtree_absolute,
    path_between_nodes,
    reconnect_branch as geometry_reconnect_branch,
    subtree_node_ids,
)
from swcstudio.tools.morphology_editing.features.dendrogram_editing import (
    reassign_subtree_types_in_file,
)
from swcstudio.tools.morphology_editing.features.manual_label import set_node_type_file
from swcstudio.tools.morphology_editing.features.manual_radii import set_node_radius_file
from swcstudio.tools.morphology_editing.features.simplification import (
    get_config as get_simplification_config,
    simplify_file as simplify_morphology_file,
)

from swcstudio.tools.validation.features.auto_fix import auto_fix_file
from swcstudio.tools.validation.features.auto_typing import auto_label_file as validation_auto_label_file
from swcstudio.tools.validation.features.index_clean import index_clean_file as validation_index_clean_file
from swcstudio.tools.validation.features.radii_cleaning import clean_path as validation_clean_radii_path
from swcstudio.tools.validation.features.run_checks import validate_file as run_validation_checks_file
from swcstudio.tools.validation import build_precheck_summary, load_validation_config
from swcstudio.tools.visualization.features.mesh_editing import build_mesh_from_file
from swcstudio.core.validation_catalog import group_rows_by_category, rule_for_key
from swcstudio.core.reporting import (
    format_validation_report_text,
    validation_index_clean_detail_lines,
    validation_log_path_for_file,
    write_text_report,
)


def _print_json(payload) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def _config_params(overrides: dict | None, effective: dict | None) -> dict:
    """Build the {effective_config, config_overrides} portion of an op's
    params, deduplicating when the two would be byte-identical.

    Rules:
      * Always include effective_config (the full set of values that
        actually controlled the algorithm).
      * Only include config_overrides when it's a non-trivial delta
        (non-empty AND not equal to effective_config). When the user
        passed nothing OR passed the entire effective config, the
        delta carries no extra information and we drop it.
    """
    out: dict = {}
    if effective is not None:
        out["effective_config"] = effective
    if overrides and overrides != effective:
        out["config_overrides"] = overrides
    return out


def _tracked_batch(
    folder: Path,
    *,
    op_kind,
    mutate_text,
    params_for=lambda swc: {},
    message="",
    per_file_summary=None,
) -> dict:
    """Per-file tracked_op loop for batch CLI handlers.

    For each .swc in ``folder``, opens a tracked_op on that file's own
    .history/ and runs ``mutate_text`` on op.input_bytes. One commit per
    file. Errors per file are caught so a single bad file doesn't stop
    the batch; they show up in the returned ``failures`` list.

    The new design produces NO separate batch output folder — every
    output lands as a commit on its input file's own .history/. The
    summary dict matches the legacy shape (folder, files_total,
    files_processed, files_failed, per_file, failures) plus a new
    ``commits`` list mapping each processed file to its commit sha.
    """
    from swcstudio.core.provenance import tracked_op

    swcs = sorted(
        [p for p in folder.iterdir() if is_swc_data_file(p)],
        key=lambda p: p.name.lower(),
    )
    if not swcs:
        raise FileNotFoundError(f"No .swc files found in: {folder}")

    processed = 0
    failures: list[str] = []
    per_file: list[str] = []
    commits: list[dict] = []

    for swc in swcs:
        try:
            with tracked_op(
                swc,
                kind=op_kind,
                params=params_for(swc),
                message=message or f"batch {op_kind.value if hasattr(op_kind, 'value') else op_kind} on {swc.name}",
            ) as op:
                in_bytes = op.input_bytes if op.input_bytes is not None else swc.read_bytes()
                result = mutate_text(in_bytes.decode("utf-8", errors="ignore"))
                op.set_output(result["bytes"])
            processed += 1
            commits.append({"file": swc.name, "commit_sha": op.result.commit_sha})
            if per_file_summary is not None:
                per_file.append(per_file_summary(swc, result, op.result))
        except Exception as e:  # noqa: BLE001
            failures.append(f"{swc.name}: {e}")

    return {
        "folder":          str(folder),
        "files_total":     len(swcs),
        "files_processed": processed,
        "files_failed":    len(failures),
        "per_file":        per_file,
        "failures":        failures,
        "commits":         commits,
    }


def _summarize_batch_radii_output(out: dict) -> dict:
    mode = str(out.get("mode", ""))
    if mode == "file":
        summary = {
            k: v
            for k, v in out.items()
            if k not in {"dataframe", "bytes", "config_used", "change_details", "change_lines"}
        }
        summary["change_count"] = int(out.get("change_count", len(list(out.get("change_details", []) or []))))
        return summary

    if mode == "folder":
        per_file_summary = []
        for row in list(out.get("per_file", []) or []):
            per_file_summary.append(
                {
                    "file": row.get("file"),
                    "radius_changes": int(row.get("radius_changes", 0)),
                    "change_count": int(row.get("change_count", 0)),
                    "out_file": row.get("out_file"),
                }
            )
        summary = {
            k: v
            for k, v in out.items()
            if k not in {"config_used", "per_file"}
        }
        summary["per_file"] = per_file_summary
        return summary

    return {
        k: v
        for k, v in out.items()
        if k not in {"dataframe", "bytes", "config_used", "change_details", "change_lines"}
    }


def _summarize_dendrogram_edit_output(out: dict) -> dict:
    changed_node_ids = list(out.get("changed_node_ids", []) or [])
    summary = {
        k: v
        for k, v in out.items()
        if k not in {"bytes", "dataframe", "changed_node_ids"}
    }
    summary["changed_node_count"] = len(changed_node_ids)
    if changed_node_ids:
        summary["changed_node_id_preview"] = changed_node_ids[:10]
    return summary


def _feature_json_arg(sp: argparse.ArgumentParser) -> None:
    sp.add_argument(
        "--config-json",
        default="",
        help="Inline JSON object used to override feature config values for this run.",
    )


def _parse_config_overrides(raw: str) -> dict | None:
    if not raw:
        return None
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("--config-json must be a JSON object")
    return data


def _print_validation_precheck(report: dict) -> None:
    print("Pre-check Summary")
    print("-----------------")
    groups = group_rows_by_category(list(report.get("precheck", [])))
    for category, items in groups:
        print(f"{category}:")
        for item in items:
            key = str(item.get("key", ""))
            rule = rule_for_key(key)
            params = item.get("params") or {}
            print(f"- {item.get('label', item.get('key', ''))}")
            if rule:
                print(f"  rule: {rule}")
            if params:
                print(f"  params: {params}")
        print("")


def _status_tag(status: str) -> str:
    s = str(status or "").lower()
    if s == "pass":
        return "PASS"
    if s == "info":
        return "INFO"
    if s == "warning":
        return "WARN"
    return "FAIL"


def _print_validation_results(report: dict) -> None:
    summary = report.get("summary", {})
    print("")
    print("Validation Results")
    print("------------------")
    print(
        f"total={summary.get('total', 0)} "
        f"pass={summary.get('pass', 0)} "
        f"info={summary.get('info', 0)} "
        f"warning={summary.get('warning', 0)} "
        f"fail={summary.get('fail', 0)}"
    )
    groups = group_rows_by_category(list(report.get("results", [])))
    for category, items in groups:
        print(f"{category}:")
        for row in items:
            print(f"- [{_status_tag(str(row.get('status', '')))}] {row.get('label', row.get('key', ''))}")
        print("")

    details = [
        r
        for r in report.get("results", [])
        if r.get("status") in {"info", "warning", "fail"}
    ]
    if details:
        print("")
        print("Detailed Findings")
        print("-----------------")
        for row in details:
            print(f"* {row.get('label', row.get('key', ''))} ({_status_tag(str(row.get('status', '')))})")
            print(f"  reason: {row.get('message')}")
            print(f"  params: {row.get('params_used', {})}")
            print(f"  thresholds: {row.get('thresholds_used', {})}")
            print(f"  failing_node_ids: {row.get('failing_node_ids', [])}")
            print(f"  failing_section_ids: {row.get('failing_section_ids', [])}")
            print(f"  metrics: {row.get('metrics', {})}")


def _print_auto_typing_guide() -> None:
    from swcstudio.core.model_paths import labeler_info  # noqa: PLC0415

    print(
        f"Auto-labeling: SWC auto-labeler {labeler_info()['version']}\n"
        "----------------------------------\n"
        "Stage 0  structural checks: one connected cell, a soma, finite geometry\n"
        "Stage 1  cell class: 3-class (axon, basal, apical) or 2-class (axon, dendrite)\n"
        "Stage 2  subtree classifier\n"
        "Stage 3  topology rules, and the proximal branch correction for 3-class cells\n"
        "Stage 4  quality flag: each cell's predicted per-cell F1; a batch flags\n"
        "         the riskiest share of each cell class for review\n"
        "\n"
        "Models are resolved via --model-dir, SWCSTUDIO_MODEL_DIR, model_dir in the\n"
        "auto-typing configuration, the user data directory, or the bundled package\n"
        "directory, in that order.\n"
        "Run `swcstudio models status` for a full diagnostic.\n",
        file=sys.stderr,
    )


def _cell_class_arg(raw: str) -> str:
    """argparse type for --cell-class: auto, 3-class or 2-class."""
    from swcstudio.core.auto_typing import normalize_cell_class  # noqa: PLC0415

    try:
        return normalize_cell_class(raw) or "auto"
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _flag_budget_arg(raw: str) -> float:
    value = float(raw)
    if not 0.0 < value <= 1.0:
        raise argparse.ArgumentTypeError("flag budget must be a share in (0, 1], e.g. 0.10")
    return value


def _print_simplification_rule_guide(cfg: dict | None = None) -> None:
    cfg0 = dict(cfg or {})
    thr = dict(cfg0.get("thresholds", {}))
    flags = dict(cfg0.get("flags", {}))
    eps = float(thr.get("epsilon", 2.0))
    radius_tol = float(thr.get("radius_tolerance", 0.5))
    keep_tips = bool(flags.get("keep_tips", True))
    keep_bifs = bool(flags.get("keep_bifurcations", True))
    keep_roots = bool(flags.get("keep_roots", True))
    lines = [
        "Simplification Rule Guide",
        "-------------------------",
        "1) Build directed SWC graph from id/parent.",
        "2) Protect structural nodes (roots + optional tips + optional bifurcations).",
        "3) Split into anchor-to-anchor linear paths.",
        "4) Run RDP on each path interior using epsilon.",
        "5) Protect radius-sensitive nodes when deviation exceeds radius_tolerance.",
        "6) Rewire kept nodes to nearest kept ancestor.",
        "",
        "Radius-sensitive rule:",
        "  abs(node_radius - path_mean_radius) / path_mean_radius > radius_tolerance",
        "",
        "Parameters used for this run:",
        f"- epsilon: {eps}",
        f"- radius_tolerance: {radius_tol}",
        f"- keep_tips: {keep_tips}",
        f"- keep_bifurcations: {keep_bifs}",
        f"- keep_roots: {keep_roots}",
        "",
    ]
    print("\n".join(lines))


def _print_batch_validation_results(batch_report: dict) -> None:
    print("")
    print("Batch Validation Results")
    print("------------------------")
    print(f"folder={batch_report.get('folder', '')}")
    print(
        f"files_total={batch_report.get('files_total', 0)} "
        f"files_validated={batch_report.get('files_validated', 0)} "
        f"files_failed={batch_report.get('files_failed', 0)}"
    )
    totals = dict(batch_report.get("summary_total", {}))
    print(
        f"checks_total={totals.get('total', 0)} "
        f"pass={totals.get('pass', 0)} "
        f"info={totals.get('info', 0)} "
        f"warning={totals.get('warning', 0)} "
        f"fail={totals.get('fail', 0)}"
    )

    for file_row in batch_report.get("results", []):
        file_name = str(file_row.get("file", ""))
        report = dict(file_row.get("report", {}))
        print("")
        print(f"File: {file_name}")
        _print_validation_results(report)

    fails = list(batch_report.get("failures", []))
    if fails:
        print("")
        print("Batch file-level errors")
        print("-----------------------")
        for err in fails:
            print(f"- {err}")


def _print_issue_check_results(file_path: Path, issues: list[dict]) -> None:
    critical = sum(1 for item in issues if str(item.get("severity", "")).strip() == "critical")
    warning = sum(1 for item in issues if str(item.get("severity", "")).strip() == "warning")
    info = sum(1 for item in issues if str(item.get("severity", "")).strip() == "info")
    muted = sum(1 for item in issues if str(item.get("status", "")).strip().lower() in {"muted", "skipped"})

    print("Issue Check")
    print("-----------")
    print(f"Input: {file_path}")
    print(f"Total: {len(issues)}")
    print(f"Critical: {critical}")
    print(f"Warning: {warning}")
    print(f"Info: {info}")
    print(f"Muted: {muted}")

    if not issues:
        print("")
        print("No issues found.")
        return

    grouped = {
        "critical": [item for item in issues if str(item.get("severity", "")).strip() == "critical"],
        "warning": [item for item in issues if str(item.get("severity", "")).strip() == "warning"],
        "info": [item for item in issues if str(item.get("severity", "")).strip() == "info"],
        "muted": [item for item in issues if str(item.get("status", "")).strip().lower() in {"muted", "skipped"}],
    }

    for label, items in grouped.items():
        if not items:
            continue
        print("")
        print(label.capitalize())
        print("-" * len(label.capitalize()))
        for idx, issue in enumerate(items, start=1):
            node_ids = [int(v) for v in issue.get("node_ids", []) if str(v).strip()]
            section_ids = [int(v) for v in issue.get("section_ids", []) if str(v).strip()]
            tool_target = str(issue.get("tool_target", "")).strip() or "validation"
            print(f"{idx}. {issue.get('title', 'Issue')}")
            print(f"   key: {issue.get('source_key', '') or 'n/a'}")
            print(f"   certainty: {issue.get('certainty', '') or 'n/a'}")
            print(f"   tool: {tool_target}")
            if issue.get("description"):
                print(f"   detail: {issue.get('description')}")
            if issue.get("suggested_fix"):
                print(f"   suggested fix: {issue.get('suggested_fix')}")
            if node_ids:
                preview = ", ".join(str(v) for v in node_ids[:20])
                if len(node_ids) > 20:
                    preview += f", ... (+{len(node_ids) - 20} more)"
                print(f"   node_ids ({len(node_ids)}): {preview}")
            if section_ids:
                preview = ", ".join(str(v) for v in section_ids[:20])
                if len(section_ids) > 20:
                    preview += f", ... (+{len(section_ids) - 20} more)"
                print(f"   section_ids ({len(section_ids)}): {preview}")


def _build_cli_issue_list(file_path: Path, *, config_overrides: dict | None = None) -> list[dict]:
    report = run_validation_checks_file(str(file_path), config_overrides=config_overrides).to_dict()
    text = file_path.read_text(encoding="utf-8", errors="ignore")
    df = parse_swc_text_preserve_tokens(text)
    return build_issue_list(df, report)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="swcstudio",
        description=(
            "SWC-Studio CLI. Use direct commands such as 'validate', 'radii-clean', "
            "'simplify', 'set-type', or 'connect'. Legacy grouped commands remain supported."
        ),
    )
    check = p.add_subparsers(dest="tool")
    check_cmd = check.add_parser("check", help="Print the GUI-style issue list for one SWC file")
    check_cmd.add_argument("file", type=Path)
    _feature_json_arg(check_cmd)
    doctor = check.add_parser(
        "doctor",
        help="Verify runtime packages, bundled configuration, models, and GUI imports.",
    )
    doctor.add_argument(
        "--json",
        action="store_true",
        help="Print the structured installation report as JSON.",
    )
    doctor.add_argument(
        "--quick",
        action="store_true",
        help="Check that model files exist without deserializing them.",
    )
    sub = check

    # ------------------------------ batch
    batch = sub.add_parser("batch", help="Batch Processing features")
    batch_sub = batch.add_subparsers(dest="feature")

    batch_validate = batch_sub.add_parser(
        "validate",
        help="Batch Validation on a folder, or use literal rule-guide to print rules",
    )
    batch_validate.add_argument(
        "folder",
        type=Path,
        help="Folder path, or literal rule-guide",
    )
    _feature_json_arg(batch_validate)

    batch_split = batch_sub.add_parser("split", help="Split SWC files by soma roots")
    batch_split.add_argument("folder", type=Path)
    _feature_json_arg(batch_split)

    batch_auto = batch_sub.add_parser(
        "auto-typing",
        help="Auto-label every SWC file in a folder and rank the cells for review.",
    )
    batch_auto.add_argument("folder", type=Path)
    batch_auto.add_argument(
        "--model-dir",
        type=Path,
        default=None,
        help=(
            "Use the labeler model folder at this path (see `swcstudio models status`)."
        ),
    )
    batch_auto.add_argument(
        "--cell-class", "--cell-type",
        dest="cell_type",
        type=_cell_class_arg,
        default="auto",
        metavar="{auto,3-class,2-class}",
        help=("Cell class: auto (Stage 1 decides, the default), 3-class (axon, basal, "
              "apical) or 2-class (axon, dendrite). A chosen class that Stage 1 disputes "
              "is used, and the cell is flagged as a class mismatch."),
    )
    batch_auto.add_argument(
        "--flag-budget",
        type=_flag_budget_arg,
        default=0.10,
        help="Share of each cell class flagged for review, riskiest first (default 0.10).",
    )
    batch_auto.add_argument(
        "--no-flag",
        action="store_true",
        help="Do not score the quality flag.",
    )
    _feature_json_arg(batch_auto)

    batch_radii = batch_sub.add_parser("radii-clean", help="Radii cleaning on a file or folder")
    batch_radii.add_argument("target", type=Path)
    _feature_json_arg(batch_radii)

    batch_simplify = batch_sub.add_parser("simplify", help="Run simplification on all SWC files in a folder")
    batch_simplify.add_argument("folder", type=Path)
    _feature_json_arg(batch_simplify)

    batch_index = batch_sub.add_parser("index-clean", help="Reorder and reindex all SWC files in a folder")
    batch_index.add_argument("folder", type=Path)
    _feature_json_arg(batch_index)

    # ------------------------------ validation
    validation = sub.add_parser("validation", help="Validation features")
    val_sub = validation.add_subparsers(dest="feature")

    val_auto_fix = val_sub.add_parser("auto-fix", help="Validate and sanitize one SWC file")
    val_auto_fix.add_argument("file", type=Path)
    _feature_json_arg(val_auto_fix)

    val_guide = val_sub.add_parser("rule-guide", help="Show validation rule guide (no file required)")
    _feature_json_arg(val_guide)

    val_run = val_sub.add_parser("run", help="Run structured validation checks on one SWC file")
    val_run.add_argument("file", type=Path)
    _feature_json_arg(val_run)

    val_auto_label = val_sub.add_parser(
        "auto-label",
        help="Auto-label one SWC file.",
    )
    val_auto_label.add_argument("file", type=Path)
    val_auto_label.add_argument(
        "--model-dir",
        type=Path,
        default=None,
        help=(
            "Use the labeler model folder at this path (see `swcstudio models status`)."
        ),
    )
    val_auto_label.add_argument(
        "--cell-class", "--cell-type",
        dest="cell_type",
        type=_cell_class_arg,
        default="auto",
        metavar="{auto,3-class,2-class}",
        help=("Cell class: auto (Stage 1 decides, the default), 3-class (axon, basal, "
              "apical) or 2-class (axon, dendrite). A chosen class that Stage 1 disputes "
              "is used, and the cell is flagged as a class mismatch."),
    )
    val_auto_label.add_argument(
        "--no-flag",
        action="store_true",
        help="Do not score the quality flag.",
    )
    _feature_json_arg(val_auto_label)

    val_radii = val_sub.add_parser("radii-clean", help="Radii cleaning on a file or folder")
    val_radii.add_argument("target", type=Path)
    _feature_json_arg(val_radii)

    val_index = val_sub.add_parser("index-clean", help="Reorder and reindex one SWC file")
    val_index.add_argument("file", type=Path)
    _feature_json_arg(val_index)

    # ------------------------------ visualization
    visualization = sub.add_parser("visualization", help="Visualization backends")
    viz_sub = visualization.add_subparsers(dest="feature")

    viz_mesh = viz_sub.add_parser("mesh-editing", help="Build reusable mesh payload for a file")
    viz_mesh.add_argument("file", type=Path)
    viz_mesh.add_argument("--include-edges", action="store_true", default=False)
    _feature_json_arg(viz_mesh)

    # ------------------------------ morphology
    morphology = sub.add_parser("morphology", help="Morphology Editing features")
    morph_sub = morphology.add_subparsers(dest="feature")

    morph_d = morph_sub.add_parser("dendrogram-edit", help="Reassign a subtree node type")
    morph_d.add_argument("file", type=Path)
    morph_d.add_argument("--node-id", required=True, type=int)
    morph_d.add_argument("--new-type", required=True, type=int)
    _feature_json_arg(morph_d)

    morph_radius = morph_sub.add_parser("set-radius", help="Set one node radius in a file")
    morph_radius.add_argument("file", type=Path)
    morph_radius.add_argument("--node-id", required=True, type=int)
    morph_radius.add_argument("--radius", required=True, type=float)
    _feature_json_arg(morph_radius)

    morph_type = morph_sub.add_parser("set-type", help="Set one node type in a file")
    morph_type.add_argument("file", type=Path)
    morph_type.add_argument("--node-id", required=True, type=int)
    morph_type.add_argument("--new-type", required=True, type=int)
    _feature_json_arg(morph_type)

    # ------------------------------ geometry
    geometry = sub.add_parser("geometry", help="Geometry editing operations without GUI")
    geom_sub = geometry.add_subparsers(dest="feature")

    geom_simplify = geom_sub.add_parser("simplify", help="Graph-aware SWC simplification")
    geom_simplify.add_argument("file", type=Path)
    _feature_json_arg(geom_simplify)

    geom_move_node = geom_sub.add_parser("move-node", help="Move one node to absolute XYZ")
    geom_move_node.add_argument("file", type=Path)
    geom_move_node.add_argument("--node-id", required=True, type=int)
    geom_move_node.add_argument("--x", required=True, type=float)
    geom_move_node.add_argument("--y", required=True, type=float)
    geom_move_node.add_argument("--z", required=True, type=float)
    geom_move_subtree = geom_sub.add_parser("move-subtree", help="Move a subtree by setting its root to absolute XYZ")
    geom_move_subtree.add_argument("file", type=Path)
    geom_move_subtree.add_argument("--root-id", required=True, type=int)
    geom_move_subtree.add_argument("--x", required=True, type=float)
    geom_move_subtree.add_argument("--y", required=True, type=float)
    geom_move_subtree.add_argument("--z", required=True, type=float)
    geom_connect = geom_sub.add_parser("connect", help="Set end-node parent to start-node")
    geom_connect.add_argument("file", type=Path)
    geom_connect.add_argument("--start-id", required=True, type=int)
    geom_connect.add_argument("--end-id", required=True, type=int)
    geom_disconnect = geom_sub.add_parser("disconnect", help="Disconnect all edges along the path between start and end")
    geom_disconnect.add_argument("file", type=Path)
    geom_disconnect.add_argument("--start-id", required=True, type=int)
    geom_disconnect.add_argument("--end-id", required=True, type=int)
    geom_delete = geom_sub.add_parser("delete-node", help="Delete one node")
    geom_delete.add_argument("file", type=Path)
    geom_delete.add_argument("--node-id", required=True, type=int)
    geom_delete.add_argument("--reconnect-children", action="store_true", default=False)
    geom_delete_sub = geom_sub.add_parser("delete-subtree", help="Delete one subtree")
    geom_delete_sub.add_argument("file", type=Path)
    geom_delete_sub.add_argument("--root-id", required=True, type=int)
    geom_insert = geom_sub.add_parser("insert", help="Insert a node after start and optionally before end")
    geom_insert.add_argument("file", type=Path)
    geom_insert.add_argument("--start-id", required=True, type=int)
    geom_insert.add_argument("--end-id", type=int, default=-1)
    geom_insert.add_argument("--x", required=True, type=float)
    geom_insert.add_argument("--y", required=True, type=float)
    geom_insert.add_argument("--z", required=True, type=float)
    geom_insert.add_argument("--radius", type=float, default=None)
    geom_insert.add_argument("--type-id", type=int, default=None)
    # ------------------------------ train (the labeler on your own data)
    train = sub.add_parser(
        "train",
        help="Train the auto-labeler on your own labeled SWC files.",
    )
    train_sub = train.add_subparsers(dest="feature")

    train_auto = train_sub.add_parser(
        "auto-typing",
        help="Train the labeler's four stages on a labeled SWC dataset.",
    )
    train_auto.add_argument(
        "--data-dir",
        type=Path,
        required=True,
        help="Folder with pyramidal/ and interneuron/ subdirectories of labeled SWCs.",
    )
    train_auto.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Model folder to write (use it with --model-dir).",
    )
    train_auto.add_argument("--seed", type=int, default=None,
                            help="Seed of the held-out split (random when omitted; recorded).")
    train_auto.add_argument("--test-size", type=float, default=0.20,
                            help="Held-out share, which the quality flag is fitted on (default 0.20).")
    train_auto.add_argument("--device", choices=("cpu", "cuda"), default="cpu",
                            help="Fit Stages 1 and 2 on the CPU (default) or an NVIDIA GPU.")
    train_auto.add_argument("--skip", default="",
                            help="Comma list of stages to leave out: stage1, stage2, correction, flag.")

    # ------------------------------ models (status / info)
    models = sub.add_parser(
        "models",
        help="Inspect the auto-labeler's model folder and where it was found.",
    )
    models_sub = models.add_subparsers(dest="feature")
    models_status = models_sub.add_parser(
        "status",
        help="Report the labeler version and which model folder is used.",
    )
    models_status.add_argument(
        "--model-dir",
        type=Path,
        default=None,
        help="Override the model directory.",
    )

    # ------------------------------ plugins
    plugins = sub.add_parser("plugins", help="Plugin manager and registry inspection")
    plugins_sub = plugins.add_subparsers(dest="feature")

    plugins_list = plugins_sub.add_parser("list", help="List plugin + builtin methods")
    plugins_list.add_argument("--feature-key", default="")
    plugins_list_loaded = plugins_sub.add_parser("list-loaded", help="List loaded plugin manifests")
    plugins_load = plugins_sub.add_parser("load", help="Load plugin module by import path")
    plugins_load.add_argument("module", help="Python module path, e.g. my_plugins.brain_globe")

    # ------------------------------ history (provenance)
    # New 'history' tool group from PROVENANCE_SPEC §13. Self-contained
    # in cli/history_cli.py to keep this file's footprint tiny.
    from swcstudio.cli.history_cli import add_history_subparser
    add_history_subparser(sub)

    return p


def _looks_like_file_target(raw: str) -> bool:
    target = Path(str(raw or ""))
    if target.exists():
        return target.is_file()
    return target.suffix.lower() == ".swc"


def _normalize_cli_argv(argv: list[str]) -> list[str]:
    if not argv:
        return argv

    first = str(argv[0]).strip()
    if first in {
        "check",
        "batch",
        "validation",
        "visualization",
        "morphology",
        "geometry",
        "plugins",
        "doctor",
        "models",
        "train",
        "history",
    }:
        return argv

    if first == "plugins-list":
        return ["plugins", "list", *argv[1:]]
    if first == "plugins-list-loaded":
        return ["plugins", "list-loaded", *argv[1:]]
    if first == "plugins-load":
        return ["plugins", "load", *argv[1:]]

    if first == "rule-guide":
        return ["validation", "rule-guide", *argv[1:]]
    if first == "run":
        return ["validation", "run", *argv[1:]]
    if first == "auto-fix":
        return ["validation", "auto-fix", *argv[1:]]
    if first == "auto-label":
        return ["validation", "auto-label", *argv[1:]]
    if first == "radii-clean":
        return ["validation", "radii-clean", *argv[1:]]
    if first == "mesh-editing":
        return ["visualization", "mesh-editing", *argv[1:]]
    if first == "dendrogram-edit":
        return ["morphology", "dendrogram-edit", *argv[1:]]
    if first == "set-radius":
        return ["morphology", "set-radius", *argv[1:]]
    if first == "set-type":
        return ["morphology", "set-type", *argv[1:]]
    if first in {
        "move-node",
        "move-subtree",
        "connect",
        "disconnect",
        "delete-node",
        "delete-subtree",
        "insert",
    }:
        return ["geometry", first, *argv[1:]]
    if first == "split":
        return ["batch", "split", *argv[1:]]
    if first == "auto-typing":
        return ["batch", "auto-typing", *argv[1:]]

    if first == "validate":
        if len(argv) < 2:
            return argv
        target = str(argv[1]).strip()
        if target.lower() == "rule-guide":
            return ["validation", "rule-guide", *argv[2:]]
        if _looks_like_file_target(target):
            return ["validation", "run", *argv[1:]]
        return ["batch", "validate", *argv[1:]]

    if first == "simplify":
        if len(argv) < 2:
            return argv
        return (
            ["geometry", "simplify", *argv[1:]]
            if _looks_like_file_target(str(argv[1]))
            else ["batch", "simplify", *argv[1:]]
        )

    if first == "index-clean":
        if len(argv) < 2:
            return argv
        return (
            ["validation", "index-clean", *argv[1:]]
            if _looks_like_file_target(str(argv[1]))
            else ["batch", "index-clean", *argv[1:]]
        )

    return argv


def main(argv: list[str] | None = None) -> int:
    autoload_plugins_from_environment()
    argv = argv if argv is not None else sys.argv[1:]
    argv = _normalize_cli_argv(list(argv))
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.tool == "history":
            # New provenance tool group (PROVENANCE_SPEC §13). Lives in
            # its own module so it doesn't entangle with the rest of
            # this dispatcher.
            from swcstudio.cli.history_cli import dispatch_history
            return dispatch_history(args)

        if args.tool == "check":
            file_path = Path(args.file)
            if not file_path.exists():
                raise FileNotFoundError(str(file_path))
            issues = _build_cli_issue_list(file_path, config_overrides=_parse_config_overrides(args.config_json))
            _print_issue_check_results(file_path, issues)
            return 0

        if args.tool == "doctor":
            from swcstudio.core.install_check import (  # noqa: PLC0415
                check_installation,
                format_installation_report,
            )

            report = check_installation(load_models=not bool(args.quick))
            if bool(args.json):
                _print_json(report)
            else:
                print(format_installation_report(report))
            return 0 if report["ok"] else 2

        # -------- batch
        if args.tool == "batch" and args.feature == "validate":
            cfg_overrides = _parse_config_overrides(args.config_json)
            target = str(args.folder).strip().lower()
            if target == "rule-guide":
                cfg = load_validation_config(overrides=cfg_overrides)
                precheck = [p.to_dict() for p in build_precheck_summary(cfg)]
                _print_validation_precheck({"precheck": precheck})
                return 0

            out = validate_folder(
                str(args.folder),
                config_overrides=cfg_overrides,
            )
            _print_validation_precheck({"precheck": out.get("precheck", [])})
            _print_batch_validation_results(out)
            if out.get("log_path"):
                print(f"\nReport file: {out.get('log_path')}")
            return 0

        if args.tool == "batch" and args.feature == "split":
            # Split is structurally unlike the other batch verbs: each
            # input SWC produces N output SWCs (one per soma root).
            #
            # New provenance shape:
            #   * No commit on the input file (it is read, not modified).
            #   * Each output file is a NEW dataset with its own .history/.
            #     Its first commit records derived_from = {root_sha,
            #     commit_sha, path} pointing back to the source — when
            #     present, the source's tracked history; otherwise the
            #     raw input hash + a sentinel.
            #   * Outputs land at <input_stem>/<input_stem>_tree_<N>.swc
            #     in a per-batch timestamped folder, matching the legacy
            #     "single_output_subdir" structure so users keep familiar
            #     folder layouts.
            from swcstudio.core.provenance import (
                OpKind,
                canonical_swc,
                derived_from_for_swc_path,
                derived_from_payload,
                sha256_hex,
                tracked_op,
            )
            from swcstudio.tools.batch_processing.features.swc_splitter import (
                split_swc_text,
            )
            import time

            folder = Path(args.folder)
            if not folder.is_dir():
                raise NotADirectoryError(str(folder))

            cfg_overrides = _parse_config_overrides(args.config_json)
            run_ts = time.strftime("%Y%m%d_%H%M%S")
            out_dir = folder / f"{folder.name}_batch_split_{run_ts}"
            out_dir.mkdir(parents=True, exist_ok=True)

            swcs = sorted(
                [p for p in folder.iterdir() if is_swc_data_file(p)],
                key=lambda p: p.name.lower(),
            )
            if not swcs:
                raise FileNotFoundError(f"No .swc files found in: {folder}")

            files_total = len(swcs)
            files_split = files_skipped = trees_saved = 0
            failures: list[str] = []
            output_files: list[str] = []
            output_commits: list[dict] = []

            for fp in swcs:
                try:
                    text = fp.read_text(encoding="utf-8", errors="ignore")
                    trees = split_swc_text(text, config_overrides=cfg_overrides)
                    if len(trees) <= 1:
                        files_skipped += 1
                        continue
                    files_split += 1
                    # Use the input file's root hash + (if available) its
                    # current branch tip as the derived_from anchor.
                    src_payload = derived_from_for_swc_path(fp)
                    if src_payload is None:
                        src_payload = derived_from_payload(
                            source_root_sha=sha256_hex(canonical_swc(fp.read_bytes())),
                            source_commit_sha="sha256:" + ("0" * 64),  # sentinel: no commit yet
                            source_path=fp.name,
                        )

                    file_out_dir = out_dir / fp.stem
                    file_out_dir.mkdir(parents=True, exist_ok=True)
                    for idx, (_root_id, sub_text, _node_count) in enumerate(trees, start=1):
                        out_path = file_out_dir / f"{fp.stem}_tree_{idx}.swc"
                        # First, create the new file with the split bytes.
                        out_path.write_bytes(sub_text.encode("utf-8"))
                        # Now record a derived_from commit on its history.
                        try:
                            with tracked_op(
                                out_path,
                                kind=OpKind.SPLIT,
                                params={"source": fp.name, "tree_index": idx,
                                        "tree_count": len(trees)},
                                message=f"split: tree {idx}/{len(trees)} from {fp.name}",
                                derived_from=src_payload,
                            ) as op:
                                # No-op edit relative to the just-written file —
                                # this records the commit + derived_from with
                                # input_sha == output_sha.
                                op.set_output(out_path.read_bytes())
                            output_commits.append({
                                "file":       str(out_path.relative_to(out_dir)),
                                "commit_sha": op.result.commit_sha,
                            })
                        except Exception as e:  # noqa: BLE001
                            failures.append(f"{out_path.name}: history record failed: {e}")
                        output_files.append(str(out_path.relative_to(out_dir)))
                        trees_saved += 1
                except Exception as e:  # noqa: BLE001
                    failures.append(f"{fp.name}: {e}")

            result = {
                "folder":         str(folder),
                "out_dir":        str(out_dir),
                "files_total":    files_total,
                "files_split":    files_split,
                "files_skipped":  files_skipped,
                "trees_saved":    trees_saved,
                "output_files":   output_files,
                "output_commits": output_commits,
                "failures":       failures,
            }
            _print_json(result)
            return 0

        if args.tool == "batch" and args.feature == "auto-typing":
            _print_auto_typing_guide()
            opts = BatchOptions(
                soma=True,
                axon=True,
                apic=True,
                basal=True,
                rad=False,
                zip_output=False,
                cell_type=args.cell_type,
                flag_enabled=not bool(getattr(args, "no_flag", False)),
                flag_budget=float(getattr(args, "flag_budget", 0.10)),
            )
            cfg_overrides = _parse_config_overrides(args.config_json) or {}
            if getattr(args, "model_dir", None):
                cfg_overrides["model_dir"] = str(args.model_dir)
            from swcstudio.core.auto_typing import is_available  # noqa: PLC0415
            # The run's own check: --model-dir, else SWCSTUDIO_MODEL_DIR, else
            # model_dir in the auto-typing configuration, else the default search.
            ok, reason = is_available(model_dir=cfg_overrides.get("model_dir") or None, auto_download=True)
            if not ok:
                print(f"ERROR: auto-typing engine unavailable.\n{reason}", file=sys.stderr)
                return 2

            folder = Path(args.folder)
            if not folder.is_dir():
                raise NotADirectoryError(str(folder))
            # The same batch the GUI runs: each file labeled in place with its
            # own history, one flag review and one text report in the folder.
            from swcstudio.tools.batch_processing.features.auto_typing import (  # noqa: PLC0415
                run_folder as run_batch_auto_typing,
            )
            # As the GUI does, so the recorded configuration is the one used.
            cfg_overrides.update(cell_type=opts.cell_type, flag_enabled=bool(opts.flag_enabled),
                                 flag_budget=float(opts.flag_budget))
            res = run_batch_auto_typing(
                str(folder),
                options=opts,
                config_overrides=cfg_overrides,
                message="batch auto-label",
            )
            _print_json(
                {
                    "folder": res.folder,
                    "files_total": res.files_total,
                    "files_processed": res.files_processed,
                    "files_failed": res.files_failed,
                    "files_qc_failed": res.files_qc_failed,
                    "files_with_warnings": res.files_with_warnings,
                    "total_nodes": res.total_nodes,
                    "total_type_changes": res.total_type_changes,
                    "total_radius_changes": res.total_radius_changes,
                    "files_flagged": res.files_flagged,
                    "flag_budget": res.flag_budget,
                    "flag_review_path": res.flag_review_path,
                    "flagged_files": [r["file"] for r in (res.flag_records or []) if r.get("flagged")],
                    "per_file": res.per_file,
                    "failures": res.failures,
                    "commits": [
                        {**commit, "output_path": str(Path(res.folder) / str(commit.get("file")))}
                        for commit in (res.commits or [])
                    ],
                    "log_path": res.log_path,
                    "out_dir": None,
                    "model_record": res.model_record,
                }
            )
            return 0

        if args.tool == "batch" and args.feature == "radii-clean":
            # The 'target' arg can be a file or a folder. File mode delegates
            # to the already-converted single-file path. Folder mode iterates
            # via _tracked_batch.
            target = Path(args.target)
            cfg_overrides = _parse_config_overrides(args.config_json)
            if target.is_file():
                # Reuse the converted single-file 'validation radii-clean' path.
                from swcstudio.core.provenance import OpKind, current_swc_path_for, tracked_op
                # NOTE: alias the import to avoid shadowing the top-level
                # merge_config (re-binding it as a local in main() would
                # break the geometry-simplify branch that uses the global).
                from swcstudio.core.config import merge_config as _merge_radii
                from swcstudio.tools.batch_processing.features.radii_cleaning import (
                    clean_swc_text, get_config as _radii_default_config,
                )
                effective_cfg = _merge_radii(_radii_default_config(), cfg_overrides or {})
                with tracked_op(
                    target, kind=OpKind.RADII_CLEAN,
                    params=_config_params(cfg_overrides, effective_cfg),
                    message="batch radii-clean (single file mode)",
                ) as op:
                    in_bytes = op.input_bytes if op.input_bytes is not None else target.read_bytes()
                    result = clean_swc_text(in_bytes.decode("utf-8", errors="ignore"),
                                            config_overrides=cfg_overrides)
                    op.set_output(result["bytes"])
                _print_json({
                    "mode": "file",
                    "input_path": str(target),
                    "output_path": str(current_swc_path_for(target)),
                    "passes": int(result.get("passes", 0)),
                    "radius_changes": int(result.get("changes", 0)),
                    "change_count": int(len(result.get("change_details", []) or [])),
                    "commit_sha": op.result.commit_sha,
                    "branch": op.result.branch,
                })
                return 0
            if not target.is_dir():
                raise NotADirectoryError(str(target))

            from swcstudio.core.provenance import OpKind
            from swcstudio.tools.batch_processing.features.radii_cleaning import clean_swc_text

            def _mutate(text: str):
                return clean_swc_text(text, config_overrides=cfg_overrides)

            def _summary(swc, result, op_result):
                return (
                    f"{swc.name}: passes={int(result.get('passes', 0))}, "
                    f"radius_changes={int(result.get('changes', 0))}"
                )

            from swcstudio.core.config import merge_config as _merge_radii
            from swcstudio.tools.batch_processing.features.radii_cleaning import (
                get_config as _radii_default_config,
            )
            _radii_effective = _merge_radii(_radii_default_config(), cfg_overrides or {})
            out = _tracked_batch(
                target,
                op_kind=OpKind.RADII_CLEAN,
                mutate_text=_mutate,
                params_for=lambda _: _config_params(cfg_overrides, _radii_effective),
                message="batch radii-clean",
                per_file_summary=_summary,
            )
            out["mode"] = "folder"
            _print_json(out)
            return 0

        if args.tool == "batch" and args.feature == "simplify":
            from swcstudio.core.provenance import OpKind
            from swcstudio.core.config import merge_config as _merge_simp
            from swcstudio.tools.morphology_editing.features.simplification import (
                get_config as _simp_default_config,
                simplify_swc_text,
            )

            folder = Path(args.folder)
            if not folder.is_dir():
                raise NotADirectoryError(str(folder))
            cfg_overrides = _parse_config_overrides(args.config_json)
            _simp_effective = _merge_simp(_simp_default_config(), cfg_overrides or {})

            def _mutate(text: str):
                return simplify_swc_text(text, config_overrides=cfg_overrides)

            def _summary(swc, result, op_result):
                return (
                    f"{swc.name}: {int(result.get('original_node_count', 0))} -> "
                    f"{int(result.get('new_node_count', 0))} nodes "
                    f"({float(result.get('reduction_percent', 0.0)):.2f}% reduction)"
                )

            out = _tracked_batch(
                folder,
                op_kind=OpKind.SIMPLIFICATION,
                mutate_text=_mutate,
                params_for=lambda _: _config_params(cfg_overrides, _simp_effective),
                message="batch simplify",
                per_file_summary=_summary,
            )
            _print_json(out)
            return 0

        if args.tool == "batch" and args.feature == "index-clean":
            # Each input SWC gets one commit on its own .history/;
            # no separate batch output folder is created. Outputs land at
            # directly to each source SWC.
            from swcstudio.core.provenance import OpKind
            from swcstudio.tools.validation.features.index_clean import index_clean_text

            folder = Path(args.folder)
            if not folder.is_dir():
                raise NotADirectoryError(str(folder))
            cfg_overrides = _parse_config_overrides(args.config_json)

            def _mutate(text: str):
                return index_clean_text(text, config_overrides=cfg_overrides)

            def _summary(swc, result, op_result):
                return (
                    f"{swc.name}: {int(result.get('original_node_count', 0))} nodes -> "
                    f"{int(result.get('new_node_count', 0))} nodes, "
                    f"remapped IDs: {int(result.get('remapped_id_count', 0))}"
                )

            out = _tracked_batch(
                folder,
                op_kind=OpKind.INDEX_CLEAN,
                mutate_text=_mutate,
                params_for=lambda _: {},
                message="batch index-clean",
                per_file_summary=_summary,
            )
            _print_json(out)
            return 0

        # -------- validation
        if args.tool == "validation" and args.feature == "rule-guide":
            cfg = load_validation_config(overrides=_parse_config_overrides(args.config_json))
            precheck = [p.to_dict() for p in build_precheck_summary(cfg)]
            _print_validation_precheck({"precheck": precheck})
            return 0

        if args.tool == "validation" and args.feature == "run":
            report = run_validation_checks_file(
                str(args.file),
                config_overrides=_parse_config_overrides(args.config_json),
            ).to_dict()
            _print_validation_results(report)
            report_path = write_text_report(
                validation_log_path_for_file(args.file),
                format_validation_report_text(report),
            )
            print(f"\nReport file: {report_path}")
            return 0

        if args.tool == "validation" and args.feature == "radii-clean":
            # File mode records one provenance commit. Folder mode falls
            # through to the shared validation path below.
            target = Path(args.target)
            if target.exists() and target.is_file():
                from swcstudio.core.provenance import (
                    OpKind,
                    current_swc_path_for,
                    tracked_op,
                )
                from swcstudio.core.config import merge_config as _merge_radii
                from swcstudio.tools.batch_processing.features.radii_cleaning import (
                    clean_swc_text,
                    get_config as _radii_default_config,
                )

                cfg_overrides = _parse_config_overrides(args.config_json)
                _radii_effective = _merge_radii(_radii_default_config(), cfg_overrides or {})

                with tracked_op(
                    target,
                    kind=OpKind.RADII_CLEAN,
                    params=_config_params(cfg_overrides, _radii_effective),
                    message="radii-clean (auto)",
                ) as op:
                    in_bytes = op.input_bytes if op.input_bytes is not None else target.read_bytes()
                    in_text = in_bytes.decode("utf-8", errors="ignore")
                    result = clean_swc_text(in_text, config_overrides=cfg_overrides)
                    op.set_output(result["bytes"])

                out_print = {
                    "mode":               "file",
                    "input_path":         str(target),
                    "output_path":        str(current_swc_path_for(target)),
                    "operation_log_path": None,
                    "passes":             int(result.get("passes", 0)),
                    "radius_changes":     int(result.get("changes", 0)),
                    "change_count":       int(len(result.get("change_details", []) or [])),
                    "commit_sha":         op.result.commit_sha,
                    "branch":             op.result.branch,
                    "input_sha":          op.result.input_sha,
                    "output_sha":         op.result.output_sha,
                    "diff_ref":           op.result.diff_ref,
                }
                _print_json(out_print)
                return 0

            # Folder / batch mode — untouched until checklist item 1.2 #18.
            out = validation_clean_radii_path(
                str(args.target),
                write_file_report=False,
                config_overrides=_parse_config_overrides(args.config_json),
            )
            out_print = {
                k: v
                for k, v in out.items()
                if k not in {"dataframe", "bytes", "config_used", "change_details", "change_lines"}
            }
            if not out_print.get("log_path"):
                out_print.pop("log_path", None)
            _print_json(out_print)
            if out.get("log_path"):
                print(f"\nReport file: {out.get('log_path')}")
            return 0

        if args.tool == "validation" and args.feature == "auto-fix":
            # Auto-fix carries extra metadata beyond the diff (which rules
            # fired, what issues were found). We:
            #   * print the validation results to stdout (user-visible),
            #   * embed a summary of those results into the commit's params
            #     so it's queryable later via 'history show',
            #   * drop the separate validation_auto_fix_<ts>.txt sidecar
            #     (per spec M9 — equivalent text available via
            #     'history show <sha> --format=text' plus the validation
            #     results we just printed).
            from swcstudio.core.provenance import (
                OpKind,
                current_swc_path_for,
                tracked_op,
            )
            from swcstudio.tools.validation.features.auto_fix import auto_fix_text

            src = Path(args.file)
            if not src.exists():
                raise FileNotFoundError(str(src))

            # Run auto-fix once up front so we can include the result count
            # in the op's params before opening the tracked_op block. The
            # function is deterministic given the same input + config, so
            # we'll re-run it inside the block on the same bytes for the
            # actual mutation. (Cheap; auto-fix is in-memory only.)
            cfg_overrides = _parse_config_overrides(args.config_json)
            pre_text = src.read_text(encoding="utf-8", errors="ignore")
            pre_run = auto_fix_text(pre_text, config_overrides=cfg_overrides)
            pre_rows = list(pre_run.get("rows", []) or [])
            pre_report = pre_run.get("report") if isinstance(pre_run.get("report"), dict) else {}
            pre_summary = dict((pre_report or {}).get("summary", {}))
            # Effective config (defaults merged with --config-json overrides)
            # so the recorded params fully describe what controlled the run.
            from swcstudio.core.config import merge_config as _merge_af
            from swcstudio.tools.validation.features.auto_fix import (
                get_config as _af_default_config,
            )
            _af_effective = _merge_af(_af_default_config(), cfg_overrides or {})

            _af_params = _config_params(cfg_overrides, _af_effective)
            _af_params["result_count"]   = len(pre_rows)
            _af_params["report_summary"] = pre_summary
            with tracked_op(
                src,
                kind=OpKind.AUTO_FIX,
                params=_af_params,
                message=f"auto-fix ({len(pre_rows)} issue(s); {pre_summary})",
            ) as op:
                in_bytes = op.input_bytes if op.input_bytes is not None else src.read_bytes()
                in_text = in_bytes.decode("utf-8", errors="ignore")
                result = auto_fix_text(in_text, config_overrides=cfg_overrides)
                op.set_output(result["sanitized_bytes"])

            # Print validation results so users still see what was fixed,
            # exactly like the old handler did.
            if isinstance(result.get("report"), dict):
                _print_validation_results(result["report"])

            out_print = {
                "input_path":            str(src),
                "output_path":           str(current_swc_path_for(src)),
                "report_path":           None,  # dropped — see 'history show' + stdout output above
                "operation_log_path":    None,
                "result_count":          len(list(result.get("rows", []) or [])),
                "sanitized_text_length": len(str(result.get("sanitized_text", "") or "")),
                "commit_sha":            op.result.commit_sha,
                "branch":                op.result.branch,
                "input_sha":             op.result.input_sha,
                "output_sha":            op.result.output_sha,
                "diff_ref":              op.result.diff_ref,
            }
            _print_json(out_print)
            return 0

        if args.tool == "validation" and args.feature == "auto-label":
            from swcstudio.core.provenance import OpKind, current_swc_path_for, tracked_op

            opts = BatchOptions(
                soma=True,
                axon=True,
                apic=True,
                basal=True,
                rad=False,
                zip_output=False,
                cell_type=args.cell_type,
                flag_enabled=not bool(getattr(args, "no_flag", False)),
                flag_budget=float(getattr(args, "flag_budget", 0.10)),
            )
            cfg_overrides = _parse_config_overrides(args.config_json) or {}
            if getattr(args, "model_dir", None):
                cfg_overrides["model_dir"] = str(args.model_dir)
            from swcstudio.core.auto_typing import is_available, resolve_models  # noqa: PLC0415
            from swcstudio.core.model_paths import model_record  # noqa: PLC0415
            model_argument = cfg_overrides.get("model_dir") or None
            # The run's own check: --model-dir, else SWCSTUDIO_MODEL_DIR, else
            # model_dir in the auto-typing configuration, else the default search.
            ok, reason = is_available(model_dir=model_argument, auto_download=True)
            if not ok:
                print(f"ERROR: auto-typing engine unavailable.\n{reason}", file=sys.stderr)
                return 2
            src = Path(args.file)
            if not src.exists():
                raise FileNotFoundError(str(src))
            models = resolve_models(model_argument)
            out = validation_auto_label_file(
                str(src),
                options=opts,
                config_overrides=cfg_overrides,
                output_path=None,
                write_output=False,
                write_log=False,
                models=models,
            )
            out_counts = dict(out.get("out_type_counts", {}) or {})
            flag_result = dict(out.get("flag_result", {}) or {})
            # model_dir (home as ~), model_source, model_version, model_sha, model_files
            record = dict(out.get("model_record") or model_record(models.folder, models.source))
            out["model_record"] = record
            params = {
                "cell_type": opts.cell_type,
                "flag_enabled": bool(opts.flag_enabled),
                "labeler_version": out.get("labeler_version"),
                **record,
                "nodes_total": int(out.get("nodes_total", 0)),
                "type_changes": int(out.get("type_changes", 0)),
                "cell_type_result": out.get("cell_type"),
                "cell_type_source": out.get("cell_type_source"),
                "stage1_cell_type": out.get("stage1_cell_type"),
                "stage1_confidence": out.get("stage1_confidence"),
                "class_mismatch": bool(out.get("class_mismatch", False)),
                "flagged": bool(flag_result.get("flagged", False)),
                "predicted_f1": flag_result.get("predicted_f1"),
                "out_type_counts": out_counts,
                "unlabeled_nodes": int(out.get("unlabeled_nodes", 0) or 0),
            }
            with tracked_op(
                src,
                kind=OpKind.AUTO_LABEL,
                params=params,
                message=(
                    "auto-label "
                    f"type_changes={int(out.get('type_changes', 0))} "
                    f"cell_class={out.get('cell_type') or 'auto'}"
                ),
                is_ai=True,
            ) as op:
                op.set_output(bytes(out.get("bytes") or b""))

            out.update(
                {
                    "output_path": str(current_swc_path_for(src)),
                    "operation_log_path": None,
                    "commit_sha": op.result.commit_sha,
                    "branch": op.result.branch,
                    "input_sha": op.result.input_sha,
                    "output_sha": op.result.output_sha,
                    "diff_ref": op.result.diff_ref,
                    "ai_run_ref": op.result.ai_run_ref,
                }
            )
            out_print = {k: v for k, v in out.items() if k not in {"dataframe", "bytes", "result_obj"}}
            _print_json(out_print)
            return 0

        if args.tool == "validation" and args.feature == "index-clean":
            from swcstudio.core.provenance import (
                OpKind,
                current_swc_path_for,
                tracked_op,
            )
            from swcstudio.tools.validation.features.index_clean import (
                index_clean_text,
            )

            src = Path(args.file)
            if not src.exists():
                raise FileNotFoundError(str(src))

            with tracked_op(
                src,
                kind=OpKind.INDEX_CLEAN,
                params={},
                message="index-clean (reorder + reindex)",
            ) as op:
                in_bytes = op.input_bytes if op.input_bytes is not None else src.read_bytes()
                in_text = in_bytes.decode("utf-8", errors="ignore")
                result = index_clean_text(
                    in_text,
                    config_overrides=_parse_config_overrides(args.config_json),
                )
                op.set_output(result["bytes"])

            out_print = {
                "original_node_count": int(result.get("original_node_count", 0)),
                "new_node_count":      int(result.get("new_node_count", 0)),
                "remapped_id_count":   int(result.get("remapped_id_count", 0)),
                "id_map_size":         len(dict(result.get("id_map", {}))),
                "input_path":          str(src),
                "output_path":         str(current_swc_path_for(src)),
                "operation_log_path":  None,
                "commit_sha":          op.result.commit_sha,
                "branch":              op.result.branch,
                "input_sha":           op.result.input_sha,
                "output_sha":          op.result.output_sha,
                "diff_ref":            op.result.diff_ref,
            }
            _print_json(out_print)
            return 0

        # -------- visualization
        if args.tool == "visualization" and args.feature == "mesh-editing":
            cfg = _parse_config_overrides(args.config_json) or {}
            if args.include_edges:
                cfg["output"] = dict(cfg.get("output", {}))
                cfg["output"]["include_edges"] = True
            out = build_mesh_from_file(str(args.file), config_overrides=cfg)
            _print_json(out)
            return 0

        # -------- morphology
        if args.tool == "morphology" and args.feature == "dendrogram-edit":
            from swcstudio.core.provenance import (
                OpKind,
                current_swc_path_for,
                tracked_op,
            )
            from swcstudio.tools.morphology_editing.features.dendrogram_editing import (
                reassign_subtree_types,
            )

            src = Path(args.file)
            if not src.exists():
                raise FileNotFoundError(str(src))

            with tracked_op(
                src,
                kind=OpKind.DENDROGRAM_EDIT,
                params={
                    "node_id":  int(args.node_id),
                    "new_type": int(args.new_type),
                },
                message=f"dendrogram-edit subtree at node={args.node_id} → type={args.new_type}",
            ) as op:
                in_bytes = op.input_bytes if op.input_bytes is not None else src.read_bytes()
                in_text = in_bytes.decode("utf-8", errors="ignore")
                result = reassign_subtree_types(
                    in_text,
                    node_id=int(args.node_id),
                    new_type=int(args.new_type),
                    config_overrides=_parse_config_overrides(args.config_json),
                )
                op.set_output(result["bytes"])

            changed_ids = list(result.get("changed_node_ids", []) or [])
            out_print = {
                "changes":                int(result.get("changes", 0)),
                "changed_node_count":     len(changed_ids),
                "input_path":             str(src),
                "output_path":            str(current_swc_path_for(src)),
                "operation_log_path":     None,
                "commit_sha":             op.result.commit_sha,
                "branch":                 op.result.branch,
                "input_sha":              op.result.input_sha,
                "output_sha":             op.result.output_sha,
                "diff_ref":               op.result.diff_ref,
            }
            if changed_ids:
                out_print["changed_node_id_preview"] = changed_ids[:10]
            _print_json(out_print)
            return 0

        if args.tool == "morphology" and args.feature == "set-radius":
            # Same tracked-edit pattern as morphology set-type.
            from swcstudio.core.provenance import (
                OpKind,
                current_swc_path_for,
                tracked_op,
            )
            from swcstudio.tools.morphology_editing.features.manual_radii import (
                set_node_radius_text,
            )

            src = Path(args.file)
            if not src.exists():
                raise FileNotFoundError(str(src))

            with tracked_op(
                src,
                kind=OpKind.SET_RADIUS,
                params={"node_id": int(args.node_id), "radius": float(args.radius)},
                message=f"set-radius node={args.node_id} radius={args.radius}",
            ) as op:
                in_bytes = op.input_bytes if op.input_bytes is not None else src.read_bytes()
                in_text = in_bytes.decode("utf-8", errors="ignore")
                result = set_node_radius_text(
                    in_text,
                    node_id=int(args.node_id),
                    radius=float(args.radius),
                    config_overrides=_parse_config_overrides(args.config_json),
                )
                op.set_output(result["bytes"])

            out_print = {
                "node_id":            int(args.node_id),
                "old_radius":         float(result.get("old_radius", 0.0)),
                "new_radius":         float(result.get("new_radius", 0.0)),
                "input_path":         str(src),
                "output_path":        str(current_swc_path_for(src)),
                "operation_log_path": None,
                "commit_sha":         op.result.commit_sha,
                "branch":             op.result.branch,
                "input_sha":          op.result.input_sha,
                "output_sha":         op.result.output_sha,
                "diff_ref":           op.result.diff_ref,
            }
            _print_json(out_print)
            return 0

        if args.tool == "morphology" and args.feature == "set-type":
            # The mutation flows through
            # tracked_op so the edit is recorded as a commit in .history/;
            # the old timestamped output file + text report path is replaced
            # by refreshing the source SWC with an @PROV header.
            from swcstudio.core.provenance import (
                OpKind,
                current_swc_path_for,
                tracked_op,
            )
            from swcstudio.tools.morphology_editing.features.manual_label import (
                set_node_type_text,
            )

            src = Path(args.file)
            if not src.exists():
                raise FileNotFoundError(str(src))

            with tracked_op(
                src,
                kind=OpKind.SET_TYPE,
                params={"node_id": int(args.node_id), "new_type": int(args.new_type)},
                message=f"set-type node={args.node_id} type={args.new_type}",
            ) as op:
                # op.input_bytes is the latest committed state (falls back to
                # the original file on the first commit). Always edit from
                # there so chained commits build on each other.
                in_bytes = op.input_bytes if op.input_bytes is not None else src.read_bytes()
                in_text = in_bytes.decode("utf-8", errors="ignore")
                result = set_node_type_text(
                    in_text,
                    node_id=int(args.node_id),
                    new_type=int(args.new_type),
                    config_overrides=_parse_config_overrides(args.config_json),
                )
                op.set_output(result["bytes"])

            # JSON contract: keep every key the old handler emitted, plus the
            # new provenance fields scripts can opt into.
            out_print = {
                "node_id":            int(args.node_id),
                "new_type":           int(args.new_type),
                "old_type":           int(result.get("old_type", 0)),
                "input_path":         str(src),
                "output_path":        str(current_swc_path_for(src)),
                "operation_log_path": None,  # no per-op text report under the new design
                "commit_sha":         op.result.commit_sha,
                "branch":             op.result.branch,
                "input_sha":          op.result.input_sha,
                "output_sha":         op.result.output_sha,
                "diff_ref":           op.result.diff_ref,
            }
            _print_json(out_print)
            return 0

        # -------- geometry
        if args.tool == "geometry" and args.feature:
            if args.feature == "simplify":
                from swcstudio.core.provenance import (
                    OpKind,
                    current_swc_path_for,
                    tracked_op,
                )
                from swcstudio.tools.morphology_editing.features.simplification import (
                    simplify_swc_text,
                )

                src = Path(args.file)
                if not src.exists():
                    raise FileNotFoundError(str(src))

                cfg_overrides = _parse_config_overrides(args.config_json)
                cfg_effective = merge_config(get_simplification_config(), cfg_overrides or {})
                _print_simplification_rule_guide(cfg_effective)

                with tracked_op(
                    src,
                    kind=OpKind.SIMPLIFICATION,
                    params=_config_params(cfg_overrides, cfg_effective),
                    message="geometry simplify",
                ) as op:
                    in_bytes = op.input_bytes if op.input_bytes is not None else src.read_bytes()
                    in_text = in_bytes.decode("utf-8", errors="ignore")
                    result = simplify_swc_text(in_text, config_overrides=cfg_overrides)
                    op.set_output(result["bytes"])

                out_print = {
                    "input_path":          str(src),
                    "output_path":         str(current_swc_path_for(src)),
                    "operation_log_path":  None,
                    "original_node_count": int(result.get("original_node_count", 0)),
                    "new_node_count":      int(result.get("new_node_count", 0)),
                    "reduction_percent":   float(result.get("reduction_percent", 0.0)),
                    "kept_node_count":     len(list(result.get("kept_node_ids", []) or [])),
                    "removed_node_count":  len(list(result.get("removed_node_ids", []) or [])),
                    "protected_counts":    dict(result.get("protected_counts", {}) or {}),
                    "params_used":         dict(result.get("params_used", {}) or {}),
                    "commit_sha":          op.result.commit_sha,
                    "branch":              op.result.branch,
                    "input_sha":           op.result.input_sha,
                    "output_sha":          op.result.output_sha,
                    "diff_ref":            op.result.diff_ref,
                }
                _print_json(out_print)
                return 0

            file_path = Path(args.file)
            if not file_path.exists():
                raise FileNotFoundError(str(file_path))
            # NOTE: each geometry sub-handler below uses tracked_op to read
            # op.input_bytes (latest committed state) rather than re-reading
            # the source file. The old pre-parse of `df` is unnecessary now.

            if args.feature == "move-node":
                from swcstudio.core.provenance import OpKind, current_swc_path_for, tracked_op
                with tracked_op(
                    file_path,
                    kind=OpKind.GEOMETRY_EDIT,
                    params={"op": "move-node", "node_id": int(args.node_id),
                            "x": float(args.x), "y": float(args.y), "z": float(args.z)},
                    message=f"geometry move-node id={args.node_id} → ({args.x},{args.y},{args.z})",
                ) as op:
                    in_bytes = op.input_bytes if op.input_bytes is not None else file_path.read_bytes()
                    in_df = parse_swc_text_preserve_tokens(in_bytes.decode("utf-8", errors="ignore"))
                    out_df = geometry_move_node_absolute(
                        in_df, int(args.node_id),
                        float(args.x), float(args.y), float(args.z),
                    )
                    op.set_output(write_swc_to_bytes_preserve_tokens(out_df))
                _print_json({
                    "operation":          "move-node",
                    "node_id":            int(args.node_id),
                    "output_path":        str(current_swc_path_for(file_path)),
                    "operation_log_path": None,
                    "commit_sha":         op.result.commit_sha,
                    "branch":             op.result.branch,
                    "input_sha":          op.result.input_sha,
                    "output_sha":         op.result.output_sha,
                    "diff_ref":           op.result.diff_ref,
                })
                return 0

            if args.feature == "move-subtree":
                from swcstudio.core.provenance import OpKind, current_swc_path_for, tracked_op
                with tracked_op(
                    file_path,
                    kind=OpKind.GEOMETRY_EDIT,
                    params={"op": "move-subtree", "root_id": int(args.root_id),
                            "x": float(args.x), "y": float(args.y), "z": float(args.z)},
                    message=f"geometry move-subtree root={args.root_id} → ({args.x},{args.y},{args.z})",
                ) as op:
                    in_bytes = op.input_bytes if op.input_bytes is not None else file_path.read_bytes()
                    in_df = parse_swc_text_preserve_tokens(in_bytes.decode("utf-8", errors="ignore"))
                    out_df = geometry_move_subtree_absolute(
                        in_df, int(args.root_id),
                        float(args.x), float(args.y), float(args.z),
                    )
                    op.set_output(write_swc_to_bytes_preserve_tokens(out_df))
                _print_json({
                    "operation": "move-subtree", "root_id": int(args.root_id),
                    "output_path": str(current_swc_path_for(file_path)),
                    "operation_log_path": None,
                    "commit_sha": op.result.commit_sha, "branch": op.result.branch,
                    "input_sha": op.result.input_sha, "output_sha": op.result.output_sha,
                    "diff_ref": op.result.diff_ref,
                })
                return 0

            if args.feature == "connect":
                from swcstudio.core.provenance import OpKind, current_swc_path_for, tracked_op
                with tracked_op(
                    file_path,
                    kind=OpKind.GEOMETRY_EDIT,
                    params={"op": "connect", "start_id": int(args.start_id), "end_id": int(args.end_id)},
                    message=f"geometry connect end={args.end_id} → parent={args.start_id}",
                ) as op:
                    in_bytes = op.input_bytes if op.input_bytes is not None else file_path.read_bytes()
                    in_df = parse_swc_text_preserve_tokens(in_bytes.decode("utf-8", errors="ignore"))
                    out_df = geometry_reconnect_branch(in_df, int(args.start_id), int(args.end_id))
                    op.set_output(write_swc_to_bytes_preserve_tokens(out_df))
                _print_json({
                    "operation": "connect",
                    "start_id": int(args.start_id), "end_id": int(args.end_id),
                    "output_path": str(current_swc_path_for(file_path)),
                    "operation_log_path": None,
                    "commit_sha": op.result.commit_sha, "branch": op.result.branch,
                    "input_sha": op.result.input_sha, "output_sha": op.result.output_sha,
                    "diff_ref": op.result.diff_ref,
                })
                return 0

            if args.feature == "disconnect":
                from swcstudio.core.provenance import OpKind, current_swc_path_for, tracked_op
                with tracked_op(
                    file_path,
                    kind=OpKind.GEOMETRY_EDIT,
                    params={"op": "disconnect", "start_id": int(args.start_id), "end_id": int(args.end_id)},
                    message=f"geometry disconnect path {args.start_id} … {args.end_id}",
                ) as op:
                    in_bytes = op.input_bytes if op.input_bytes is not None else file_path.read_bytes()
                    in_df = parse_swc_text_preserve_tokens(in_bytes.decode("utf-8", errors="ignore"))
                    # Sanity check the path exists (raises if not connected)
                    path = path_between_nodes(in_df, int(args.start_id), int(args.end_id))
                    if len(path) < 2:
                        raise ValueError("Start and end nodes are not connected.")
                    out_df = geometry_disconnect_branch(in_df, int(args.start_id), int(args.end_id))
                    op.set_output(write_swc_to_bytes_preserve_tokens(out_df))
                _print_json({
                    "operation": "disconnect",
                    "start_id": int(args.start_id), "end_id": int(args.end_id),
                    "output_path": str(current_swc_path_for(file_path)),
                    "operation_log_path": None,
                    "commit_sha": op.result.commit_sha, "branch": op.result.branch,
                    "input_sha": op.result.input_sha, "output_sha": op.result.output_sha,
                    "diff_ref": op.result.diff_ref,
                })
                return 0

            if args.feature == "delete-node":
                from swcstudio.core.provenance import OpKind, current_swc_path_for, tracked_op
                with tracked_op(
                    file_path,
                    kind=OpKind.GEOMETRY_EDIT,
                    params={"op": "delete-node", "node_id": int(args.node_id),
                            "reconnect_children": bool(args.reconnect_children)},
                    message=f"geometry delete-node id={args.node_id} reconnect={bool(args.reconnect_children)}",
                ) as op:
                    in_bytes = op.input_bytes if op.input_bytes is not None else file_path.read_bytes()
                    in_df = parse_swc_text_preserve_tokens(in_bytes.decode("utf-8", errors="ignore"))
                    out_df = geometry_delete_node(in_df, int(args.node_id),
                                                  reconnect_children=bool(args.reconnect_children))
                    op.set_output(write_swc_to_bytes_preserve_tokens(out_df))
                _print_json({
                    "operation": "delete-node",
                    "node_id": int(args.node_id),
                    "reconnect_children": bool(args.reconnect_children),
                    "output_path": str(current_swc_path_for(file_path)),
                    "operation_log_path": None,
                    "commit_sha": op.result.commit_sha, "branch": op.result.branch,
                    "input_sha": op.result.input_sha, "output_sha": op.result.output_sha,
                    "diff_ref": op.result.diff_ref,
                })
                return 0

            if args.feature == "delete-subtree":
                from swcstudio.core.provenance import OpKind, current_swc_path_for, tracked_op
                with tracked_op(
                    file_path,
                    kind=OpKind.GEOMETRY_EDIT,
                    params={"op": "delete-subtree", "root_id": int(args.root_id)},
                    message=f"geometry delete-subtree root={args.root_id}",
                ) as op:
                    in_bytes = op.input_bytes if op.input_bytes is not None else file_path.read_bytes()
                    in_df = parse_swc_text_preserve_tokens(in_bytes.decode("utf-8", errors="ignore"))
                    out_df = geometry_delete_subtree(in_df, int(args.root_id))
                    op.set_output(write_swc_to_bytes_preserve_tokens(out_df))
                _print_json({
                    "operation": "delete-subtree",
                    "root_id": int(args.root_id),
                    "output_path": str(current_swc_path_for(file_path)),
                    "operation_log_path": None,
                    "commit_sha": op.result.commit_sha, "branch": op.result.branch,
                    "input_sha": op.result.input_sha, "output_sha": op.result.output_sha,
                    "diff_ref": op.result.diff_ref,
                })
                return 0

            if args.feature == "insert":
                from swcstudio.core.provenance import OpKind, current_swc_path_for, tracked_op
                with tracked_op(
                    file_path,
                    kind=OpKind.GEOMETRY_EDIT,
                    params={"op": "insert", "start_id": int(args.start_id), "end_id": int(args.end_id),
                            "x": float(args.x), "y": float(args.y), "z": float(args.z),
                            "radius": args.radius, "type_id": args.type_id},
                    message=f"geometry insert between {args.start_id} and {args.end_id}",
                ) as op:
                    in_bytes = op.input_bytes if op.input_bytes is not None else file_path.read_bytes()
                    in_df = parse_swc_text_preserve_tokens(in_bytes.decode("utf-8", errors="ignore"))
                    out_df = geometry_insert_node_between(
                        in_df,
                        int(args.start_id), int(args.end_id),
                        x=float(args.x), y=float(args.y), z=float(args.z),
                        radius=args.radius, type_id=args.type_id,
                    )
                    op.set_output(write_swc_to_bytes_preserve_tokens(out_df))
                _print_json({
                    "operation": "insert",
                    "start_id": int(args.start_id), "end_id": int(args.end_id),
                    "output_path": str(current_swc_path_for(file_path)),
                    "operation_log_path": None,
                    "commit_sha": op.result.commit_sha, "branch": op.result.branch,
                    "input_sha": op.result.input_sha, "output_sha": op.result.output_sha,
                    "diff_ref": op.result.diff_ref,
                })
                return 0

        # -------- train (the labeler on your own data)
        if args.tool == "train" and args.feature == "auto-typing":
            from swcstudio.core.auto_typing_train import train_user_models  # noqa: PLC0415
            res = train_user_models(
                data_dir=args.data_dir,
                output_dir=args.output_dir,
                seed=args.seed,
                device=str(args.device),
                skip=[s for s in str(args.skip or "").split(",") if s.strip()],
                test_size=float(args.test_size),
            )
            _print_json({
                "output_dir": res.output_dir,
                "seed": res.seed,
                "model_paths": res.model_paths,
                "counts": res.counts,
                "timings_min": res.timings_min,
                "skipped": res.skipped,
            })
            return 0

        # -------- models (status)
        if args.tool == "models" and args.feature == "status":
            from swcstudio.core.auto_typing import backend_status  # noqa: PLC0415
            md = str(args.model_dir) if args.model_dir else None
            st = backend_status(model_dir=md)
            print(st["search_diagnostic"])
            print()
            _print_json({k: v for k, v in st.items() if k != "search_diagnostic"})
            return 0

        # -------- plugins
        if args.tool == "plugins" and args.feature == "list":
            if args.feature_key:
                _print_json(list_feature_methods(args.feature_key))
            else:
                _print_json(list_all_feature_methods())
            return 0
        if args.tool == "plugins" and args.feature == "list-loaded":
            _print_json({"plugins": list_plugins()})
            return 0
        if args.tool == "plugins" and args.feature == "load":
            _print_json(load_plugin_module(str(args.module)))
            return 0

        parser.print_help()
        return 1

    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
