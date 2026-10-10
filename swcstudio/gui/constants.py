from swcstudio.core.custom_types import get_custom_type_definition, label_for_type as core_label_for_type, TYPE_LABELS

APP_TITLE = "SWC Tools – Dendrogram Editor"

SWC_COLS = ["id", "type", "x", "y", "z", "radius", "parent"]
TYPE_LABEL = dict(TYPE_LABELS)

DEFAULT_COLORS = {
    "undefined": "#636363",
    "soma": "#2ca02c",
    "axon": "#1a6ca7",
    "basal dendrite": "#ff0000",
    "apical dendrite": "#e377c2",
    "custom": "#ff7f0e",  # for types >= 5
}

# Distinct colors for identifying trees (up to 10)
TREE_COLORS = [
    "#5e353f", "#878714", "#6684f0", "#f58231", "#911eb4",
    "#00d0ff", "#e032f0", "#bfef45", "#fabed4", "#471212",
]

def label_for_type(t: int) -> str:
    return core_label_for_type(int(t))

def color_for_type(t: int) -> str:
    t = int(t)
    if t <= 4:
        return DEFAULT_COLORS.get(label_for_type(t), DEFAULT_COLORS["custom"])
    definition = get_custom_type_definition(t)
    if definition and str(definition.get("color", "")).strip():
        return str(definition["color"]).strip()
    return DEFAULT_COLORS["custom"]

def type_legend_entries(types) -> list[tuple[int, str, str]]:
    """Return ``(type id, label, colour)`` for each distinct type in ``types``.

    Soma, axon, basal and apical come first, then custom types, then undefined
    (0) and invalid negative types. Labels and colours come from
    ``label_for_type`` / ``color_for_type``, the helpers the views draw with,
    so a legend built from this cannot disagree with the drawing.
    """
    present: set[int] = set()
    for t in types:
        try:
            present.add(int(t))
        except (TypeError, ValueError):
            continue
    ordered = sorted(present, key=lambda t: (t <= 0, abs(t)))
    return [(t, label_for_type(t), color_for_type(t)) for t in ordered]
