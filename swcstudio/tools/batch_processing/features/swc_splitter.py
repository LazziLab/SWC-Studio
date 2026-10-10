"""SWC splitter feature for Batch Processing."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from swcstudio.core.config import load_feature_config, merge_config
from swcstudio.core.provenance import (
    OpKind,
    canonical_swc,
    derived_from_for_swc_path,
    derived_from_payload,
    sha256_hex,
    tracked_op,
)
from swcstudio.core.reporting import (
    format_split_report_text,
    operation_output_dir_for_folder,
    operation_output_path_for_file,
    operation_report_path_for_folder,
    timestamp_slug,
    write_text_report,
)
from swcstudio.core.geometry_editing import (
    require_known_parents,
    require_no_parent_cycles,
    require_unique_node_ids,
)
from swcstudio.core.swc_io import is_swc_data_file, parse_swc_text_preserve_tokens
from swcstudio.core.validation import _split_swc_by_soma_roots
from swcstudio.plugins.registry import register_builtin_method, resolve_method

TOOL = "batch_processing"
FEATURE = "swc_splitter"
FEATURE_KEY = f"{TOOL}.{FEATURE}"

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": True,
    "method": "default",
    "naming": {
        "output_mode": "single_output_subdir",
        "output_dir_pattern": "{folder_name}_split",
        "tree_pattern": "{stem}_tree{index}.swc",
    },
}


def _builtin_split_text(swc_text: str, config: dict[str, Any]) -> list[tuple[int, str, int]]:
    _ = config
    return _split_swc_by_soma_roots(swc_text)


register_builtin_method(FEATURE_KEY, "default", _builtin_split_text)


def get_config() -> dict[str, Any]:
    loaded = load_feature_config(TOOL, FEATURE, default=DEFAULT_CONFIG)
    return merge_config(DEFAULT_CONFIG, loaded)


def refuse_unsafe_split(swc_text: str) -> int:
    """Raise ValueError for a file a split would silently corrupt.

    With duplicate node IDs a parent ID could mean either row, and nodes on
    a parent cycle reach no soma, so the split would drop them. Returns the
    file's row count, which the trees must add up to.
    """
    df = parse_swc_text_preserve_tokens(swc_text)
    require_unique_node_ids(df, "split this file")
    require_no_parent_cycles(df, "split this file")
    require_known_parents(df, "split this file")
    return int(len(df))


def split_swc_text(swc_text: str, *, config_overrides: dict | None = None):
    cfg = merge_config(get_config(), config_overrides)
    rows = refuse_unsafe_split(swc_text)
    method = str(cfg.get("method", "default"))
    fn = resolve_method(FEATURE_KEY, method)
    trees = fn(swc_text, cfg)
    placed = sum(int(node_count) for _root, _text, node_count in trees)
    if len(trees) > 1 and placed != rows:
        raise ValueError(
            f"Cannot split this file: the trees hold {placed} of its {rows} nodes, "
            "so the split would drop or duplicate nodes."
        )
    return trees


def split_folder(folder: str, *, config_overrides: dict | None = None) -> dict[str, Any]:
    cfg = merge_config(get_config(), config_overrides)

    in_dir = Path(folder)
    if not in_dir.exists() or not in_dir.is_dir():
        raise NotADirectoryError(folder)

    swc_files = sorted(p for p in in_dir.iterdir() if is_swc_data_file(p))
    files_split = 0
    files_skipped = 0
    trees_saved = 0
    failures: list[str] = []
    output_files: list[str] = []
    output_commits: list[dict[str, Any]] = []

    naming_cfg = dict(cfg.get("naming", {}))
    output_mode = str(naming_cfg.get("output_mode", "single_output_subdir")).lower()
    run_timestamp = timestamp_slug()
    out_dir = operation_output_dir_for_folder(in_dir, "batch_split", timestamp=run_timestamp)

    for fp in swc_files:
        try:
            text = fp.read_text(encoding="utf-8", errors="ignore")
            trees = split_swc_text(text, config_overrides=cfg)
            if len(trees) <= 1:
                files_skipped += 1
                continue

            files_split += 1
            source_provenance = derived_from_for_swc_path(fp)
            if source_provenance is None:
                source_provenance = derived_from_payload(
                    source_root_sha=sha256_hex(canonical_swc(fp.read_bytes())),
                    source_commit_sha="sha256:" + ("0" * 64),
                    source_path=fp.name,
                )
            if output_mode == "per_file_subdir":
                file_out_dir = out_dir / fp.stem
                file_out_dir.mkdir(parents=True, exist_ok=True)
            else:
                file_out_dir = out_dir

            for idx, (_root_id, sub_text, _node_count) in enumerate(trees, start=1):
                out_path = operation_output_path_for_file(
                    fp,
                    "batch_split",
                    output_dir=file_out_dir,
                    timestamp=run_timestamp,
                    variant=f"tree_{idx}",
                )
                with tracked_op(
                    out_path,
                    kind=OpKind.SPLIT,
                    params={
                        "source": fp.name,
                        "tree_index": idx,
                        "tree_count": len(trees),
                    },
                    message=f"GUI batch split tree {idx}/{len(trees)} from {fp.name}",
                    derived_from=source_provenance,
                ) as op:
                    op.set_output(sub_text.encode("utf-8"))
                trees_saved += 1
                output_commits.append(
                    {
                        "file": str(out_path.relative_to(out_dir)),
                        "commit_sha": op.result.commit_sha if op.result else None,
                        "operation_id": op.result.operation_label if op.result else None,
                    }
                )
                if output_mode == "per_file_subdir":
                    output_files.append(str(out_path.relative_to(out_dir)))
                else:
                    output_files.append(out_path.name)
        except Exception as e:  # noqa: BLE001
            failures.append(f"{fp.name}: {e}")

    result = {
        "folder": str(in_dir),
        "out_dir": str(out_dir),
        "files_total": len(swc_files),
        "files_split": files_split,
        "files_skipped": files_skipped,
        "trees_saved": trees_saved,
        "output_files": output_files,
        "output_commits": output_commits,
        "failures": failures,
    }

    log_text = format_split_report_text(result)
    result["log_path"] = write_text_report(
        operation_report_path_for_folder(in_dir, "batch_split", output_dir=out_dir, timestamp=run_timestamp),
        log_text,
    )
    return result
