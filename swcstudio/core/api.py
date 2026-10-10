"""Public core API.

This module exposes shared backend primitives used by tool/feature modules.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from swcstudio.core.auto_typing import BatchOptions, run_batch
from swcstudio.core.custom_types import label_for_type
from swcstudio.core.geometry_editing import (
    disconnect_branch,
    delete_node,
    delete_subtree,
    insert_node_between,
    move_node_absolute,
    move_subtree_absolute,
    reconnect_branch,
    reindex_dataframe_with_map,
)
from swcstudio.core.swc_io import (
    SWC_COLS,
    parse_swc_text_preserve_tokens,
    write_swc_to_bytes_preserve_tokens,
)
from swcstudio.core.validation import (
    _split_swc_by_soma_roots,
    run_format_validation_from_text,
    run_per_tree_validation,
)


def parse_swc_text(text: str):
    return parse_swc_text_preserve_tokens(text)


def write_swc_bytes(df) -> bytes:
    return write_swc_to_bytes_preserve_tokens(df)


def validate_text(swc_text: str):
    return run_format_validation_from_text(swc_text)


def validate_file(path: Path):
    txt = Path(path).read_text(encoding="utf-8", errors="ignore")
    return validate_text(txt)


def per_tree_validation(swc_text: str):
    return run_per_tree_validation(swc_text)


def split_by_soma_roots(swc_text: str):
    from swcstudio.tools.batch_processing.features.swc_splitter import split_swc_text

    return split_swc_text(swc_text)


def run_auto_typing_folder(folder: str, options: BatchOptions | None = None) -> Any:
    opts = options if options is not None else BatchOptions()
    return run_batch(folder, opts)


__all__ = [
    "SWC_COLS",
    "BatchOptions",
    "label_for_type",
    "parse_swc_text",
    "write_swc_bytes",
    "validate_text",
    "validate_file",
    "per_tree_validation",
    "split_by_soma_roots",
    "run_auto_typing_folder",
    "move_node_absolute",
    "move_subtree_absolute",
    "reconnect_branch",
    "disconnect_branch",
    "delete_node",
    "delete_subtree",
    "insert_node_between",
    "reindex_dataframe_with_map",
]
