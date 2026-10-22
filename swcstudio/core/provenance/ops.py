"""Closed taxonomy of operation kinds (PROVENANCE_SPEC §3).

Why a closed enum + lightweight schema:

* The JSONL event log must be **stable across versions**: a v1 reader
  encountering a v1 op kind it doesn't recognize is a bug. So we lock
  the v1 set here and any new kind requires a v1.x additive bump.
* CLI/GUI handlers and plugins all reach for the same names. Having
  one place to look avoids drift between "auto_label" and "autolabel".
* Per-kind param validation is intentionally light — the heavy
  validation lives in the actual implementation modules
  (auto_label, radii_clean, etc.). What we promise here is "this
  field name exists and has this type"; what we don't promise is
  "the values are semantically valid for the operation."
"""

from __future__ import annotations

from enum import Enum
from typing import Any

__all__ = [
    "OpKind",
    "is_ai_op",
    "operation_display_name",
    "operation_display_parameters",
    "validate_op_record",
]


class OpKind(str, Enum):
    """Closed list of v1 operation kinds.

    String values double as the on-disk ``kind`` field. Inherits from
    ``str`` so callers can pass an OpKind anywhere a string is
    expected (``json.dumps`` works directly).
    """

    SET_TYPE         = "set_type"
    SET_RADIUS       = "set_radius"
    MANUAL_RADII     = "manual_radii"
    AUTO_FIX         = "auto_fix"
    AUTO_LABEL       = "auto_label"           # AI
    DENDROGRAM_EDIT  = "dendrogram_edit"
    GEOMETRY_EDIT    = "geometry_edit"
    RADII_CLEAN      = "radii_clean"
    SIMPLIFICATION   = "simplification"
    INDEX_CLEAN      = "index_clean"
    SPLIT            = "split"
    PLUGIN_OP        = "plugin_op"


# AI-classified ops — these are the ones tracked_op() must capture an
# environment fingerprint and AI-run record for. Matches PROVENANCE_SPEC
# §3 "Op kind" / §4 "AI ops also carry an ai_run_ref".
_AI_KINDS: frozenset[str] = frozenset({
    OpKind.AUTO_LABEL.value,
})


def is_ai_op(kind: str) -> bool:
    """Return True if ``kind`` is one of the AI-classified ops."""
    return kind in _AI_KINDS


def operation_display_name(kind: str, params: dict[str, Any] | None = None) -> str:
    """Return a clear user-facing operation name.

    Exact operation kinds remain unchanged in history. This presentation
    helper also gives older generic ``plugin_op`` records a useful name
    when they contain descriptive parameters.
    """
    kind_text = str(kind or "operation").strip()
    params = params if isinstance(params, dict) else {}
    source = str(params.get("source", "")).strip().lower()
    title = str(params.get("title", "")).strip()
    plugin = str(params.get("plugin", "")).strip().lower()

    if kind_text == OpKind.SET_TYPE.value or source == "editor_table":
        return "Manual Labeling"
    if kind_text in {OpKind.SET_RADIUS.value, OpKind.MANUAL_RADII.value}:
        return "Manual Radius Editing"
    if kind_text == OpKind.PLUGIN_OP.value and plugin == "consolidate_soma":
        return "Consolidate Soma"
    if title:
        return title
    return kind_text.replace("_", " ").title()


_PARAMETER_CONTAINERS = (
    "effective_config",
    "options",
    "config",
    "settings",
    "config_overrides",
    "params_used",
)

_HIDDEN_PARAMETER_KEYS = {
    "action",
    "branch",
    "commit",
    "commit_sha",
    "diff_ref",
    "env_hash",
    "input_sha",
    "legacy_file",
    "message",
    "migration",
    "model_dir",
    "model_path",
    "model_sha",
    "op",
    "output_path",
    "output_sha",
    "plugin",
    "repo",
    "repo_id",
    "source",
    "target_operation",
    "target_sha",
    "title",
    "zip_output",
}

_RESULT_PARAMETER_KEYS = {
    "applied_count",
    "cell_type_result",
    "cell_type_source",
    "changed",
    "class_mismatch",
    "flag_score",
    "flagged",
    "group_count",
    "new_node_count",
    "nodes_total",
    "original_node_count",
    "out_type_counts",
    "passes",
    "predicted_f1",
    "radius_changes",
    "reduction_percent",
    "removed_nodes",
    "remapped_id_count",
    "report_summary",
    "result_count",
    "stage1_cell_type",
    "stage1_confidence",
    "tree_count",
    "tree_index",
    "type_changes",
}

_PARAMETER_LABELS = {
    "anchor_id": "Anchor Node ID",
    "apic": "Apical Labeling",
    "axon": "Axon Labeling",
    "basal": "Basal Labeling",
    "cell_type": "Cell Class",
    "clamp_max": "Maximum Radius",
    "clamp_min": "Minimum Radius",
    "end_id": "End Node ID",
    "epsilon": "Simplification Epsilon",
    "flag_budget": "Flag Review Budget",
    "flag_enabled": "Flagging Enabled",
    "flag_feature_mode": "Flag Feature Mode",
    "flag_strictness": "Flag Strictness",
    "inserted_node_id": "Inserted Node ID",
    "keep_bifurcations": "Keep Bifurcations",
    "keep_roots": "Keep Roots",
    "keep_tips": "Keep Tips",
    "labeler_version": "Labeler Version",
    "lower_abs": "Absolute Minimum Radius",
    "lower_percentile": "Lower Percentile",
    "max_passes": "Maximum Passes",
    "max_percent_deviation": "Maximum Local Deviation",
    "max_ratio": "Maximum Ratio to Neighbours",
    "min_effective_delta": "Minimum Effective Change",
    "min_radius": "Minimum Radius",
    "new_type": "New Type",
    "node_id": "Node ID",
    "node_ids": "Node IDs",
    "random_seed": "AI Seed",
    "rad": "Radius Cleaning",
    "radius": "Radius",
    "radius_tolerance": "Radius Tolerance",
    "reverted_from_operation": "Reverted From Operation",
    "reverted_from_version": "Reverted From Version",
    "restore_mode": "Restore Mode",
    "reconnect_children": "Reconnect Children",
    "root_id": "Root Node ID",
    "seed": "AI Seed",
    "selected_node_ids": "Selected Node IDs",
    "small_radius_zero_only": "Repair Zero Radii Only",
    "slack": "Taper Slack",
    "soma": "Soma Labeling",
    "start_id": "Start Node ID",
    "type_id": "Type ID",
    "upper_abs": "Absolute Maximum Radius",
    "upper_percentile": "Upper Percentile",
    "use_subtree_stage2": "Use Subtree Stage 2",
    "x": "X",
    "y": "Y",
    "z": "Z",
}

_MANUAL_PARAMETER_KEYS = {
    "new_type",
    "node_id",
    "node_ids",
    "radius",
    "root_id",
}

_GEOMETRY_PARAMETER_KEYS = {
    "anchor_id",
    "end_id",
    "inserted_node_id",
    "node_id",
    "radius",
    "reconnect_children",
    "root_id",
    "selected_node_ids",
    "start_id",
    "type_id",
    "x",
    "y",
    "z",
}

_AUTO_LABEL_PARAMETER_KEYS = {
    "cell_type",
    "flag_budget",
    "flag_enabled",
    "labeler_version",
    # Older history archives record these settings too.
    "flag_feature_mode",
    "flag_strictness",
    "use_subtree_stage2",
}

_SIMPLIFICATION_PARAMETER_KEYS = {
    "epsilon",
    "keep_bifurcations",
    "keep_roots",
    "keep_tips",
    "radius_tolerance",
}

_RADII_PARAMETER_KEYS = {
    "axon_floor",
    "clamp_max",
    "clamp_min",
    "gaussian_sigma_fraction",
    "lower_abs",
    "lower_percentile",
    "max_passes",
    "max_percent_deviation",
    "max_ratio",
    "min_effective_delta",
    "min_radius",
    "polyorder",
    "slack",
    "small_radius_zero_only",
    "upper_abs",
    "upper_percentile",
}

_AUTO_FIX_PARAMETER_KEYS = {
    "sanitize_invalid_types",
}


def operation_display_parameters(
    kind: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return human-facing operation settings with internals removed.

    Raw parameters remain untouched in the encrypted history archive.
    This helper is only for the normal GUI/CLI operation views.
    """
    kind_text = str(kind or "")
    raw = params if isinstance(params, dict) else {}
    flattened: dict[tuple[str, ...], Any] = {}

    # Configuration containers are displayed first. Later containers
    # represent more specific/effective values and replace duplicates.
    for container in _PARAMETER_CONTAINERS:
        value = raw.get(container)
        if isinstance(value, dict):
            _flatten_display_parameters(value, (), flattened)

    direct = {
        key: value
        for key, value in raw.items()
        if key not in _PARAMETER_CONTAINERS
    }
    _flatten_display_parameters(direct, (), flattened)

    displayed: dict[str, Any] = {}
    for path, value in flattened.items():
        if (
            not path
            or _hide_parameter(path, value)
            or not _important_parameter(kind_text, path)
        ):
            continue
        label = _parameter_label(path)
        displayed[label] = _parameter_value(value)
    return displayed


def _flatten_display_parameters(
    values: dict[str, Any],
    prefix: tuple[str, ...],
    out: dict[tuple[str, ...], Any],
) -> None:
    for raw_key, value in values.items():
        key = str(raw_key or "").strip().lower()
        if not key:
            continue
        path = (*prefix, key)
        if isinstance(value, dict):
            _flatten_display_parameters(value, path, out)
        else:
            out[path] = value


def _hide_parameter(path: tuple[str, ...], value: Any) -> bool:
    leaf = path[-1]
    if any(part in _HIDDEN_PARAMETER_KEYS for part in path):
        return True
    if any(part in _RESULT_PARAMETER_KEYS for part in path):
        return True
    if any(
        token in part
        for part in path
        for token in ("sha", "checksum", "hash")
    ):
        return True
    if value is None or value == "" or value == {} or value == []:
        return True
    if isinstance(value, str) and value.lower().startswith("sha256:"):
        return True
    return False


def _important_parameter(kind: str, path: tuple[str, ...]) -> bool:
    leaf = path[-1]
    if leaf in {
        "reverted_from_operation",
        "reverted_from_version",
        "restore_mode",
    }:
        return True
    if leaf in {"seed", "random_seed"} or "threshold" in leaf:
        return True
    if kind == OpKind.AUTO_LABEL.value:
        return leaf in _AUTO_LABEL_PARAMETER_KEYS
    if kind in {OpKind.SET_TYPE.value, OpKind.DENDROGRAM_EDIT.value}:
        return leaf in _MANUAL_PARAMETER_KEYS
    if kind in {OpKind.SET_RADIUS.value, OpKind.MANUAL_RADII.value}:
        return leaf in _MANUAL_PARAMETER_KEYS
    if kind == OpKind.GEOMETRY_EDIT.value:
        return leaf in _GEOMETRY_PARAMETER_KEYS
    if kind == OpKind.SIMPLIFICATION.value:
        return leaf in _SIMPLIFICATION_PARAMETER_KEYS
    if kind == OpKind.RADII_CLEAN.value:
        return "per_type" not in path and leaf in _RADII_PARAMETER_KEYS
    if kind == OpKind.AUTO_FIX.value:
        return leaf in _AUTO_FIX_PARAMETER_KEYS
    return False


def _parameter_label(path: tuple[str, ...]) -> str:
    leaf = path[-1]
    if leaf in _PARAMETER_LABELS:
        return _PARAMETER_LABELS[leaf]
    display_path = tuple(
        part for part in path
        if part not in {"parameters", "thresholds"}
    ) or (leaf,)
    words = " ".join(part.replace("_", " ") for part in display_path)
    label = words.title()
    if leaf.endswith("_threshold"):
        label = label.replace(" Threshold", "") + " Threshold"
    return label


def _parameter_value(value: Any) -> Any:
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, float):
        return f"{value:.6g}"
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        if len(items) <= 8:
            return ", ".join(str(item) for item in items)
        return f"{len(items)} values"
    return value


# ---------------------------------------------------------------------
# minimal record validation (shape only; semantic checks live elsewhere)
# ---------------------------------------------------------------------


_VALID_KINDS: frozenset[str] = frozenset(k.value for k in OpKind)


def validate_op_record(op: dict[str, Any]) -> None:
    """Sanity-check the shape of an op dict before it's appended.

    Raises :class:`ValueError` on any problem. Specifically checks:

    * ``kind`` is one of the v1 OpKind values.
    * ``params`` is a dict (may be empty) — tracked_op stores op
      parameters here for later inspection / replay.
    * ``summary`` is a dict — small diff counts (nodes_added/modified/
      removed/etc.) for fast timeline display per spec §6 / M6.
    * If ``ai_run_ref`` is present, kind must be an AI op.

    What we do **not** check here: whether the params are semantically
    valid for that op kind. That's the implementation module's job.
    """
    if not isinstance(op, dict):
        raise ValueError(f"op must be a dict, got {type(op).__name__}")
    kind = op.get("kind")
    if kind not in _VALID_KINDS:
        raise ValueError(f"unknown op kind: {kind!r} (valid: {sorted(_VALID_KINDS)})")
    if "params" in op and not isinstance(op["params"], dict):
        raise ValueError(f"op.params must be a dict if present, got {type(op['params']).__name__}")
    if "summary" in op and not isinstance(op["summary"], dict):
        raise ValueError(f"op.summary must be a dict if present, got {type(op['summary']).__name__}")
    if "ai_run_ref" in op:
        if not is_ai_op(str(kind)):
            raise ValueError(f"ai_run_ref present on non-AI op kind {kind!r}")
        ref = op["ai_run_ref"]
        if not (isinstance(ref, str) and ref.startswith("sha256:")):
            raise ValueError(f"ai_run_ref must be a sha256:... string, got {ref!r}")
