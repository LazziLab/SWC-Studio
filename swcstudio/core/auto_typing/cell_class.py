"""The two cell classes the labeler knows, as SWC-Studio names them.

The labeler has one model set per cell class. SWC-Studio names a class by the
neurite labels it assigns rather than by a cell type, because the class fits any
cell with that compartment layout:

* **3-class** -- axon, basal dendrite and apical dendrite. The labeler calls it
  ``pyramidal``, the cells its models were trained on.
* **2-class** -- axon and dendrite. The labeler calls it ``interneuron``.

Internally, in saved settings and in result records, the labeler's names are
kept; these helpers translate at the user-facing edges.
"""
from __future__ import annotations

# Internal name -> user-facing name.
CELL_CLASS_NAMES = {"pyramidal": "3-class", "interneuron": "2-class"}
CELL_CLASS_DESCRIPTIONS = {
    "pyramidal": "3-class (axon, basal, apical)",
    "interneuron": "2-class (axon, dendrite)",
}

_AUTO = {"", "auto", "unknown", "stage1", "none"}
_ALIASES = {
    "pyramidal": "pyramidal", "3class": "pyramidal", "3-class": "pyramidal", "3": "pyramidal",
    "interneuron": "interneuron", "2class": "interneuron", "2-class": "interneuron", "2": "interneuron",
}


def normalize_cell_class(raw: str | None) -> str | None:
    """The labeler's name for a cell class, or ``None`` for automatic (Stage 1 decides).

    Accepts the user-facing names (``3-class``, ``2-class``, ``auto``), the
    labeler's names (``pyramidal``, ``interneuron``) and the old ``unknown``.
    """
    value = str(raw or "").strip().lower().replace("_", "-").replace(" ", "")
    if value in _AUTO:
        return None
    if value in _ALIASES:
        return _ALIASES[value]
    raise ValueError("cell class must be one of: auto, 3-class, 2-class")


def cell_class_name(cell_type: str | None) -> str:
    """User-facing name of a cell class: ``3-class``, ``2-class`` or ``auto``."""
    return CELL_CLASS_NAMES.get(str(cell_type or ""), "auto")


__all__ = ["CELL_CLASS_NAMES", "CELL_CLASS_DESCRIPTIONS", "normalize_cell_class", "cell_class_name"]
