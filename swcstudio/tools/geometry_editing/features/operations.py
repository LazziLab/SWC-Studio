"""Thin tool-layer wrappers over core geometry editing operations."""

from __future__ import annotations

import pandas as pd

from swcstudio.core.custom_types import label_for_type
from swcstudio.core.geometry_editing import (
    GeometrySelection,
    delete_node as _delete_node,
    delete_subtree as _delete_subtree,
    disconnect_branch as _disconnect_branch,
    insert_node_between as _insert_node_between,
    make_selection as _make_selection,
    move_node_absolute as _move_node_absolute,
    move_selection_by_anchor_absolute as _move_selection_by_anchor_absolute,
    move_subtree_absolute as _move_subtree_absolute,
    path_between_nodes as _path_between_nodes,
    reconnect_branch as _reconnect_branch,
    reindex_dataframe as _reindex_dataframe,
    reindex_dataframe_with_map as _reindex_dataframe_with_map,
    subtree_node_ids as _subtree_node_ids,
)


def path_between_nodes(df: pd.DataFrame, start_id: int, end_id: int) -> list[int]:
    return _path_between_nodes(df, start_id, end_id)


def subtree_node_ids(df: pd.DataFrame, root_id: int) -> list[int]:
    return _subtree_node_ids(df, root_id)


def make_selection(df: pd.DataFrame, *, kind: str, anchor_id: int, hops: int | None = None) -> GeometrySelection:
    return _make_selection(df, kind=kind, anchor_id=anchor_id, hops=hops)


def reindex_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    return _reindex_dataframe(df)


def reindex_dataframe_with_map(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[int, int]]:
    return _reindex_dataframe_with_map(df)


def move_node_absolute(df: pd.DataFrame, node_id: int, x: float, y: float, z: float) -> pd.DataFrame:
    return _move_node_absolute(df, node_id, x, y, z)


def move_subtree_absolute(df: pd.DataFrame, root_id: int, x: float, y: float, z: float) -> pd.DataFrame:
    return _move_subtree_absolute(df, root_id, x, y, z)


def move_selection_by_anchor_absolute(
    df: pd.DataFrame,
    selection: GeometrySelection | dict | list[int],
    anchor_id: int,
    x: float,
    y: float,
    z: float,
) -> pd.DataFrame:
    if isinstance(selection, GeometrySelection):
        node_ids = list(selection.node_ids)
    elif isinstance(selection, dict):
        node_ids = list(selection.get("node_ids", []) or [])
    else:
        node_ids = list(selection or [])
    return _move_selection_by_anchor_absolute(df, node_ids, anchor_id, x, y, z)


def reconnect_branch(df: pd.DataFrame, source_id: int, target_id: int) -> pd.DataFrame:
    return _reconnect_branch(df, source_id, target_id)


def disconnect_branch(df: pd.DataFrame, start_id: int, end_id: int) -> pd.DataFrame:
    return _disconnect_branch(df, start_id, end_id)


def delete_node(df: pd.DataFrame, node_id: int, reconnect_children: bool = False):
    return _delete_node(df, node_id, reconnect_children=reconnect_children)


def delete_subtree(df: pd.DataFrame, root_id: int, return_id_map: bool = False):
    return _delete_subtree(df, root_id, return_id_map=return_id_map)


def insert_node_between(
    df: pd.DataFrame,
    start_id: int,
    end_id: int,
    x: float,
    y: float,
    z: float,
    radius: float | None = None,
    type_id: int | None = None,
):
    return _insert_node_between(
        df,
        start_id,
        end_id,
        x=x,
        y=y,
        z=z,
        radius=radius,
        type_id=type_id,
    )
