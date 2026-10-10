"""Auto-typing feature for the Batch Processing tool.

Thin wrapper around :mod:`swcstudio.core.auto_typing`, which runs the SWC
auto-labeler. This module registers the feature with the plugin system and
carries the model directory, cell class and quality-flag settings the GUI / CLI
need. A batch labels every file of a folder in place, each with its own history,
then ranks the labeled cells for review: each cell class flags its riskiest
share (``flag_budget``), and the ranking is saved next to the folder as
``<folder>_flag_review_<timestamp>.json`` (and ``.csv``) for the Flag Review window.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from swcstudio.core.auto_typing import (
    BatchOptions,
    BatchResult,
    DEFAULT_CONFIG,
    apply_flag_budget,
    cell_class_name,
    get_config as _get_engine_config,
    write_flag_review,
)
from swcstudio.core.auto_typing.runner import _warning_text, flag_record
from swcstudio.core.config import merge_config
from swcstudio.core.provenance import OpKind, config_params, run_tracked_batch
from swcstudio.core.reporting import operation_report_path_for_folder, timestamp_slug, write_text_report
from swcstudio.plugins.registry import register_builtin_method

TOOL = "batch_processing"
FEATURE = "auto_typing"
FEATURE_KEY = f"{TOOL}.{FEATURE}"


def _flag_text(flag_result: dict[str, Any]) -> str:
    f1 = flag_result.get("predicted_f1")
    return (f"flag={bool(flag_result.get('flagged', False))}"
            + (f", predicted_f1={float(f1):.3f}" if f1 is not None else ""))


def _builtin_run(folder: str, options: BatchOptions, config: dict[str, Any]):
    from swcstudio.tools.validation.features.auto_typing import auto_label_file

    effective_config = {
        key: value
        for key, value in config.items()
        if not str(key).startswith("__")
    }
    totals = {"nodes": 0, "type_changes": 0, "radius_changes": 0}
    records: list[dict[str, Any]] = []

    def _transform(path, _text):
        return auto_label_file(
            str(path),
            options=options,
            config_overrides=effective_config,
            output_path=None,
            write_output=False,
            write_log=False,
        )

    def _summary(path, result):
        out_counts = dict(result.get("out_type_counts", {}) or {})
        flag_result = dict(result.get("flag_result", {}) or {})
        totals["nodes"] += int(result.get("nodes_total", 0))
        totals["type_changes"] += int(result.get("type_changes", 0))
        totals["radius_changes"] += int(result.get("radius_changes", 0))
        records.append(flag_record(Path(path), Path(path), result.get("result_obj"),
                                   int(result.get("nodes_total", 0)), int(result.get("type_changes", 0)),
                                   out_type_counts=out_counts))
        source = "user" if result.get("cell_type_source") == "user" else "auto"
        return (
            f"{path.name}: nodes={int(result.get('nodes_total', 0))}, "
            f"type_changes={int(result.get('type_changes', 0))}, "
            f"radius_changes={int(result.get('radius_changes', 0))}, "
            f"cell_class={cell_class_name(result.get('cell_type'))} ({source}), "
            f"{_flag_text(flag_result)}, "
            "out_types(soma/axon/basal/apic)="
            f"{out_counts.get(1, 0)}/{out_counts.get(2, 0)}/"
            f"{out_counts.get(3, 0)}/{out_counts.get(4, 0)}"
            f"{_warning_text(records[-1])}"
        )

    run_timestamp = timestamp_slug()
    tracked = run_tracked_batch(
        folder,
        kind=OpKind.AUTO_LABEL,
        transform=_transform,
        params_for=lambda _path, result: {
            **config_params(None, effective_config),
            "options": asdict(options),
            "labeler_version": result.get("labeler_version"),
            "nodes_total": int(result.get("nodes_total", 0)),
            "type_changes": int(result.get("type_changes", 0)),
            "radius_changes": int(result.get("radius_changes", 0)),
            "cell_type_result": result.get("cell_type"),
            "cell_type_source": result.get("cell_type_source"),
            "stage1_cell_type": result.get("stage1_cell_type"),
            "stage1_confidence": result.get("stage1_confidence"),
            "class_mismatch": bool(result.get("class_mismatch", False)),
            "predicted_f1": dict(result.get("flag_result", {}) or {}).get("predicted_f1"),
        },
        summary_for=_summary,
        message="GUI batch auto label",
        progress_callback=config.get("__progress_callback"),
    )
    failures = list(tracked.get("failures", []) or [])
    folder_path = Path(str(tracked["folder"]))
    for item in failures:
        name, _, message = str(item).partition(": ")
        qc = message.split("QC rejected; ", 1)[1].split("; ") if "QC rejected; " in message else None
        records.append(flag_record(folder_path / name, None, None, 0, 0, qc_reasons=qc,
                                   error=None if qc else message))

    budget = float(getattr(options, "flag_budget", 0.10))
    flag_review_path: str | None = None
    files_flagged = 0
    if bool(getattr(options, "flag_enabled", True)) and records:
        apply_flag_budget(records, budget)
        files_flagged = sum(1 for r in records if r.get("flagged"))
        review = operation_report_path_for_folder(folder_path, "flag_review", extension=".json",
                                                  timestamp=run_timestamp)
        flag_review_path = str(write_flag_review(review, records, budget=budget, folder=str(folder_path),
                                                 timestamp=run_timestamp))

    files_qc_failed = sum("QC rejected" in str(item) for item in failures)
    files_with_warnings = sum(1 for r in records if r.get("warnings"))
    per_file = list(tracked.get("per_file", []) or [])
    # The GUI shows a shortened list; the report keeps every file and error.
    report_lines = [
        "Auto-labeling batch report",
        f"Folder: {folder_path}",
        f"SWC files: {int(tracked['files_total'])}",
        f"Labeled: {int(tracked['files_processed'])}",
        f"Labeled with warnings: {files_with_warnings}",
        f"Not labeled: {len(failures)} ({files_qc_failed} failed the structural checks, "
        f"{len(failures) - files_qc_failed} errors)",
        "",
        "Per file:",
        *per_file,
        "",
        "Not labeled:",
        *(failures or ["(none)"]),
    ]
    log_path = write_text_report(
        operation_report_path_for_folder(folder_path, "batch_auto_typing", timestamp=run_timestamp),
        "\n".join(report_lines) + "\n",
    )

    return BatchResult(
        folder=str(tracked["folder"]),
        out_dir=None,
        zip_path=None,
        files_total=int(tracked["files_total"]),
        files_processed=int(tracked["files_processed"]),
        files_failed=int(tracked["files_failed"]),
        files_qc_failed=files_qc_failed,
        files_with_warnings=files_with_warnings,
        total_nodes=int(totals["nodes"]),
        total_type_changes=int(totals["type_changes"]),
        total_radius_changes=int(totals["radius_changes"]),
        files_flagged=files_flagged,
        failures=failures,
        per_file=per_file,
        log_path=log_path,
        commits=list(tracked.get("commits", []) or []),
        flag_records=records if flag_review_path else None,
        flag_review_path=flag_review_path,
        flag_budget=budget if flag_review_path else None,
    )


register_builtin_method(FEATURE_KEY, "default", _builtin_run)


def get_config() -> dict[str, Any]:
    return _get_engine_config()


def _options_from_config(cfg: dict[str, Any]) -> BatchOptions:
    return BatchOptions(
        soma=True,
        axon=True,
        apic=True,
        basal=True,
        rad=False,
        zip_output=False,
        cell_type=(cfg.get("cell_type") or "auto"),
        flag_enabled=bool(cfg.get("flag_enabled", True)),
        flag_budget=float(cfg.get("flag_budget", 0.10)),
    )


def run_folder(
    folder: str,
    *,
    options: BatchOptions | None = None,
    config_overrides: dict | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
):
    cfg = merge_config(get_config(), config_overrides)
    opts = options if options is not None else _options_from_config(cfg)
    if progress_callback is not None:
        # Stash the callback in the config dict so the registered method
        # can pick it up without breaking the (folder, options, config)
        # plugin signature.
        cfg = dict(cfg)
        cfg["__progress_callback"] = progress_callback
    return _builtin_run(folder, opts, cfg)


def options_to_dict(opts: BatchOptions) -> dict[str, Any]:
    return asdict(opts)


__all__ = [
    "TOOL",
    "FEATURE",
    "FEATURE_KEY",
    "DEFAULT_CONFIG",
    "BatchOptions",
    "get_config",
    "run_folder",
    "options_to_dict",
]
