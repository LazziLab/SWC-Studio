"""Batch validation feature for Batch Processing."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from swcstudio.core.config import load_feature_config, merge_config
from swcstudio.core.reporting import (
    format_batch_validation_report_text,
    operation_report_path_for_folder,
    timestamp_slug,
    write_text_report,
)
from swcstudio.core.swc_io import is_swc_data_file
from swcstudio.core.validation_engine import run_validation_text
from swcstudio.plugins.registry import register_builtin_method, resolve_method

TOOL = "batch_processing"
FEATURE = "batch_validation"
FEATURE_KEY = f"{TOOL}.{FEATURE}"

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": True,
    "method": "default",
    "config_overrides": {},
    "include": {"extensions": [".swc"]},
}


def _builtin_validate_text(swc_text: str, config: dict[str, Any]):
    cfg_overrides = config.get("config_overrides")
    report = run_validation_text(swc_text, config_overrides=cfg_overrides)
    return report.to_dict()


register_builtin_method(FEATURE_KEY, "default", _builtin_validate_text)


def get_config() -> dict[str, Any]:
    loaded = load_feature_config(TOOL, FEATURE, default=DEFAULT_CONFIG)
    return merge_config(DEFAULT_CONFIG, loaded)


def validate_swc_text(swc_text: str, *, config_overrides: dict | None = None):
    cfg = merge_config(get_config(), config_overrides)
    method = str(cfg.get("method", "default"))
    fn = resolve_method(FEATURE_KEY, method)
    return fn(swc_text, cfg)


def validate_folder(folder: str, *, config_overrides: dict | None = None) -> dict[str, Any]:
    cfg = merge_config(get_config(), config_overrides)

    in_dir = Path(folder)
    if not in_dir.exists() or not in_dir.is_dir():
        raise NotADirectoryError(folder)

    exts = {e.lower() for e in cfg.get("include", {}).get("extensions", [".swc"])}
    swc_files = sorted(p for p in in_dir.iterdir() if is_swc_data_file(p, extensions=exts))

    rows: list[dict[str, Any]] = []
    failures = []
    precheck: list[dict[str, Any]] = []
    agg = {"total": 0, "pass": 0, "info": 0, "warning": 0, "fail": 0}

    for fp in swc_files:
        try:
            text = fp.read_text(encoding="utf-8", errors="ignore")
            report = validate_swc_text(text, config_overrides=cfg)
            if not precheck:
                precheck = list(report.get("precheck", []))
            summary = dict(report.get("summary", {}))
            agg["total"] += int(summary.get("total", 0))
            agg["pass"] += int(summary.get("pass", 0))
            agg["info"] += int(summary.get("info", 0))
            agg["warning"] += int(summary.get("warning", 0))
            agg["fail"] += int(summary.get("fail", 0))
            rows.append({"file": fp.name, "report": report})
        except Exception as e:  # noqa: BLE001
            failures.append(f"{fp.name}: {e}")

    out = {
        "folder": str(in_dir),
        "profile": "default",
        "files_total": len(swc_files),
        "files_validated": len(rows),
        "files_failed": len(failures),
        "precheck": precheck,
        "summary_total": agg,
        "results": rows,
        "failures": failures,
    }

    log_path = operation_report_path_for_folder(in_dir, "batch_validation", timestamp=timestamp_slug())
    out["log_path"] = write_text_report(log_path, format_batch_validation_report_text(out))
    return out
