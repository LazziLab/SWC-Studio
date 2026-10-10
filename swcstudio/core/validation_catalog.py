"""Shared validation label/category catalog used by GUI and CLI."""

from __future__ import annotations

from typing import Any


CHECK_CATALOG: list[tuple[str, list[tuple[str, str, str]]]] = [
    (
        "Structural presence",
        [
            (
                "valid_soma_format",
                "Soma format is simple",
                "Warns when a soma is represented by multiple connected type-1 nodes. The other checks still run, on a temporary working copy with each soma group merged into one node.",
            ),
            (
                "multiple_somas",
                "Only one connected soma group remains",
                "After temporary soma consolidation, more than one remaining soma means the file likely contains disconnected cells.",
            ),
            ("has_soma", "Soma present", "At least one node is labeled as soma (type 1)."),
            (
                "no_invalid_negative_types",
                "No invalid negative node types",
                "Type values below 0 are invalid and treated as hard errors.",
            ),
            (
                "custom_types_defined",
                "Custom node types are defined",
                "Any node type value >= 5 is treated as a custom type and should have a user-defined display name and color.",
            ),
            ("has_axon", "Axon present", "At least one node is labeled as axon (type 2)."),
            (
                "has_basal_dendrite",
                "Basal dendrite present",
                "At least one node is labeled as basal dendrite (type 3).",
            ),
            (
                "has_apical_dendrite",
                "Apical dendrite present",
                "At least one node is labeled as apical dendrite (type 4).",
            ),
        ],
    ),
    (
        "Radius and size",
        [
            (
                "all_neurite_radii_nonzero",
                "All neurite radii are positive",
                "Every non-soma node must have radius > 0.",
            ),
            (
                "soma_radius_nonzero",
                "Soma radius is positive",
                "Soma radius must be greater than zero when soma exists.",
            ),
            (
                "no_ultranarrow_sections",
                "No extremely narrow sections",
                "Flags sections containing radii below the narrow-section threshold.",
            ),
            (
                "no_ultranarrow_starts",
                "No extremely narrow branch starts",
                "Flags branches that start with very small radius values.",
            ),
            (
                "no_fat_terminal_ends",
                "No oversized terminal ends",
                "Terminal points should not be abnormally thicker than the branch trend.",
            ),
            (
                "radius_upper_bound",
                "No oversized radii",
                "Flags nodes whose radius exceeds the configured maximum (max_radius, 20 by default). Off by default.",
            ),
        ],
    ),
    (
        "Length and geometry",
        [
            (
                "all_section_lengths_nonzero",
                "All section lengths are positive",
                "Each section must have total geometric length > 0.",
            ),
            (
                "all_segment_lengths_nonzero",
                "All segment lengths are positive",
                "Every parent-child segment must have positive 3D length.",
            ),
            (
                "no_back_tracking",
                "No geometric backtracking",
                "Branches should progress without folding back on themselves excessively.",
            ),
            (
                "no_flat_neurites",
                "No flattened neurites",
                "Neurites should maintain realistic 3D spread rather than collapsing into a plane.",
            ),
            (
                "no_duplicate_3d_points",
                "No duplicate 3D points",
                "Two different nodes should not share identical XYZ coordinates.",
            ),
            (
                "no_extreme_spatial_jump",
                "No extreme spatial jumps",
                "Flags parent-child segments whose 3D length is an extreme outlier relative to the morphology and above a conservative absolute jump threshold.",
            ),
        ],
    ),
    (
        "Topology",
        [
            (
                "no_dangling_branches",
                "No dangling branches",
                "Any non-soma node with parent -1 is treated as a dangling branch.",
            ),
            (
                "no_self_loop",
                "No self loops",
                "A node must never list itself as its own parent.",
            ),
            (
                "no_single_child_chains",
                "No single-child chains",
                "Avoid long runs of trivial one-child sections that suggest indexing artifacts.",
            ),
            (
                "has_unifurcation",
                "Contains unifurcation",
                "Reports whether unifurcation patterns are present in the morphology.",
            ),
            (
                "has_multifurcation",
                "Contains multifurcation",
                "Reports whether branch points with more than two children are present.",
            ),
        ],
    ),
    (
        "Index consistency",
        [
            (
                "no_section_index_jumps",
                "No large section z-axis jumps",
                "Checks whether consecutive section points jump too far along the z-axis.",
            ),
            (
                "no_root_index_jumps",
                "Neurite roots too far from soma",
                "Checks whether neurite root points start too far from the soma center relative to soma radius.",
            ),
            (
                "parent_id_less_than_child_id",
                "Parent ID is less than child ID",
                "Warns when a valid parent ID is greater than or equal to its child ID, which violates the usual SWC topological ordering convention.",
            ),
            (
                "no_node_id_gaps",
                "Node IDs are continuous",
                "Warns when sorted node IDs skip missing integers, which usually means the file should be reindexed for a clean continuous sequence.",
            ),
        ],
    ),
]


CHECK_ORDER: dict[str, int] = {}
CHECK_CATEGORY: dict[str, str] = {}
CHECK_LABEL: dict[str, str] = {}
CHECK_RULE: dict[str, str] = {}
CHECK_FAILURE_LABEL: dict[str, str] = {
    "valid_soma_format": "Complex soma format found",
    "multiple_somas": "Multiple somas found",
    "has_soma": "Soma missing",
    "no_invalid_negative_types": "Invalid negative types found",
    "custom_types_defined": "Custom types need definitions",
    "has_axon": "Axon missing",
    "has_basal_dendrite": "Basal dendrite missing",
    "has_apical_dendrite": "Apical dendrite missing",
    "all_neurite_radii_nonzero": "Invalid neurite radii found",
    "soma_radius_nonzero": "Invalid soma radius found",
    "no_ultranarrow_sections": "Ultranarrow sections found",
    "no_ultranarrow_starts": "Ultranarrow branch starts found",
    "no_fat_terminal_ends": "Oversized terminal ends found",
    "all_section_lengths_nonzero": "Zero-length sections found",
    "all_segment_lengths_nonzero": "Zero-length segments found",
    "no_back_tracking": "Geometric backtracking found",
    "no_flat_neurites": "Flattened neurites found",
    "no_duplicate_3d_points": "Duplicated points found",
    "no_extreme_spatial_jump": "Extreme spatial jumps found",
    "no_dangling_branches": "Dangling branches found",
    "no_self_loop": "Self loops found",
    "no_single_child_chains": "Single-child chains found",
    "has_unifurcation": "Unifurcation found",
    "has_multifurcation": "Multifurcation found",
    "no_section_index_jumps": "Sections jump too far along Z",
    "no_root_index_jumps": "Neurite roots too far from soma",
    "parent_id_less_than_child_id": "Parent-child ID order violations found",
    "no_node_id_gaps": "Node ID gaps found",
    "radius_upper_bound": "Oversized radii found",
}
for _idx, (_category, _items) in enumerate(CHECK_CATALOG):
    for _j, (_key, _label, _rule) in enumerate(_items):
        CHECK_ORDER[_key] = _idx * 100 + _j
        CHECK_CATEGORY[_key] = _category
        CHECK_LABEL[_key] = _label
        CHECK_RULE[_key] = _rule


def sort_key_for_check(key: str, label: str) -> tuple[int, str]:
    return (CHECK_ORDER.get(key, 10_000), str(label).lower())


def rule_for_key(key: str) -> str:
    return str(CHECK_RULE.get(str(key), ""))


def display_label_for_result(key: str, passed: bool, label: str | None = None) -> str:
    key = str(key or "").strip()
    if passed:
        return str(label or CHECK_LABEL.get(key) or key)
    return str(CHECK_FAILURE_LABEL.get(key) or label or CHECK_LABEL.get(key) or key)


def group_rows_by_category(rows: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    groups: dict[str, list[dict[str, Any]]] = {cat: [] for cat, _ in CHECK_CATALOG}
    other: list[dict[str, Any]] = []

    for row in rows:
        item = dict(row)
        key = str(item.get("key", ""))
        label = str(item.get("label") or CHECK_LABEL.get(key) or key)
        item["label"] = label
        category = CHECK_CATEGORY.get(key, "Other")
        if category == "Other":
            other.append(item)
        else:
            groups[category].append(item)

    out: list[tuple[str, list[dict[str, Any]]]] = []
    for category, _checks in CHECK_CATALOG:
        items = groups.get(category, [])
        if items:
            items.sort(key=lambda r: sort_key_for_check(str(r.get("key", "")), str(r.get("label", ""))))
            out.append((category, items))

    if other:
        other.sort(key=lambda r: str(r.get("label", "")).lower())
        out.append(("Other", other))
    return out
