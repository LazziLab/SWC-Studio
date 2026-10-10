"""Batch processing controls for shared batch feature backends."""

import os
import json
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from swcstudio.core.auto_typing import (
    BatchOptions,
    backend_status,
    get_config as get_auto_typing_config,
    is_available,
    save_config as save_auto_typing_config,
)
from swcstudio.core.config import feature_config_path
from swcstudio.core.swc_io import is_swc_data_file
from swcstudio.tools.batch_processing.features.batch_validation import validate_folder as run_batch_validation
from swcstudio.tools.batch_processing.features.index_clean import run_folder as run_batch_index_clean
from swcstudio.tools.batch_processing.features.simplification import run_folder as run_batch_simplification
from swcstudio.tools.batch_processing.features.swc_splitter import split_folder
from .auto_typing_workers import _AutoLabelBatchWorker
from .validation_auto_label_panel import add_cell_class_items, select_cell_class
from .report_popup import ReportPopupDialog
from .radii_cleaning_panel import RadiiCleaningPanel

_CFG_PATH = feature_config_path("batch_processing", "auto_typing")


def _not_labeled_line(result: object) -> str:
    # Structural-check rejections are part of the failures, not extra to them.
    failed = int(getattr(result, "files_failed", 0) or 0)
    qc = int(getattr(result, "files_qc_failed", 0) or 0)
    line = f"Not labeled: {failed}"
    if failed:
        line += f" ({qc} failed the structural checks, {failed - qc} errors)"
    return line


class _AutoTypingConfigDialog(QDialog):
    """On-demand JSON editor for batch auto-typing config."""

    saved = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Auto-Typing JSON")
        self.resize(820, 620)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        path_label = QLabel(f"Config file: {_CFG_PATH}")
        path_label.setWordWrap(True)
        path_label.setStyleSheet("font-size: 12px; color: #555;")
        root.addWidget(path_label)

        self._editor = QPlainTextEdit()
        self._editor.setStyleSheet(
            "QPlainTextEdit {"
            "  background: #fafafa; border: 1px solid #ddd; color: #333;"
            "  font-family: Menlo, Consolas, monospace; font-size: 12px;"
            "}"
        )
        root.addWidget(self._editor, stretch=1)

        btn_row = QHBoxLayout()
        self._btn_reload = QPushButton("Reload")
        self._btn_reload.clicked.connect(self.reload_from_source)
        btn_row.addWidget(self._btn_reload)

        self._btn_save = QPushButton("Save")
        self._btn_save.clicked.connect(self._on_save)
        btn_row.addWidget(self._btn_save)

        btn_row.addStretch()
        self._status = QLabel("")
        self._status.setStyleSheet("font-size: 12px; color: #555;")
        btn_row.addWidget(self._status)

        self._btn_close = QPushButton("Close")
        self._btn_close.clicked.connect(self.close)
        btn_row.addWidget(self._btn_close)
        root.addLayout(btn_row)

        self.reload_from_source()

    def reload_from_source(self):
        try:
            txt = json.dumps(get_auto_typing_config(), indent=2, sort_keys=True)
            self._editor.setPlainText(txt)
            self._status.setText("Loaded.")
        except Exception as e:  # noqa: BLE001
            self._status.setText(f"Load failed: {e}")

    def _on_save(self):
        try:
            data = json.loads(self._editor.toPlainText())
            if not isinstance(data, dict):
                raise ValueError("JSON root must be an object")
            save_auto_typing_config(data)
            self._status.setText("Saved.")
            self.saved.emit("Auto-typing JSON saved.")
        except Exception as e:  # noqa: BLE001
            self._status.setText(f"Save failed: {e}")


class BatchTabWidget(QWidget):
    """Owns batch control pages used by the right-side inspector tabs."""

    log_message = Signal(str)
    batch_validation_ready = Signal(dict)
    precheck_requested = Signal()
    # Path of a batch's flag review (``*_flag_review_*.json``) to open.
    flag_review_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._status_boxes: list[QPlainTextEdit] = []
        self._config_dialog: _AutoTypingConfigDialog | None = None
        self._batch_run_id: int = 0
        self._batch_worker: _AutoLabelBatchWorker | None = None
        self._batch_worker_thread: QThread | None = None
        self._split_input_dir: str = ""
        self._batch_input_dir: str = ""
        self._batch_model_dir: str = ""
        self._last_flag_review_path: str = ""
        self._validation_input_dir: str = ""
        self._simplify_input_dir: str = ""
        self._index_clean_input_dir: str = ""
        self._split_page = self._build_split_page()
        self._auto_page = self._build_auto_page()
        self._validation_page = self._build_validation_page()
        self._radii_page = self._build_radii_page()
        self._simplify_page = self._build_simplify_page()
        self._index_clean_page = self._build_index_clean_page()

        # This root widget is not shown directly; pages are used in main window tabs.
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(QLabel("Batch controls are shown as tabs in the Inspector."))

    # --------------------------------------------------------- Public page access
    def split_tab_widget(self) -> QWidget:
        return self._split_page

    def auto_tab_widget(self) -> QWidget:
        return self._auto_page

    def radii_tab_widget(self) -> QWidget:
        return self._radii_page

    def validation_tab_widget(self) -> QWidget:
        return self._validation_page

    def simplify_tab_widget(self) -> QWidget:
        return self._simplify_page

    def index_clean_tab_widget(self) -> QWidget:
        return self._index_clean_page

    def set_loaded_swc(self, df, filename: str, file_path: str = ""):
        # Batch controls are folder-oriented; they should not do per-file work on document open.
        _ = (df, filename, file_path)

    # --------------------------------------------------------- UI builders
    def _build_split_page(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)
        root.setAlignment(Qt.AlignTop)

        desc = QLabel(
            "Select a folder and split each multi-cell SWC into separate trees.\n"
            "Output folder: <selected>/<selected>_batch_split_<timestamp>\n"
            "Output naming: <original_file_name>_batch_split_tree_<index>_<timestamp>.swc"
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("font-size: 12px; color: #555;")
        root.addWidget(desc)

        action_row = QHBoxLayout()
        action_row.setSpacing(6)
        self._btn_split_folder = self._compact_button(QPushButton("Select Folder"), 136)
        self._btn_split_folder.clicked.connect(self._on_browse_split_input_dir)
        action_row.addWidget(self._btn_split_folder)
        self._btn_run_split_folder = self._compact_button(QPushButton("Run"), 60)
        self._btn_run_split_folder.clicked.connect(self._on_run_split_folder)
        action_row.addWidget(self._btn_run_split_folder)
        action_row.addStretch()
        root.addLayout(action_row)

        root.addLayout(self._make_selected_folder_row("_split_input_dir_lbl"))

        self._split_status = self._new_status_box()
        root.addWidget(self._split_status, stretch=1)
        return page

    def _build_auto_page(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)
        root.setAlignment(Qt.AlignTop)

        desc = QLabel(
            "Auto-label every SWC file in a folder with the SWC auto-labeler, then rank "
            "the cells for review: the quality flag predicts how well each cell was "
            "labeled, and the riskiest share of each cell class is flagged. Each source "
            "SWC is updated and recorded in its own history; the ranking opens in the "
            "Flag Review window."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("font-size: 12px; color: #555;")
        root.addWidget(desc)

        # ---- input folder controls
        action_row = QHBoxLayout()
        action_row.setSpacing(6)
        self._batch_btn_browse_input = self._compact_button(QPushButton("Select Folder"), 136)
        self._batch_btn_browse_input.clicked.connect(self._on_browse_batch_input_dir)
        action_row.addWidget(self._batch_btn_browse_input)
        self._btn_run_batch_check = self._compact_button(QPushButton("Run"), 60)
        self._btn_run_batch_check.clicked.connect(self._on_run_batch_check)
        action_row.addWidget(self._btn_run_batch_check)
        self._btn_edit_auto_cfg = self._compact_button(QPushButton("Show JSON"), 108)
        self._btn_edit_auto_cfg.clicked.connect(self._on_edit_auto_typing_json)
        action_row.addWidget(self._btn_edit_auto_cfg)
        action_row.addStretch()
        root.addLayout(action_row)

        root.addLayout(self._make_selected_folder_row("_batch_input_dir_lbl"))

        # ---- model dir picker (optional override)
        self._batch_model_row = QHBoxLayout()
        self._batch_model_row.setSpacing(6)
        model_lbl = QLabel("Model folder (optional):")
        model_lbl.setStyleSheet("font-size: 12px; color: #333;")
        self._batch_model_row.addWidget(model_lbl)
        self._batch_edit_model_dir = QLineEdit()
        self._batch_edit_model_dir.setPlaceholderText("Leave blank for the shipped models")
        self._batch_edit_model_dir.setToolTip("A folder written by `swcstudio train auto-typing`.\n"
                                              "Leave blank to use the shipped or updated models.")
        self._batch_edit_model_dir.setFixedWidth(260)
        self._batch_edit_model_dir.editingFinished.connect(self._on_batch_model_dir_edited)
        self._batch_model_row.addWidget(self._batch_edit_model_dir)
        self._batch_btn_browse_model = self._compact_button(QPushButton("Browse..."), 96)
        self._batch_btn_browse_model.clicked.connect(self._on_browse_batch_model_dir)
        self._batch_model_row.addWidget(self._batch_btn_browse_model)
        self._batch_backend_status_lbl = QLabel("")
        self._batch_backend_status_lbl.setStyleSheet("font-size: 11px; color: #888;")
        self._batch_model_row.addWidget(self._batch_backend_status_lbl)
        self._batch_model_row.addStretch()
        root.addLayout(self._batch_model_row)
        self._refresh_batch_backend_status()

        type_row = QHBoxLayout()
        type_row.setSpacing(6)
        cell_lbl = QLabel("Cell class:")
        cell_lbl.setStyleSheet("font-size: 12px; color: #333;")
        type_row.addWidget(cell_lbl)
        self._batch_cell_type_combo = QComboBox()
        add_cell_class_items(self._batch_cell_type_combo)
        try:
            select_cell_class(self._batch_cell_type_combo, get_auto_typing_config().get("cell_type"))
        except Exception:  # noqa: BLE001
            pass
        type_row.addWidget(self._batch_cell_type_combo)
        self._batch_flag_enabled = QCheckBox("Quality flag")
        self._batch_flag_enabled.setChecked(True)
        type_row.addWidget(self._batch_flag_enabled)
        type_row.addStretch()
        root.addLayout(type_row)

        share_row = QHBoxLayout()
        share_row.setSpacing(6)
        share_lbl = QLabel("Flag the riskiest")
        share_lbl.setStyleSheet("font-size: 12px; color: #333;")
        share_row.addWidget(share_lbl)
        self._batch_flag_budget = QSpinBox()
        self._batch_flag_budget.setRange(1, 50)
        self._batch_flag_budget.setSuffix(" %")
        try:
            budget = float(get_auto_typing_config().get("flag_budget", 0.10))
        except Exception:  # noqa: BLE001
            budget = 0.10
        self._batch_flag_budget.setValue(int(round(min(max(budget, 0.01), 0.5) * 100)))
        self._batch_flag_budget.setFixedWidth(72)
        self._batch_flag_budget.setToolTip(
            "Share of each cell class flagged for review, ranked by predicted per-cell F1.\n"
            "10% is the share the labeler's flag was evaluated at. A class with too few\n"
            "cells for that share uses the flag's fixed cut-off instead.")
        share_row.addWidget(self._batch_flag_budget)
        share_tail = QLabel("of each cell class")
        share_tail.setStyleSheet("font-size: 12px; color: #333;")
        share_row.addWidget(share_tail)
        self._batch_btn_flag_review = self._compact_button(QPushButton("Open Flag Review"), 136)
        self._batch_btn_flag_review.setEnabled(False)
        self._batch_btn_flag_review.setToolTip("Open the ranked list of the last batch.")
        self._batch_btn_flag_review.clicked.connect(
            lambda: self.flag_review_requested.emit(self._last_flag_review_path))
        share_row.addWidget(self._batch_btn_flag_review)
        share_row.addStretch()
        root.addLayout(share_row)

        # Per-file progress for batch runs. Hidden until a worker starts.
        self._batch_progress = QProgressBar()
        self._batch_progress.setVisible(False)
        self._batch_progress.setTextVisible(True)
        self._batch_progress.setFormat("%v / %m files")
        root.addWidget(self._batch_progress)

        self._batch_progress_label = QLabel("")
        self._batch_progress_label.setVisible(False)
        self._batch_progress_label.setWordWrap(True)
        self._batch_progress_label.setStyleSheet("font-size: 11px; color: #555;")
        root.addWidget(self._batch_progress_label)

        # Status box for the per-run summary written when a job finishes.
        self._batch_status_box = self._new_status_box()
        root.addWidget(self._batch_status_box, stretch=1)
        # Keep controls pinned to the top of the tab even when there is extra height.
        root.addStretch(1)
        return page

    def _on_browse_batch_input_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Select folder with SWC files for auto-labeling"
        )
        if path:
            self._set_batch_input_dir(path)

    def _on_browse_batch_model_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select a labeler model folder")
        if path:
            self._batch_model_dir = path
            self._sync_batch_model_dir_label()
            self._refresh_batch_backend_status()

    def _on_batch_model_dir_edited(self) -> None:
        self._batch_model_dir = (self._batch_edit_model_dir.text() or "").strip()
        self._sync_batch_model_dir_label()
        self._refresh_batch_backend_status()

    def _make_selected_folder_row(self, label_attr: str) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(6)
        lbl = QLabel("Selected folder:")
        lbl.setStyleSheet("font-size: 12px; color: #333;")
        row.addWidget(lbl)

        value_lbl = QLabel("No folder selected.")
        value_lbl.setWordWrap(True)
        value_lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        value_lbl.setStyleSheet("font-size: 12px; color: #555;")
        row.addWidget(value_lbl, stretch=1)
        setattr(self, label_attr, value_lbl)
        return row

    def _compact_button(self, button: QPushButton, max_width: int | None = None) -> QPushButton:
        button.setMinimumWidth(0)
        button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        if max_width is not None:
            button.setFixedWidth(max_width)
        return button

    def _short_path_text(self, path: str, empty: str) -> str:
        if not path:
            return empty
        try:
            p = Path(path)
            parent = p.parent.name
            return f".../{parent}/{p.name}" if parent else p.name
        except Exception:  # noqa: BLE001
            return path

    def _set_input_dir(self, attr_name: str, label: QLabel, path: str) -> None:
        value = str(path or "").strip()
        setattr(self, attr_name, value)
        label.setText(self._short_path_text(value, "No folder selected."))
        label.setToolTip(value)

    def _set_batch_input_dir(self, path: str) -> None:
        self._batch_input_dir = str(path or "").strip()
        self._batch_input_dir_lbl.setText(
            self._short_path_text(self._batch_input_dir, "No folder selected.")
        )
        self._batch_input_dir_lbl.setToolTip(self._batch_input_dir)

    def _sync_batch_model_dir_label(self) -> None:
        self._batch_edit_model_dir.blockSignals(True)
        self._batch_edit_model_dir.setText(self._batch_model_dir)
        self._batch_edit_model_dir.setToolTip(
            self._batch_model_dir or "Leave blank to use the shipped or updated models."
        )
        self._batch_edit_model_dir.blockSignals(False)

    def _refresh_batch_backend_status(self) -> None:
        md = (self._batch_model_dir or "").strip() or None
        ok, reason = is_available(model_dir=md)
        if ok:
            self._batch_backend_status_lbl.setText("ready")
            self._batch_backend_status_lbl.setStyleSheet("font-size: 11px; color: #2a7;")
            self._batch_backend_status_lbl.setToolTip("The labeler's model folder was found.")
        else:
            self._batch_backend_status_lbl.setText("unavailable")
            self._batch_backend_status_lbl.setStyleSheet("font-size: 11px; color: #c33;")
            self._batch_backend_status_lbl.setToolTip(str(reason))

    def _build_radii_page(self) -> QWidget:
        page = RadiiCleaningPanel(self, allow_loaded_swc_run=False)
        page.log_message.connect(self.log_message.emit)
        return page

    def _build_validation_page(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)
        root.setAlignment(Qt.AlignTop)

        desc = QLabel(
            "Run the same validation checks as Validation tool, but for all SWC files in a folder."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("font-size: 12px; color: #555;")
        root.addWidget(desc)

        row = QHBoxLayout()
        row.setSpacing(6)
        self._btn_batch_validation_folder = self._compact_button(QPushButton("Select Folder"), 136)
        self._btn_batch_validation_folder.clicked.connect(self._on_browse_batch_validation_dir)
        row.addWidget(self._btn_batch_validation_folder)
        self._btn_batch_validate = self._compact_button(QPushButton("Run"), 60)
        self._btn_batch_validate.clicked.connect(self._on_run_batch_validation)
        row.addWidget(self._btn_batch_validate)
        self._btn_show_precheck = self._compact_button(QPushButton("Rule Guide"), 108)
        self._btn_show_precheck.clicked.connect(self.precheck_requested.emit)
        row.addWidget(self._btn_show_precheck)
        row.addStretch()
        root.addLayout(row)

        root.addLayout(self._make_selected_folder_row("_batch_validation_input_dir_lbl"))

        self._batch_validation_status = self._new_status_box()
        self._batch_validation_status.setPlainText("No batch validation run yet.")
        root.addWidget(self._batch_validation_status, stretch=1)
        return page

    def _build_simplify_page(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)
        root.setAlignment(Qt.AlignTop)

        desc = QLabel(
            "Run the same Simplification workflow on all SWC files in a folder.\n"
            "Each source SWC is updated in place and gets its own history operation."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("font-size: 12px; color: #555;")
        root.addWidget(desc)

        action_row = QHBoxLayout()
        action_row.setSpacing(6)
        self._btn_batch_simplify_folder = self._compact_button(QPushButton("Select Folder"), 136)
        self._btn_batch_simplify_folder.clicked.connect(self._on_browse_batch_simplify_dir)
        action_row.addWidget(self._btn_batch_simplify_folder)
        self._btn_batch_simplify = self._compact_button(QPushButton("Run"), 60)
        self._btn_batch_simplify.clicked.connect(self._on_run_batch_simplify)
        action_row.addWidget(self._btn_batch_simplify)
        action_row.addStretch()
        root.addLayout(action_row)

        root.addLayout(self._make_selected_folder_row("_batch_simplify_input_dir_lbl"))

        self._batch_simplify_status = self._new_status_box()
        root.addWidget(self._batch_simplify_status, stretch=1)
        return page

    def _build_index_clean_page(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)
        root.setAlignment(Qt.AlignTop)

        desc = QLabel(
            "Reorder and reindex all SWC files in a folder so parents come before children and IDs become continuous.\n"
            "Each source SWC is updated in place and gets its own history operation."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("font-size: 12px; color: #555;")
        root.addWidget(desc)

        action_row = QHBoxLayout()
        action_row.setSpacing(6)
        self._btn_batch_index_clean_folder = self._compact_button(QPushButton("Select Folder"), 136)
        self._btn_batch_index_clean_folder.clicked.connect(self._on_browse_batch_index_clean_dir)
        action_row.addWidget(self._btn_batch_index_clean_folder)
        self._btn_batch_index_clean = self._compact_button(QPushButton("Run"), 60)
        self._btn_batch_index_clean.clicked.connect(self._on_run_batch_index_clean)
        action_row.addWidget(self._btn_batch_index_clean)
        action_row.addStretch()
        root.addLayout(action_row)

        root.addLayout(self._make_selected_folder_row("_batch_index_clean_input_dir_lbl"))

        self._batch_index_clean_status = self._new_status_box()
        root.addWidget(self._batch_index_clean_status, stretch=1)
        return page

    def _new_status_box(self) -> QPlainTextEdit:
        w = QPlainTextEdit()
        w.setReadOnly(True)
        w.setMinimumHeight(120)
        w.setStyleSheet(
            "QPlainTextEdit {"
            "  background: #fafafa; border: 1px solid #ddd; color: #333;"
            "  font-family: Menlo, Consolas, monospace; font-size: 12px;"
            "}"
        )
        self._status_boxes.append(w)
        return w

    def _show_report_popup(self, title: str, report_path: str | None):
        if not report_path:
            return
        try:
            ReportPopupDialog.open_report(self, title=title, report_path=str(report_path))
        except Exception as e:  # noqa: BLE001
            self.log_message.emit(f"Could not open report popup: {e}")

    # --------------------------------------------------------- Public operations
    def run_split_folder(self):
        self._on_run_split_folder()

    def run_auto_typing_batch(self):
        self._on_run_batch_check()

    def set_active_subtab(self, name: str):
        # Kept for compatibility with older callers.
        _ = name

    # --------------------------------------------------------- Batch logic
    def _set_status(self, text: str, target: QPlainTextEdit | None = None):
        if target is not None:
            target.setPlainText(text)
        self.log_message.emit(text)

    def _on_edit_auto_typing_json(self):
        if self._config_dialog is None:
            self._config_dialog = _AutoTypingConfigDialog(self)
            self._config_dialog.saved.connect(self._set_status)
        self._config_dialog.reload_from_source()
        self._config_dialog.show()
        self._config_dialog.raise_()
        self._config_dialog.activateWindow()

    def _on_browse_split_input_dir(self) -> None:
        in_folder = QFileDialog.getExistingDirectory(self, "Choose folder containing SWC files")
        if in_folder:
            self._set_input_dir("_split_input_dir", self._split_input_dir_lbl, in_folder)

    def _on_split_folder(self):
        # Compatibility wrapper for older callers.
        self._on_run_split_folder()

    def _on_run_split_folder(self):
        in_folder = (self._split_input_dir or "").strip()
        if not in_folder:
            self._set_status("Select an input folder before running Split.", self._split_status)
            return
        if not os.path.isdir(in_folder):
            self._set_status(f"Selected input folder does not exist:\n{in_folder}", self._split_status)
            return
        try:
            result = split_folder(in_folder)
        except Exception as e:
            self._set_status(f"Folder split failed:\n{e}", self._split_status)
            return

        summary = [
            "Folder split completed.",
            f"Folder: {result['folder']}",
            f"Output folder: {result.get('out_dir', '')}",
            f"Processed: {result['files_total']} SWC file(s)",
            f"Split files: {result['files_split']}",
            f"Skipped (<=1 soma-root cell): {result['files_skipped']}",
            f"Saved split files: {result['trees_saved']}",
            f"Failures: {len(result['failures'])}",
        ]
        if result["failures"]:
            summary.extend(["", "First errors:"])
            summary.extend(result["failures"][:5])
        if result.get("log_path"):
            summary.extend(["", f"Report file: {result.get('log_path')}"])
        self._set_status("\n".join(summary), self._split_status)
        self._show_report_popup("Batch Split Report", result.get("log_path"))

    def _on_run_batch_check(self):
        if self._batch_worker_thread is not None and self._batch_worker_thread.isRunning():
            self._set_status(
                "Auto-labeling batch is already running.",
                self._batch_status_box,
            )
            return

        folder_path = (self._batch_input_dir or "").strip()
        if not folder_path:
            self._set_status(
                "Select an input folder before running Auto Label.",
                self._batch_status_box,
            )
            return
        if not os.path.isdir(folder_path):
            self._set_status(
                f"Selected input folder does not exist:\n{folder_path}",
                self._batch_status_box,
            )
            return

        swc_files = [
            f for f in os.listdir(folder_path)
            if is_swc_data_file(os.path.join(folder_path, f))
        ]
        if not swc_files:
            self._set_status(
                f"No .swc files found in:\n{folder_path}",
                self._batch_status_box,
            )
            return

        opts = BatchOptions(
            soma=True,
            axon=True,
            apic=True,
            basal=True,
            rad=False,
            zip_output=False,
            cell_type=self._batch_cell_type_combo.currentData() or "auto",
            flag_enabled=self._batch_flag_enabled.isChecked(),
            flag_budget=float(self._batch_flag_budget.value()) / 100.0,
        )

        md = (self._batch_model_dir or "").strip() or None
        config_overrides: dict = {}
        if md:
            config_overrides["model_dir"] = md
        config_overrides["cell_type"] = self._batch_cell_type_combo.currentData() or "auto"
        config_overrides["flag_enabled"] = self._batch_flag_enabled.isChecked()
        config_overrides["flag_budget"] = float(self._batch_flag_budget.value()) / 100.0

        # Hand off to a worker thread so the UI stays responsive while
        # the engine processes potentially many files.
        self._batch_run_id += 1
        run_id = int(self._batch_run_id)
        total = len(swc_files)

        self._set_batch_running(True, total, folder_path)

        self._batch_worker_thread = QThread(self)
        self._batch_worker = _AutoLabelBatchWorker(
            run_id, folder_path, opts, config_overrides,
        )
        self._batch_worker.moveToThread(self._batch_worker_thread)
        self._batch_worker_thread.started.connect(self._batch_worker.run)
        self._batch_worker.progress.connect(self._on_batch_progress)
        self._batch_worker.finished.connect(self._on_batch_finished)
        self._batch_worker.failed.connect(self._on_batch_failed)
        self._batch_worker.finished.connect(self._batch_worker_thread.quit)
        self._batch_worker.failed.connect(self._batch_worker_thread.quit)
        self._batch_worker_thread.finished.connect(self._cleanup_batch_worker_refs)
        self._batch_worker_thread.start()

    def _set_batch_running(self, running: bool, total: int = 0, folder_path: str = "") -> None:
        """Toggle running state for the batch auto-label panel — disables
        controls and shows the progress bar while a worker is in flight."""
        self._btn_run_batch_check.setEnabled(not running)
        self._btn_edit_auto_cfg.setEnabled(not running)
        self._batch_btn_browse_input.setEnabled(not running)
        self._batch_edit_model_dir.setEnabled(not running)
        self._batch_btn_browse_model.setEnabled(not running)
        self._batch_cell_type_combo.setEnabled(not running)
        self._batch_flag_enabled.setEnabled(not running)
        self._batch_flag_budget.setEnabled(not running)
        self._batch_btn_flag_review.setEnabled(not running and bool(self._last_flag_review_path))

        self._batch_progress.setVisible(running)
        self._batch_progress_label.setVisible(running)
        if running:
            self._batch_progress.setRange(0, max(int(total), 1))
            self._batch_progress.setValue(0)
            self._batch_progress_label.setText(
                f"Starting auto-labeling on {int(total)} file(s) in:\n{folder_path}"
            )
            self._set_status(
                f"Running auto-labeling on {int(total)} file(s)…",
                self._batch_status_box,
            )

    @Slot(int, int, str)
    def _on_batch_progress(self, idx: int, total: int, name: str) -> None:
        if total > 0 and self._batch_progress.maximum() != total:
            self._batch_progress.setRange(0, int(total))
        self._batch_progress.setValue(int(idx))
        self._batch_progress_label.setText(f"Processing {idx + 1} / {total} — {name}")

    @Slot(int, object)
    def _on_batch_finished(self, run_id: int, result: object) -> None:
        if int(run_id) != int(self._batch_run_id):
            return
        # Bring the bar to 100% before hiding it.
        self._batch_progress.setValue(self._batch_progress.maximum())

        lines = [
            "Auto-labeling batch processing completed.",
            f"Folder: {getattr(result, 'folder', '')}",
            "Each processed source file was updated and recorded in its own history.",
            f"SWC files detected: {getattr(result, 'files_total', 0)}",
            f"Labeled: {getattr(result, 'files_processed', 0)}",
            f"Labeled with warnings (a compartment is missing): {getattr(result, 'files_with_warnings', 0)}",
            _not_labeled_line(result),
            f"Total nodes processed: {getattr(result, 'total_nodes', 0)}",
            f"Type changes: {getattr(result, 'total_type_changes', 0)}",
            f"Radius changes: {getattr(result, 'total_radius_changes', 0)}",
        ]
        review_path = getattr(result, "flag_review_path", None)
        budget = getattr(result, "flag_budget", None)
        if review_path:
            lines.append(
                f"Flagged for review: {getattr(result, 'files_flagged', 0)} "
                f"(the riskiest {float(budget or 0.10):.0%} of each cell class)")
            lines.append(f"Flag review: {review_path}")
        zip_path = getattr(result, "zip_path", None)
        if zip_path:
            lines.append(f"ZIP output: {zip_path}")
        per_file = list(getattr(result, "per_file", []) or [])
        if per_file:
            lines.append("")
            lines.append("Per-file summary:")
            lines.extend(per_file[:25])
            if len(per_file) > 25:
                lines.append(f"... ({len(per_file) - 25} more in the report file)")
        failures = list(getattr(result, "failures", []) or [])
        if failures:
            lines.append("")
            lines.append("Not labeled:")
            lines.extend(failures)
        log_path = getattr(result, "log_path", None)
        if log_path:
            lines.extend(["", f"Report file: {log_path}"])

        self._set_status("\n".join(lines), self._batch_status_box)
        self._last_flag_review_path = str(review_path or "")
        self._set_batch_running(False)
        if review_path:
            self.flag_review_requested.emit(str(review_path))
        else:
            self._show_report_popup("Auto-Typing Batch Report", log_path)

    @Slot(int, str)
    def _on_batch_failed(self, run_id: int, message: str) -> None:
        if int(run_id) != int(self._batch_run_id):
            return
        self._set_status(
            f"Auto labeling batch failed:\n{message}",
            self._batch_status_box,
        )
        self._set_batch_running(False)

    @Slot()
    def _cleanup_batch_worker_refs(self) -> None:
        self._batch_worker = None
        self._batch_worker_thread = None

    def _on_browse_batch_validation_dir(self) -> None:
        folder_path = QFileDialog.getExistingDirectory(
            self, "Select folder with SWC files for batch validation"
        )
        if folder_path:
            self._set_input_dir(
                "_validation_input_dir",
                self._batch_validation_input_dir_lbl,
                folder_path,
            )

    def _on_run_batch_validation(self):
        folder_path = (self._validation_input_dir or "").strip()
        if not folder_path:
            msg = "Select an input folder before running Validation."
            self._set_status(msg, self._batch_validation_status)
            return
        if not os.path.isdir(folder_path):
            msg = f"Selected input folder does not exist:\n{folder_path}"
            self._set_status(msg, self._batch_validation_status)
            return
        try:
            out = run_batch_validation(folder_path)
        except Exception as e:  # noqa: BLE001
            msg = f"Batch validation failed: {e}"
            self._set_status(msg, self._batch_validation_status)
            return

        totals = dict(out.get("summary_total", {}))
        msg = (
            f"Batch validation completed: files={out.get('files_validated', 0)}/"
            f"{out.get('files_total', 0)}, failed_files={out.get('files_failed', 0)}, "
            f"checks_total={totals.get('total', 0)}, pass={totals.get('pass', 0)}, "
            f"warn={totals.get('warning', 0)}, fail={totals.get('fail', 0)}"
        )
        if out.get("log_path"):
            msg = f"{msg} | report={out.get('log_path')}"
        self._set_status(msg, self._batch_validation_status)
        self.batch_validation_ready.emit(out)

    def _on_browse_batch_simplify_dir(self) -> None:
        folder_path = QFileDialog.getExistingDirectory(self, "Select folder with SWC files for batch simplification")
        if folder_path:
            self._set_input_dir(
                "_simplify_input_dir",
                self._batch_simplify_input_dir_lbl,
                folder_path,
            )

    def _on_run_batch_simplify(self):
        folder_path = (self._simplify_input_dir or "").strip()
        if not folder_path:
            self._set_status("Select an input folder before running Simplification.", self._batch_simplify_status)
            return
        if not os.path.isdir(folder_path):
            self._set_status(f"Selected input folder does not exist:\n{folder_path}", self._batch_simplify_status)
            return
        try:
            out = run_batch_simplification(folder_path)
        except Exception as e:  # noqa: BLE001
            self._set_status(f"Batch simplification failed:\n{e}", self._batch_simplify_status)
            return

        per_file = list(out.get("per_file", []))
        failures = list(out.get("failures", []))
        lines = [
            "Batch Simplification Completed",
            "------------------------------",
            f"Folder: {out.get('folder', folder_path)}",
            "Each processed source file was updated and recorded in its own history.",
            f"Detected SWC files: {int(out.get('files_total', 0))}",
            f"Processed: {int(out.get('files_processed', 0))}",
            f"Failed: {int(out.get('files_failed', 0))}",
            "",
            "Per-file summary:",
            *per_file[:100],
        ]
        if len(per_file) > 100:
            lines.append(f"... ({len(per_file) - 100} more)")
        if failures:
            lines.extend(["", "Errors:", *failures[:50]])
            if len(failures) > 50:
                lines.append(f"... ({len(failures) - 50} more)")
        report_path = str(out.get("log_path", ""))
        if report_path:
            lines.extend(["", f"Report file: {report_path}"])
        self._set_status("\n".join(lines), self._batch_simplify_status)
        self._show_report_popup("Batch Simplification Report", report_path)

    def _on_browse_batch_index_clean_dir(self) -> None:
        folder_path = QFileDialog.getExistingDirectory(self, "Select folder with SWC files for batch index clean")
        if folder_path:
            self._set_input_dir(
                "_index_clean_input_dir",
                self._batch_index_clean_input_dir_lbl,
                folder_path,
            )

    def _on_run_batch_index_clean(self):
        folder_path = (self._index_clean_input_dir or "").strip()
        if not folder_path:
            self._set_status("Select an input folder before running Index Clean.", self._batch_index_clean_status)
            return
        if not os.path.isdir(folder_path):
            self._set_status(f"Selected input folder does not exist:\n{folder_path}", self._batch_index_clean_status)
            return
        try:
            out = run_batch_index_clean(folder_path)
        except Exception as e:  # noqa: BLE001
            self._set_status(f"Batch index clean failed:\n{e}", self._batch_index_clean_status)
            return

        per_file = list(out.get("per_file", []))
        failures = list(out.get("failures", []))
        lines = [
            "Batch Index Clean Completed",
            "---------------------------",
            f"Folder: {out.get('folder', folder_path)}",
            "Each processed source file was updated and recorded in its own history.",
            f"Detected SWC files: {int(out.get('files_total', 0))}",
            f"Processed: {int(out.get('files_processed', 0))}",
            f"Failed: {int(out.get('files_failed', 0))}",
            "",
            "Per-file summary:",
            *per_file[:100],
        ]
        if len(per_file) > 100:
            lines.append(f"... ({len(per_file) - 100} more)")
        if failures:
            lines.extend(["", "Errors:", *failures[:50]])
            if len(failures) > 50:
                lines.append(f"... ({len(failures) - 50} more)")
        report_path = str(out.get("log_path", ""))
        if report_path:
            lines.extend(["", f"Report file: {report_path}"])
        self._set_status("\n".join(lines), self._batch_index_clean_status)
        self._show_report_popup("Batch Index Clean Report", report_path)
