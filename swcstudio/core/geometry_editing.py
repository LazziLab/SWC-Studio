"""Geometry editing helpers for selection expansion and graph operations."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

import pandas as pd

from swcstudio.core.custom_types import label_for_type
from swcstudio.core.swc_io import SWC_COLS


@dataclass
class GeometrySelection:
    item_id: str
    kind: str
    anchor_id: int
    node_ids: list[int]
    label: str
    detail: str = ""
    meta: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["meta"] = dict(self.meta or {})
        return out


# swc_io's original number text. Carrying it through an edit lets a save
# rewrite only the values the edit changed; the header rides in df.attrs.
_TOKEN_COLS = ["x_str", "y_str", "z_str", "radius_str", "id_str", "parent_str"]


def _df_copy(df: pd.DataFrame) -> pd.DataFrame:
    cols = SWC_COLS + [c for c in _TOKEN_COLS if c in df.columns]
    out = df.loc[:, cols].copy()
    out.attrs = dict(df.attrs)
    return out


def _append_row(df: pd.DataFrame, row: dict[str, Any]) -> pd.DataFrame:
    out = pd.concat([df, pd.DataFrame([row], columns=SWC_COLS)], ignore_index=True)
    out.attrs = dict(df.attrs)
    return out


def _id_to_row(df: pd.DataFrame) -> dict[int, int]:
    return {int(row["id"]): int(idx) for idx, row in df[["id"]].iterrows()}


def _parent_by_id(df: pd.DataFrame) -> dict[int, int]:
    return {
        int(row["id"]): int(row["parent"])
        for _, row in df[["id", "parent"]].iterrows()
    }


def _children_by_id(df: pd.DataFrame) -> dict[int, list[int]]:
    children: dict[int, list[int]] = {}
    for _, row in df[["id", "parent"]].iterrows():
        parent_id = int(row["parent"])
        if parent_id >= 0:
            children.setdefault(parent_id, []).append(int(row["id"]))
    return children


def _row_position_by_id(df: pd.DataFrame) -> dict[int, int]:
    return {int(node_id): pos for pos, node_id in enumerate(df["id"].astype(int).tolist())}


def _undirected_neighbors(df: pd.DataFrame) -> dict[int, list[int]]:
    neighbors: dict[int, list[int]] = {}
    for _, row in df[["id", "parent"]].iterrows():
        node_id = int(row["id"])
        parent_id = int(row["parent"])
        neighbors.setdefault(node_id, [])
        if parent_id >= 0:
            neighbors.setdefault(parent_id, [])
            neighbors[node_id].append(parent_id)
            neighbors[parent_id].append(node_id)
    return neighbors


def path_between_nodes(df: pd.DataFrame, start_id: int, end_id: int) -> list[int]:
    start_id = int(start_id)
    end_id = int(end_id)
    if start_id == end_id:
        return [start_id]
    neighbors = _undirected_neighbors(df)
    if start_id not in neighbors or end_id not in neighbors:
        return []
    queue: list[int] = [start_id]
    prev: dict[int, int | None] = {start_id: None}
    head = 0
    while head < len(queue):
        current = int(queue[head])
        head += 1
        if current == end_id:
            break
        for nxt in neighbors.get(current, []):
            nxt = int(nxt)
            if nxt in prev:
                continue
            prev[nxt] = current
            queue.append(nxt)
    if end_id not in prev:
        return []
    path: list[int] = []
    current: int | None = end_id
    while current is not None:
        path.append(int(current))
        current = prev.get(int(current))
    path.reverse()
    return path


def reindex_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy with ids remapped to continuous 1..N with parent-before-child order."""
    out, _ = reindex_dataframe_with_map(df)
    return out


def reindex_dataframe_with_map(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[int, int]]:
    """Return a reindexed copy and the old->new id mapping."""
    if df is None or df.empty:
        return _df_copy(df if df is not None else pd.DataFrame(columns=SWC_COLS)), {}
    work = _df_copy(df)
    row_by_id = {
        int(row["id"]): row.to_dict()
        for _, row in work.iterrows()
    }
    parent_by_id = _parent_by_id(work)
    children_by_id = _children_by_id(work)
    order_pos = _row_position_by_id(work)

    roots = [
        int(node_id)
        for node_id in work["id"].astype(int).tolist()
        if int(parent_by_id.get(int(node_id), -1)) < 0 or int(parent_by_id.get(int(node_id), -1)) not in row_by_id
    ]
    roots = sorted(set(roots), key=lambda nid: order_pos.get(int(nid), 10**9))

    ordered_ids: list[int] = []
    seen: set[int] = set()

    def _visit(node_id: int):
        start_id = int(node_id)
        if start_id in seen or start_id not in row_by_id:
            return
        stack: list[int] = [start_id]
        while stack:
            current = int(stack.pop())
            if current in seen or current not in row_by_id:
                continue
            seen.add(current)
            ordered_ids.append(current)
            children = sorted(
                children_by_id.get(current, []),
                key=lambda nid: order_pos.get(int(nid), 10**9),
                reverse=True,
            )
            stack.extend(int(child_id) for child_id in children)

    for root_id in roots:
        _visit(int(root_id))
    for node_id in work["id"].astype(int).tolist():
        _visit(int(node_id))

    id_map = {int(old_id): int(new_id) for new_id, old_id in enumerate(ordered_ids, start=1)}
    rows = []
    for old_id in ordered_ids:
        row = dict(row_by_id[int(old_id)])
        row["id"] = int(id_map[int(old_id)])
        parent_id = int(row.get("parent", -1))
        row["parent"] = int(id_map.get(parent_id, -1)) if parent_id >= 0 else -1
        rows.append(row)
    out = pd.DataFrame(rows, columns=list(work.columns))
    out.attrs = dict(work.attrs)
    return out, id_map


def subtree_node_ids(df: pd.DataFrame, root_id: int) -> list[int]:
    children_by_id = _children_by_id(df)
    row_by_id = _id_to_row(df)
    root_id = int(root_id)
    if root_id not in row_by_id:
        return []
    out: list[int] = []
    stack = [root_id]
    seen: set[int] = set()
    while stack:
        node_id = int(stack.pop())
        if node_id in seen:
            continue
        seen.add(node_id)
        out.append(node_id)
        for child_id in reversed(children_by_id.get(node_id, [])):
            stack.append(int(child_id))
    return out


def upstream_node_ids(df: pd.DataFrame, node_id: int, hops: int) -> list[int]:
    parent_by_id = _parent_by_id(df)
    node_id = int(node_id)
    out = [node_id]
    current = node_id
    for _ in range(max(0, int(hops))):
        parent_id = int(parent_by_id.get(current, -1))
        if parent_id < 0:
            break
        out.append(parent_id)
        current = parent_id
    return out


def downstream_node_ids(df: pd.DataFrame, node_id: int, hops: int) -> list[int]:
    children_by_id = _children_by_id(df)
    start = int(node_id)
    visited: set[int] = set()
    frontier = [(start, 0)]
    out: list[int] = []
    max_hops = max(0, int(hops))
    while frontier:
        current, depth = frontier.pop(0)
        if current in visited:
            continue
        visited.add(current)
        out.append(int(current))
        if depth >= max_hops:
            continue
        for child_id in children_by_id.get(int(current), []):
            frontier.append((int(child_id), depth + 1))
    return out


def branch_node_ids(df: pd.DataFrame, node_id: int) -> list[int]:
    parent_by_id = _parent_by_id(df)
    children_by_id = _children_by_id(df)
    current = int(node_id)
    chain_up = [current]
    while True:
        parent_id = int(parent_by_id.get(current, -1))
        if parent_id < 0:
            break
        if len(children_by_id.get(parent_id, [])) != 1:
            chain_up.append(parent_id)
            break
        chain_up.append(parent_id)
        current = parent_id
    chain_up.reverse()

    current = int(node_id)
    chain_down: list[int] = []
    while True:
        children = children_by_id.get(current, [])
        if len(children) != 1:
            break
        child_id = int(children[0])
        chain_down.append(child_id)
        current = child_id
    return chain_up + chain_down


def upstream_bifurcation_segment_ids(df: pd.DataFrame, node_id: int) -> list[int]:
    parent_by_id = _parent_by_id(df)
    children_by_id = _children_by_id(df)
    current = int(node_id)
    out = [current]
    while True:
        parent_id = int(parent_by_id.get(current, -1))
        if parent_id < 0:
            break
        out.append(parent_id)
        if len(children_by_id.get(parent_id, [])) != 1:
            break
        current = parent_id
    out.reverse()
    return out


def downstream_bifurcation_segment_ids(df: pd.DataFrame, node_id: int) -> list[int]:
    children_by_id = _children_by_id(df)
    current = int(node_id)
    out = [current]
    while True:
        children = children_by_id.get(current, [])
        if len(children) != 1:
            break
        current = int(children[0])
        out.append(current)
    return out


def make_selection(
    df: pd.DataFrame,
    *,
    kind: str,
    anchor_id: int,
    hops: int | None = None,
) -> GeometrySelection:
    node_id = int(anchor_id)
    if kind == "node":
        node_ids = [node_id]
        label = f"Node {node_id}"
        detail = "Single selected node"
    elif kind == "subtree":
        node_ids = subtree_node_ids(df, node_id)
        label = f"Subtree root {node_id}"
        detail = f"{len(node_ids)} node(s)"
    elif kind == "upstream_nodes":
        node_ids = upstream_node_ids(df, node_id, int(hops or 0))
        label = f"Upstream {int(hops or 0)} from {node_id}"
        detail = f"{len(node_ids)} node(s)"
    elif kind == "downstream_nodes":
        node_ids = downstream_node_ids(df, node_id, int(hops or 0))
        label = f"Downstream {int(hops or 0)} from {node_id}"
        detail = f"{len(node_ids)} node(s)"
    elif kind == "branch":
        node_ids = branch_node_ids(df, node_id)
        label = f"Branch at {node_id}"
        detail = f"{len(node_ids)} node(s)"
    elif kind == "up_bifurcation":
        node_ids = upstream_bifurcation_segment_ids(df, node_id)
        label = f"Upstream segment to {node_id}"
        detail = f"{len(node_ids)} node(s)"
    elif kind == "down_bifurcation":
        node_ids = downstream_bifurcation_segment_ids(df, node_id)
        label = f"Downstream segment from {node_id}"
        detail = f"{len(node_ids)} node(s)"
    else:
        raise ValueError(f"Unsupported selection kind: {kind}")
    return GeometrySelection(
        item_id=f"{kind}:{node_id}:{hops or 0}:{len(node_ids)}",
        kind=str(kind),
        anchor_id=node_id,
        node_ids=[int(v) for v in node_ids],
        label=label,
        detail=detail,
        meta={"hops": int(hops or 0)},
    )


def move_node_absolute(df: pd.DataFrame, node_id: int, x: float, y: float, z: float) -> pd.DataFrame:
    out = _df_copy(df)
    mask = out["id"].astype(int) == int(node_id)
    if not bool(mask.any()):
        raise ValueError(f"Node {int(node_id)} not found.")
    out.loc[mask, ["x", "y", "z"]] = [float(x), float(y), float(z)]
    return out


def move_subtree_absolute(df: pd.DataFrame, root_id: int, x: float, y: float, z: float) -> pd.DataFrame:
    out = _df_copy(df)
    root_mask = out["id"].astype(int) == int(root_id)
    if not bool(root_mask.any()):
        raise ValueError(f"Subtree root {int(root_id)} not found.")
    row = out.loc[root_mask].iloc[0]
    dx = float(x) - float(row["x"])
    dy = float(y) - float(row["y"])
    dz = float(z) - float(row["z"])
    node_ids = subtree_node_ids(out, int(root_id))
    mask = out["id"].astype(int).isin(node_ids)
    out.loc[mask, "x"] = out.loc[mask, "x"].astype(float) + dx
    out.loc[mask, "y"] = out.loc[mask, "y"].astype(float) + dy
    out.loc[mask, "z"] = out.loc[mask, "z"].astype(float) + dz
    return out


def move_selection_by_anchor_absolute(
    df: pd.DataFrame,
    node_ids: list[int] | set[int],
    anchor_id: int,
    x: float,
    y: float,
    z: float,
) -> pd.DataFrame:
    out = _df_copy(df)
    wanted = [int(v) for v in list(node_ids or [])]
    if not wanted:
        raise ValueError("No selected nodes to move.")
    root_mask = out["id"].astype(int) == int(anchor_id)
    if not bool(root_mask.any()):
        raise ValueError(f"Anchor node {int(anchor_id)} not found.")
    row = out.loc[root_mask].iloc[0]
    dx = float(x) - float(row["x"])
    dy = float(y) - float(row["y"])
    dz = float(z) - float(row["z"])
    mask = out["id"].astype(int).isin(wanted)
    out.loc[mask, "x"] = out.loc[mask, "x"].astype(float) + dx
    out.loc[mask, "y"] = out.loc[mask, "y"].astype(float) + dy
    out.loc[mask, "z"] = out.loc[mask, "z"].astype(float) + dz
    return out


def reconnect_branch(
    df: pd.DataFrame,
    source_id: int,
    target_id: int,
    *,
    return_id_map: bool = False,
):
    out = _df_copy(df)
    start_id = int(source_id)
    end_id = int(target_id)
    valid_ids = set(out["id"].astype(int).tolist())
    if start_id not in valid_ids:
        raise ValueError(f"Start node {start_id} not found.")
    if end_id not in valid_ids:
        raise ValueError(f"End node {end_id} not found.")
    if start_id == end_id:
        raise ValueError("Start and end nodes must be different.")
    if start_id in set(subtree_node_ids(out, end_id)):
        raise ValueError("Reconnection would create a cycle.")
    mask = out["id"].astype(int) == end_id
    out.loc[mask, "parent"] = start_id
    return (out, {}) if return_id_map else out


def disconnect_branch(
    df: pd.DataFrame,
    start_id: int,
    end_id: int,
    *,
    return_id_map: bool = False,
):
    out = _df_copy(df)
    start_id = int(start_id)
    end_id = int(end_id)
    valid_ids = set(out["id"].astype(int).tolist())
    if start_id not in valid_ids:
        raise ValueError(f"Start node {start_id} not found.")
    if end_id not in valid_ids:
        raise ValueError(f"End node {end_id} not found.")
    if start_id == end_id:
        raise ValueError("Start and end nodes must be different.")
    parent_by_id = _parent_by_id(out)
    path = path_between_nodes(out, start_id, end_id)
    if len(path) < 2:
        raise ValueError("Start and end nodes are not connected.")
    disconnect_children: list[int] = []
    for left, right in zip(path[:-1], path[1:]):
        left = int(left)
        right = int(right)
        if int(parent_by_id.get(left, -1)) == right:
            disconnect_children.append(left)
        elif int(parent_by_id.get(right, -1)) == left:
            disconnect_children.append(right)
        else:
            raise ValueError("Encountered a non-parent-child step while disconnecting the selected path.")
    if not disconnect_children:
        raise ValueError("No parent-child edges found to disconnect.")
    mask = out["id"].astype(int).isin([int(v) for v in disconnect_children])
    out.loc[mask, "parent"] = -1
    return (out, {}) if return_id_map else out


def delete_node(
    df: pd.DataFrame,
    node_id: int,
    *,
    reconnect_children: bool,
    return_id_map: bool = False,
):
    out = _df_copy(df)
    node_id = int(node_id)
    parent_by_id = _parent_by_id(out)
    children_by_id = _children_by_id(out)
    valid_ids = set(out["id"].astype(int).tolist())
    if node_id not in valid_ids:
        raise ValueError(f"Node {node_id} not found.")
    children = [int(v) for v in children_by_id.get(node_id, [])]
    parent_id = int(parent_by_id.get(node_id, -1))
    if children and not reconnect_children:
        raise ValueError("Node has children. Use subtree delete or reconnect children.")
    if reconnect_children:
        mask_children = out["id"].astype(int).isin(children)
        out.loc[mask_children, "parent"] = parent_id
    out = out.loc[out["id"].astype(int) != node_id].copy()
    return (out, {}) if return_id_map else out


def delete_subtree(df: pd.DataFrame, root_id: int, *, return_id_map: bool = False):
    out = _df_copy(df)
    node_ids = set(subtree_node_ids(out, int(root_id)))
    if not node_ids:
        raise ValueError(f"Subtree root {int(root_id)} not found.")
    out = out.loc[~out["id"].astype(int).isin(node_ids)].copy()
    return (out, {}) if return_id_map else out


def insert_node_before_child(
    df: pd.DataFrame,
    child_id: int,
    *,
    x: float,
    y: float,
    z: float,
    radius: float | None = None,
    type_id: int | None = None,
) -> pd.DataFrame:
    out = _df_copy(df)
    child_id = int(child_id)
    mask = out["id"].astype(int) == child_id
    if not bool(mask.any()):
        raise ValueError(f"Child node {child_id} not found.")
    child = out.loc[mask].iloc[0]
    parent_id = int(child["parent"])
    if parent_id < 0:
        raise ValueError("Cannot insert before a root node without a parent.")
    new_id = int(out["id"].astype(int).max()) + 1
    new_row = {
        "id": new_id,
        "type": int(type_id if type_id is not None else int(child["type"])),
        "x": float(x),
        "y": float(y),
        "z": float(z),
        "radius": float(radius if radius is not None else float(child["radius"])),
        "parent": parent_id,
    }
    out.loc[mask, "parent"] = new_id
    out = _append_row(out, new_row)
    return out


def insert_node_between(
    df: pd.DataFrame,
    start_id: int,
    end_id: int,
    *,
    x: float,
    y: float,
    z: float,
    radius: float | None = None,
    type_id: int | None = None,
    return_id_map: bool = False,
):
    out = _df_copy(df)
    start_id = int(start_id)
    end_id = int(end_id)
    valid_ids = set(out["id"].astype(int).tolist())
    if start_id not in valid_ids:
        raise ValueError(f"Start node {start_id} not found.")
    if end_id >= 0 and end_id not in valid_ids:
        raise ValueError(f"End node {end_id} not found.")
    if end_id >= 0 and start_id == end_id:
        raise ValueError("Start and end node must be different.")
    if end_id >= 0 and start_id in set(subtree_node_ids(out, end_id)):
        raise ValueError("Insert would create a cycle.")
    end_row = None
    end_mask = None
    if end_id >= 0:
        end_mask = out["id"].astype(int) == end_id
        end_row = out.loc[end_mask].iloc[0]
    new_id = int(out["id"].astype(int).max()) + 1
    new_row = {
        "id": new_id,
        "type": int(
            type_id if type_id is not None else int(end_row["type"]) if end_row is not None else int(out.loc[out["id"].astype(int) == start_id].iloc[0]["type"])
        ),
        "x": float(x),
        "y": float(y),
        "z": float(z),
        "radius": float(
            radius if radius is not None else float(end_row["radius"]) if end_row is not None else float(out.loc[out["id"].astype(int) == start_id].iloc[0]["radius"])
        ),
        "parent": start_id,
    }
    if end_mask is not None:
        out.loc[end_mask, "parent"] = new_id
    out = _append_row(out, new_row)
    return (out, {}) if return_id_map else out


def selected_item_summary(df: pd.DataFrame, node_ids: list[int]) -> str:
    if df is None or df.empty or not node_ids:
        return "No selection."
    rows = df.loc[df["id"].astype(int).isin([int(v) for v in node_ids]), ["id", "type", "radius"]].copy()
    rows = rows.sort_values("id")
    preview = []
    for _, row in rows.head(8).iterrows():
        type_id = int(row["type"])
        preview.append(
            f"Node {int(row['id'])}: {label_for_type(type_id)} ({type_id}), radius={float(row['radius']):.5g}"
        )
    if len(rows) > 8:
        preview.append(f"... {len(rows) - 8} more")
    return "\n".join(preview)
