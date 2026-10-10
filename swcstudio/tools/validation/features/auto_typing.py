"""Single-file auto-typing for the Validation tool.

Thin wrapper around :mod:`swcstudio.core.auto_typing`, which runs the SWC
auto-labeler. The runtime knobs are the model directory override, the cell
class (automatic, 3-class or 2-class) and whether the quality flag runs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from swcstudio.core.auto_typing import BatchOptions, run_file as _run_engine_file
from swcstudio.core.auto_typing_results import (
    auto_typing_result_to_dataframe,
    merge_labeled_types_only,
)
from swcstudio.core.config import merge_config
from swcstudio.core.reporting import (
    operation_output_path_for_file,
    resolve_requested_output_path_for_file,
    timestamp_slug,
)
from swcstudio.core.swc_io import (
    parse_swc_text_preserve_tokens,
    write_swc_to_bytes_preserve_tokens,
)
from swcstudio.tools.batch_processing.features.auto_typing import (
    get_config as get_batch_auto_config,
)


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


def run_file(
    file_path: str,
    *,
    options: BatchOptions | None = None,
    config_overrides: dict | None = None,
    output_path: str | None = None,
    write_output: bool = True,
    write_log: bool = True,
):
    """Run auto-typing on a single file using the configured engine."""
    cfg = get_batch_auto_config()
    if isinstance(config_overrides, dict) and config_overrides:
        cfg = merge_config(cfg, config_overrides)
    opts = options if options is not None else _options_from_config(cfg)

    return _run_engine_file(
        file_path,
        opts,
        output_path=output_path,
        write_output=write_output,
        write_log=write_log,
        model_dir=(cfg.get("model_dir") or None),
    )


def result_to_dataframe(result: object) -> pd.DataFrame:
    return auto_typing_result_to_dataframe(result)


def merge_types_only(base_df: pd.DataFrame, labeled_df: pd.DataFrame) -> pd.DataFrame:
    return merge_labeled_types_only(base_df, labeled_df)


def auto_label_file(
    file_path: str,
    *,
    options: BatchOptions | None = None,
    config_overrides: dict | None = None,
    output_path: str | None = None,
    write_output: bool = True,
    write_log: bool = False,
) -> dict[str, Any]:
    in_path = Path(file_path)
    if not in_path.exists():
        raise FileNotFoundError(file_path)

    base_df = parse_swc_text_preserve_tokens(in_path.read_text(encoding="utf-8", errors="ignore"))
    result_obj = run_file(
        file_path,
        options=options,
        config_overrides=config_overrides,
        output_path=None,
        write_output=False,
        write_log=write_log,
    )
    labeled_df = result_to_dataframe(result_obj)
    merged_df = merge_types_only(base_df, labeled_df)
    out_bytes = write_swc_to_bytes_preserve_tokens(merged_df)

    out_path: Path | None = None
    run_timestamp = timestamp_slug()
    if write_output:
        out_path = (
            resolve_requested_output_path_for_file(in_path, output_path)
            if output_path
            else operation_output_path_for_file(in_path, "validation_auto_label", timestamp=run_timestamp)
        )
        out_path.write_bytes(out_bytes)

    return {
        "dataframe": merged_df,
        "bytes": out_bytes,
        "input_path": str(in_path),
        "output_path": str(out_path) if out_path is not None else None,
        "nodes_total": int(getattr(result_obj, "nodes_total", 0)),
        "type_changes": int(getattr(result_obj, "type_changes", 0)),
        "radius_changes": 0,
        "out_type_counts": dict(getattr(result_obj, "out_type_counts", {}) or {}),
        "cell_type": getattr(result_obj, "cell_type", None),
        "cell_type_source": getattr(result_obj, "cell_type_source", "stage1"),
        "stage1_cell_type": getattr(result_obj, "stage1_cell_type", None),
        "stage1_confidence": getattr(result_obj, "stage1_confidence", None),
        "class_mismatch": bool(getattr(result_obj, "class_mismatch", False)),
        "notes": list(getattr(result_obj, "notes", []) or []),
        "labeler_version": getattr(result_obj, "labeler_version", None),
        "qc_result": dict(getattr(result_obj, "qc_result", {}) or {}),
        "flag_result": dict(getattr(result_obj, "flag_result", {}) or {}),
        "change_details": list(getattr(result_obj, "change_details", []) or []),
        "result_obj": result_obj,
    }


__all__ = [
    "BatchOptions",
    "run_file",
    "result_to_dataframe",
    "merge_types_only",
    "auto_label_file",
]
