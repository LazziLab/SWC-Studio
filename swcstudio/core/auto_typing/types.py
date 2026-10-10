"""Public dataclasses for the auto-typing engine.

These describe the user-facing options (which neurite types to assign, the
cell class, the quality flag, radius cleanup, zipping the batch output) and the
result shapes returned by :func:`run_file` / :func:`run_batch`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class BatchOptions:
    # Which predicted labels to write. A label type switched off keeps the
    # file's original type on those nodes.
    soma: bool = True
    axon: bool = True
    apic: bool = True
    basal: bool = True
    rad: bool = False
    zip_output: bool = False
    # Cell class: None (automatic, Stage 1 decides), "3-class" / "pyramidal",
    # or "2-class" / "interneuron". See ``cell_class``.
    cell_type: str | None = None
    flag_enabled: bool = True
    # In a batch, the share of each cell class flagged for review, riskiest first.
    flag_budget: float = 0.10


@dataclass
class BatchResult:
    folder: str
    out_dir: str | None
    zip_path: str | None
    files_total: int
    files_processed: int
    files_failed: int
    total_nodes: int
    total_type_changes: int
    total_radius_changes: int
    failures: list[str]
    per_file: list[str]
    log_path: str | None
    files_flagged: int = 0
    files_qc_failed: int = 0
    commits: list[dict[str, Any]] | None = None
    # One record per labeled file, for the Flag Review window (see ``flag_review``).
    flag_records: list[dict[str, Any]] | None = None
    flag_review_path: str | None = None
    flag_budget: float | None = None


@dataclass
class FileResult:
    input_file: str
    output_file: str | None
    nodes_total: int
    type_changes: int
    radius_changes: int
    out_type_counts: dict[int, int]
    cell_type: str | None
    cell_type_source: str
    stage1_confidence: float | None
    qc_result: dict[str, Any] | None
    flag_result: dict[str, Any] | None
    failures: list[str]
    change_details: list[str]
    log_path: str | None
    headers: list[str]
    rows: list[dict[str, Any]]
    types: list[int]
    radii: list[float]
    # The cell class Stage 1 predicts, even when the user chose one.
    stage1_cell_type: str | None = None
    # True when the user's cell class differs from Stage 1's prediction.
    class_mismatch: bool = False
    # What the labeler assumed about an unlabeled input file.
    notes: list[str] = field(default_factory=list)
    labeler_version: str | None = None


__all__ = ["BatchOptions", "BatchResult", "FileResult"]
