"""Auto-typing feature for the Batch Processing tool.

Thin wrapper around :mod:`swcstudio.core.auto_typing`, which runs the SWC
auto-labeler. This module registers the feature with the plugin system and
carries the model directory, cell class and quality-flag settings the GUI / CLI
need. A batch labels every file of a folder in place, each with its own history,
then ranks the labeled cells for review: each cell class flags its riskiest
share (``flag_budget``). The ranking is saved inside the folder as
``<folder>_flag_review_<timestamp>.json`` (and ``.csv``) for the Flag Review
window, and a text report as ``<folder>_batch_auto_typing_<timestamp>.txt``.
The GUI batch and ``swcstudio auto-typing`` both run :func:`run_folder`.
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
    get_config as _get_engine_config,
    write_flag_review,
)
from swcstudio.core.auto_typing.runner import (
    batch_file_line,
    batch_report_text,
    flag_record,
    resolve_models,
)
from swcstudio.core.config import merge_config
from swcstudio.core.model_paths import model_record as _model_record, recorded_model_dir
from swcstudio.core.provenance import OpKind, config_params, run_tracked_batch
from swcstudio.core.reporting import operation_report_path_for_folder, timestamp_slug, write_text_report
from swcstudio.plugins.registry import register_builtin_method

TOOL = "batch_processing"
FEATURE = "auto_typing"
FEATURE_KEY = f"{TOOL}.{FEATURE}"

# Keys ``run_folder`` passes to ``_builtin_run`` beside the configuration.
_MODEL_ARGUMENT = "__model_dir_argument"
_MESSAGE = "__message"


def _model_dir_argument(config: dict[str, Any], effective_config: dict[str, Any]) -> str | None:
    """The model folder chosen for this run (not the saved configuration's)."""
    if _MODEL_ARGUMENT in config:
        return str(config[_MODEL_ARGUMENT] or "").strip() or None
    # Called with a merged configuration: its model_dir is an explicit choice
    # only where it differs from the saved one, which the resolver reads itself
    # (after SWCSTUDIO_MODEL_DIR).
    value = str(effective_config.get("model_dir") or "").strip()
    saved = str(get_config().get("model_dir") or "").strip()
    return value if value and value != saved else None


def _builtin_run(folder: str, options: BatchOptions, config: dict[str, Any]):
    from swcstudio.tools.validation.features.auto_typing import auto_label_file

    effective_config = {
        key: value
        for key, value in config.items()
        if not str(key).startswith("__")
    }
    # One model folder for the whole batch, and its provenance record.
    models = resolve_models(_model_dir_argument(config, effective_config))
    record = _model_record(models.folder, models.source)
    # Commit ids hash the params, so the configuration is recorded with the
    # model folder in its private form (home written as ~) from the start.
    recorded_config = dict(effective_config)
    if "model_dir" in recorded_config:
        recorded_config["model_dir"] = recorded_model_dir(str(recorded_config.get("model_dir") or "")) or ""
    flag_enabled = bool(getattr(options, "flag_enabled", True))
    totals = {"nodes": 0, "type_changes": 0, "radius_changes": 0}
    labeled: list[tuple[dict[str, Any], dict[str, Any]]] = []  # (result, flag record)

    def _transform(path, _text):
        return auto_label_file(
            str(path),
            options=options,
            config_overrides=effective_config,
            output_path=None,
            write_output=False,
            write_log=False,
            models=models,
        )

    def _params(_path, result):
        return {
            **config_params(None, recorded_config),
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
            "unlabeled_nodes": int(result.get("unlabeled_nodes", 0) or 0),
            # model_dir, model_source, model_version, model_sha, model_files
            **(result.get("model_record") or record),
        }

    def _summary(path, result):
        totals["nodes"] += int(result.get("nodes_total", 0))
        totals["type_changes"] += int(result.get("type_changes", 0))
        totals["radius_changes"] += int(result.get("radius_changes", 0))
        rec = flag_record(Path(path), Path(path), result.get("result_obj"),
                          int(result.get("nodes_total", 0)), int(result.get("type_changes", 0)),
                          out_type_counts=dict(result.get("out_type_counts", {}) or {}))
        labeled.append((result, rec))
        return rec

    run_timestamp = timestamp_slug()
    tracked = run_tracked_batch(
        folder,
        kind=OpKind.AUTO_LABEL,
        transform=_transform,
        params_for=_params,
        summary_for=_summary,
        message=str(config.get(_MESSAGE) or "GUI batch auto label"),
        progress_callback=config.get("__progress_callback"),
    )
    folder_path = Path(str(tracked["folder"]))
    failed_files = [(str(name), str(error)) for name, error in (tracked.get("failed_files") or [])]
    records = [rec for _result, rec in labeled]
    for name, error in failed_files:
        qc = error.split("QC rejected; ", 1)[1].split("; ") if "QC rejected; " in error else None
        records.append(flag_record(folder_path / name, None, None, 0, 0, qc_reasons=qc,
                                   error=None if qc else error))

    budget = float(getattr(options, "flag_budget", 0.10))
    flag_review_path: str | None = None
    files_flagged = 0
    if flag_enabled and records:
        apply_flag_budget(records, budget)
        files_flagged = sum(1 for r in records if r.get("flagged"))
        review = operation_report_path_for_folder(folder_path, "flag_review", extension=".json",
                                                  timestamp=run_timestamp)
        flag_review_path = str(write_flag_review(review, records, budget=budget, folder=str(folder_path),
                                                 timestamp=run_timestamp))

    # Built after the budget, so each line says the file's final flag.
    per_file = [
        batch_file_line(rec, radius_changes=int(result.get("radius_changes", 0)),
                        out_type_counts=result.get("out_type_counts"), flag_enabled=flag_enabled,
                        flag_result=result.get("flag_result"))
        for result, rec in labeled
    ]
    failures = [f"{name}: {error}" for name, error in failed_files]
    files_qc_failed = sum(1 for _name, error in failed_files if "QC rejected" in error)
    files_with_warnings = sum(1 for _result, rec in labeled if rec.get("warnings"))
    # The GUI shows a shortened list; the report keeps every file and error.
    log_path = write_text_report(
        operation_report_path_for_folder(folder_path, "batch_auto_typing", timestamp=run_timestamp),
        batch_report_text(folder=folder_path, files_total=int(tracked["files_total"]),
                          files_processed=int(tracked["files_processed"]), failed_files=failed_files,
                          files_with_warnings=files_with_warnings, per_file=per_file,
                          files_flagged=files_flagged if flag_enabled else None,
                          flag_review_path=flag_review_path, model_record=record, totals=totals),
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
        model_record=dict(record),
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
    message: str | None = None,
):
    """Label every SWC file of ``folder`` in place, each with its own history.

    ``config_overrides["model_dir"]`` is the explicit model folder for this
    run; without it ``SWCSTUDIO_MODEL_DIR``, then ``model_dir`` in the saved
    configuration, then the default search decide. ``message`` is the history
    message of each commit.
    """
    cfg = dict(merge_config(get_config(), config_overrides))
    opts = options if options is not None else _options_from_config(cfg)
    # Values for the registered method that are not configuration; keys
    # starting with "__" keep the (folder, options, config) plugin signature.
    cfg[_MODEL_ARGUMENT] = (config_overrides or {}).get("model_dir") or None
    if progress_callback is not None:
        cfg["__progress_callback"] = progress_callback
    if message:
        cfg[_MESSAGE] = message
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
