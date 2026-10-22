"""Auto-typing engine for swcstudio.

This package is the single auto-labeling backend used everywhere in swcstudio.
It runs the SWC auto-labeler (``swcstudio.core.labeler``, a copy of the released
labeler's code, with its model set ``labeler-<version>``), which labels every
node of an SWC file as soma, axon, basal dendrite or apical dendrite:

* Stage 0: structural checks (one connected cell, a soma, finite geometry).
* Stage 1: the cell class, 3-class (axon, basal, apical) or 2-class (axon,
  dendrite), unless the user chooses it.
* Stage 2: a gradient-boosted classifier labels each primary subtree.
* Stage 3: topology rules, and for 3-class cells a learned correction of the
  branches near the soma.
* Stage 4: a quality flag per cell class that predicts each cell's per-cell F1,
  so a curator can review the riskiest cells first.

End users reach this code through the CLI (``swcstudio auto-label``,
``swcstudio auto-typing``) or the GUI's Auto Label Editing panels and the Flag
Review window. The model folder is resolved by :mod:`swcstudio.core.model_paths`;
name one with ``--model-dir`` or the GUI selector, ``SWCSTUDIO_MODEL_DIR``, or
``model_dir`` in the auto-typing configuration, in that order of precedence.
"""
from __future__ import annotations

import warnings as _warnings

# Silence sklearn's InconsistentVersionWarning for the bundled pickles. The
# package pins ``scikit-learn>=1.5,<1.6``, so any mismatch with the trained-on
# version is patch-level only, which sklearn keeps wire-compatible; sklearn would
# otherwise warn once per estimator inside each bundle.
try:
    from sklearn.exceptions import InconsistentVersionWarning as _InconsistentVersionWarning  # noqa: PLC0415
    _warnings.filterwarnings("ignore", category=_InconsistentVersionWarning)
except ImportError:  # pragma: no cover - sklearn is a required dep
    pass

from .cell_class import CELL_CLASS_DESCRIPTIONS, CELL_CLASS_NAMES, cell_class_name, normalize_cell_class
from .config import DEFAULT_CONFIG, get_config, save_config
from .runner import (
    FLAG_REVIEW_CSV,
    FLAG_REVIEW_JSON,
    apply_flag_budget,
    backend_status,
    is_available,
    labeled_text,
    resolve_models,
    run_batch,
    run_file,
    write_flag_review,
)
from .types import BatchOptions, BatchResult, FileResult


def run_folder(folder: str, opts: BatchOptions | None = None) -> BatchResult:
    """Convenience: run the engine on a folder with default options."""
    return run_batch(folder, opts if opts is not None else BatchOptions())


__all__ = [
    "BatchOptions",
    "BatchResult",
    "FileResult",
    "CELL_CLASS_NAMES",
    "CELL_CLASS_DESCRIPTIONS",
    "cell_class_name",
    "normalize_cell_class",
    "DEFAULT_CONFIG",
    "get_config",
    "save_config",
    "is_available",
    "resolve_models",
    "backend_status",
    "labeled_text",
    "run_file",
    "run_batch",
    "run_folder",
    "apply_flag_budget",
    "write_flag_review",
    "FLAG_REVIEW_JSON",
    "FLAG_REVIEW_CSV",
]
