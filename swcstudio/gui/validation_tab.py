"""Validation widgets for SWC Studio."""

from __future__ import annotations

import io
import json
import os
import time
from pathlib import Path

import numpy as np
import pandas as pd

from PySide6.QtCore import QObject, QThread, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QBrush, QColor, QDesktopServices, QFont
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget, QHeaderView, QSizePolicy

from swcstudio.core.config import feature_config_path, load_feature_config
from swcstudio.core.reporting import (
    format_validation_report_text,
    write_text_report,
)
from swcstudio.tools.batch_processing.features.swc_splitter import split_swc_text
from swcstudio.core.validation_catalog import CHECK_CATALOG, CHECK_ORDER, display_label_for_result, group_rows_by_category
from swcstudio.tools.geometry_editing.features.operations import reindex_dataframe_with_map
from swcstudio.tools.validation.features.core import run_checks_text
from swcstudio.gui.constants import SWC_COLS
from swcstudio.gui.font_utils import bold_font

# swc_io's per-node token columns (the exact number text of each field).
_TOKEN_COLS = ("x_str", "y_str", "z_str", "radius_str", "id_str", "parent_str")

_VALIDATION_CFG_PATH = feature_config_path("validation", "default")
_VALIDATION_CFG = load_feature_config("validation", "default", default={"checks": {}})


def _params_text_for_check(key: str) -> str:
    checks = dict(_VALIDATION_CFG.get("checks") or {})
    entry = dict(checks.get(str(key)) or {})
    params = dict(entry.get("params") or {})
    if not params:
        return "Parameters: none"
    parts = [f"{name}={json.dumps(value)}" for name, value in params.items()]
    return "Parameters: " + ", ".join(parts)


def _rule_text_with_params(key: str, rule: str) -> str:
    params_text = _params_text_for_check(key)
    base = str(rule or "").strip()
    if not base:
        return params_text
    return f"{base} {params_text}"
_ISSUE_GUIDE_CATALOG: list[tuple[str, list[tuple[str, str]]]] = [
    (
        "Suspicious issue detectors",
        [
            (
                "Outlier radii detected",
                "Aggregates nodes whose radii look suspicious relative to nearby branches and proposes cleanup values.",
            ),
            (
                "Likely wrong labels",
                "Aggregates nodes whose neurite types look inconsistent with the auto-typing engine's predictions.",
            ),
        ],
    ),
]


def _tree_bold_font(widget: QWidget) -> QFont:
    return bold_font(widget.font(), point_size=11)


class _ValidationWorker(QObject):
    finished = Signal(int, dict)
    failed = Signal(int, str)

    def __init__(self, run_id: int, swc_text: str):
        super().__init__()
        self._run_id = int(run_id)
        self._swc_text = swc_text

    @Slot()
    def run(self):
        try:
            report = run_checks_text(self._swc_text)
            self.finished.emit(self._run_id, report.to_dict())
        except Exception as e:  # noqa: BLE001
            self.failed.emit(self._run_id, str(e))


class ValidationPrecheckWidget(QWidget):
    """Floating pre-check summary grouped by validation category."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self._build_ui()
        self.populate_catalog()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        title = QLabel("Rule Guide")
        title.setStyleSheet("font-size: 14px; font-weight: 700; color: #222;")
        layout.addWidget(title)

        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Issue", "Rule"])
        self._tree.setUniformRowHeights(False)
        self._tree.setAlternatingRowColors(True)
        self._tree.setWordWrap(True)
        self._tree.setStyleSheet(
            "QTreeWidget { font-size: 12px; gridline-color: #ddd; }"
            "QHeaderView::section { font-weight: 600; padding: 4px; }"
        )
        header = self._tree.header()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(0, QHeaderView.Interactive)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        self._tree.setColumnWidth(0, 220)
        layout.addWidget(self._tree, stretch=1)

    def populate_catalog(self):
        self._tree.clear()
        bold_item_font = _tree_bold_font(self)
        for category, checks in CHECK_CATALOG:
            group = QTreeWidgetItem([category, ""])
            group.setFirstColumnSpanned(True)
            group.setExpanded(True)
            group.setFont(0, bold_item_font)
            self._tree.addTopLevelItem(group)
            for _key, label, rule in checks:
                issue_label = display_label_for_result(_key, False, label)
                item = QTreeWidgetItem([issue_label, _rule_text_with_params(_key, rule)])
                item.setTextAlignment(0, Qt.AlignLeft | Qt.AlignVCenter)
                item.setTextAlignment(1, Qt.AlignLeft | Qt.AlignVCenter)
                group.addChild(item)
        for category, checks in _ISSUE_GUIDE_CATALOG:
            group = QTreeWidgetItem([category, ""])
            group.setFirstColumnSpanned(True)
            group.setExpanded(True)
            group.setFont(0, bold_item_font)
            self._tree.addTopLevelItem(group)
            for label, rule in checks:
                item = QTreeWidgetItem([label, rule])
                item.setTextAlignment(0, Qt.AlignLeft | Qt.AlignVCenter)
                item.setTextAlignment(1, Qt.AlignLeft | Qt.AlignVCenter)
                group.addChild(item)
        self._tree.expandAll()


class ValidationTabWidget(QWidget):
    """Validation results panel with lazy execution for fast SWC loading."""

    precheck_requested = Signal()
    report_ready = Signal(dict)
    validation_failed = Signal(str)
    result_activated = Signal(dict)
    def __init__(self, parent=None, as_panel: bool = True):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self._as_panel = as_panel
        self._source_stem = "file"
        self._source_file_path = ""
        self._df: pd.DataFrame | None = None
        self._swc_text: str = ""
        self._swc_dirty = True
        self._trees: list = []
        self._report: dict | None = None
        self._results_rows: list[dict] = []
        self._show_save_all = False
        self._run_id = 0
        self._worker_thread: QThread | None = None
        self._worker: _ValidationWorker | None = None
        # Every validation thread still running, current or superseded, with
        # its worker. A superseded run is never waited for: it finishes in the
        # background and its result is ignored (its run id is stale). Keeping
        # the references until then stops Qt deleting a running thread.
        self._live_runs: dict[QThread, _ValidationWorker] = {}
        # Run id whose report (or failure) has not been delivered yet.
        self._awaiting_run_id: int | None = None

        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        results_root = QVBoxLayout()
        results_root.setContentsMargins(0, 0, 0, 0)
        results_root.setSpacing(8)

        header = QHBoxLayout()
        self._btn_save_all = QPushButton("Save All Trees")
        self._btn_save_all.setVisible(False)
        self._btn_save_all.clicked.connect(self._on_save_all)
        header.addWidget(self._btn_save_all)
        self._btn_download_report = QPushButton("Download Validation Report")
        self._btn_download_report.setEnabled(False)
        self._btn_download_report.clicked.connect(self._on_download_report)
        header.addWidget(self._btn_download_report)
        header.addStretch()
        results_root.addLayout(header)

        self._status_label = QLabel("No validation report yet.")
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet("font-size: 12px; color: #555;")
        results_root.addWidget(self._status_label)

        self._results_tree = QTreeWidget()
        self._results_tree.setHeaderLabels(["Issue", "Status", "Detail"])
        header = self._results_tree.header()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(0, QHeaderView.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        self._results_tree.setColumnWidth(0, 220)
        self._results_tree.itemActivated.connect(self._on_results_tree_activated)
        self._results_tree.itemDoubleClicked.connect(self._on_results_tree_activated)
        results_root.addWidget(self._results_tree, stretch=1)
        layout.addLayout(results_root)

    # --------------------------------------------------------- Public API
    def has_results(self) -> bool:
        return bool(self._report)

    def load_swc(self, df: pd.DataFrame, filename: str, file_path: str = "", auto_run: bool = True):
        # The validation already running is for exactly these contents (the
        # same tab shown again): let it finish rather than start over.
        if self.is_running() and self._is_current_data(df, file_path):
            return
        # Invalidate any in-flight worker so its results cannot attach to a
        # newly loaded document. It is not waited for: switching documents
        # must not block the UI while a long validation finishes.
        self._run_id += 1
        self._retire_current_worker()
        self._source_stem = Path(filename or "file").stem or "file"
        self._source_file_path = str(file_path or "")
        self._df = df.copy()
        self._swc_text = ""
        self._swc_dirty = True
        self._trees = []
        self._report = None
        self._results_rows = []
        self._show_save_all = False
        self._btn_save_all.setVisible(self._show_save_all)
        self._btn_save_all.setEnabled(self._show_save_all)
        self._btn_download_report.setEnabled(False)
        self._status_label.setText("No validation report yet.")
        self._results_tree.clear()
        if auto_run:
            self.run_validation()

    def run_validation(self):
        """Validate the loaded contents in the background.

        Starts a fresh run; a run still going for the same widget is
        superseded (its result is ignored), never waited for.
        """
        if self._df is None or self._df.empty:
            return
        try:
            self._ensure_swc_text()
            self._start_validation_worker(self._swc_text)
        except Exception:
            return

    def is_running(self) -> bool:
        """True until the current run (the one whose result will be shown)
        has delivered its report or failure."""
        return self._awaiting_run_id is not None and self._awaiting_run_id == self._run_id

    def stop_worker(self, wait_ms: int = 3000):
        """Drop the current run's result and wait up to ``wait_ms`` in total
        for every validation thread to finish (used when the app closes)."""
        self._run_id += 1
        self._retire_current_worker()
        remaining = max(0, int(wait_ms))
        for thread in list(self._live_runs):
            if remaining <= 0:
                break
            try:
                started = time.monotonic()
                if thread.isRunning():
                    thread.quit()
                    thread.wait(remaining)
                remaining -= int((time.monotonic() - started) * 1000)
            except Exception:  # noqa: BLE001
                pass

    def _is_current_data(self, df: pd.DataFrame, file_path: str) -> bool:
        if self._df is None or not isinstance(df, pd.DataFrame):
            return False
        if str(file_path or "") != self._source_file_path:
            return False
        try:
            mine = self._df.loc[:, SWC_COLS].reset_index(drop=True)
            theirs = df.loc[:, SWC_COLS].reset_index(drop=True)
            return bool(mine.equals(theirs))
        except Exception:  # noqa: BLE001
            return False

    def _retire_current_worker(self):
        """Stop tracking the current run without waiting for it to finish."""
        if self._worker_thread is not None:
            try:
                self._worker_thread.quit()  # leaves once the running check returns
            except Exception:  # noqa: BLE001
                pass
        self._worker = None
        self._worker_thread = None
        self._awaiting_run_id = None

    def _start_validation_worker(self, swc_text: str):
        self._run_id += 1
        run_id = int(self._run_id)
        self._retire_current_worker()
        self._awaiting_run_id = run_id

        thread = QThread(self)
        worker = _ValidationWorker(run_id, swc_text)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_validation_finished)
        worker.failed.connect(self._on_validation_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(self._on_thread_finished)
        self._live_runs[thread] = worker
        self._worker_thread = thread
        self._worker = worker
        self._status_label.setText("Running validation...")
        thread.start()

    @Slot()
    def _on_thread_finished(self):
        thread = self.sender()
        if not isinstance(thread, QThread):
            return
        self._live_runs.pop(thread, None)
        if thread is self._worker_thread:
            self._worker = None
            self._worker_thread = None
            if self.is_running():
                # The thread ended without its worker reporting back.
                self._on_validation_failed(self._run_id, "the validation stopped without a result")
        thread.deleteLater()

    @Slot()
    def _cleanup_worker_refs(self):
        self._worker = None
        self._worker_thread = None

    @Slot(int, dict)
    def _on_validation_finished(self, run_id: int, report_dict: dict):
        if int(run_id) != int(self._run_id):
            return
        self._awaiting_run_id = None
        self._report = dict(report_dict)
        rows = list(self._report.get("results", []))
        rows.sort(key=self._result_sort_key)
        self._results_rows = rows
        self._sync_split_actions_from_report(rows)
        self._btn_download_report.setEnabled(True)
        self._populate_results_tree(rows)
        self.report_ready.emit(dict(self._report))

    def _sync_split_actions_from_report(self, rows: list[dict]):
        multiple_somas_row = next((row for row in rows if str(row.get("key", "")).strip() == "multiple_somas"), None)
        can_split = False
        if isinstance(multiple_somas_row, dict):
            metrics = dict(multiple_somas_row.get("metrics", {}) or {})
            can_split = bool(metrics.get("can_split_trees")) and str(multiple_somas_row.get("status", "")).lower() != "pass"

        self._show_save_all = bool(can_split)
        self._btn_save_all.setVisible(self._show_save_all)
        self._btn_save_all.setEnabled(self._show_save_all)

    def _populate_results_tree(self, rows: list[dict]):
        self._results_tree.clear()
        if not rows:
            self._status_label.setText("No validation results available.")
            return
        bold_item_font = _tree_bold_font(self)
        fail_count = sum(1 for row in rows if str(row.get("status", "")).strip().lower() == "fail")
        warn_count = sum(1 for row in rows if str(row.get("status", "")).strip().lower() == "warning")
        error_count = sum(1 for row in rows if bool(row.get("error")))
        self._status_label.setText(
            f"{len(rows)} checks shown. {fail_count} fail, {warn_count} warning, {error_count} backend error."
        )
        for category, items in group_rows_by_category(rows):
            group = QTreeWidgetItem([category, "", ""])
            group.setFirstColumnSpanned(True)
            group.setExpanded(True)
            group.setFont(0, bold_item_font)
            self._results_tree.addTopLevelItem(group)
            for row in items:
                status = str(row.get("status", "")).strip().lower()
                detail = str(row.get("message", "") or "").strip()
                item = QTreeWidgetItem(
                    [
                        str(row.get("label", "")).strip(),
                        status.capitalize() if status else "",
                        detail,
                    ]
                )
                item.setData(0, Qt.UserRole, dict(row))
                bg, fg = self._status_brushes(status, bool(row.get("error")))
                for col in range(3):
                    item.setBackground(col, bg)
                    item.setForeground(col, fg)
                group.addChild(item)
        self._results_tree.expandAll()

    def _status_brushes(self, status: str, is_error: bool) -> tuple[QBrush, QBrush]:
        if is_error:
            return (QBrush(QColor("#eceff3")), QBrush(QColor("#334155")))
        key = str(status or "").strip().lower()
        if key == "fail":
            return (QBrush(QColor("#fde2e2")), QBrush(QColor("#8b1e1e")))
        if key == "warning":
            return (QBrush(QColor("#fff4d6")), QBrush(QColor("#8a5a00")))
        if key == "pass":
            return (QBrush(QColor("#e3f6e8")), QBrush(QColor("#166534")))
        return (QBrush(QColor("#e7f0ff")), QBrush(QColor("#1e3a8a")))

    def _on_results_tree_activated(self, item: QTreeWidgetItem, _column: int = 0):
        if item is None:
            return
        payload = item.data(0, Qt.UserRole)
        if isinstance(payload, dict) and payload.get("key"):
            self.result_activated.emit(dict(payload))

    @Slot(int, str)
    def _on_validation_failed(self, run_id: int, error_text: str):
        if int(run_id) != int(self._run_id):
            return
        self._awaiting_run_id = None
        message = str(error_text or "").strip() or "unknown error"
        self._status_label.setText(f"Validation failed: {message}")
        self.validation_failed.emit(message)

    def _write_report_to_path(self, out_path: str):
        if not self._report:
            return
        try:
            write_text_report(out_path, format_validation_report_text(self._report))
        except Exception:
            return

    # --------------------------------------------------------- Internal helpers
    def _ensure_swc_text(self):
        if not self._swc_dirty and self._swc_text:
            return
        if self._df is None or self._df.empty:
            self._swc_text = ""
            self._swc_dirty = False
            return

        arr = self._df[["id", "type", "x", "y", "z", "radius", "parent"]].to_numpy(copy=False)
        buf = io.StringIO()
        # Keep sufficient float precision so validation does not create false duplicates
        # by rounding nearby coordinates to 4 decimals.
        np.savetxt(
            buf,
            arr,
            fmt=["%d", "%d", "%.10g", "%.10g", "%.10g", "%.10g", "%d"],
            delimiter=" ",
        )
        self._swc_text = "# id type x y z radius parent\n" + buf.getvalue()
        self._swc_dirty = False

    def _ensure_trees(self):
        if self._trees:
            return
        self._ensure_swc_text()
        if not self._swc_text:
            return
        # Guarded split: raises ValueError (duplicate node ids, a parent
        # cycle, or trees that would not hold every node) instead of
        # silently dropping or duplicating nodes.
        self._trees = split_swc_text(self._swc_text)

    def _result_sort_key(self, row: dict) -> tuple[int, str]:
        key = str(row.get("key", ""))
        label = str(row.get("label", ""))
        return (CHECK_ORDER.get(key, 1000), label.lower())

    def _on_save_all(self) -> list[str]:
        if self._df is None or self._df.empty:
            return []

        try:
            self._ensure_trees()
        except ValueError as exc:
            QMessageBox.warning(self, "Save All Trees", str(exc))
            return []
        if not self._trees:
            return []

        folder = QFileDialog.getExistingDirectory(self, "Choose folder to save all trees")
        if not folder:
            return []

        targets = [
            os.path.join(folder, f"{self._source_stem}_tree{tidx}.swc")
            for tidx in range(1, len(self._trees) + 1)
        ]
        existing = [p for p in targets if os.path.exists(p)]
        if existing:
            names = "\n".join(f"  {os.path.basename(p)}" for p in existing[:10])
            more = f"\n  ... and {len(existing) - 10} more" if len(existing) > 10 else ""
            answer = QMessageBox.question(
                self,
                "Save All Trees",
                f"{len(existing)} of these files already exist in that folder:\n\n{names}{more}"
                "\n\nReplace them?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return []

        try:
            saved_paths: list[str] = []
            for out_path, (_root_id, sub_text, _node_count) in zip(targets, self._trees):
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write(sub_text)
                saved_paths.append(out_path)
        except Exception:
            return []
        return saved_paths

    def _on_export_json(self):
        if not self._report:
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Validation JSON",
            "validation.json",
            "JSON Files (*.json);;All Files (*)",
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._report, f, indent=2, default=str)
        except Exception:
            return

    def _on_download_report(self):
        if not self._report:
            return
        if self._source_file_path:
            source = Path(self._source_file_path)
            default_path = str(source.parent / f"{source.stem}_validation_report.txt")
        else:
            default_path = str(Path.cwd() / f"{self._source_stem}_validation_report.txt")
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Download Validation Report",
            default_path,
            "Text Files (*.txt);;All Files (*)",
        )
        if not path:
            return
        self._write_report_to_path(path)


class ValidationIndexCleanWidget(QWidget):
    index_clean_requested = Signal(object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._df: pd.DataFrame | None = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        desc = QLabel(
            "Reorder and reindex the current SWC so parent IDs come before children and all node IDs become continuous."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("font-size: 12px; color: #555;")
        layout.addWidget(desc)

        self._btn_apply = QPushButton("Apply Index Clean")
        self._btn_apply.setEnabled(False)
        self._btn_apply.clicked.connect(self._on_apply)
        layout.addWidget(self._btn_apply)
        layout.addStretch()

    def set_loaded_swc(self, df: pd.DataFrame | None):
        if isinstance(df, pd.DataFrame) and not df.empty:
            # Keep the original number text (the *_str token columns) so it
            # moves with each node when the nodes are renumbered.
            cols = SWC_COLS + [c for c in _TOKEN_COLS if c in df.columns]
            self._df = df.loc[:, cols].copy()
            self._df.attrs = dict(getattr(df, "attrs", {}) or {})
        else:
            self._df = None
        self._btn_apply.setEnabled(self._df is not None and not self._df.empty)

    def _on_apply(self):
        if self._df is None or self._df.empty:
            return
        try:
            new_df, id_map = reindex_dataframe_with_map(self._df)
        except ValueError as exc:
            # Refused (duplicate node ids, a parent cycle): nothing is changed.
            QMessageBox.warning(self, "Index Clean", str(exc))
            return
        self.index_clean_requested.emit(new_df, dict(id_map))
