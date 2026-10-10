"""Shared text report builders and file writers.

All interfaces (CLI + GUI) should use these helpers so generated log text stays
consistent regardless of entry point.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
import re
from typing import Any

import pandas as pd

from swcstudio import __version__ as SWCSTUDIO_VERSION
from swcstudio.core.custom_types import load_custom_type_definitions
from swcstudio.core.validation_catalog import group_rows_by_category, rule_for_key


def _status_tag(status: str) -> str:
    s = str(status or "").lower()
    if s == "pass":
        return "PASS"
    if s == "info":
        return "INFO"
    if s == "warning":
        return "WARN"
    return "FAIL"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _timestamp_slug() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def timestamp_slug() -> str:
    return _timestamp_slug()


def _operation_slug(operation_name: str) -> str:
    raw = str(operation_name or "").strip().lower()
    if not raw:
        return "operation"
    raw = re.sub(r"[^a-z0-9]+", "_", raw)
    raw = re.sub(r"_+", "_", raw).strip("_")
    return raw or "operation"


def _label_type_legend_lines() -> list[str]:
    lines = [
        "Label types:",
        "- 0: undefined",
        "- 1: soma",
        "- 2: axon",
        "- 3: basal dendrite",
        "- 4: apical dendrite",
    ]
    custom_defs = load_custom_type_definitions(force=True)
    if custom_defs:
        for type_id in sorted(custom_defs):
            item = custom_defs.get(type_id) or {}
            name = str(item.get("name", "")).strip() or f"custom type {type_id}"
            color = str(item.get("color", "")).strip()
            notes = str(item.get("notes", "")).strip()
            detail = f"- {type_id}: {name}"
            if color:
                detail += f" [color={color}]"
            if notes:
                detail += f" [notes={notes}]"
            lines.append(detail)
    return lines


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    i = 1
    while True:
        candidate = parent / f"{stem}_{i}{suffix}"
        if not candidate.exists():
            return candidate
        i += 1


def output_dir_for_file(path: str | Path) -> Path:
    p = Path(path)
    if str(p.parent.name).endswith("_swc_studio_output"):
        out_dir = p.parent
    else:
        out_dir = p.parent / f"{p.stem}_swc_studio_output"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def log_dir_for_file(path: str | Path) -> Path:
    return output_dir_for_file(path)


def report_path_for_file(path: str | Path, report_tag: str, *, extension: str = ".txt") -> Path:
    p = Path(path)
    log_dir = log_dir_for_file(p)
    base = log_dir / f"{p.stem}_{report_tag}_{_timestamp_slug()}{extension}"
    return _unique_path(base)


def operation_output_path_for_file(
    path: str | Path,
    operation_name: str,
    *,
    extension: str | None = None,
    output_dir: str | Path | None = None,
    timestamp: str | None = None,
    variant: str = "",
) -> Path:
    p = Path(path)
    parent = Path(output_dir) if output_dir is not None else output_dir_for_file(p)
    ext = extension if extension is not None else p.suffix
    op = _operation_slug(operation_name)
    parts = [p.stem, op]
    variant_slug = _operation_slug(variant) if str(variant).strip() else ""
    if variant_slug:
        parts.append(variant_slug)
    parts.append(str(timestamp or _timestamp_slug()))
    base = parent / f"{'_'.join(parts)}{ext}"
    return _unique_path(base)


def resolve_requested_output_path_for_file(
    path: str | Path,
    requested_output_path: str | Path,
) -> Path:
    src = Path(path)
    requested = Path(requested_output_path)
    if requested.exists() and requested.is_dir():
        return operation_output_path_for_file(src, "output", output_dir=requested)
    if requested.is_absolute():
        return requested
    return requested


def operation_report_path_for_file(
    path: str | Path,
    operation_name: str,
    *,
    output_dir: str | Path | None = None,
    extension: str = ".txt",
    timestamp: str | None = None,
) -> Path:
    return operation_output_path_for_file(
        path,
        operation_name,
        extension=extension,
        output_dir=output_dir if output_dir is not None else log_dir_for_file(path),
        timestamp=timestamp,
    )


def operation_output_dir_for_folder(
    folder: str | Path,
    operation_name: str,
    *,
    output_root: str | Path | None = None,
    timestamp: str | None = None,
) -> Path:
    p = Path(folder)
    parent = Path(output_root) if output_root is not None else p
    op = _operation_slug(operation_name)
    base = parent / f"{p.name}_{op}_{str(timestamp or _timestamp_slug())}"
    candidate = base
    i = 1
    while candidate.exists():
        candidate = parent / f"{base.name}_{i}"
        i += 1
    candidate.mkdir(parents=True, exist_ok=True)
    return candidate


def operation_report_path_for_folder(
    folder: str | Path,
    operation_name: str,
    *,
    output_dir: str | Path | None = None,
    extension: str = ".txt",
    timestamp: str | None = None,
) -> Path:
    p = Path(folder)
    parent = Path(output_dir) if output_dir is not None else p
    op = _operation_slug(operation_name)
    base = parent / f"{p.name}_{op}_{str(timestamp or _timestamp_slug())}{extension}"
    return _unique_path(base)


def write_text_report(path: str | Path, text: str) -> str:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return str(p)


def validation_log_path_for_file(path: str | Path) -> Path:
    return operation_report_path_for_file(path, "validation_run")


def correction_summary_log_path_for_file(path: str | Path) -> Path:
    return report_path_for_file(path, "correction_summary")


def simplification_log_path_for_file(path: str | Path) -> Path:
    return operation_report_path_for_file(path, "geometry_simplify")


def auto_typing_log_path_for_file(path: str | Path) -> Path:
    return operation_report_path_for_file(path, "auto_typing")


def radii_cleaning_log_path_for_file(path: str | Path) -> Path:
    return operation_report_path_for_file(path, "radii_cleaning")


def format_validation_precheck_text(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("Pre-check Summary")
    lines.append("-----------------")
    groups = group_rows_by_category(list(report.get("precheck", [])))
    for category, items in groups:
        lines.append(f"{category}:")
        for item in items:
            key = str(item.get("key", ""))
            label = str(item.get("label", key))
            lines.append(f"- {label}")
            rule = rule_for_key(key)
            if rule:
                lines.append(f"  rule: {rule}")
            params = item.get("params") or {}
            if params:
                lines.append(f"  params: {json.dumps(params, sort_keys=True)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def format_validation_results_text(report: dict[str, Any]) -> str:
    lines: list[str] = []
    summary = dict(report.get("summary", {}))
    lines.append("Validation Results")
    lines.append("------------------")
    lines.append(
        f"total={summary.get('total', 0)} "
        f"pass={summary.get('pass', 0)} "
        f"info={summary.get('info', 0)} "
        f"warning={summary.get('warning', 0)} "
        f"fail={summary.get('fail', 0)}"
    )
    groups = group_rows_by_category(list(report.get("results", [])))
    for category, items in groups:
        lines.append(f"{category}:")
        for row in items:
            label = str(row.get("label", row.get("key", "")))
            lines.append(f"- [{_status_tag(str(row.get('status', '')))}] {label}")
        lines.append("")

    details = [r for r in report.get("results", []) if r.get("status") in {"info", "warning", "fail"}]
    if details:
        lines.append("")
        lines.append("Detailed Findings")
        lines.append("-----------------")
        for row in details:
            label = str(row.get("label", row.get("key", "")))
            lines.append(f"* {label} ({_status_tag(str(row.get('status', '')))})")
            lines.append(f"  reason: {row.get('message')}")
            lines.append(f"  params: {row.get('params_used', {})}")
            lines.append(f"  thresholds: {row.get('thresholds_used', {})}")
            lines.append(f"  failing_node_ids: {row.get('failing_node_ids', [])}")
            lines.append(f"  failing_section_ids: {row.get('failing_section_ids', [])}")
            lines.append(f"  metrics: {row.get('metrics', {})}")
    return "\n".join(lines).rstrip() + "\n"


def format_validation_report_text(report: dict[str, Any]) -> str:
    pre = format_validation_precheck_text(report)
    res = format_validation_results_text(report)
    return f"Generated: {_now()}\n\n{pre}\n{res}"


def format_batch_validation_report_text(batch_report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("Batch Validation Report")
    lines.append("-----------------------")
    lines.append(f"Generated: {_now()}")
    lines.append(f"Folder: {batch_report.get('folder', '')}")
    lines.append(
        f"Files: total={batch_report.get('files_total', 0)} "
        f"validated={batch_report.get('files_validated', 0)} "
        f"failed={batch_report.get('files_failed', 0)}"
    )
    totals = dict(batch_report.get("summary_total", {}))
    lines.append(
        f"Checks: total={totals.get('total', 0)} "
        f"pass={totals.get('pass', 0)} "
        f"info={totals.get('info', 0)} "
        f"warning={totals.get('warning', 0)} "
        f"fail={totals.get('fail', 0)}"
    )
    lines.append("")

    precheck = {"precheck": list(batch_report.get("precheck", []))}
    lines.append(format_validation_precheck_text(precheck).rstrip())
    lines.append("")

    for file_row in batch_report.get("results", []):
        file_name = str(file_row.get("file", ""))
        report = dict(file_row.get("report", {}))
        lines.append("=" * 72)
        lines.append(f"File: {file_name}")
        lines.append("=" * 72)
        lines.append(format_validation_results_text(report).rstrip())
        lines.append("")

    failures = list(batch_report.get("failures", []))
    if failures:
        lines.append("Batch File-Level Errors")
        lines.append("-----------------------")
        for err in failures:
            lines.append(f"- {err}")

    return "\n".join(lines).rstrip() + "\n"


def format_split_report_text(split_report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("Batch Split Report")
    lines.append("------------------")
    lines.append(f"Generated: {_now()}")
    lines.append(f"Folder: {split_report.get('folder', '')}")
    lines.append(f"Output folder: {split_report.get('out_dir', '')}")
    lines.append(f"SWC files detected: {split_report.get('files_total', 0)}")
    lines.append(f"Split files: {split_report.get('files_split', 0)}")
    lines.append(f"Skipped: {split_report.get('files_skipped', 0)}")
    lines.append(f"Saved split files: {split_report.get('trees_saved', 0)}")

    outputs = list(split_report.get("output_files", []))
    if outputs:
        lines.append("")
        lines.append("Created files:")
        for name in outputs:
            lines.append(f"- {name}")

    failures = list(split_report.get("failures", []))
    if failures:
        lines.append("")
        lines.append("Errors:")
        for err in failures:
            lines.append(f"- {err}")

    return "\n".join(lines).rstrip() + "\n"


def format_radii_cleaning_report_text(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("Radii Cleaning Report")
    lines.append("---------------------")
    lines.append(f"Generated: {_now()}")

    mode = str(payload.get("mode", ""))
    if mode == "file":
        lines.append(f"Input file: {payload.get('input_path', '')}")
        lines.append(f"Output file: {payload.get('output_path', '')}")
        lines.append(f"Radius changes: {payload.get('radius_changes', 0)}")
        lines.append(f"Change rows: {payload.get('change_count', 0)}")

        change_lines = list(payload.get("change_lines", []))
        if change_lines:
            lines.append("")
            lines.append("Node changes:")
            lines.extend(change_lines)
        return "\n".join(lines).rstrip() + "\n"

    lines.append(f"Folder: {payload.get('folder', '')}")
    lines.append(f"Output folder: {payload.get('out_dir', '')}")
    lines.append(f"SWC files detected: {payload.get('files_total', 0)}")
    lines.append(f"Processed: {payload.get('files_processed', 0)}")
    lines.append(f"Failed: {payload.get('files_failed', 0)}")
    lines.append(f"Total radius changes: {payload.get('total_radius_changes', 0)}")

    per_file = list(payload.get("per_file", []))
    if per_file:
        lines.append("")
        lines.append("Per-file summary:")
        for row in per_file:
            lines.append(
                f"{row.get('file', '')}: radius_changes={row.get('radius_changes', 0)}, "
                f"out_file={row.get('out_file', '')}"
            )
            detail_rows = list(row.get("change_lines", []))
            for d in detail_rows:
                lines.append(f"  {d}")

    failures = list(payload.get("failures", []))
    if failures:
        lines.append("")
        lines.append("Errors:")
        lines.extend(f"- {e}" for e in failures)

    return "\n".join(lines).rstrip() + "\n"


def format_auto_typing_report_text(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("Rule-Based Auto-Typing Batch Report")
    lines.append("-----------------------------------")
    lines.append(f"Generated: {_now()}")
    lines.append(f"Folder: {payload.get('folder', '')}")
    lines.append(f"Output folder: {payload.get('out_dir', '')}")
    lines.append(f"SWC files detected: {payload.get('files_total', 0)}")
    lines.append(f"Processed: {payload.get('files_processed', 0)}")
    if "files_qc_failed" in payload:
        lines.append(f"QC rejected: {payload.get('files_qc_failed', 0)}")
    lines.append(f"Failed: {payload.get('files_failed', 0)}")
    lines.append(f"Total nodes processed: {payload.get('total_nodes', 0)}")
    lines.append(f"Type changes: {payload.get('total_type_changes', 0)}")
    lines.append(f"Radius changes: {payload.get('total_radius_changes', 0)}")
    if "files_flagged" in payload:
        lines.append(f"Flagged files: {payload.get('files_flagged', 0)}")
    if payload.get("zip_path"):
        lines.append(f"Zip output: {payload.get('zip_path')}")

    per_file = list(payload.get("per_file", []))
    if per_file:
        lines.append("")
        lines.append("Per-file summary:")
        lines.extend(per_file)

    change_details = list(payload.get("change_details", []))
    if change_details:
        lines.append("")
        lines.append("Detailed Node Changes:")
        lines.extend(change_details)

    failures = list(payload.get("failures", []))
    if failures:
        lines.append("")
        lines.append("Errors:")
        for err in failures:
            lines.append(f"- {err}")

    return "\n".join(lines).rstrip() + "\n"


def format_simplification_report_text(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("Simplification Log")
    lines.append("------------------")
    lines.append(f"Generated: {_now()}")
    lines.append(f"Input: {payload.get('input_path', '')}")
    lines.append(f"Output: {payload.get('output_path', '')}")
    lines.append(f"Original Node Count: {payload.get('original_node_count', 0)}")
    lines.append(f"New Node Count: {payload.get('new_node_count', 0)}")
    lines.append(f"Reduction (%): {payload.get('reduction_percent', 0.0):.2f}")

    params = dict(payload.get("params_used", {}))
    if params:
        lines.append("")
        lines.append("Parameters Used:")
        for k in sorted(params):
            lines.append(f"- {k}: {params.get(k)}")

    pc = dict(payload.get("protected_counts", {}))
    if pc:
        lines.append("")
        lines.append("Protected Node Stats:")
        for k in sorted(pc):
            lines.append(f"- {k}: {pc.get(k)}")

    removed = list(payload.get("removed_node_ids", []))
    if removed:
        lines.append("")
        lines.append(f"Removed Node IDs ({len(removed)}):")
        chunk = []
        for i, nid in enumerate(removed, start=1):
            chunk.append(str(nid))
            if len(chunk) >= 25:
                lines.append(", ".join(chunk))
                chunk = []
        if chunk:
            lines.append(", ".join(chunk))

    return "\n".join(lines).rstrip() + "\n"


def _format_operation_entries_report_text(
    *,
    source_file: str,
    generated_at: str,
    operations: list[dict[str, Any]],
) -> str:
    operations = list(operations or [])
    lines: list[str] = []
    lines.append("SWC Operation Report")
    lines.append("--------------------")
    lines.append(f"Tool Version: SWC-Studio {SWCSTUDIO_VERSION}")
    lines.append(f"Source file: {source_file}")
    lines.append(f"Generated: {generated_at}")
    lines.append("")
    lines.extend(_label_type_legend_lines())
    lines.append("")
    lines.append("Change Summary")
    lines.append("--------------")
    if not operations:
        lines.append("No morphology changes were recorded.")
        return "\n".join(lines).rstrip() + "\n"

    field_order = ["id", "type", "parent", "radius", "x", "y", "z"]
    field_labels = {
        "id": "ID",
        "type": "Type",
        "parent": "Parent",
        "radius": "Radius",
        "x": "X",
        "y": "Y",
        "z": "Z",
    }

    for op in operations:
        lines.append(f"Operation: {str(op.get('title', '')).strip()}")
        summary = str(op.get("summary", "")).strip()
        if summary:
            lines.append(f"Summary: {summary}")
        lines.append(f"Affected nodes: {int(op.get('affected_nodes', 0))}")
        for detail in list(op.get("details", []) or []):
            lines.append(f"- {detail}")
        changes = list(op.get("changes", []) or [])
        if changes:
            op_fields: list[str] = []
            for key in field_order:
                if any(key in list(row.get("changed_keys", []) or []) for row in changes):
                    op_fields.append(key)
            if not op_fields:
                op_fields = ["id"]

            columns: list[dict[str, Any]] = [
                {"key": "seq", "header": "Seq", "values": [str(row.get("seq", "")) for row in changes]},
                {"key": "time", "header": "Time", "values": [str(row.get("time", "")) for row in changes]},
                {"key": "node_id", "header": "NodeID", "values": [str(row.get("node_id", "")) for row in changes]},
            ]
            for key in op_fields:
                label = field_labels.get(key, key)
                columns.append(
                    {
                        "key": f"old_{key}",
                        "header": f"Old{label}",
                        "values": [str(dict(row.get("old_values", {}) or {}).get(key, "")) for row in changes],
                    }
                )
                columns.append(
                    {
                        "key": f"new_{key}",
                        "header": f"New{label}",
                        "values": [str(dict(row.get("new_values", {}) or {}).get(key, "")) for row in changes],
                    }
                )

            for col in columns:
                col["width"] = max(len(str(col["header"])), *(len(v) for v in list(col["values"]) or [""]))

            def _rule() -> str:
                return "+" + "+".join("-" * (int(col["width"]) + 2) for col in columns) + "+"

            def _fmt_row(values: list[str]) -> str:
                cells = [f" {str(value):<{int(col['width'])}} " for value, col in zip(values, columns)]
                return "|" + "|".join(cells) + "|"

            lines.append(_rule())
            lines.append(_fmt_row([str(col["header"]) for col in columns]))
            lines.append(_rule())
            for row in changes:
                values = [
                    str(row.get("seq", "")),
                    str(row.get("time", "")),
                    str(row.get("node_id", "")),
                ]
                old_values = dict(row.get("old_values", {}) or {})
                new_values = dict(row.get("new_values", {}) or {})
                for key in op_fields:
                    values.append(str(old_values.get(key, "")))
                    values.append(str(new_values.get(key, "")))
                lines.append(_fmt_row(values))
            lines.append(_rule())
        else:
            lines.append("No node-level parameter changes recorded.")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _snapshot_log_value(key: str, value: Any) -> str:
    if value is None:
        return ""
    if key in {"id", "type", "parent"}:
        try:
            return str(int(float(value)))
        except Exception:
            return str(value)
    if key in {"radius", "x", "y", "z"}:
        try:
            return f"{float(value):.10g}"
        except Exception:
            return str(value)
    return str(value)


def _row_snapshot_for_log(row: pd.Series | dict[str, Any]) -> dict[str, str]:
    return {
        "id": _snapshot_log_value("id", row["id"]),
        "type": _snapshot_log_value("type", row["type"]),
        "parent": _snapshot_log_value("parent", row["parent"]),
        "radius": _snapshot_log_value("radius", row["radius"]),
        "x": _snapshot_log_value("x", row["x"]),
        "y": _snapshot_log_value("y", row["y"]),
        "z": _snapshot_log_value("z", row["z"]),
    }


def _format_snapshot_fields(snap: dict[str, str], keys: list[str]) -> str:
    labels = {
        "id": "id",
        "type": "type",
        "parent": "parent",
        "radius": "radius",
        "x": "x",
        "y": "y",
        "z": "z",
    }
    parts: list[str] = []
    for key in keys:
        val = str((snap or {}).get(key, "")).strip()
        if val == "":
            continue
        parts.append(f"{labels[key]}={val}")
    return ", ".join(parts)


def build_change_rows_for_dataframes(
    old_df: pd.DataFrame | None,
    new_df: pd.DataFrame | None,
    *,
    id_map: dict[int, int] | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(old_df, pd.DataFrame) and not isinstance(new_df, pd.DataFrame):
        return []

    old_lookup: dict[int, dict[str, str]] = {}
    new_lookup: dict[int, dict[str, str]] = {}
    if isinstance(old_df, pd.DataFrame) and not old_df.empty:
        old_lookup = {
            int(row["id"]): _row_snapshot_for_log(row)
            for _, row in old_df.loc[:, ["id", "type", "x", "y", "z", "radius", "parent"]].iterrows()
        }
    if isinstance(new_df, pd.DataFrame) and not new_df.empty:
        new_lookup = {
            int(row["id"]): _row_snapshot_for_log(row)
            for _, row in new_df.loc[:, ["id", "type", "x", "y", "z", "radius", "parent"]].iterrows()
        }

    change_rows: list[dict[str, Any]] = []
    used_old: set[int] = set()
    used_new: set[int] = set()
    compare_keys = ["id", "type", "parent", "radius", "x", "y", "z"]

    if isinstance(id_map, dict) and id_map:
        for old_id, new_id in sorted((int(k), int(v)) for k, v in id_map.items()):
            old_snap = old_lookup.get(old_id)
            new_snap = new_lookup.get(new_id)
            if old_snap is None or new_snap is None:
                continue
            used_old.add(old_id)
            used_new.add(new_id)
            changed_keys = [key for key in compare_keys if str(old_snap.get(key, "")) != str(new_snap.get(key, ""))]
            if not changed_keys:
                continue
            change_rows.append(
                {
                    "node_id": f"{old_id}->{new_id}" if old_id != new_id else str(old_id),
                    "changed_keys": list(changed_keys),
                    "old_values": {key: str(old_snap.get(key, "")) for key in changed_keys},
                    "new_values": {key: str(new_snap.get(key, "")) for key in changed_keys},
                    "old_parameters": _format_snapshot_fields(old_snap, changed_keys),
                    "new_parameters": _format_snapshot_fields(new_snap, changed_keys),
                }
            )

    for nid in sorted(set(old_lookup).intersection(new_lookup)):
        if nid in used_old or nid in used_new:
            continue
        old_snap = old_lookup[nid]
        new_snap = new_lookup[nid]
        used_old.add(nid)
        used_new.add(nid)
        changed_keys = [key for key in compare_keys if str(old_snap.get(key, "")) != str(new_snap.get(key, ""))]
        if not changed_keys:
            continue
        change_rows.append(
            {
                "node_id": str(nid),
                "changed_keys": list(changed_keys),
                "old_values": {key: str(old_snap.get(key, "")) for key in changed_keys},
                "new_values": {key: str(new_snap.get(key, "")) for key in changed_keys},
                "old_parameters": _format_snapshot_fields(old_snap, changed_keys),
                "new_parameters": _format_snapshot_fields(new_snap, changed_keys),
            }
        )

    all_keys = ["id", "type", "parent", "radius", "x", "y", "z"]
    for nid in sorted(set(old_lookup) - used_old):
        old_snap = old_lookup[nid]
        change_rows.append(
            {
                "node_id": str(nid),
                "changed_keys": list(all_keys),
                "old_values": {key: str(old_snap.get(key, "")) for key in all_keys},
                "new_values": {},
                "old_parameters": _format_snapshot_fields(old_snap, all_keys),
                "new_parameters": "[deleted]",
            }
        )

    for nid in sorted(set(new_lookup) - used_new):
        new_snap = new_lookup[nid]
        change_rows.append(
            {
                "node_id": str(nid),
                "changed_keys": list(all_keys),
                "old_values": {},
                "new_values": {key: str(new_snap.get(key, "")) for key in all_keys},
                "old_parameters": "[inserted]",
                "new_parameters": _format_snapshot_fields(new_snap, all_keys),
            }
        )

    return change_rows


def build_operation_entry(
    *,
    title: str,
    summary: str,
    details: list[str] | None = None,
    old_df: pd.DataFrame | None = None,
    new_df: pd.DataFrame | None = None,
    id_map: dict[int, int] | None = None,
    change_rows: list[dict[str, Any]] | None = None,
    op_time: str | None = None,
) -> dict[str, Any]:
    node_changes = (
        list(change_rows or [])
        if isinstance(change_rows, list)
        else build_change_rows_for_dataframes(old_df, new_df, id_map=id_map)
    )
    stamp = str(op_time or datetime.now().strftime("%H:%M:%S"))
    stamped_rows: list[dict[str, Any]] = []
    seq = 0
    for row in node_changes:
        seq += 1
        stamped_rows.append(
            {
                "seq": seq,
                "time": stamp,
                "node_id": str(row.get("node_id", "")),
                "changed_keys": list(row.get("changed_keys", []) or []),
                "old_values": dict(row.get("old_values", {}) or {}),
                "new_values": dict(row.get("new_values", {}) or {}),
                "old_parameters": str(row.get("old_parameters", "")),
                "new_parameters": str(row.get("new_parameters", "")),
            }
        )
    return {
        "time": stamp,
        "title": str(title),
        "summary": str(summary),
        "details": list(details or []),
        "affected_nodes": len(stamped_rows),
        "changes": stamped_rows,
    }


def validation_index_clean_detail_lines(
    *,
    input_path: str,
    output_path: str,
    original_node_count: int,
    new_node_count: int,
    remapped_id_count: int,
) -> list[str]:
    return [
        f"Input: {str(input_path or '').strip() or '(unknown)'}",
        f"Output: {str(output_path or '').strip() or '(not written yet)'}",
        f"Original node count: {int(original_node_count)}",
        f"New node count: {int(new_node_count)}",
        f"Remapped ID count: {int(remapped_id_count)}",
    ]


def format_operation_report_text(
    *,
    source_file: str,
    title: str,
    summary: str,
    details: list[str] | None = None,
    old_df: pd.DataFrame | None = None,
    new_df: pd.DataFrame | None = None,
    id_map: dict[int, int] | None = None,
    change_rows: list[dict[str, Any]] | None = None,
    generated_at: str | None = None,
) -> str:
    stamp = str(generated_at or _now())
    operation = build_operation_entry(
        title=title,
        summary=summary,
        details=list(details or []),
        old_df=old_df,
        new_df=new_df,
        id_map=id_map,
        change_rows=change_rows,
        op_time=stamp.split(" ")[-1] if " " in stamp else stamp,
    )
    return _format_operation_entries_report_text(
        source_file=str(source_file),
        generated_at=stamp,
        operations=[operation],
    )


def write_operation_report_for_file(
    source_path: str | Path,
    operation_name: str,
    *,
    title: str,
    summary: str,
    details: list[str] | None = None,
    old_df: pd.DataFrame | None = None,
    new_df: pd.DataFrame | None = None,
    id_map: dict[int, int] | None = None,
    change_rows: list[dict[str, Any]] | None = None,
    report_path: str | Path | None = None,
    output_dir: str | Path | None = None,
    timestamp: str | None = None,
) -> str:
    src = Path(source_path)
    target = Path(report_path) if report_path is not None else operation_report_path_for_file(
        src,
        operation_name,
        output_dir=output_dir,
        timestamp=timestamp,
    )
    text = format_operation_report_text(
        source_file=src.name,
        title=title,
        summary=summary,
        details=details,
        old_df=old_df,
        new_df=new_df,
        id_map=id_map,
        change_rows=change_rows,
    )
    return write_text_report(target, text)


def format_correction_summary_report_text(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("SWC Correction Summary")
    lines.append("----------------------")
    lines.append(f"Generated: {_now()}")
    lines.append(f"Input file: {payload.get('input_path', '')}")
    lines.append(f"Output file: {payload.get('output_path', '')}")
    lines.append(f"Remaining issues: {payload.get('remaining_issues', 0)}")
    lines.append(f"Fixed issues: {payload.get('fixed_issues', 0)}")
    lines.append(f"Skipped issues: {payload.get('skipped_issues', 0)}")

    summary = dict(payload.get("diff_summary", {}))
    if summary:
        lines.append("")
        lines.append("Before/After Diff")
        lines.append("-----------------")
        for key in (
            "original_nodes",
            "current_nodes",
            "type_changes",
            "radius_changes",
            "parent_changes",
            "geometry_changes",
        ):
            lines.append(f"{key}: {summary.get(key, 0)}")

    remaining = list(payload.get("remaining_issue_titles", []))
    if remaining:
        lines.append("")
        lines.append("Remaining Issues")
        lines.append("----------------")
        for row in remaining:
            lines.append(f"- {row}")

    provenance = list(payload.get("provenance_lines", []))
    if provenance:
        lines.append("")
        lines.append("Applied Fixes / Events")
        lines.append("----------------------")
        lines.extend(str(line) for line in provenance)

    return "\n".join(lines).rstrip() + "\n"
