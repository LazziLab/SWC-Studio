"""Geometry editing helpers for selection expansion and graph operations."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
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


def duplicate_node_ids(ids: Iterable[int]) -> list[int]:
    """Sorted node IDs that appear on more than one row."""
    counts = Counter(int(v) for v in ids)
    return sorted(node_id for node_id, count in counts.items() if count > 1)


def _strongly_connected_components(nodes: list[int], succ: dict[int, set[int]]) -> list[list[int]]:
    """Tarjan's algorithm, iterative so deep trees cannot overflow the stack."""
    index: dict[int, int] = {}
    low: dict[int, int] = {}
    on_stack: set[int] = set()
    stack: list[int] = []
    components: list[list[int]] = []
    counter = 0
    for root in nodes:
        if root in index:
            continue
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        work = [(root, iter(sorted(succ.get(root, ()))))]
        while work:
            node, successors = work[-1]
            descended = False
            for nxt in successors:
                if nxt not in index:
                    index[nxt] = low[nxt] = counter
                    counter += 1
                    stack.append(nxt)
                    on_stack.add(nxt)
                    work.append((nxt, iter(sorted(succ.get(nxt, ())))))
                    descended = True
                    break
                if nxt in on_stack:
                    low[node] = min(low[node], index[nxt])
            if descended:
                continue
            work.pop()
            if work:
                caller = work[-1][0]
                low[caller] = min(low[caller], low[node])
            if low[node] == index[node]:
                component: list[int] = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                components.append(component)
    return components


def parent_cycles(
    ids: Iterable[int],
    parents: Iterable[int],
    *,
    include_self_loops: bool = False,
) -> list[list[int]]:
    """Return every closed parent chain as a sorted list of node IDs.

    Following parent IDs from a node on a cycle never reaches a root, so the
    cycle and everything below it are cut off from the cell. A node that is
    its own parent is a cycle of one; it is left out unless
    ``include_self_loops`` is set. Parent IDs that name no row end a chain.
    Duplicate IDs are allowed: an ID then has one parent link per row.
    """
    id_list = [int(v) for v in ids]
    parent_list = [int(v) for v in parents]
    present = set(id_list)
    succ: dict[int, set[int]] = {}
    for node_id, parent_id in zip(id_list, parent_list):
        if parent_id in present:
            succ.setdefault(node_id, set()).add(parent_id)

    cycles: list[list[int]] = []
    if all(len(targets) == 1 for targets in succ.values()):
        # Unique parent per ID: walk each chain once.
        parent_of = {node_id: next(iter(targets)) for node_id, targets in succ.items()}
        done: set[int] = set()
        for start in id_list:
            if start in done:
                continue
            path: list[int] = []
            position: dict[int, int] = {}
            node: int | None = start
            while node is not None and node not in done and node not in position:
                position[node] = len(path)
                path.append(node)
                node = parent_of.get(node)
            if node is not None and node in position:
                cycles.append(sorted(path[position[node]:]))
            done.update(path)
    else:
        for component in _strongly_connected_components(sorted(present), succ):
            if len(component) > 1 or component[0] in succ.get(component[0], set()):
                cycles.append(sorted(component))

    if not include_self_loops:
        cycles = [cycle for cycle in cycles if len(cycle) > 1]
    cycles.sort(key=lambda cycle: cycle[0])
    return cycles


def nodes_cut_off_by_cycles(ids: Iterable[int], parents: Iterable[int], cycles: list[list[int]]) -> list[int]:
    """Sorted IDs on a cycle or below one: their parent chain never reaches a root."""
    cycle_ids = {int(v) for cycle in cycles for v in cycle}
    if not cycle_ids:
        return []
    children: dict[int, list[int]] = {}
    for node_id, parent_id in zip((int(v) for v in ids), (int(v) for v in parents)):
        if parent_id != node_id:
            children.setdefault(parent_id, []).append(node_id)
    seen = set(cycle_ids)
    stack = list(cycle_ids)
    while stack:
        for child in children.get(stack.pop(), []):
            if child not in seen:
                seen.add(child)
                stack.append(child)
    return sorted(seen)


def format_id_list(node_ids: list[int], limit: int = 10) -> str:
    """'4', '4 and 7', '4, 7 and 9'; past ``limit`` IDs, '... and N more'."""
    shown = [str(int(v)) for v in node_ids[:limit]]
    extra = len(node_ids) - len(shown)
    if extra > 0:
        return ", ".join(shown) + f" and {extra} more"
    if len(shown) > 1:
        return ", ".join(shown[:-1]) + " and " + shown[-1]
    return shown[0] if shown else ""


def require_unique_node_ids(df: pd.DataFrame, action: str) -> None:
    """Raise ValueError when any node ID is on more than one row.

    A parent ID that names a duplicated ID could mean either row, so an edit
    by ID would change or drop the wrong rows.
    """
    if df is None or df.empty:
        return
    dups = duplicate_node_ids(df["id"].astype(int).tolist())
    if not dups:
        return
    noun = "node ID" if len(dups) == 1 else "node IDs"
    verb = "appears" if len(dups) == 1 else "appear"
    raise ValueError(
        f"Cannot {action}: {noun} {format_id_list(dups)} {verb} on more than one row, "
        "so a parent ID that names a duplicated ID could mean either row. "
        "Renumber or remove the duplicated rows first."
    )


def require_no_parent_cycles(df: pd.DataFrame, action: str) -> None:
    """Raise ValueError when following parent IDs loops instead of reaching a root."""
    if df is None or df.empty:
        return
    cycles = parent_cycles(
        df["id"].astype(int).tolist(),
        df["parent"].astype(int).tolist(),
        include_self_loops=True,
    )
    if not cycles:
        return
    parts: list[str] = []
    for cycle in cycles[:5]:
        if len(cycle) == 1:
            parts.append(f"node {cycle[0]} is its own parent")
        else:
            parts.append(f"nodes {format_id_list(cycle)} form a parent cycle")
    if len(cycles) > 5:
        parts.append(f"{len(cycles) - 5} more cycle(s)")
    raise ValueError(
        f"Cannot {action}: " + "; ".join(parts) + ". "
        "Their parent chain never reaches a root, so there is no parent-before-child order. "
        "Break the cycle first (Geometry Editing: disconnect, then reconnect the node to its real parent)."
    )


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
    """Return a reindexed copy and the old->new id mapping.

    Raises ValueError, and changes nothing, when a node ID is on more than
    one row (the rows would collapse into one) or when parent IDs form a
    cycle (no parent-before-child order exists).
    """
    if df is None or df.empty:
        return _df_copy(df if df is not None else pd.DataFrame(columns=SWC_COLS)), {}
    require_unique_node_ids(df, "reindex")
    require_no_parent_cycles(df, "reindex")
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
    seen = {node_id}
    current = node_id
    for _ in range(max(0, int(hops))):
        parent_id = int(parent_by_id.get(current, -1))
        # A parent cycle would revisit nodes; stop where it closes.
        if parent_id < 0 or parent_id in seen:
            break
        out.append(parent_id)
        seen.add(parent_id)
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
    # The seen set stops each walk where a parent cycle closes; without it
    # a cycle of single-child nodes would loop forever.
    parent_by_id = _parent_by_id(df)
    children_by_id = _children_by_id(df)
    current = int(node_id)
    chain_up = [current]
    seen = {current}
    while True:
        parent_id = int(parent_by_id.get(current, -1))
        if parent_id < 0 or parent_id in seen:
            break
        seen.add(parent_id)
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
        if child_id in seen:
            break
        seen.add(child_id)
        chain_down.append(child_id)
        current = child_id
    return chain_up + chain_down


def upstream_bifurcation_segment_ids(df: pd.DataFrame, node_id: int) -> list[int]:
    parent_by_id = _parent_by_id(df)
    children_by_id = _children_by_id(df)
    current = int(node_id)
    out = [current]
    seen = {current}
    while True:
        parent_id = int(parent_by_id.get(current, -1))
        if parent_id < 0 or parent_id in seen:
            break
        seen.add(parent_id)
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
    seen = {current}
    while True:
        children = children_by_id.get(current, [])
        if len(children) != 1:
            break
        current = int(children[0])
        if current in seen:
            break
        seen.add(current)
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
    require_unique_node_ids(df, f"move node {int(node_id)}")
    out = _df_copy(df)
    mask = out["id"].astype(int) == int(node_id)
    if not bool(mask.any()):
        raise ValueError(f"Node {int(node_id)} not found.")
    out.loc[mask, ["x", "y", "z"]] = [float(x), float(y), float(z)]
    return out


def move_subtree_absolute(df: pd.DataFrame, root_id: int, x: float, y: float, z: float) -> pd.DataFrame:
    require_unique_node_ids(df, f"move the subtree at node {int(root_id)}")
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
    require_unique_node_ids(df, "move the selection")
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
    require_unique_node_ids(df, f"reconnect node {int(target_id)} to node {int(source_id)}")
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
    require_unique_node_ids(df, f"disconnect the path from node {int(start_id)} to node {int(end_id)}")
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
    require_unique_node_ids(df, f"delete node {int(node_id)}")
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
    require_unique_node_ids(df, f"delete the subtree at node {int(root_id)}")
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
    require_unique_node_ids(df, f"insert a node before node {int(child_id)}")
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
    require_unique_node_ids(df, f"insert a node between nodes {int(start_id)} and {int(end_id)}")
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
