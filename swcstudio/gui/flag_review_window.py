"""Flag Review: a batch's labeled cells ranked for curator review.

A batch run writes ``*_flag_review_*.json`` (and a CSV copy) with one record per
file: its cell class, the quality flag's predicted per-cell F1, its rank within
its class, and whether it is flagged. This panel lists those records riskiest
first. The share of each class flagged can be changed here; double-clicking a
cell (or Open Cell) opens the selected cells for review, Previous / Next step
through the list one cell at a time, and the review marks (OK, corrected) are
saved back into the same file so a review can be resumed later.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import QItemSelectionModel, Qt, Signal
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

COLUMNS = ("Flag", "File", "Class", "Predicted F1", "Rank", "Reason", "Warnings", "Stage 1",
           "Nodes", "Changes", "Review")
_WARNINGS_COL = COLUMNS.index("Warnings")
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


def warnings_text(rec: dict[str, Any]) -> str:
    """A record's warnings (e.g. "no axon labeled") as one line; "" if it has none.

    Reviews written before warnings were recorded have no ``warnings`` key.
    """
    warnings = rec.get("warnings")
    if not warnings:
        return ""
    if isinstance(warnings, str):
        return warnings.strip()
    try:
        return "; ".join(str(w).strip() for w in warnings if str(w).strip())
    except TypeError:
        return str(warnings)


class FlagReviewPanel(QWidget):
    """The Flag Review window's content; the main window hosts it in a dock."""

    open_files_requested = Signal(list)  # list[str]: SWC paths to open, one tab each
    log_message = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._path: Path | None = None
        self._payload: dict[str, Any] = {}
        self._records: list[dict[str, Any]] = []
        # Indices (into _records) of the cells last selected. Previous/Next
        # continue from them when nothing is selected any more, e.g. after
        # Mark OK took the cell out of the "Not reviewed" list.
        self._cursor: list[int] = []
        self._filling = False
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
        self._table.cellDoubleClicked.connect(lambda row, _col: self._on_row_double_clicked(row))
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        root.addWidget(self._table, stretch=1)

        actions = QHBoxLayout()
        self._btn_previous = QPushButton("Previous")
        self._btn_previous.setToolTip("Select and open the cell before the current one in the list.")
        self._btn_previous.clicked.connect(lambda: self.step(-1))
        actions.addWidget(self._btn_previous)
        self._btn_next = QPushButton("Next")
        self._btn_next.setToolTip("Select and open the cell after the current one in the list "
                                  "(the next riskiest).")
        self._btn_next.clicked.connect(lambda: self.step(1))
        actions.addWidget(self._btn_next)
        self._btn_open_cell = QPushButton("Open Cell")
        self._btn_open_cell.setToolTip("Open the selected cells, each in its own tab, to review their labels "
                                       "(or double-click a row).")
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
        # A new review starts at its riskiest cell: forget the previous
        # review's selection and position (its rows point at other records).
        self._filling = True
        try:
            self._table.clearSelection()
        finally:
            self._filling = False
        self._cursor = []
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
            self._update_step_buttons()
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

    def _risk_order(self) -> list[int]:
        """Indices of all records, riskiest first."""
        return sorted(range(len(self._records)), key=lambda i: _risk_key(self._records[i]))

    @staticmethod
    def _row_values(rec: dict[str, Any], risk_pos: int, share: float) -> list[tuple[str, Any]]:
        """(text, sort key) of each column for one record."""
        f1 = rec.get("predicted_f1")
        stage1 = (f"{cell_class_name(rec.get('stage1_cell_type'))} ({float(rec['stage1_confidence']):.2f})"
                  if rec.get("stage1_confidence") is not None else "")
        rank = (f"{rec['rank_in_class']} / {rec['class_size']}"
                if rec.get("rank_in_class") is not None else "")
        warnings = warnings_text(rec)
        return [
            ("●" if rec.get("flagged") else "", risk_pos),  # position in the riskiest-first order
            (rec.get("file", ""), rec.get("file", "").lower()),
            (cell_class_name(rec.get("cell_type")) if rec.get("labeled") else "", rec.get("cell_type") or "~"),
            (f"{float(f1):.3f}" if f1 is not None else "", float(f1) if f1 is not None else 9.0),
            (rank, float(rec.get("rank_in_class") or 1e9)),
            (reason_text(rec, share), reason_text(rec, share)),
            (warnings, warnings or "~"),  # "~": rows without warnings sort last
            (stage1, stage1),
            (str(rec.get("nodes") or ""), int(rec.get("nodes") or 0)),
            (str(rec.get("type_changes") or 0) if rec.get("labeled") else "", int(rec.get("type_changes") or 0)),
            (REVIEW_TEXT.get(rec.get("review_status", "unreviewed"), ""), rec.get("review_status", "")),
        ]

    def _fill_table(self) -> None:
        share = self._share.value() / 100.0
        # Rows are rebuilt from scratch, so remember the selected cells (not
        # row numbers: a marked cell can leave the list and shift the rows).
        selected = set(self._selected_record_indices())
        order = self._risk_order()
        shown = [i for i in order if self._visible(self._records[i])]
        risk_pos = {idx: pos for pos, idx in enumerate(order)}
        header = self._table.horizontalHeader()
        sort_section, sort_order = header.sortIndicatorSection(), header.sortIndicatorOrder()
        self._filling = True
        try:
            self._table.clearSelection()
            self._table.setSortingEnabled(False)
            self._table.setRowCount(len(shown))
            for i, idx in enumerate(shown):
                self._fill_row(i, idx, self._row_values(self._records[idx], risk_pos[idx], share))
            self._table.setSortingEnabled(True)
            self._table.sortItems(sort_section, sort_order)
            self._reselect(selected)
        finally:
            self._filling = False
        # Reviews written before warnings were recorded never carry any: hide
        # the column for them rather than show it empty.
        self._table.setColumnHidden(_WARNINGS_COL, not any(warnings_text(r) for r in self._records))
        self._update_summary()
        self._update_step_buttons()

    def _reselect(self, record_indices: set[int]) -> None:
        model, selection = self._table.model(), self._table.selectionModel()
        first = None
        for row in range(self._table.rowCount()):
            if self._record_index_at(row) in record_indices:
                selection.select(model.index(row, 0), QItemSelectionModel.Select | QItemSelectionModel.Rows)
                if first is None:
                    first = row
        if first is not None:
            selection.setCurrentIndex(model.index(first, 0), QItemSelectionModel.NoUpdate)

    def _fill_row(self, i: int, idx: int, values: list[tuple[str, Any]]) -> None:
        rec = self._records[idx]
        for col, (text, key) in enumerate(values):
            item = _SortItem(text)
            item.setData(Qt.UserRole, key)
            item.setData(Qt.UserRole + 1, idx)
            if col == 0:
                item.setForeground(QBrush(QColor(200, 40, 40)))
                item.setTextAlignment(Qt.AlignCenter)
            if col == _WARNINGS_COL and text:
                item.setToolTip(text)
            if not rec.get("labeled"):
                item.setForeground(QBrush(_UNLABELED_FG))
            elif rec.get("class_mismatch"):
                item.setBackground(QBrush(_MISMATCH_BG))
            elif rec.get("flagged"):
                item.setBackground(QBrush(_FLAGGED_BG))
            self._table.setItem(i, col, item)

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
    def _record_index_at(self, row: int) -> int | None:
        item = self._table.item(row, 0)
        if item is None:
            return None
        idx = item.data(Qt.UserRole + 1)
        if idx is None or not 0 <= int(idx) < len(self._records):
            return None
        return int(idx)

    def _record_at(self, row: int) -> dict[str, Any] | None:
        idx = self._record_index_at(row)
        return self._records[idx] if idx is not None else None

    def _row_of_record(self, idx: int) -> int | None:
        for row in range(self._table.rowCount()):
            if self._record_index_at(row) == idx:
                return row
        return None

    def _selected_rows(self) -> list[int]:
        return sorted({ix.row() for ix in self._table.selectionModel().selectedRows()})

    def _selected_record_indices(self) -> list[int]:
        """Selected records, in table order."""
        return [idx for idx in (self._record_index_at(r) for r in self._selected_rows()) if idx is not None]

    def _selected_records(self) -> list[dict[str, Any]]:
        return [self._records[idx] for idx in self._selected_record_indices()]

    def _on_selection_changed(self) -> None:
        if self._filling:
            return
        selected = self._selected_record_indices()
        if selected:
            self._cursor = selected
        self._update_step_buttons()

    def _candidate_paths(self, value: str) -> list[Path]:
        """Where the file recorded as ``value`` may be.

        Absolute paths are used as they are. ``swcstudio batch auto-label``
        records paths as given on its command line, so they can be relative
        to the folder it ran in; the review file is written inside the batch
        folder (its recorded ``folder``), so they are resolved from there.
        """
        p = Path(value)
        if p.is_absolute() or self._path is None:
            return [p]
        review_dir = self._path.parent
        candidates: list[Path] = []
        folder = str(self._payload.get("folder") or "").strip()
        if folder:
            folder_path = Path(folder)
            if folder_path.is_absolute():
                candidates.append(folder_path / p)
            else:
                # "cells/a.swc" recorded with folder "cells": the file is
                # "a.swc" in that folder, which holds the review.
                try:
                    candidates.append(review_dir / p.relative_to(folder_path))
                except ValueError:
                    pass
        candidates.append(review_dir / p)
        candidates.append(review_dir / p.name)
        unique: list[Path] = []
        for c in candidates:
            if c not in unique:
                unique.append(c)
        return unique

    def _record_path(self, rec: dict[str, Any]) -> str | None:
        """The labeled file of a record (its output, else its input), if found."""
        for key in ("output_path", "input_path"):
            value = rec.get(key)
            if not value:
                continue
            for candidate in self._candidate_paths(str(value)):
                if candidate.is_file():
                    return str(candidate)
        return None

    def _open_rows(self, rows: list[int]) -> None:
        """Ask the main window to open the cells in ``rows``, in table order."""
        paths: list[str] = []
        for row in rows:
            rec = self._record_at(row)
            if rec is None:
                continue
            path = self._record_path(rec)
            if path is None:
                self.log_message.emit(f"Flag review: cannot find the file for {rec.get('file')}.")
            elif path not in paths:
                paths.append(path)
        if paths:
            self.open_files_requested.emit(paths)

    def _on_open_selected(self) -> None:
        self._open_rows(self._selected_rows())

    def _on_row_double_clicked(self, row: int) -> None:
        rows = self._selected_rows()
        self._open_rows(rows if row in rows else [row])

    def _step_order(self) -> list[int]:
        """Every record, shown or not, in the order Previous/Next walk.

        That is the table's order: riskiest first unless another column was
        clicked to sort by it. Records the filter hides keep their place, so
        stepping on from a cell that just left the list (marked OK while
        showing "Not reviewed") continues with the cell that followed it.
        """
        risk = self._risk_order()
        header = self._table.horizontalHeader()
        section, order = header.sortIndicatorSection(), header.sortIndicatorOrder()
        descending = order == Qt.DescendingOrder
        if not 0 < section < len(COLUMNS):
            return list(reversed(risk)) if descending and section == 0 else risk
        share = self._share.value() / 100.0
        keys = {idx: self._row_values(self._records[idx], pos, share)[section][1]
                for pos, idx in enumerate(risk)}
        # Stable, like the table's own sort: ties keep the riskiest-first order.
        return sorted(risk, key=lambda idx: keys[idx], reverse=descending)

    def _step_target(self, delta: int) -> int | None:
        """The record ``delta`` cells after (before, if negative) the current one.

        The current cells are the selected ones (Next continues after the
        last of them, Previous before the first); with none selected, the
        cells last selected. With neither, Next starts at the top of the list
        and Previous at the bottom.
        """
        if self._path is None or not self._records or not delta:
            return None
        order = self._step_order()
        shown = [idx for idx in order if self._visible(self._records[idx])]
        if not shown:
            return None
        anchors = self._selected_record_indices() or [i for i in self._cursor if 0 <= i < len(self._records)]
        steps = abs(int(delta))
        if not anchors:
            pool = shown if delta > 0 else list(reversed(shown))
            return pool[steps - 1] if len(pool) >= steps else None
        position = {idx: pos for pos, idx in enumerate(order)}
        if delta > 0:
            start = max(position[a] for a in anchors)
            after = [idx for idx in order[start + 1:] if self._visible(self._records[idx])]
            return after[steps - 1] if len(after) >= steps else None
        start = min(position[a] for a in anchors)
        before = [idx for idx in order[:start] if self._visible(self._records[idx])]
        return before[-steps] if len(before) >= steps else None

    def step(self, delta: int) -> None:
        """Select and open the cell ``delta`` places after (before, if
        negative) the current one; see ``_step_target``."""
        target = self._step_target(delta)
        if target is None:
            return
        row = self._row_of_record(target)
        if row is None:
            return
        self._table.setCurrentCell(row, 0, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
        self._table.scrollToItem(self._table.item(row, 0))
        self._cursor = [target]
        self._open_rows([row])

    def _update_step_buttons(self) -> None:
        self._btn_previous.setEnabled(self._step_target(-1) is not None)
        self._btn_next.setEnabled(self._step_target(1) is not None)

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


__all__ = ["FlagReviewPanel", "reason_text", "warnings_text"]
