"""Shared per-file provenance behavior for mutating batch operations."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from swcstudio.core.provenance.ops import OpKind
from swcstudio.core.provenance.tracked_op import tracked_op
from swcstudio.core.swc_io import is_swc_data_file

BatchTransform = Callable[[Path, str], dict[str, Any]]
BatchParams = Callable[[Path, dict[str, Any]], dict[str, Any]]
BatchSummary = Callable[[Path, dict[str, Any]], Any]
BatchProgress = Callable[[int, int, str], None]


def config_params(
    overrides: dict[str, Any] | None,
    effective: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return compact raw configuration provenance for an operation."""
    out: dict[str, Any] = {}
    if effective is not None:
        out["effective_config"] = effective
    if overrides and overrides != effective:
        out["config_overrides"] = overrides
    return out


def run_tracked_batch(
    folder: str | Path,
    *,
    kind: str | OpKind,
    transform: BatchTransform,
    params_for: BatchParams | None = None,
    summary_for: BatchSummary | None = None,
    message: str = "",
    progress_callback: BatchProgress | None = None,
) -> dict[str, Any]:
    """Apply one independently tracked operation to every SWC in a folder.

    Each source file owns its own history archive and operation-number
    sequence. A failure in one file does not affect any other file.
    """
    folder_path = Path(folder)
    if not folder_path.exists() or not folder_path.is_dir():
        raise NotADirectoryError(str(folder))

    swc_files = sorted(
        [
            path
            for path in folder_path.iterdir()
            if is_swc_data_file(path)
        ],
        key=lambda path: path.name.lower(),
    )
    if not swc_files:
        raise FileNotFoundError(f"No .swc files found in: {folder_path}")

    processed = 0
    failures: list[str] = []
    per_file: list[Any] = []
    commits: list[dict[str, Any]] = []
    total = len(swc_files)

    for index, swc_path in enumerate(swc_files):
        if progress_callback is not None:
            progress_callback(index, total, swc_path.name)
        try:
            source_text = swc_path.read_text(encoding="utf-8", errors="ignore")
            result = transform(swc_path, source_text)
            output = result.get("bytes")
            if not isinstance(output, (bytes, bytearray)):
                raise TypeError("batch transform must return bytes in result['bytes']")
            params = params_for(swc_path, result) if params_for is not None else {}
            kind_name = kind.value if isinstance(kind, OpKind) else str(kind)
            with tracked_op(
                swc_path,
                kind=kind,
                params=params,
                message=message or f"batch {kind_name} on {swc_path.name}",
            ) as op:
                op.set_output(bytes(output))

            commits.append(
                {
                    "file": swc_path.name,
                    "commit_sha": op.result.commit_sha if op.result else None,
                    "branch": op.result.branch if op.result else None,
                    "operation_id": op.result.operation_label if op.result else None,
                }
            )
            processed += 1
            if summary_for is not None:
                per_file.append(summary_for(swc_path, result))
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{swc_path.name}: {exc}")

    return {
        "folder": str(folder_path),
        "out_dir": None,
        "files_total": total,
        "files_processed": processed,
        "files_failed": len(failures),
        "per_file": per_file,
        "failures": failures,
        "commits": commits,
        "log_path": None,
    }


__all__ = ["config_params", "run_tracked_batch"]
