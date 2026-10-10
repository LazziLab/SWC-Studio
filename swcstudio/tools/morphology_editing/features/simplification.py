"""Smart decimation wrapper for morphology editing."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from swcstudio.core.config import load_feature_config, merge_config
from swcstudio.core.reporting import (
    format_simplification_report_text,
    operation_output_path_for_file,
    operation_report_path_for_file,
    resolve_requested_output_path_for_file,
    simplification_log_path_for_file,
    timestamp_slug,
    write_text_report,
)
from swcstudio.core.simplification import simplify_morphology_dataframe
from swcstudio.core.swc_io import parse_swc_text_preserve_tokens, write_swc_to_bytes_preserve_tokens
from swcstudio.plugins.registry import register_builtin_method, resolve_method

TOOL = "morphology_editing"
FEATURE = "simplification"
FEATURE_KEY = f"{TOOL}.{FEATURE}"

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": True,
    "method": "default",
    "thresholds": {
        "epsilon": 2.0,
        "radius_tolerance": 0.5,
    },
    "flags": {
        "keep_tips": True,
        "keep_bifurcations": True,
        "keep_roots": True,
        "keep_soma": True,
    },
    "output": {
        "suffix": "_simplified",
    },
}

def _builtin_simplify_dataframe(df: pd.DataFrame, config: dict[str, Any]) -> dict[str, Any]:
    return simplify_morphology_dataframe(df, config)


register_builtin_method(FEATURE_KEY, "default", _builtin_simplify_dataframe)


def get_config() -> dict[str, Any]:
    loaded = load_feature_config(TOOL, FEATURE, default=DEFAULT_CONFIG)
    return merge_config(DEFAULT_CONFIG, loaded)


def simplify_dataframe(df: pd.DataFrame, *, config_overrides: dict | None = None) -> dict[str, Any]:
    cfg = merge_config(get_config(), config_overrides)
    method = str(cfg.get("method", "default"))
    fn = resolve_method(FEATURE_KEY, method)
    out = fn(df.copy(), cfg)
    if not isinstance(out, dict) or "dataframe" not in out:
        raise TypeError("simplification method must return dict with 'dataframe'")
    out["config_used"] = cfg
    return out


def simplify_swc_text(swc_text: str, *, config_overrides: dict | None = None) -> dict[str, Any]:
    df = parse_swc_text_preserve_tokens(swc_text)
    out = simplify_dataframe(df, config_overrides=config_overrides)
    out_df = out["dataframe"]
    out["bytes"] = write_swc_to_bytes_preserve_tokens(out_df)
    return out


def simplify_file(
    path: str,
    *,
    out_path: str | None = None,
    write_output: bool = False,
    write_report: bool = True,
    config_overrides: dict | None = None,
) -> dict[str, Any]:
    fp = Path(path)
    if not fp.exists():
        raise FileNotFoundError(path)

    text = fp.read_text(encoding="utf-8", errors="ignore")
    out = simplify_swc_text(text, config_overrides=config_overrides)

    run_timestamp = timestamp_slug()

    output_path: Path | None = None
    if write_output:
        output_path = (
            resolve_requested_output_path_for_file(fp, out_path)
            if out_path
            else operation_output_path_for_file(fp, "geometry_simplify", timestamp=run_timestamp)
        )
        output_path.write_bytes(out["bytes"])

    payload = {
        "mode": "file",
        "input_path": str(fp),
        "output_path": str(output_path) if output_path else None,
        "original_node_count": int(out.get("original_node_count", 0)),
        "new_node_count": int(out.get("new_node_count", 0)),
        "reduction_percent": float(out.get("reduction_percent", 0.0)),
        "params_used": dict(out.get("params_used", {})),
        "protected_counts": dict(out.get("protected_counts", {})),
        "removed_node_ids": list(out.get("removed_node_ids", [])),
    }

    payload["log_path"] = None
    if write_report:
        report_path = (
            simplification_log_path_for_file(fp)
            if out_path
            else operation_report_path_for_file(fp, "geometry_simplify", timestamp=run_timestamp)
        )
        payload["log_path"] = write_text_report(report_path, format_simplification_report_text(payload))
    out["summary"] = payload
    out["input_path"] = str(fp)
    out["output_path"] = str(output_path) if output_path else None
    out["log_path"] = payload["log_path"]
    return out
