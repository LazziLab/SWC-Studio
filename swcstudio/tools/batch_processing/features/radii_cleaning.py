"""Radii cleaning feature for Batch Processing.

This module is the shared backend used by:
- Batch GUI radii cleaning tab
- Validation GUI radii cleaning tab
- CLI batch/validation radii-clean commands
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from swcstudio.core.config import load_feature_config, merge_config
from swcstudio.core.provenance import (
    OpKind,
    config_params,
    run_tracked_batch,
)
from swcstudio.core.radii_cleaning import clean_radii_dataframe, radii_stats_by_type
from swcstudio.core.reporting import (
    format_radii_cleaning_report_text,
    operation_output_path_for_file,
    operation_report_path_for_file,
    radii_cleaning_log_path_for_file,
    resolve_requested_output_path_for_file,
    timestamp_slug,
    write_text_report,
)
from swcstudio.core.swc_io import (
    is_swc_data_file,
    parse_swc_text_preserve_tokens,
    write_swc_to_bytes_preserve_tokens,
)
from swcstudio.plugins.registry import register_builtin_method, resolve_method

TOOL = "batch_processing"
FEATURE = "radii_cleaning"
FEATURE_KEY = f"{TOOL}.{FEATURE}"

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": True,
    "method": "default",
    "rules": {
        "small_radius_zero_only": True,
        "sanity_bounds": {
            "global": {
                "lower_percentile": 1.0,
                "upper_percentile": 99.5,
                "lower_abs": 0.05,
                "upper_abs": 30.0,
            },
            "per_type": {
                "2": {"enabled": True, "lower_percentile": 1.0, "upper_percentile": 99.5, "lower_abs": 0.05, "upper_abs": 30.0},
                "3": {"enabled": True, "lower_percentile": 1.0, "upper_percentile": 99.5, "lower_abs": 0.05, "upper_abs": 30.0},
                "4": {"enabled": True, "lower_percentile": 1.0, "upper_percentile": 99.5, "lower_abs": 0.05, "upper_abs": 30.0},
            },
        },
        "local_outlier": {
            "enabled": True,
            "window_nodes": 5,
            "max_percent_deviation": 0.5,
        },
        "taper": {
            "enabled": True,
            "slack": 0.05,
        },
        "axon_floor": {
            "enabled": True,
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
    },
    "output": {
        "folder_suffix": "_radii_cleaned",
        "file_suffix": "_radii_cleaned",
        "report_name": "radii_cleaning_report.txt",
    },
}


def _builtin_clean_dataframe(df: pd.DataFrame, config: dict[str, Any]) -> dict[str, Any]:
    return clean_radii_dataframe(df, rules=dict(config.get("rules", {})))


register_builtin_method(FEATURE_KEY, "default", _builtin_clean_dataframe)


def get_config() -> dict[str, Any]:
    loaded = load_feature_config(TOOL, FEATURE, default=DEFAULT_CONFIG)
    return merge_config(DEFAULT_CONFIG, loaded)


def _list_swc_files(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir() if is_swc_data_file(p))


def _normalize_method_output(method_out: Any, input_df: pd.DataFrame) -> tuple[pd.DataFrame, int, list[dict[str, Any]]]:
    # Backward compatibility with older plugin signatures.
    if isinstance(method_out, tuple) and len(method_out) >= 2:
        out_df = method_out[0]
        changes = int(method_out[1])
        details = []
        if isinstance(out_df, pd.DataFrame):
            in_r = input_df["radius"].to_numpy(dtype=float)
            out_r = out_df["radius"].to_numpy(dtype=float)
            n = min(len(in_r), len(out_r), len(input_df))
            for i in range(n):
                if float(in_r[i]) == float(out_r[i]):
                    continue
                details.append(
                    {
                        "node_id": int(input_df.iloc[i]["id"]),
                        "old_radius": float(in_r[i]),
                        "new_radius": float(out_r[i]),
                        "reasons": ["plugin_change"],
                    }
                )
        return out_df, changes, details

    if isinstance(method_out, dict):
        out_df = method_out.get("dataframe")
        if not isinstance(out_df, pd.DataFrame):
            raise TypeError("radii-clean method returned dict without DataFrame 'dataframe'")
        details = list(method_out.get("change_details", []))
        changes = int(method_out.get("total_changes", len(details)))
        return out_df, changes, details

    raise TypeError("radii-clean method output must be tuple(DataFrame, int) or dict")


def clean_swc_text(swc_text: str, *, config_overrides: dict | None = None) -> dict[str, Any]:
    cfg = merge_config(get_config(), config_overrides)

    df = parse_swc_text_preserve_tokens(swc_text)
    method = str(cfg.get("method", "default"))
    fn = resolve_method(FEATURE_KEY, method)
    method_out = fn(df, cfg)
    out_df, changes, details = _normalize_method_output(method_out, df)
    stats_by_type = {}
    passes = 0
    if isinstance(method_out, dict):
        stats_by_type = dict(method_out.get("stats_by_type", {}))
        passes = int(method_out.get("passes", 0))

    out_bytes = write_swc_to_bytes_preserve_tokens(out_df)
    return {
        "changes": int(changes),
        "change_details": details,
        "bytes": out_bytes,
        "dataframe": out_df,
        "stats_by_type": stats_by_type,
        "passes": int(passes),
        "config_used": cfg,
    }


def preview_folder_radii(folder: str, *, bins: int = 12) -> dict[str, Any]:
    in_dir = Path(folder)
    if not in_dir.exists() or not in_dir.is_dir():
        raise NotADirectoryError(folder)

    swc_files = _list_swc_files(in_dir)
    failures: list[str] = []
    frames: list[pd.DataFrame] = []
    files_loaded = 0

    for fp in swc_files:
        try:
            text = fp.read_text(encoding="utf-8", errors="ignore")
            df = parse_swc_text_preserve_tokens(text)
            if not df.empty:
                frame = df.loc[:, ["type", "radius"]].copy()
                frame["source_file"] = fp.name
                frames.append(frame)
            files_loaded += 1
        except Exception as e:  # noqa: BLE001
            failures.append(f"{fp.name}: {e}")

    if frames:
        combined_df = pd.concat(frames, ignore_index=True)
    else:
        combined_df = pd.DataFrame(columns=["type", "radius", "source_file"])

    stats = radii_stats_by_type(combined_df, bins=bins)
    return {
        "mode": "folder_preview",
        "folder": str(in_dir),
        "files_total": len(swc_files),
        "files_loaded": int(files_loaded),
        "files_failed": len(failures),
        "failures": failures,
        "combined_dataframe": combined_df,
        "stats": stats,
        "type_stats": dict(stats.get("type_stats", {})),
    }


def _format_change_lines(change_details: list[dict[str, Any]], *, limit: int | None = None) -> list[str]:
    rows = change_details if limit is None else change_details[: max(0, int(limit))]
    out: list[str] = []
    for row in rows:
        reasons = ",".join(str(r) for r in row.get("reasons", [])) or "unknown"
        out.append(
            f"node_id={row.get('node_id')} old={row.get('old_radius')} "
            f"new={row.get('new_radius')} reasons={reasons}"
        )
    if limit is not None and len(change_details) > limit:
        out.append(f"... ({len(change_details) - int(limit)} more node changes)")
    return out


def clean_file(
    path: str,
    *,
    out_path: str | None = None,
    write_output: bool = True,
    write_report: bool = True,
    config_overrides: dict | None = None,
) -> dict[str, Any]:
    cfg = merge_config(get_config(), config_overrides)

    in_path = Path(path)
    if not in_path.exists() or not in_path.is_file():
        raise FileNotFoundError(path)

    text = in_path.read_text(encoding="utf-8", errors="ignore")
    out = clean_swc_text(text, config_overrides=cfg)

    output_path: Path | None = None
    run_timestamp = timestamp_slug()
    if write_output:
        if out_path:
            output_path = resolve_requested_output_path_for_file(in_path, out_path)
        else:
            output_path = operation_output_path_for_file(in_path, "radii_cleaning", timestamp=run_timestamp)
        output_path.write_bytes(out["bytes"])

    report = {
        "mode": "file",
        "input_path": str(in_path),
        "output_path": str(output_path) if output_path else None,
        "radius_changes": int(out["changes"]),
        "passes": int(out.get("passes", 0)),
        "change_count": int(len(out.get("change_details", []))),
        "change_details": list(out.get("change_details", [])),
        "change_lines": _format_change_lines(list(out.get("change_details", []))),
        "dataframe": out.get("dataframe"),
        "bytes": out.get("bytes"),
        "config_used": cfg,
    }

    report["log_path"] = None
    if write_report:
        report_path = (
            radii_cleaning_log_path_for_file(in_path)
            if out_path
            else operation_report_path_for_file(in_path, "radii_cleaning", timestamp=run_timestamp)
        )
        report["log_path"] = write_text_report(report_path, format_radii_cleaning_report_text(report))
    return report


def clean_folder(folder: str, *, config_overrides: dict | None = None) -> dict[str, Any]:
    cfg = merge_config(get_config(), config_overrides)

    out_report = run_tracked_batch(
        folder,
        kind=OpKind.RADII_CLEAN,
        transform=lambda _path, text: clean_swc_text(
            text,
            config_overrides=cfg,
        ),
        params_for=lambda _path, result: {
            **config_params(config_overrides, cfg),
            "passes": int(result.get("passes", 0)),
            "radius_changes": int(result.get("changes", 0)),
        },
        summary_for=lambda path, result: {
            "file": path.name,
            "radius_changes": int(result.get("changes", 0)),
            "out_file": str(path),
            "change_count": len(list(result.get("change_details", []) or [])),
            "change_lines": _format_change_lines(
                list(result.get("change_details", []) or [])
            ),
        },
        message="GUI batch radii clean",
    )
    out_report["mode"] = "folder"
    out_report["total_radius_changes"] = sum(
        int(row.get("radius_changes", 0))
        for row in out_report.get("per_file", [])
        if isinstance(row, dict)
    )
    out_report["config_used"] = cfg
    return out_report


def clean_path(
    path: str,
    *,
    write_file_report: bool = True,
    config_overrides: dict | None = None,
) -> dict[str, Any]:
    p = Path(path)
    if p.is_dir():
        return clean_folder(str(p), config_overrides=config_overrides)
    if p.is_file():
        return clean_file(str(p), write_report=write_file_report, config_overrides=config_overrides)
    raise FileNotFoundError(path)
