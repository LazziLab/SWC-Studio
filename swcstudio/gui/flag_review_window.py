"""Flag Review: a batch's labeled cells ranked for curator review.

A batch run writes ``*_flag_review_*.json`` (and a CSV copy) with one record per
file: its cell class, the quality flag's predicted per-cell F1, its rank within
its class, and whether it is flagged. This panel lists those records riskiest
first. The share of each class flagged can be changed here; double-clicking a
cell opens it for review, and the review marks (OK, corrected) are saved back
into the same file so a review can be resumed later.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from swcstudio.core.auto_typing import apply_flag_budget, cell_class_name, write_flag_review

COLUMNS = ("Flag", "File", "Class", "Predicted F1", "Rank", "Reason", "Stage 1", "Nodes",
           "Changes", "Review")
FILTERS = (
    ("All cells", "all"),
    ("Flagged", "flagged"),
    ("3-class", "pyramidal"),
    ("2-class", "interneuron"),
    ("Class mismatch", "mismatch"),
    ("Not reviewed", "unreviewed"),
    ("Not labeled", "unlabeled"),
)
REVIEW_TEXT = {"unreviewed": "", "ok": "OK", "corrected": "Corrected"}
_FLAGGED_BG = QColor(253, 226, 226)
_MISMATCH_BG = QColor(255, 237, 213)
_UNLABELED_FG = QColor(140, 140, 140)


class _SortItem(QTableWidgetItem):
    """Table item that sorts by a key kept in ``Qt.UserRole``, not by its text."""

    def __lt__(self, other: QTableWidgetItem) -> bool:  # noqa: D401
        a, b = self.data(Qt.UserRole), other.data(Qt.UserRole)
        if a is None or b is None:
            return super().__lt__(other)
        return a < b


def _risk_key(rec: dict[str, Any]) -> tuple:
    """Riskiest first: flagged, then class mismatches, then lowest predicted F1;
    files that were not labeled last."""
    if not rec.get("labeled"):
        return (3, 0.0, rec.get("file", ""))
    f1 = rec.get("predicted_f1")
    return (0 if rec.get("flagged") else 1, 0 if rec.get("class_mismatch") else 1,
            float(f1) if f1 is not None else 2.0, rec.get("file", ""))


def reason_text(rec: dict[str, Any], share: float) -> str:
    """Short reason for one record, as shown in the table."""
    if not rec.get("labeled"):
        if rec.get("qc_reasons"):
            return "not labeled: " + "; ".join(rec["qc_reasons"])
        return "not labeled: " + str(rec.get("error") or "unknown error")
    reason = rec.get("flag_reason")
    if reason == "riskiest_share":
        return f"riskiest {share:.0%} of its class"
    if reason == "below_cutoff":
        return "predicted F1 below the flag's cut-off"
    if reason == "class_mismatch":
        return f"class mismatch: Stage 1 predicts {cell_class_name(rec.get('stage1_cell_type'))}"
    return ""


class FlagReviewPanel(QWidget):
    """The Flag Review window's content; the main window hosts it in a dock."""

    open_file_requested = Signal(str)
    log_message = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._path: Path | None = None
        self._payload: dict[str, Any] = {}
        self._records: list[dict[str, Any]] = []
        self._build_ui()
        self._refresh()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        top = QHBoxLayout()
        self._title = QLabel("No flag review loaded.")
        self._title.setStyleSheet("font-size: 12px; font-weight: 600; color: #222;")
        self._title.setWordWrap(True)
        top.addWidget(self._title, stretch=1)
        self._btn_open_review = QPushButton("Open Review...")
        self._btn_open_review.setToolTip("Open a *_flag_review_*.json written by a batch run.")
        self._btn_open_review.clicked.connect(self._on_open_review)
        top.addWidget(self._btn_open_review)
        self._btn_export = QPushButton("Export CSV...")
        self._btn_export.clicked.connect(self._on_export_csv)
        top.addWidget(self._btn_export)
        root.addLayout(top)

        controls = QHBoxLayout()
        controls.setSpacing(6)
        controls.addWidget(QLabel("Flag the riskiest"))
        self._share = QSpinBox()
        self._share.setRange(1, 50)
        self._share.setSuffix(" %")
        self._share.setValue(10)
        self._share.setToolTip(
            "Share of each cell class flagged, ranked by predicted per-cell F1.\n"
            "10% is the share the labeler's flag was evaluated at. A class with too\n"
            "few cells for that share uses the flag's fixed cut-off instead.")
        self._share.valueChanged.connect(self._on_share_changed)
        controls.addWidget(self._share)
        controls.addWidget(QLabel("of each cell class"))
        controls.addSpacing(16)
        controls.addWidget(QLabel("Show:"))
        self._filter = QComboBox()
        for text, value in FILTERS:
            self._filter.addItem(text, value)
        self._filter.currentIndexChanged.connect(lambda _i: self._fill_table())
        controls.addWidget(self._filter)
        controls.addStretch()
        root.addLayout(controls)

        self._summary = QLabel("")
        self._summary.setStyleSheet("font-size: 11px; color: #555;")
        self._summary.setWordWrap(True)
        root.addWidget(self._summary)

        self._table = QTableWidget(0, len(COLUMNS))
        self._table.setHorizontalHeaderLabels(COLUMNS)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(False)
        self._table.verticalHeader().setVisible(False)
        header = self._table.horizontalHeader()
        # The Flag column sorts by overall risk, so its ascending order is the
        # riskiest-first list; clicking another header sorts by that column.
        header.setSortIndicator(0, Qt.AscendingOrder)
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(5, QHeaderView.Stretch)
        self._table.cellDoubleClicked.connect(lambda row, _col: self._open_row(row))
        root.addWidget(self._table, stretch=1)

        actions = QHBoxLayout()
        self._btn_open_cell = QPushButton("Open Cell")
        self._btn_open_cell.setToolTip("Open the selected cell to review its labels (or double-click a row).")
        self._btn_open_cell.clicked.connect(self._on_open_selected)
        actions.addWidget(self._btn_open_cell)
        self._btn_mark_ok = QPushButton("Mark OK")
        self._btn_mark_ok.setToolTip("The labels were checked and are right.")
        self._btn_mark_ok.clicked.connect(lambda: self._mark_selected("ok"))
        actions.addWidget(self._btn_mark_ok)
        self._btn_mark_corrected = QPushButton("Mark Corrected")
        self._btn_mark_corrected.setToolTip("The labels were checked and fixed by hand.")
        self._btn_mark_corrected.clicked.connect(lambda: self._mark_selected("corrected"))
        actions.addWidget(self._btn_mark_corrected)
        self._btn_clear = QPushButton("Clear Mark")
        self._btn_clear.clicked.connect(lambda: self._mark_selected("unreviewed"))
        actions.addWidget(self._btn_clear)
        actions.addStretch()
        self._file_label = QLabel("")
        self._file_label.setStyleSheet("font-size: 11px; color: #888;")
        actions.addWidget(self._file_label)
        root.addLayout(actions)

    # ------------------------------------------------------------- loading
    def load(self, path: str | Path) -> bool:
        """Load a flag review file. Returns False (and logs why) if it cannot be read."""
        p = Path(path)
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
            if payload.get("kind") != "swcstudio_flag_review":
                raise ValueError("not a SWC-Studio flag review file")
            records = list(payload.get("records") or [])
        except Exception as exc:  # noqa: BLE001
            self.log_message.emit(f"Could not open flag review {p}: {exc}")
            return False
        self._path, self._payload, self._records = p, payload, records
        share = float(payload.get("flag_budget") or 0.10)
        self._share.blockSignals(True)
        self._share.setValue(int(round(min(max(share, 0.01), 0.5) * 100)))
        self._share.blockSignals(False)
        self._refresh()
        self.log_message.emit(f"Opened flag review: {p}")
        return True

    def current_path(self) -> str | None:
        return str(self._path) if self._path is not None else None

    def _on_open_review(self) -> None:
        start = str(self._path.parent) if self._path is not None else ""
        path, _ = QFileDialog.getOpenFileName(self, "Open flag review", start,
                                              "Flag review (*flag_review*.json);;JSON (*.json)")
        if path:
            self.load(path)

    # ------------------------------------------------------------ display
    def _refresh(self) -> None:
        loaded = self._path is not None
        for w in (self._btn_export, self._share, self._filter, self._btn_open_cell,
                  self._btn_mark_ok, self._btn_mark_corrected, self._btn_clear):
            w.setEnabled(loaded)
        if not loaded:
            self._title.setText("No flag review loaded. Run Auto Label Editing on a folder "
                                "(Batch Processing), or open a saved review.")
            self._summary.setText("")
            self._file_label.setText("")
            self._table.setRowCount(0)
            return
        folder = self._payload.get("folder") or str(self._path.parent)
        self._title.setText(f"Flag review of {folder}  ·  {self._payload.get('timestamp', '')}  ·  "
                            f"labeler {self._payload.get('labeler_version', '?')}")
        self._file_label.setText(str(self._path))
        self._fill_table()

    def _visible(self, rec: dict[str, Any]) -> bool:
        mode = self._filter.currentData()
        if mode == "flagged":
            return bool(rec.get("flagged"))
        if mode in ("pyramidal", "interneuron"):
            return rec.get("cell_type") == mode
        if mode == "mismatch":
            return bool(rec.get("class_mismatch"))
        if mode == "unreviewed":
            return bool(rec.get("labeled")) and rec.get("review_status", "unreviewed") == "unreviewed"
        if mode == "unlabeled":
            return not rec.get("labeled")
        return True

    def _fill_table(self) -> None:
        share = self._share.value() / 100.0
        rows = sorted(self._records, key=_risk_key)
        shown = [r for r in rows if self._visible(r)]
        index_of = {id(r): i for i, r in enumerate(self._records)}
        header = self._table.horizontalHeader()
        sort_section, sort_order = header.sortIndicatorSection(), header.sortIndicatorOrder()
        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(shown))
        for i, rec in enumerate(shown):
            idx = index_of[id(rec)]
            f1 = rec.get("predicted_f1")
            stage1 = (f"{cell_class_name(rec.get('stage1_cell_type'))} ({float(rec['stage1_confidence']):.2f})"
                      if rec.get("stage1_confidence") is not None else "")
            rank = (f"{rec['rank_in_class']} / {rec['class_size']}"
                    if rec.get("rank_in_class") is not None else "")
            values = [
                ("●" if rec.get("flagged") else "", i),  # i: position in the riskiest-first order
                (rec.get("file", ""), rec.get("file", "").lower()),
                (cell_class_name(rec.get("cell_type")) if rec.get("labeled") else "", rec.get("cell_type") or "~"),
                (f"{float(f1):.3f}" if f1 is not None else "", float(f1) if f1 is not None else 9.0),
                (rank, float(rec.get("rank_in_class") or 1e9)),
                (reason_text(rec, share), reason_text(rec, share)),
                (stage1, stage1),
                (str(rec.get("nodes") or ""), int(rec.get("nodes") or 0)),
                (str(rec.get("type_changes") or 0) if rec.get("labeled") else "", int(rec.get("type_changes") or 0)),
                (REVIEW_TEXT.get(rec.get("review_status", "unreviewed"), ""), rec.get("review_status", "")),
            ]
            for col, (text, key) in enumerate(values):
                item = _SortItem(text)
                item.setData(Qt.UserRole, key)
                item.setData(Qt.UserRole + 1, idx)
                if col == 0:
                    item.setForeground(QBrush(QColor(200, 40, 40)))
                    item.setTextAlignment(Qt.AlignCenter)
                if not rec.get("labeled"):
                    item.setForeground(QBrush(_UNLABELED_FG))
                elif rec.get("class_mismatch"):
                    item.setBackground(QBrush(_MISMATCH_BG))
                elif rec.get("flagged"):
                    item.setBackground(QBrush(_FLAGGED_BG))
                self._table.setItem(i, col, item)
        self._table.setSortingEnabled(True)
        self._table.sortItems(sort_section, sort_order)
        self._update_summary()

    def _update_summary(self) -> None:
        labeled = [r for r in self._records if r.get("labeled")]
        flagged = [r for r in labeled if r.get("flagged")]
        by_class = {ct: sum(1 for r in flagged if r.get("cell_type") == ct) for ct in ("pyramidal", "interneuron")}
        mismatch = sum(1 for r in labeled if r.get("class_mismatch"))
        reviewed = sum(1 for r in labeled if r.get("review_status", "unreviewed") != "unreviewed")
        unlabeled = len(self._records) - len(labeled)
        parts = [
            f"{len(labeled)} labeled cells",
            f"{len(flagged)} flagged ({by_class['pyramidal']} 3-class, {by_class['interneuron']} 2-class)",
            f"{mismatch} class mismatch{'es' if mismatch != 1 else ''}",
            f"{reviewed} reviewed",
        ]
        if unlabeled:
            parts.append(f"{unlabeled} not labeled")
        self._summary.setText("  ·  ".join(parts))

    # ------------------------------------------------------------ actions
    def _record_at(self, row: int) -> dict[str, Any] | None:
        item = self._table.item(row, 0)
        if item is None:
            return None
        idx = item.data(Qt.UserRole + 1)
        return self._records[int(idx)] if idx is not None else None

    def _selected_records(self) -> list[dict[str, Any]]:
        rows = sorted({ix.row() for ix in self._table.selectionModel().selectedRows()})
        return [rec for rec in (self._record_at(r) for r in rows) if rec is not None]

    def _open_row(self, row: int) -> None:
        rec = self._record_at(row)
        if rec is None:
            return
        for key in ("output_path", "input_path"):
            path = rec.get(key)
            if path and Path(path).is_file():
                self.open_file_requested.emit(str(path))
                return
        self.log_message.emit(f"Flag review: cannot find the file for {rec.get('file')}.")

    def _on_open_selected(self) -> None:
        rows = sorted({ix.row() for ix in self._table.selectionModel().selectedRows()})
        if rows:
            self._open_row(rows[0])

    def _mark_selected(self, status: str) -> None:
        recs = [r for r in self._selected_records() if r.get("labeled")]
        if not recs:
            return
        for rec in recs:
            rec["review_status"] = status
        self._save()
        self._fill_table()

    def _on_share_changed(self, value: int) -> None:
        if not self._records:
            return
        apply_flag_budget(self._records, value / 100.0)
        self._save()
        self._fill_table()

    def _save(self) -> None:
        if self._path is None:
            return
        try:
            write_flag_review(self._path, self._records, budget=self._share.value() / 100.0,
                              folder=str(self._payload.get("folder", "")),
                              timestamp=str(self._payload.get("timestamp", "")),
                              labeler_version=self._payload.get("labeler_version"))
        except Exception as exc:  # noqa: BLE001
            self.log_message.emit(f"Could not save flag review {self._path}: {exc}")

    def _on_export_csv(self) -> None:
        if self._path is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export flag review", str(self._path.with_suffix(".csv")),
                                              "CSV (*.csv)")
        if not path:
            return
        try:
            self._save()
            src = self._path.with_suffix(".csv")
            if Path(path).resolve() != src.resolve():
                Path(path).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            self.log_message.emit(f"Exported flag review to {path}")
        except Exception as exc:  # noqa: BLE001
            self.log_message.emit(f"Could not export flag review: {exc}")


__all__ = ["FlagReviewPanel", "reason_text"]
