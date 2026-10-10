"""Main window layout for the issue-driven SWC Studio workspace."""

import os
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from PySide6.QtCore import QEvent, QRect, QSize, QThread, Qt, Signal, Slot
from PySide6.QtGui import QAction, QDragEnterEvent, QDropEvent, QFontMetrics, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDockWidget,
    QDialog,
    QFrame,
    QGroupBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLayout,
    QMainWindow,
    QMenuBar,
    QPlainTextEdit,
    QPushButton,
    QMessageBox,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .auto_typing_guide import AutoTypingGuideWidget
from .auto_typing_workers import _AutoLabelFileWorker, _TypeSuspicionWorker
from .flag_review_window import FlagReviewPanel
from .batch_tab import BatchTabWidget
from .constants import SWC_COLS, label_for_type
from .context_inspector import ContextInspectorWidget
from .custom_type_dialog import DefineCustomTypesDialog
from .editor_tab import EditorTab
from .edit_history import DataFrameUndoHistory
from .geometry_editing_panel import GeometryEditingPanel
from .issue_panel import IssuePanelWidget
from .manual_radii_panel import ManualRadiiPanel
from .neuron_3d_widget import Neuron3DWidget
from .radii_cleaning_panel import RadiiCleaningPanel
from .simplification_panel import SimplificationPanel
from .swc_table_widget import SWCTableWidget
from .validation_auto_label_panel import ValidationAutoLabelPanel
from .validation_tab import ValidationIndexCleanWidget, ValidationPrecheckWidget, ValidationTabWidget
from swcstudio.core.issues import (
    build_issue_list,
    issues_from_validation_report,
)
from swcstudio.core.custom_types import save_custom_type_definitions
from swcstudio.tools.geometry_editing.features.operations import (
    delete_node as geometry_delete_node,
    delete_subtree as geometry_delete_subtree,
    disconnect_branch,
    insert_node_between,
    move_selection_by_anchor_absolute,
    path_between_nodes,
    reconnect_branch,
    subtree_node_ids,
)
from swcstudio.core.radii_cleaning import radii_stats_by_type
from swcstudio.core.auto_typing_results import (
    auto_typing_result_to_dataframe as auto_label_result_to_dataframe,
    merge_labeled_types_only as auto_label_merge_types_only,
)
from swcstudio.core.reporting import (
    auto_typing_log_path_for_file,
    correction_summary_log_path_for_file,
    format_correction_summary_report_text,
    format_auto_typing_report_text,
    format_simplification_report_text,
    simplification_log_path_for_file,
    validation_index_clean_detail_lines,
    write_text_report,
)
from swcstudio.core.swc_io import SWC_HEADER_ATTR, parse_swc_text_preserve_tokens, write_swc_to_bytes_preserve_tokens
from swcstudio.core.validation_engine import consolidate_complex_somas_array
from swcstudio.tools.morphology_editing.features.simplification import simplify_dataframe
from swcstudio.tools.validation.features.auto_typing import BatchOptions, run_file as run_validation_auto_typing_file


# ---------------------------------------------------------------------------
# Token-fidelity helper for GUI-side provenance commits.
# ---------------------------------------------------------------------------
#
# The GUI's pandas dataframes don't carry the per-cell *_str token columns
# that swc_io's writer uses to round-trip exact number formatting (e.g.
# "2.5390625000000e+00"). Without those, the writer falls back to Python's
# default float repr ("2.5390625"), and the provenance diff then flags every
# numeric cell as "changed" — even rows the user never touched.
#
# This helper merges the original tokens from the previous on-disk state
# (op.input_bytes inside tracked_op) into the GUI's new_df, by node id,
# whenever the typed value still matches the original. That makes the
# diff faithful: only fields the user actually changed get a fresh repr.

_TOKEN_COLS = ("id_str", "type_str", "x_str", "y_str", "z_str",
               "radius_str", "parent_str")


def _enrich_df_with_original_tokens(new_df: pd.DataFrame,
                                    input_bytes: bytes | None) -> pd.DataFrame:
    """Return ``new_df`` augmented with original *_str token columns.

    For each row whose id matches a row in the parsed input bytes, the
    original token strings are attached as the corresponding *_str columns.
    Rows whose id didn't exist before, or rows lacking an id, simply don't
    receive token columns — the writer will format them with default repr,
    which is the correct behavior for genuinely new content.
    """
    if input_bytes is None or new_df is None or new_df.empty:
        return new_df
    if all(c in new_df.columns for c in _TOKEN_COLS):
        return new_df  # GUI already supplied tokens; nothing to do
    if "id" not in new_df.columns:
        return new_df
    try:
        from swcstudio.core.swc_io import parse_swc_text_preserve_tokens  # noqa: PLC0415
        prev = parse_swc_text_preserve_tokens(
            input_bytes.decode("utf-8", errors="ignore")
        )
    except Exception:
        return new_df
    if prev is None or prev.empty:
        return new_df
    # Build a lookup table id → original token columns.
    have = [c for c in _TOKEN_COLS if c in prev.columns]
    if not have:
        return new_df
    lookup = prev.set_index(prev["id"].astype(int))[have]
    out = new_df.copy()
    out.attrs.update(getattr(prev, "attrs", {}) or {})
    ids = out["id"].astype(int)
    for col in have:
        out[col] = ids.map(lookup[col])
    return out


def _copy_swc_metadata(dst: pd.DataFrame, src: pd.DataFrame | None) -> pd.DataFrame:
    """Carry SWC comment/header metadata across dataframe-only operations."""
    if not isinstance(dst, pd.DataFrame):
        return dst
    if isinstance(src, pd.DataFrame):
        for key, value in getattr(src, "attrs", {}).items():
            if key not in dst.attrs or not dst.attrs.get(key):
                dst.attrs[key] = value
    return dst


# Opening more new files than this at once asks first: every tab builds its own
# 3D views, projections and dendrogram, so many tabs use a lot of memory.
_MANY_FILES_CONFIRM = 10


def _path_key(path: str | Path) -> str:
    """Comparable form of a file path: resolved, absolute, case-folded on Windows."""
    try:
        resolved = str(Path(path).resolve())
    except Exception:
        resolved = os.path.abspath(str(path))
    return os.path.normcase(resolved)


def _suggest_closed_output_path(base: str | Path, *, timestamp: str | None = None) -> Path:
    """Return a non-existing Save As path without creating directories."""
    source = Path(base)
    ts = str(timestamp or datetime.now().strftime("%Y%m%d_%H%M%S"))
    candidate = source.parent / f"{source.stem}_closed_{ts}.swc"
    i = 1
    while candidate.exists():
        candidate = source.parent / f"{source.stem}_closed_{ts}_{i}.swc"
        i += 1
    return candidate


@dataclass
class _DocumentState:
    """Open SWC document state bound to one editor tab/window."""

    editor: EditorTab
    controls: QWidget
    df: pd.DataFrame
    filename: str
    file_path: str
    original_df: pd.DataFrame | None = None
    session_started_at: str = ""
    session_operations: list[dict] = field(default_factory=list)
    session_seq: int = 0
    is_preview: bool = False
    source_editor: EditorTab | None = None
    preview_kind: str = ""
    validation_report: dict | None = None
    issues: list[dict] = field(default_factory=list)
    issue_status_overrides: dict[str, str] = field(default_factory=dict)
    fixed_issue_count: int = 0
    pending_resolved_issue_ids: set[str] = field(default_factory=set)
    selected_issue_id: str = ""
    recovery_path: str = ""
    edit_history: DataFrameUndoHistory = field(default_factory=DataFrameUndoHistory)
    last_auto_label_result: dict | None = None
    last_auto_label_options: dict | None = None
    pending_type_suspicion_issues: list[dict] | None = None
    auto_label_preview_df: pd.DataFrame | None = None
    auto_label_preview_base_df: pd.DataFrame | None = None
    last_simplification_result: dict | None = None


class _CanvasTabs(QTabWidget):
    """Tab widget for open SWC documents with drag-out support."""

    detach_requested = Signal(int, int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTabsClosable(True)
        self.setMovable(True)
        self.setDocumentMode(True)
        self._drag_tab_index = -1
        self._drag_start_global = None
        self.tabBar().installEventFilter(self)

    def eventFilter(self, watched, event):
        if watched is self.tabBar():
            et = event.type()
            if et == QEvent.MouseButtonPress and getattr(event, "button", lambda: None)() == Qt.LeftButton:
                pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
                self._drag_tab_index = self.tabBar().tabAt(pos)
                self._drag_start_global = (
                    event.globalPosition().toPoint() if hasattr(event, "globalPosition") else event.globalPos()
                )
            elif et == QEvent.MouseMove and self._drag_tab_index >= 0 and self._drag_start_global is not None:
                now_global = (
                    event.globalPosition().toPoint() if hasattr(event, "globalPosition") else event.globalPos()
                )
                if (now_global - self._drag_start_global).manhattanLength() >= QApplication.startDragDistance():
                    local = self.tabBar().mapFromGlobal(now_global)
                    if not self.tabBar().rect().adjusted(-32, -24, 32, 24).contains(local):
                        idx = int(self._drag_tab_index)
                        self._drag_tab_index = -1
                        self._drag_start_global = None
                        self.detach_requested.emit(idx, int(now_global.x()), int(now_global.y()))
                        return True
            elif et in (QEvent.MouseButtonRelease, QEvent.Leave):
                self._drag_tab_index = -1
                self._drag_start_global = None
        return super().eventFilter(watched, event)


class _FlowLayout(QLayout):
    """Compact wrapping layout for toolbar-style buttons."""

    def __init__(self, parent=None, *, margin: int = 0, h_spacing: int = 6, v_spacing: int = 6):
        super().__init__(parent)
        self._items = []
        self._h_spacing = int(h_spacing)
        self._v_spacing = int(v_spacing)
        self.setContentsMargins(margin, margin, margin, margin)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QSize(margins.left() + margins.right(), margins.top() + margins.bottom())
        return size

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        margins = self.contentsMargins()
        effective = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x = effective.x()
        y = effective.y()
        line_height = 0

        for item in self._items:
            widget = item.widget()
            if widget is not None and widget.isHidden():
                continue
            item_size = item.sizeHint()
            next_x = x + item_size.width() + self._h_spacing
            if x > effective.x() and next_x - self._h_spacing > effective.right() + 1:
                x = effective.x()
                y += line_height + self._v_spacing
                next_x = x + item_size.width() + self._h_spacing
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(x, y, item_size.width(), item_size.height()))
            x = next_x
            line_height = max(line_height, item_size.height())

        return y + line_height - rect.y() + margins.bottom()


class _DetachedEditorWindow(QMainWindow):
    """Floating window hosting one detached SWC editor."""

    editor_closing = Signal(QWidget)

    def __init__(self, editor: EditorTab, title: str, parent=None):
        super().__init__(parent)
        self._editor = editor
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setWindowTitle(title)
        self.resize(980, 760)
        self.setCentralWidget(editor)

    def closeEvent(self, event):
        parent = self.parent()
        if parent is not None and hasattr(parent, "_request_detached_editor_close"):
            try:
                allow_close = bool(parent._request_detached_editor_close(self._editor))
            except Exception:
                allow_close = True
            if not allow_close:
                event.ignore()
                return
        else:
            self.editor_closing.emit(self._editor)
        super().closeEvent(event)


class SWCMainWindow(QMainWindow):
    """Top-level app window with tabbed top bar, workspace, side panels, and edit log."""

    swc_loaded = Signal(pd.DataFrame, str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("SWC Studio")
        self.setAcceptDrops(False)

        self._df: pd.DataFrame | None = None
        self._filename: str = ""
        self._file_path: str = ""
        self._recent_paths: list[str] = []
        self._active_tool: str = ""
        self._documents: dict[EditorTab, _DocumentState] = {}
        self._detached_windows: dict[EditorTab, _DetachedEditorWindow] = {}
        self._control_wrappers: dict[int, QScrollArea] = {}
        self._floating_control_dialogs: dict[str, QDialog] = {}
        self._simplify_preview_by_source: dict[EditorTab, EditorTab] = {}
        self._simplify_source_by_preview: dict[EditorTab, EditorTab] = {}
        self._simplify_result_by_preview: dict[EditorTab, dict] = {}
        self._auto_label_preview_by_source: dict[EditorTab, EditorTab] = {}
        self._auto_label_source_by_preview: dict[EditorTab, EditorTab] = {}
        self._auto_label_result_by_preview: dict[EditorTab, dict] = {}
        self._batch_has_results: bool = False
        self._closing_app: bool = False
        self._runtime_log_lines: list[str] = []
        self._type_legend_visible: bool = True

        # Auto-label single-file worker state. Run on a QThread so the
        # GUI stays responsive during the (~1-second) inference.
        self._validation_auto_label_run_id: int = 0
        self._validation_auto_label_worker: _AutoLabelFileWorker | None = None
        self._validation_auto_label_thread: QThread | None = None
        self._validation_auto_label_tmp_path: Path | None = None
        self._validation_auto_label_source_doc: _DocumentState | None = None
        self._validation_auto_label_options: object | None = None

        # Type-suspicion worker — runs the auto-typing engine in the
        # background after a fast-path issue list is shown so the
        # ``Likely wrong labels`` rows can stream in without making the
        # user wait for inference on file open.
        self._type_suspicion_run_id: int = 0
        self._type_suspicion_worker: _TypeSuspicionWorker | None = None
        self._type_suspicion_thread: QThread | None = None
        self._type_suspicion_doc: _DocumentState | None = None

        self._build_ui()
        self._build_status_bar()
        self._apply_initial_window_geometry()

    def _apply_initial_window_geometry(self) -> None:
        """Start within the available screen work area to avoid Qt geometry warnings."""
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            self.resize(1480, 920)
            return

        available = screen.availableGeometry()
        target_width = min(1480, max(1100, available.width() - 120))
        target_height = min(920, max(760, available.height() - 120))
        target_width = min(target_width, max(200, available.width()))
        target_height = min(target_height, max(200, available.height()))
        self.resize(target_width, target_height)

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        self.setDockOptions(QMainWindow.AllowNestedDocks | QMainWindow.AllowTabbedDocks)
        self.setDockNestingEnabled(True)

        # Use an in-window top strip instead of the OS menu bar.
        self.menuBar().setVisible(False)

        # ---------------- Top unified bar: menu + tools/features ----------------
        self._top_bar = self._build_top_bar()

        # ---------------- Center workspace ----------------
        self._canvas_empty = QWidget()
        self._canvas_empty.setStyleSheet("background: #e9eef5; border-radius: 18px;")

        self._canvas_tabs = _CanvasTabs()
        self._canvas_tabs.currentChanged.connect(self._on_document_tab_changed)
        self._canvas_tabs.tabCloseRequested.connect(self._on_document_tab_close_requested)
        self._canvas_tabs.detach_requested.connect(self._on_document_detach_requested)

        self._batch_canvas = EditorTab()
        self._batch_canvas.set_mode(EditorTab.MODE_BATCH)

        self._canvas_stack = QStackedWidget()
        self._canvas_stack.addWidget(self._canvas_empty)
        self._canvas_stack.addWidget(self._canvas_tabs)
        self._canvas_stack.addWidget(self._batch_canvas)

        # ---------------- Side docks: issue navigation + contextual inspector ----------------
        self._data_tabs = QTabWidget()
        self._data_tabs.setMinimumWidth(0)
        self._data_tabs.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self._data_tabs.tabBar().setUsesScrollButtons(False)
        self._issue_panel = IssuePanelWidget()
        self._issue_panel.issue_selected.connect(self._on_issue_selected)
        self._issue_panel.rule_guide_requested.connect(self._on_precheck_requested)
        self._issue_panel.export_swc_requested.connect(self._on_issue_panel_export_swc_requested)
        self._table_widget = SWCTableWidget()
        self._table_widget.setMinimumWidth(0)
        self._table_widget.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)

        self._info_label = QLabel("No SWC file loaded.")
        self._info_label.setWordWrap(True)
        self._info_label.setStyleSheet("font-size: 13px; color: #444; padding: 8px;")
        file_panel = QWidget()
        file_layout = QVBoxLayout(file_panel)
        file_layout.setContentsMargins(6, 6, 6, 6)
        file_layout.setSpacing(8)
        file_layout.addWidget(self._info_label, stretch=0)
        file_layout.addWidget(self._table_widget, stretch=1)

        self._edit_log_text = QPlainTextEdit()
        self._edit_log_text.setReadOnly(True)
        self._edit_log_text.setStyleSheet(
            "QPlainTextEdit {"
            "  background: #fafafa; border: 1px solid #ddd; color: #333;"
            "  font-family: Menlo, Consolas, monospace; font-size: 12px;"
            "}"
        )
        self._edit_log_text.setPlainText("No morphology edits recorded for this session yet.")

        self._data_tabs.addTab(self._issue_panel, "Issues")
        self._data_tabs.addTab(file_panel, "SWC File")
        self._control_tabs = QTabWidget()
        self._control_tabs.setMinimumWidth(0)
        self._control_tabs.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self._control_tabs.tabBar().hide()
        self._batch_tab = BatchTabWidget()
        self._batch_tab.batch_validation_ready.connect(self._on_batch_validation_ready)
        self._batch_tab.precheck_requested.connect(self._on_precheck_requested)
        self._validation_tab = ValidationTabWidget(as_panel=False)
        self._validation_tab.precheck_requested.connect(self._on_precheck_requested)
        self._validation_tab.report_ready.connect(self._on_validation_report_ready)
        self._validation_tab.result_activated.connect(self._on_validation_result_activated)
        self._validation_index_clean = ValidationIndexCleanWidget(self)
        self._validation_index_clean.index_clean_requested.connect(self._on_validation_index_clean_requested)
        self._validation_auto_label_panel = ValidationAutoLabelPanel(self)
        self._validation_auto_label_panel.guide_requested.connect(self._show_auto_typing_guide_floating)
        self._validation_auto_label_panel.log_message.connect(lambda msg: self._append_log(msg, "AUTO"))
        self._validation_auto_label_panel.process_requested.connect(
            self._on_validation_auto_label_process_requested
        )
        self._validation_radii_panel = RadiiCleaningPanel(self)
        self._validation_radii_panel.log_message.connect(lambda msg: self._append_log(msg, "RADII"))
        self._validation_radii_panel.loaded_apply_requested.connect(self._on_validation_radii_apply_requested)
        self._manual_radii_panel = ManualRadiiPanel(self)
        self._manual_radii_panel.apply_requested.connect(self._on_manual_radii_apply_requested)
        self._geometry_panel = GeometryEditingPanel(self)
        self._geometry_panel.log_message.connect(lambda msg: self._append_log(msg, "GEOM"))
        self._geometry_panel.selection_preview_changed.connect(self._on_geometry_selection_preview_changed)
        self._geometry_panel.focus_requested.connect(self._on_geometry_focus_requested)
        self._geometry_panel.move_selection_requested.connect(self._on_geometry_move_selection_requested)
        self._geometry_panel.reconnect_requested.connect(self._on_geometry_reconnect_requested)
        self._geometry_panel.disconnect_requested.connect(self._on_geometry_disconnect_requested)
        self._geometry_panel.delete_node_requested.connect(self._on_geometry_delete_node_requested)
        self._geometry_panel.delete_subtree_requested.connect(self._on_geometry_delete_subtree_requested)
        self._geometry_panel.insert_node_requested.connect(self._on_geometry_insert_node_requested)
        self._validation_precheck = ValidationPrecheckWidget()
        self._auto_typing_guide = AutoTypingGuideWidget()
        self._simplification_panel = SimplificationPanel(self)
        self._simplification_panel.log_message.connect(lambda msg: self._append_log(msg, "SIMPLIFY"))
        self._simplification_panel.process_requested.connect(self._on_simplification_process_requested)
        self._viz_control = self._build_visualization_control_panel()
        self._control_tabs.currentChanged.connect(self._on_control_tab_changed)
        self._set_control_tabs_for_feature("")
        self._context_inspector = ContextInspectorWidget(self)
        self._context_inspector.apply_suggested_fix_requested.connect(self._on_apply_suggested_fix_requested)
        self._context_inspector.open_tool_requested.connect(self._on_context_open_tool_requested)
        self._context_inspector.skip_issue_requested.connect(self._on_skip_issue_requested)
        self._context_inspector.custom_action_requested.connect(self._on_context_custom_action_requested)
        self._inspector_host = QWidget()
        self._inspector_layout = QVBoxLayout(self._inspector_host)
        self._inspector_layout.setContentsMargins(0, 0, 0, 0)
        self._inspector_layout.setSpacing(10)
        self._inspector_layout.setAlignment(Qt.AlignTop)
        self._inspector_layout.addWidget(self._context_inspector, stretch=0)
        self._inspector_layout.addWidget(self._control_tabs, stretch=1)

        self._data_dock = QDockWidget("Issue Navigator", self)
        self._data_dock.setObjectName("DataExplorerDock")
        self._data_dock.setFeatures(
            QDockWidget.DockWidgetMovable
            | QDockWidget.DockWidgetFloatable
            | QDockWidget.DockWidgetClosable
        )
        self._data_dock.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        self._data_dock.setMinimumWidth(24)
        self._data_dock.setWidget(self._data_tabs)
        self._make_panel_freely_resizable(self._data_dock)
        self.addDockWidget(Qt.LeftDockWidgetArea, self._data_dock)

        self._control_dock = QDockWidget("Inspector", self)
        self._control_dock.setObjectName("ControlCenterDock")
        self._control_dock.setFeatures(
            QDockWidget.DockWidgetMovable
            | QDockWidget.DockWidgetFloatable
            | QDockWidget.DockWidgetClosable
        )
        self._control_dock.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        self._control_dock.setMinimumWidth(24)
        self._control_dock.setWidget(self._inspector_host)
        self._make_panel_freely_resizable(self._control_dock)
        self.addDockWidget(Qt.RightDockWidgetArea, self._control_dock)

        self._precheck_dock = QDockWidget("Rule Guide", self)
        self._precheck_dock.setObjectName("ValidationPrecheckDock")
        self._precheck_dock.setFeatures(
            QDockWidget.DockWidgetMovable
            | QDockWidget.DockWidgetFloatable
            | QDockWidget.DockWidgetClosable
        )
        self._precheck_dock.setAllowedAreas(
            Qt.TopDockWidgetArea | Qt.BottomDockWidgetArea | Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea
        )
        self._precheck_dock.setWidget(self._validation_precheck)
        self._make_panel_freely_resizable(self._precheck_dock)
        self.addDockWidget(Qt.TopDockWidgetArea, self._precheck_dock)
        self._precheck_dock.hide()

        self._auto_guide_dock = QDockWidget("Auto Typing Guide", self)
        self._auto_guide_dock.setObjectName("AutoTypingGuideDock")
        self._auto_guide_dock.setFeatures(
            QDockWidget.DockWidgetMovable
            | QDockWidget.DockWidgetFloatable
            | QDockWidget.DockWidgetClosable
        )
        self._auto_guide_dock.setAllowedAreas(
            Qt.TopDockWidgetArea | Qt.BottomDockWidgetArea | Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea
        )
        self._auto_guide_dock.setWidget(self._auto_typing_guide)
        self._make_panel_freely_resizable(self._auto_guide_dock)
        self.addDockWidget(Qt.TopDockWidgetArea, self._auto_guide_dock)
        self._auto_guide_dock.hide()

        # Flag Review: a batch's labeled cells ranked for curator review.
        self._flag_review_panel = FlagReviewPanel(self)
        self._flag_review_dock = QDockWidget("Flag Review", self)
        self._flag_review_dock.setObjectName("FlagReviewDock")
        self._flag_review_dock.setFeatures(
            QDockWidget.DockWidgetMovable
            | QDockWidget.DockWidgetFloatable
            | QDockWidget.DockWidgetClosable
        )
        self._flag_review_dock.setAllowedAreas(
            Qt.TopDockWidgetArea | Qt.BottomDockWidgetArea | Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea
        )
        self._flag_review_dock.setWidget(self._flag_review_panel)
        self._make_panel_freely_resizable(self._flag_review_dock)
        self.addDockWidget(Qt.BottomDockWidgetArea, self._flag_review_dock)
        self._flag_review_dock.hide()
        self._flag_review_panel.open_files_requested.connect(self._on_flag_review_open_requested)
        self._flag_review_panel.log_message.connect(lambda msg: self._append_log(msg, "FLAG"))
        self._batch_tab.flag_review_requested.connect(self._open_flag_review)

        self._batch_tab.log_message.connect(lambda msg: self._append_log(msg, "BATCH"))

        # ---------------- Bottom edit log ----------------
        self._bottom_log_title = QLabel("Edit Log")
        self._bottom_log_title.setStyleSheet("font-size: 13px; font-weight: 700; color: #132238; padding: 0 2px;")
        self._edit_log_text.setMinimumHeight(110)
        self._edit_log_text.setMaximumHeight(240)

        # ---------------- Root layout ----------------
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(6, 6, 6, 0)
        root.setSpacing(6)
        root.addWidget(self._top_bar, stretch=0)
        root.addWidget(self._canvas_stack, stretch=1)
        root.addWidget(self._bottom_log_title, stretch=0)
        root.addWidget(self._edit_log_text, stretch=0)
        self.setCentralWidget(central)
        self._apply_modern_theme()

        self._refresh_canvas_surface()
        self._reset_layout()
        self._append_log("UI initialized. Open SWC files from File menu.", "INFO")

    def _build_top_bar(self) -> QWidget:
        top_bg = "#f7f9fc"
        top_fg = "#132238"
        top_border = "#d8e0eb"
        top_hover = "#edf3fb"

        panel = QFrame()
        panel.setFrameShape(QFrame.StyledPanel)
        panel.setStyleSheet(
            "QFrame {"
            f"  background: {top_bg}; border: 1px solid {top_border}; border-radius: 18px;"
            "}"
        )

        root = QVBoxLayout(panel)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        self._home_menu_bar = QMenuBar(panel)
        self._home_menu_bar.setNativeMenuBar(False)
        self._home_menu_bar.setStyleSheet(
            "QMenuBar {"
            f"  background: {top_bg}; border-bottom: 1px solid {top_border}; color: {top_fg};"
            "}"
            "QMenuBar::item {"
            "  padding: 8px 12px; background: transparent; border-radius: 8px;"
            "}"
            "QMenuBar::item:selected {"
            f"  background: {top_hover};"
            "}"
            "QMenu {"
            f"  background: {top_bg}; color: {top_fg}; border: 1px solid {top_border};"
            "}"
            "QMenu::item {"
            "  padding: 6px 20px 6px 24px; background: transparent;"
            "}"
            "QMenu::item:selected {"
            f"  background: {top_hover};"
            "}"
        )
        self._populate_home_menus(self._home_menu_bar)
        root.addWidget(self._home_menu_bar, stretch=0)

        group = QGroupBox("Tools")
        group.setStyleSheet(
            "QGroupBox { border: 1px solid #d8e0eb; margin-top: 8px; border-radius: 14px; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 6px; color: #5f6b7a; }"
            "QPushButton#toolMenuItem {"
            "  background: transparent; color: #132238; border: none;"
            "  padding: 8px 12px; text-align: left; border-radius: 10px;"
            "}"
            "QPushButton#toolMenuItem:hover {"
            "  background: #edf3fb;"
            "}"
            "QPushButton#toolMenuItem:checked {"
            "  background: #d9e8fb; border: 1px solid #84aee8; font-weight: 700;"
            "}"
            "QPushButton#featureBtn {"
            f"  background: {top_bg}; color: {top_fg}; border: 1px solid {top_border};"
            "  border-radius: 10px; padding: 8px 12px;"
            "}"
            "QPushButton#featureBtn:hover {"
            f"  background: {top_hover};"
            "}"
            "QPushButton#featureBtn:checked {"
            "  background: #d9e8fb; border: 1px solid #84aee8; font-weight: 700;"
            "}"
        )
        group_layout = QVBoxLayout(group)
        group_layout.setContentsMargins(8, 10, 8, 8)
        group_layout.setSpacing(6)

        tools_row = _FlowLayout(h_spacing=8, v_spacing=6)
        tools_row.setContentsMargins(0, 0, 0, 0)

        tool_defs = [
            ("Batch Processing", "batch"),
            ("Validation", "validation"),
            ("Visualization", "visualization"),
            ("Morphology Editing", "morphology_editing"),
            ("Geometry Editing", "geometry_editing"),
        ]

        normal_fm = QFontMetrics(self.font())
        bold_font = self.font()
        bold_font.setBold(True)
        bold_fm = QFontMetrics(bold_font)
        tool_btn_min_height = max(self.fontMetrics().height() + 18, 36)
        self._tool_menu_buttons: dict[str, QPushButton] = {}
        for label, key in tool_defs:
            btn = QPushButton(label)
            btn.setObjectName("toolMenuItem")
            btn.setCheckable(True)
            btn.setFlat(True)
            btn.setMinimumWidth(max(normal_fm.horizontalAdvance(label), bold_fm.horizontalAdvance(label)) + 46)
            btn.setMinimumHeight(tool_btn_min_height)
            btn.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _=False, k=key: self._activate_feature(k))
            btn.setProperty("tool_key", key)
            tools_row.addWidget(btn)
            self._tool_menu_buttons[key] = btn
        group_layout.addLayout(tools_row)

        self._feature_strip = QWidget()
        self._feature_strip.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self._feature_row = _FlowLayout(self._feature_strip, h_spacing=6, v_spacing=6)
        self._feature_row.setContentsMargins(0, 0, 0, 0)
        group_layout.addWidget(self._feature_strip)

        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(10)

        # Keep for backward compatibility with existing update calls, but hide from UI.
        self._feature_label = QLabel("")
        self._feature_label.setVisible(False)

        self._current_file_label = QLabel("Current file: (none)")
        self._current_file_label.setStyleSheet("font-size: 12px; color: #555;")
        self._current_file_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._current_file_label.setMinimumWidth(0)
        self._current_file_label.setMaximumWidth(360)
        status_row.addWidget(self._current_file_label)
        self._set_current_file_label_text("")

        status_row.addStretch()
        group_layout.addLayout(status_row)

        root.addWidget(group)
        return panel

    def _populate_home_menus(self, menu: QMenuBar):
        menu.clear()

        # File
        file_menu = menu.addMenu("File")
        open_action = QAction("Open", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self._on_open)
        file_menu.addAction(open_action)

        save_action = QAction("Save", self)
        save_action.setShortcut("Ctrl+S")
        save_action.triggered.connect(self._on_save)
        file_menu.addAction(save_action)

        save_as_action = QAction("Save As", self)
        save_as_action.setShortcut("Ctrl+Shift+S")
        save_as_action.triggered.connect(self._on_save_as)
        file_menu.addAction(save_as_action)

        export_action = QAction("Export", self)
        export_action.triggered.connect(self._on_export)
        file_menu.addAction(export_action)

        self._recent_menu = file_menu.addMenu("Recent Files")
        self._recent_menu.addAction("(empty)").setEnabled(False)

        file_menu.addSeparator()
        exit_action = QAction("Exit", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # Edit
        edit_menu = menu.addMenu("Edit")
        undo_action = QAction("Undo", self)
        undo_action.setShortcut("Ctrl+Z")
        undo_action.triggered.connect(self._undo_edit)
        edit_menu.addAction(undo_action)

        redo_action = QAction("Redo", self)
        redo_action.setShortcut("Ctrl+Shift+Z")
        redo_action.triggered.connect(self._redo_edit)
        edit_menu.addAction(redo_action)

        pref_action = QAction("Preferences", self)
        pref_action.triggered.connect(
            lambda: self._append_log("Preferences dialog is not implemented yet.", "INFO")
        )
        edit_menu.addAction(pref_action)

        # View
        view_menu = menu.addMenu("View")
        show_log_action = QAction("Show/Hide Edit Log", self)
        show_log_action.triggered.connect(
            lambda: self._toggle_log_panel(not self._edit_log_text.isVisible())
        )
        view_menu.addAction(show_log_action)

        view_menu.addSeparator()
        cam_iso_action = QAction("Camera Isometric", self)
        cam_iso_action.triggered.connect(lambda: self._set_camera("iso"))
        view_menu.addAction(cam_iso_action)
        cam_top_action = QAction("Camera Top", self)
        cam_top_action.triggered.connect(lambda: self._set_camera("top"))
        view_menu.addAction(cam_top_action)
        cam_front_action = QAction("Camera Front", self)
        cam_front_action.triggered.connect(lambda: self._set_camera("front"))
        view_menu.addAction(cam_front_action)
        cam_side_action = QAction("Camera Side", self)
        cam_side_action.triggered.connect(lambda: self._set_camera("side"))
        view_menu.addAction(cam_side_action)

        view_menu.addSeparator()
        self._type_legend_action = QAction("Show Node-Type Legend", self)
        self._type_legend_action.setCheckable(True)
        self._type_legend_action.setChecked(self._type_legend_visible)
        self._type_legend_action.toggled.connect(self._set_type_legend_visible)
        view_menu.addAction(self._type_legend_action)

        # Window
        window_menu = menu.addMenu("Window")
        reset_layout_action = QAction("Reset Layout", self)
        reset_layout_action.triggered.connect(self._reset_layout)
        window_menu.addAction(reset_layout_action)

        show_data_action = QAction("Show/Hide Issue Navigator", self)
        show_data_action.triggered.connect(
            lambda: self._toggle_data_panel(not self._data_dock.isVisible())
        )
        window_menu.addAction(show_data_action)

        show_control_action = QAction("Show/Hide Inspector", self)
        show_control_action.triggered.connect(
            lambda: self._toggle_control_panel(not self._control_dock.isVisible())
        )
        window_menu.addAction(show_control_action)

        show_precheck_action = QAction("Show/Hide Rule Guide", self)
        show_precheck_action.triggered.connect(
            lambda: self._toggle_precheck_panel(not self._precheck_dock.isVisible())
        )
        window_menu.addAction(show_precheck_action)

        show_auto_guide_action = QAction("Show/Hide Auto Typing Guide", self)
        show_auto_guide_action.triggered.connect(
            lambda: self._toggle_auto_typing_guide_panel(not self._auto_guide_dock.isVisible())
        )
        window_menu.addAction(show_auto_guide_action)

        show_flag_review_action = QAction("Show/Hide Flag Review", self)
        show_flag_review_action.triggered.connect(
            lambda: self._flag_review_dock.setVisible(not self._flag_review_dock.isVisible())
        )
        window_menu.addAction(show_flag_review_action)

        window_menu.addSeparator()
        # Standard "next/previous child window" keys: Ctrl+Tab / Ctrl+Shift+Tab
        # on Windows and Linux, the platform equivalent on macOS.
        self._next_document_action = QAction("Next File", self)
        self._next_document_action.setShortcuts(QKeySequence.NextChild)
        self._next_document_action.triggered.connect(lambda: self._cycle_document(1))
        window_menu.addAction(self._next_document_action)

        self._previous_document_action = QAction("Previous File", self)
        self._previous_document_action.setShortcuts(QKeySequence.PreviousChild)
        self._previous_document_action.triggered.connect(lambda: self._cycle_document(-1))
        window_menu.addAction(self._previous_document_action)

        # History (provenance) - see PROVENANCE_SPEC.md for the full surface.
        history_menu = menu.addMenu("History")
        open_history_action = QAction("Open History Browser…", self)
        open_history_action.triggered.connect(self._on_open_history_browser)
        history_menu.addAction(open_history_action)

        init_history_action = QAction("Initialize History for This File…", self)
        init_history_action.triggered.connect(self._on_init_history_for_current_file)
        history_menu.addAction(init_history_action)

        # Help
        help_menu = menu.addMenu("Help")
        quick_action = QAction("Quick Manual", self)
        quick_action.triggered.connect(self._show_quick_manual)
        help_menu.addAction(quick_action)
        short_action = QAction("Shortcuts", self)
        short_action.triggered.connect(self._show_shortcuts)
        help_menu.addAction(short_action)
        help_menu.addSeparator()
        update_action = QAction("Check for Updates...", self)
        update_action.triggered.connect(self._check_for_updates)
        help_menu.addAction(update_action)
        help_menu.addSeparator()
        about_action = QAction("About", self)
        about_action.triggered.connect(self._show_about_dialog)
        help_menu.addAction(about_action)

    def _build_visualization_control_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        row1 = QHBoxLayout()
        b_iso = QPushButton("Isometric")
        b_iso.clicked.connect(lambda: self._set_camera("iso"))
        row1.addWidget(b_iso)
        b_top = QPushButton("Top")
        b_top.clicked.connect(lambda: self._set_camera("top"))
        row1.addWidget(b_top)
        b_front = QPushButton("Front")
        b_front.clicked.connect(lambda: self._set_camera("front"))
        row1.addWidget(b_front)
        b_side = QPushButton("Side")
        b_side.clicked.connect(lambda: self._set_camera("side"))
        row1.addWidget(b_side)
        layout.addLayout(row1)

        row2 = QHBoxLayout()
        b_reset = QPushButton("Reset Camera")
        b_reset.clicked.connect(self._reset_camera)
        row2.addWidget(b_reset)
        row2.addWidget(QLabel("Render mode:"))
        self._render_combo = QComboBox()
        self._render_combo.addItem("Lines", Neuron3DWidget.MODE_LINES)
        self._render_combo.addItem("Spheres", Neuron3DWidget.MODE_SPHERES)
        self._render_combo.addItem("Frustum", Neuron3DWidget.MODE_FRUSTUM)
        self._render_combo.currentIndexChanged.connect(self._on_render_mode_changed)
        row2.addWidget(self._render_combo)
        layout.addLayout(row2)

        hint = QLabel(
            "Visualization mode shows:\n"
            "- one 3D view on top\n"
            "- three 2D projections (top/front/side) below"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("font-size: 12px; color: #555;")
        layout.addWidget(hint)
        layout.addStretch()
        return panel

    def _build_status_bar(self):
        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._issue_status_label = QLabel("Issues: 0")
        self._issue_status_label.setStyleSheet("font-size: 12px; color: #5f6b7a; padding-right: 8px;")
        self._status.addPermanentWidget(self._issue_status_label)
        self._status.showMessage("Ready - open an SWC file to start.")

    def _apply_modern_theme(self):
        self.setStyleSheet(
            """
            QMainWindow {
              background: #f4f7fb;
            }
            QMainWindow::separator {
              background: #c6d3e3;
              width: 14px;
              height: 14px;
            }
            QMainWindow::separator:hover {
              background: #84aee8;
            }
            QDockWidget {
              font-size: 13px;
              color: #132238;
            }
            QDockWidget::title {
              background: #eef3f9;
              color: #132238;
              text-align: left;
              padding: 10px 12px;
              border: 1px solid #d8e0eb;
              border-bottom: none;
            }
            QTabWidget::pane, QScrollArea, QPlainTextEdit, QTreeWidget, QTableWidget {
              background: #ffffff;
              border: 1px solid #d8e0eb;
              border-radius: 12px;
            }
            QTabBar::tab {
              background: #eef3f9;
              color: #415062;
              border: 1px solid #d8e0eb;
              padding: 8px 12px;
              margin-right: 4px;
              border-top-left-radius: 10px;
              border-top-right-radius: 10px;
            }
            QTabBar::tab:selected {
              background: #ffffff;
              color: #132238;
            }
            QPushButton {
              background: #ffffff;
              color: #132238;
              border: 1px solid #cfd9e6;
              border-radius: 10px;
              padding: 7px 12px;
            }
            QPushButton:hover {
              background: #f4f7fb;
            }
            QPushButton:checked {
              background: #d9e8fb;
              border-color: #84aee8;
            }
            QLineEdit, QComboBox, QDoubleSpinBox {
              background: #ffffff;
              border: 1px solid #cfd9e6;
              border-radius: 10px;
              padding: 6px 10px;
            }
            QLabel {
              color: #132238;
            }
            QStatusBar {
              background: #eef3f9;
              border-top: 1px solid #d8e0eb;
            }
            """
        )

    def _clear_feature_row(self):
        while self._feature_row.count() > 0:
            item = self._feature_row.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _tool_feature_labels(self, tool_key: str | None = None) -> list[str]:
        key = str(tool_key if tool_key is not None else (self._active_tool or "")).strip().lower()
        mapping = {
            "batch": ["Split", "Validation", "Auto Label Editing", "Radii Cleaning", "Simplification", "Index Clean"],
            "validation": ["Validation", "Index Clean"],
            "visualization": ["View Controls"],
            "morphology_editing": ["Manual Label Editing", "Auto Label Editing", "Manual Radii Editing", "Auto Radii Editing"],
            "dendrogram": ["Manual Label Editing", "Auto Label Editing", "Manual Radii Editing", "Auto Radii Editing"],
            "geometry_editing": ["Geometry Editing", "Simplification"],
        }
        return list(mapping.get(key, []))

    def _refresh_top_feature_buttons(self):
        self._clear_feature_row()
        self._top_feature_buttons: list[QPushButton] = []

        labels = self._tool_feature_labels()
        if not labels:
            return

        normal_fm = QFontMetrics(self.font())
        bold_font = self.font()
        bold_font.setBold(True)
        bold_fm = QFontMetrics(bold_font)
        feature_btn_min_height = max(self.fontMetrics().height() + 18, 38)
        for label in labels:
            btn = QPushButton(label)
            btn.setObjectName("featureBtn")
            btn.setCheckable(True)
            label_width = max(normal_fm.horizontalAdvance(label), bold_fm.horizontalAdvance(label))
            btn.setMinimumWidth(max(86, label_width + 34))
            btn.setMinimumHeight(feature_btn_min_height)
            btn.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            btn.clicked.connect(lambda _=False, lb=label: self._on_top_feature_button_clicked(lb))
            self._feature_row.addWidget(btn)
            self._top_feature_buttons.append(btn)

        self._sync_top_feature_button_selection()

    def _sync_top_feature_button_selection(self):
        buttons = getattr(self, "_top_feature_buttons", None)
        if not buttons:
            return

        active_label = ""
        idx = self._control_tabs.currentIndex()
        if idx >= 0:
            active_label = (self._control_tabs.tabText(idx) or "").strip().lower()

        for btn in buttons:
            btn.setChecked(bool(active_label) and btn.text().strip().lower() == active_label)

    def _sync_tool_tab_selection(self):
        buttons = getattr(self, "_tool_menu_buttons", None)
        if not buttons:
            return
        for key, btn in buttons.items():
            btn.setChecked(key == (self._active_tool or ""))

    def eventFilter(self, watched, event):
        # Tool changes should happen on explicit click only.
        return super().eventFilter(watched, event)

    def _on_top_feature_button_clicked(self, label: str):
        target = str(label or "").strip().lower()
        if not target:
            return

        # Keep button highlight even when no SWC/control tab is currently available.
        for btn in getattr(self, "_top_feature_buttons", []):
            btn.setChecked(btn.text().strip().lower() == target)

        for i in range(self._control_tabs.count()):
            if (self._control_tabs.tabText(i) or "").strip().lower() == target:
                self._control_tabs.setCurrentIndex(i)
                self._control_tabs.setVisible(self._control_tabs.count() > 0)
                return

        if self._active_tool in ("morphology_editing", "dendrogram", "geometry_editing") and self._active_document() is None:
            self._append_log("Open an SWC file to use editing feature controls.", "INFO")

    def _wrap_control_widget(self, inner: QWidget) -> QWidget:
        key = id(inner)
        existing = self._control_wrappers.get(key)
        if existing is not None:
            return existing

        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        area.setMinimumWidth(0)
        area.setMinimumHeight(0)

        host = QWidget()
        host.setMinimumSize(0, 0)
        host.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        lay = QVBoxLayout(host)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(0)
        lay.setAlignment(Qt.AlignTop)
        lay.addWidget(inner)

        area.setWidget(host)
        self._control_wrappers[key] = area
        return area

    def _detach_control_wrapper(self, wrapper: QWidget) -> None:
        for i in range(self._control_tabs.count()):
            if self._control_tabs.widget(i) is wrapper:
                self._control_tabs.removeTab(i)
                break
        wrapper.setParent(None)

    def _show_floating_control_dialog(self, dialog_key: str, title: str, inner: QWidget, *, width: int = 520, height: int = 720) -> None:
        existing = self._floating_control_dialogs.get(dialog_key)
        if existing is not None:
            existing.show()
            existing.raise_()
            existing.activateWindow()
            return

        wrapper = self._wrap_control_widget(inner)
        self._detach_control_wrapper(wrapper)

        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setSizeGripEnabled(True)
        dialog.setMinimumSize(180, 140)
        dialog.resize(width, height)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(wrapper)

        def _restore() -> None:
            wrapper.setParent(None)
            self._floating_control_dialogs.pop(dialog_key, None)
            if self._active_document() is not None:
                if self._active_tool in ("morphology_editing", "dendrogram"):
                    self._set_control_tabs_for_feature("morphology_editing")
                elif self._active_tool:
                    self._set_control_tabs_for_feature(self._active_tool)
            dialog.deleteLater()

        dialog.finished.connect(lambda _result=0: _restore())
        self._floating_control_dialogs[dialog_key] = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _relax_widget_size_constraints(self, widget: QWidget | None) -> None:
        if widget is None:
            return
        try:
            widget.setMinimumSize(0, 0)
        except Exception:
            pass
        try:
            widget.setMaximumSize(16777215, 16777215)
        except Exception:
            pass
        try:
            widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        except Exception:
            pass
        for child in widget.findChildren(QWidget):
            try:
                child.setMinimumSize(0, 0)
            except Exception:
                pass

    def _make_panel_freely_resizable(self, dock: QDockWidget | None) -> None:
        if dock is None:
            return
        try:
            dock.setMinimumSize(24, 24)
        except Exception:
            pass
        try:
            dock.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        except Exception:
            pass
        self._relax_widget_size_constraints(dock.widget())

    # --------------------------------------------------------- Document helpers
    def _active_editor(self) -> EditorTab | None:
        idx = self._canvas_tabs.currentIndex()
        if idx < 0:
            return None
        w = self._canvas_tabs.widget(idx)
        return w if isinstance(w, EditorTab) else None

    def _active_document(self) -> _DocumentState | None:
        ed = self._active_editor()
        if ed is None:
            return None
        return self._documents.get(ed)

    def _editor_mode_for_feature(self) -> str:
        key = (self._active_tool or "").strip().lower()
        if key == "visualization":
            return EditorTab.MODE_VIS
        if key in ("morphology_editing", "dendrogram", "geometry_editing"):
            return EditorTab.MODE_DENDRO
        return EditorTab.MODE_CANVAS

    def _apply_editor_modes(self):
        mode = self._editor_mode_for_feature()
        for doc in self._documents.values():
            doc.editor.set_mode(mode)

    def _clear_active_editor_selection_if_unfocused(self):
        doc = self._active_document()
        if doc is None:
            return
        if str(doc.selected_issue_id or "").strip():
            return
        doc.editor.clear_selection()

    def _refresh_canvas_surface(self):
        if self._active_tool == "batch":
            if self._is_batch_validation_control_active():
                self._batch_canvas.set_mode(EditorTab.MODE_BATCH)
                self._canvas_stack.setCurrentWidget(self._batch_canvas)
            else:
                self._canvas_stack.setCurrentWidget(self._canvas_empty)
            return

        if self._canvas_tabs.count() > 0:
            self._canvas_stack.setCurrentWidget(self._canvas_tabs)
        else:
            self._canvas_stack.setCurrentWidget(self._canvas_empty)

    def _sync_from_active_document(self, *, auto_run_validation: bool):
        doc = self._active_document()
        if doc is None:
            self._df = None
            self._filename = ""
            self._file_path = ""
            self._set_current_file_label_text("")
            self._table_widget.load_dataframe(pd.DataFrame(columns=SWC_COLS), "No SWC loaded")
            self._info_label.setText("No SWC file loaded.")
            self._edit_log_text.setPlainText("No morphology edits recorded for this session yet.")
            self._issue_panel.clear_issues("Open an SWC to populate issues automatically.")
            self._set_issue_status([])
            self._context_inspector.clear()
            self._validation_radii_panel.set_loaded_swc(None, "", "")
            self._validation_index_clean.set_loaded_swc(None)
            self._manual_radii_panel.set_loaded_swc(None, "")
            self._geometry_panel.set_loaded_swc(None)
            self._geometry_panel.set_current_node(None)
            self._batch_tab.set_loaded_swc(None, "", "")
            self._refresh_canvas_surface()
            self._refresh_simplification_panel_state()
            self._refresh_validation_auto_label_panel_state()
            return

        self._df = doc.df
        self._filename = doc.filename
        self._file_path = doc.file_path
        self._set_current_file_label_text(doc.filename)

        n_roots = int((doc.df["parent"] == -1).sum())
        n_soma = int((doc.df["type"] == 1).sum())
        self._table_widget.load_dataframe(doc.df, doc.filename)
        self._update_info_label(doc.df, n_roots, n_soma, filename=doc.filename)
        self._refresh_morph_edit_tab(doc)
        self._validation_radii_panel.set_loaded_swc(doc.df, doc.filename, doc.file_path)
        self._validation_index_clean.set_loaded_swc(doc.df)
        self._manual_radii_panel.set_loaded_swc(doc.df, doc.filename)
        self._geometry_panel.set_loaded_swc(doc.df)
        self._geometry_panel.set_current_node(None)
        self._batch_tab.set_loaded_swc(doc.df, doc.filename, doc.file_path)
        should_auto_run_validation = bool(auto_run_validation and not doc.is_preview and doc.validation_report is None)
        self._validation_tab.load_swc(
            doc.df,
            doc.filename,
            file_path=doc.file_path,
            auto_run=should_auto_run_validation,
        )
        if should_auto_run_validation:
            self._issue_panel.clear_issues("Running validation and issue detectors...")
            self._set_issue_status([])
            self._context_inspector.clear()
            doc.editor.clear_selection()
        self._apply_issue_state(doc)
        self._refresh_canvas_surface()
        self._refresh_simplification_panel_state()
        self._refresh_validation_auto_label_panel_state()
        self._refresh_edit_history_state(doc)

    def _start_morphology_session(self, doc: _DocumentState):
        if doc.is_preview:
            return
        doc.session_started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        doc.session_operations = []
        doc.session_seq = 0
        doc.issue_status_overrides = {}
        doc.fixed_issue_count = 0
        doc.pending_resolved_issue_ids = set()
        doc.selected_issue_id = ""
        doc.recovery_path = ""
        doc.edit_history.reset()
        doc.last_auto_label_result = None
        doc.last_auto_label_options = None
        doc.pending_type_suspicion_issues = None
        doc.auto_label_preview_df = None
        doc.auto_label_preview_base_df = None
        doc.editor.set_edit_history_state(False, False)
        if doc is self._active_document():
            self._refresh_morph_edit_tab(doc)

    def _connect_editor_signals(self, editor: EditorTab):
        editor.df_changed.connect(lambda new_df, ed=editor: self._on_editor_df_changed(ed, new_df))
        editor.node_selected.connect(lambda swc_id, ed=editor: self._on_editor_node_selected(ed, swc_id))

    def _push_document_history(
        self,
        doc: _DocumentState,
        before: pd.DataFrame | None,
        after: pd.DataFrame | None,
    ):
        if (
            doc is None
            or doc.is_preview
            or not isinstance(before, pd.DataFrame)
            or not isinstance(after, pd.DataFrame)
        ):
            return
        doc.edit_history.push(before, after)
        self._refresh_edit_history_state(doc)

    def _refresh_edit_history_state(self, doc: _DocumentState | None = None):
        row = doc or self._active_document()
        if row is None:
            return
        can_undo = (not row.is_preview) and row.edit_history.can_undo
        can_redo = (not row.is_preview) and row.edit_history.can_redo
        row.editor.set_edit_history_state(can_undo, can_redo)

    def _restore_document_history(self, doc: _DocumentState, *, direction: str):
        if doc is None or doc.is_preview:
            return False
        if direction == "Undo":
            restored = doc.edit_history.undo(doc.df)
        else:
            restored = doc.edit_history.redo(doc.df)
        if restored is None:
            return False
        self._apply_document_dataframe(
            doc,
            restored,
            event_title=direction,
            event_summary=f"{direction} one session edit step.",
            event_details=[],
            push_history=False,
            record_type_changes=True,
        )
        self._refresh_edit_history_state(doc)
        self._rerun_active_validation()
        return True

    def _undo_document(self, doc: _DocumentState | None) -> bool:
        if doc is None:
            return False
        return self._restore_document_history(doc, direction="Undo")

    def _redo_document(self, doc: _DocumentState | None) -> bool:
        if doc is None:
            return False
        return self._restore_document_history(doc, direction="Redo")

    def _snapshot_log_value(self, key: str, value):
        if value is None:
            return ""
        if key in {"id", "type", "parent"}:
            try:
                return str(int(value))
            except Exception:
                return str(value)
        if key in {"x", "y", "z", "radius"}:
            try:
                return f"{float(value):.6g}"
            except Exception:
                return str(value)
        return str(value)

    def _row_snapshot_for_log(self, row: pd.Series | dict) -> dict[str, str]:
        return {
            "id": self._snapshot_log_value("id", row["id"]),
            "type": self._snapshot_log_value("type", row["type"]),
            "parent": self._snapshot_log_value("parent", row["parent"]),
            "radius": self._snapshot_log_value("radius", row["radius"]),
            "x": self._snapshot_log_value("x", row["x"]),
            "y": self._snapshot_log_value("y", row["y"]),
            "z": self._snapshot_log_value("z", row["z"]),
        }

    def _format_snapshot_fields(self, snap: dict[str, str], keys: list[str]) -> str:
        labels = {
            "id": "id",
            "type": "type",
            "parent": "parent",
            "radius": "radius",
            "x": "x",
            "y": "y",
            "z": "z",
        }
        parts: list[str] = []
        for key in keys:
            val = str((snap or {}).get(key, "")).strip()
            if val == "":
                continue
            parts.append(f"{labels[key]}={val}")
        return ", ".join(parts)

    def _build_session_change_rows(
        self,
        old_df: pd.DataFrame | None,
        new_df: pd.DataFrame | None,
        *,
        id_map: dict[int, int] | None = None,
    ) -> list[dict]:
        if not isinstance(old_df, pd.DataFrame) and not isinstance(new_df, pd.DataFrame):
            return []

        old_lookup = {}
        new_lookup = {}
        if isinstance(old_df, pd.DataFrame) and not old_df.empty:
            old_lookup = {
                int(row["id"]): self._row_snapshot_for_log(row)
                for _, row in old_df.loc[:, ["id", "type", "x", "y", "z", "radius", "parent"]].iterrows()
            }
        if isinstance(new_df, pd.DataFrame) and not new_df.empty:
            new_lookup = {
                int(row["id"]): self._row_snapshot_for_log(row)
                for _, row in new_df.loc[:, ["id", "type", "x", "y", "z", "radius", "parent"]].iterrows()
            }

        change_rows: list[dict] = []
        used_old: set[int] = set()
        used_new: set[int] = set()
        compare_keys = ["id", "type", "parent", "radius", "x", "y", "z"]

        if isinstance(id_map, dict) and id_map:
            for old_id, new_id in sorted((int(k), int(v)) for k, v in id_map.items()):
                old_snap = old_lookup.get(old_id)
                new_snap = new_lookup.get(new_id)
                if old_snap is None or new_snap is None:
                    continue
                used_old.add(old_id)
                used_new.add(new_id)
                changed_keys = [key for key in compare_keys if str(old_snap.get(key, "")) != str(new_snap.get(key, ""))]
                if not changed_keys:
                    continue
                change_rows.append(
                    {
                        "node_id": f"{old_id}->{new_id}" if old_id != new_id else str(old_id),
                        "changed_keys": list(changed_keys),
                        "old_values": {key: str(old_snap.get(key, "")) for key in changed_keys},
                        "new_values": {key: str(new_snap.get(key, "")) for key in changed_keys},
                        "old_parameters": self._format_snapshot_fields(old_snap, changed_keys),
                        "new_parameters": self._format_snapshot_fields(new_snap, changed_keys),
                    }
                )

        for nid in sorted(set(old_lookup).intersection(new_lookup)):
            if nid in used_old or nid in used_new:
                continue
            old_snap = old_lookup[nid]
            new_snap = new_lookup[nid]
            used_old.add(nid)
            used_new.add(nid)
            changed_keys = [key for key in compare_keys if str(old_snap.get(key, "")) != str(new_snap.get(key, ""))]
            if not changed_keys:
                continue
            change_rows.append(
                {
                    "node_id": str(nid),
                    "changed_keys": list(changed_keys),
                    "old_values": {key: str(old_snap.get(key, "")) for key in changed_keys},
                    "new_values": {key: str(new_snap.get(key, "")) for key in changed_keys},
                    "old_parameters": self._format_snapshot_fields(old_snap, changed_keys),
                    "new_parameters": self._format_snapshot_fields(new_snap, changed_keys),
                }
            )
        for nid in sorted(set(old_lookup) - used_old):
            old_snap = old_lookup[nid]
            change_rows.append(
                {
                    "node_id": str(nid),
                    "changed_keys": ["id", "type", "parent", "radius", "x", "y", "z"],
                    "old_values": {key: str(old_snap.get(key, "")) for key in ["id", "type", "parent", "radius", "x", "y", "z"]},
                    "new_values": {},
                    "old_parameters": self._format_snapshot_fields(old_snap, ["id", "type", "parent", "radius", "x", "y", "z"]),
                    "new_parameters": "[deleted]",
                }
            )

        for nid in sorted(set(new_lookup) - used_new):
            new_snap = new_lookup[nid]
            change_rows.append(
                {
                    "node_id": str(nid),
                    "changed_keys": ["id", "type", "parent", "radius", "x", "y", "z"],
                    "old_values": {},
                    "new_values": {key: str(new_snap.get(key, "")) for key in ["id", "type", "parent", "radius", "x", "y", "z"]},
                    "old_parameters": "[inserted]",
                    "new_parameters": self._format_snapshot_fields(new_snap, ["id", "type", "parent", "radius", "x", "y", "z"]),
                }
            )

        return change_rows

    def _record_tracked_commit(
        self,
        doc: _DocumentState,
        new_df: pd.DataFrame,
        *,
        kind,
        params: dict,
        message: str = "",
    ) -> object | None:
        """Record a provenance commit for a single GUI mutation.

        Each Apply-button click is its own commit, mirroring
        the in-memory undo stack one-for-one. Future work may
        switch this to tracked_session-per-document with sub-ops per
        click; the per-handler call sites won't need to change.

        Returns the OpResult on success, None if the document has no
        on-disk path yet (untitled / preview docs are not tracked).
        Failures are logged and swallowed — provenance must never
        block a GUI edit from being applied to the in-memory display.

        Token-fidelity note: GUI dataframes don't carry the original
        per-cell ``*_str`` columns that the SWC writer uses to
        round-trip exact number formatting. Without that, the writer
        would reformat every number using Python's default float repr
        (e.g. ``2.5390625000000e+00`` → ``2.5390625``), which makes
        the structured diff flag every node as "changed" even when
        nothing actually moved. We re-attach the original tokens from
        the previous on-disk state before writing, so only fields
        whose typed values genuinely changed get reformatted.
        """
        if doc.is_preview or not getattr(doc, "file_path", ""):
            return None
        try:
            from swcstudio.core.provenance import OpKind, tracked_op  # noqa: PLC0415
            from swcstudio.core.swc_io import (  # noqa: PLC0415
                parse_swc_text_preserve_tokens,
                write_swc_to_bytes_preserve_tokens,
            )

            kind_value = kind.value if isinstance(kind, OpKind) else str(kind)
            with tracked_op(
                doc.file_path,
                kind=kind,
                params=dict(params or {}),
                message=message or f"GUI {kind_value}",
            ) as op:
                # Re-attach original *_str token columns from the
                # previous state for byte-faithful round-trip.
                enriched = _enrich_df_with_original_tokens(
                    new_df, op.input_bytes
                )
                op.set_output(write_swc_to_bytes_preserve_tokens(enriched))
            saved_df = new_df.copy()
            _copy_swc_metadata(saved_df, doc.df)
            doc.original_df = saved_df
            return op.result
        except Exception as e:  # noqa: BLE001
            # Never block a GUI edit because the history layer failed.
            # Surface the failure in the log so it's visible without
            # crashing the user's session.
            self._append_log(f"Provenance commit failed: {e}", "WARN")
            return None

    def _record_session_operation(
        self,
        doc: _DocumentState,
        *,
        title: str,
        summary: str,
        old_df: pd.DataFrame | None = None,
        new_df: pd.DataFrame | None = None,
        details: list[str] | None = None,
        id_map: dict[int, int] | None = None,
        change_rows: list[dict] | None = None,
    ):
        if doc.is_preview:
            return
        node_changes = list(change_rows or []) if isinstance(change_rows, list) else self._build_session_change_rows(old_df, new_df, id_map=id_map)
        stamped_rows: list[dict] = []
        for row in node_changes:
            doc.session_seq += 1
            stamped_rows.append(
                {
                    "seq": doc.session_seq,
                    "time": datetime.now().strftime("%H:%M:%S"),
                    "node_id": str(row.get("node_id", "")),
                    "changed_keys": list(row.get("changed_keys", []) or []),
                    "old_values": dict(row.get("old_values", {}) or {}),
                    "new_values": dict(row.get("new_values", {}) or {}),
                    "old_parameters": str(row.get("old_parameters", "")),
                    "new_parameters": str(row.get("new_parameters", "")),
                }
            )
        doc.session_operations.append(
            {
                "time": datetime.now().strftime("%H:%M:%S"),
                "title": str(title),
                "summary": str(summary),
                "details": list(details or []),
                "affected_nodes": len(stamped_rows),
                "changes": stamped_rows,
            }
        )
        if doc is self._active_document():
            self._refresh_morph_edit_tab(doc)

    def _record_session_event(
        self,
        doc: _DocumentState,
        *,
        kind: str,
        title: str,
        summary: str,
        details: list[str] | None = None,
    ):
        self._record_session_operation(
            doc,
            title=str(title),
            summary=str(summary),
            old_df=None,
            new_df=None,
            details=list(details or []),
        )

    def _refresh_morph_edit_tab(self, doc: _DocumentState | None = None):
        row = doc or self._active_document()
        if row is None or not row.session_operations:
            self._edit_log_text.setPlainText("No morphology edits recorded for this session yet.")
            return

        lines = [f"Session changes ({row.filename}):"]
        for op in row.session_operations:
            lines.extend(
                [
                    "",
                    f"[{str(op.get('time', ''))}] {str(op.get('title', ''))}",
                    f"Summary: {str(op.get('summary', ''))}",
                    f"Affected nodes: {int(op.get('affected_nodes', 0))}",
                ]
            )
            for detail in list(op.get("details", []) or []):
                lines.append(f"- {detail}")
            changes = list(op.get("changes", []) or [])
            if changes:
                lines.append(f"{'Seq':<5}{'Time':<10}{'NodeID':<14}{'OldParameters':<48}NewParameters")
                for c in changes:
                    lines.append(
                        f"{int(c.get('seq', 0)):<5}"
                        f"{str(c.get('time', '')):<10}"
                        f"{str(c.get('node_id', '')):<14}"
                        f"{str(c.get('old_parameters', '')):<48}"
                        f"{str(c.get('new_parameters', ''))}"
                    )

        self._edit_log_text.setPlainText("\n".join(lines))

    def _finalize_morphology_session(
        self,
        doc: _DocumentState,
        *,
        show_popup: bool,
        source_override: str | None = None,
    ) -> str | None:
        if not doc.session_operations:
            return None

        self._append_log(
            "Operation details are stored in the encrypted history archive. "
            "Open History Browser to review them.",
            "INFO",
        )
        doc.session_operations = []
        doc.session_seq = 0
        if doc is self._active_document():
            self._refresh_morph_edit_tab(doc)
        return None

    def _document_has_unsaved_edits(self, doc: _DocumentState) -> bool:
        if doc is None:
            return False
        original = doc.original_df
        current = doc.df
        if not isinstance(current, pd.DataFrame):
            return False
        if not isinstance(original, pd.DataFrame):
            return True
        try:
            return not original.equals(current)
        except Exception:  # noqa: BLE001
            return True

    def _recovery_directory(self) -> Path:
        path = Path.home() / ".swc_studio" / "recovery"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _recovery_path_for_document(self, doc: _DocumentState) -> Path:
        if str(doc.recovery_path or "").strip():
            return Path(doc.recovery_path)
        base_name = Path(doc.file_path).stem if str(doc.file_path or "").strip() else Path(doc.filename or "swc").stem
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = self._recovery_directory() / f"{base_name}_recovery_{ts}.swc"
        doc.recovery_path = str(path)
        return path

    def _write_recovery_copy(self, doc: _DocumentState):
        if doc is None or doc.is_preview or doc.df is None or doc.df.empty:
            return
        if not self._document_has_unsaved_edits(doc):
            return
        recovery_path = self._recovery_path_for_document(doc)
        self._write_swc_file(str(recovery_path), doc.df)

    def _clear_recovery_copy(self, doc: _DocumentState):
        if doc is None:
            return
        recovery_path = Path(doc.recovery_path) if str(doc.recovery_path or "").strip() else None
        doc.recovery_path = ""
        if recovery_path is None:
            return
        try:
            if recovery_path.exists():
                recovery_path.unlink()
        except Exception as e:  # noqa: BLE001
            self._append_log(f"Could not remove recovery copy {recovery_path}: {e}", "WARN")

    def _next_closed_output_path(self, doc: _DocumentState) -> Path:
        base = Path(doc.file_path) if str(doc.file_path or "").strip() else Path.cwd() / (doc.filename or "swc")
        return _suggest_closed_output_path(base)

    def _plan_document_close(self, doc: _DocumentState, *, app_closing: bool) -> dict | None:
        if doc is None or doc.is_preview:
            return {"kind": "close"}

        filename = doc.filename or "SWC"
        has_unsaved_edits = self._document_has_unsaved_edits(doc)
        has_history_operations = bool(doc.session_operations)
        title = "Exit SWC-Studio" if app_closing else "Close SWC File"

        if has_unsaved_edits:
            original_path = Path(doc.file_path) if str(doc.file_path or "").strip() else None
            default_save_as = self._next_closed_output_path(doc)
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle(title)
            box.setText(f"Close {filename}?")
            box.setInformativeText(
                "Save unsaved changes back to the original SWC file, "
                "or save a separate copy."
            )
            save_original_btn = box.addButton("Save Original", QMessageBox.AcceptRole)
            save_as_btn = box.addButton("Save As...", QMessageBox.ActionRole)
            box.addButton(QMessageBox.Cancel)
            box.setDefaultButton(save_original_btn)
            box.exec()

            clicked = box.clickedButton()
            if clicked is save_original_btn:
                if original_path is None:
                    return {"kind": "write_close_copy", "output_path": str(default_save_as)}
                return {"kind": "write_original", "output_path": str(original_path)}
            if clicked is save_as_btn:
                path, _ = QFileDialog.getSaveFileName(
                    self,
                    "Save Changed SWC As",
                    str(default_save_as),
                    "SWC Files (*.swc);;All Files (*)",
                )
                if not path:
                    self._append_log("Close cancelled while choosing a save location.", "INFO")
                    return None
                return {"kind": "write_close_copy", "output_path": str(path)}
            self._append_log(f"Close cancelled for {filename}.", "INFO")
            return None

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle(title)
        box.setText(f"Close {filename}?")
        if has_history_operations:
            box.setInformativeText(
                "No new SWC copy will be saved.\n"
                "Operation details are already stored in history."
            )
        else:
            box.setInformativeText("No unsaved changes.")
        close_btn = box.addButton("Close", QMessageBox.AcceptRole)
        box.addButton(QMessageBox.Cancel)
        box.setDefaultButton(close_btn)
        box.exec()
        if box.clickedButton() is not close_btn:
            self._append_log(f"Close cancelled for {filename}.", "INFO")
            return None
        return {"kind": "close"}

    def _apply_document_close_plan(self, doc: _DocumentState, plan: dict) -> bool:
        if doc is None:
            return True

        kind = str((plan or {}).get("kind", "close"))
        output_path = str((plan or {}).get("output_path", "") or "").strip()

        try:
            if kind == "write_original":
                target = output_path or str(doc.file_path or "")
                if not target:
                    raise ValueError("Missing original SWC path.")
                self._write_swc_file(target, doc.df)
                self._append_log(f"Saved original SWC: {target}", "INFO")
                self._after_document_write(doc, target, action_name="Save")
                self._finalize_morphology_session(doc, show_popup=False)
            elif kind == "write_close_copy":
                if not output_path:
                    raise ValueError("Missing output path for closing copy.")
                self._write_swc_file(output_path, doc.df)
                self._append_log(f"Changed SWC copy saved to: {output_path}", "INFO")
                self._finalize_morphology_session(doc, show_popup=False, source_override=output_path)
            elif kind == "close":
                self._finalize_morphology_session(doc, show_popup=False)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(
                self,
                "Close Failed",
                f"Could not finish closing {doc.filename or 'SWC'}.\n\n{e}",
            )
            self._append_log(f"Close failed for {doc.filename or 'SWC'}: {e}", "ERROR")
            return False

        self._clear_recovery_copy(doc)
        return True

    # --------------------------------------------------------- Controls per feature
    def _is_simplification_preview(self, doc: _DocumentState | None) -> bool:
        return bool(doc and doc.is_preview and doc.preview_kind == "simplification")

    def _source_document_for(self, doc: _DocumentState | None) -> _DocumentState | None:
        if doc is None:
            return None
        if self._is_simplification_preview(doc):
            src_editor = doc.source_editor or self._simplify_source_by_preview.get(doc.editor)
            if src_editor is None:
                return None
            return self._documents.get(src_editor)
        if self._is_validation_auto_label_preview(doc):
            src_editor = doc.source_editor or self._auto_label_source_by_preview.get(doc.editor)
            if src_editor is None:
                return None
            return self._documents.get(src_editor)
        return doc

    def _active_source_document(self) -> _DocumentState | None:
        return self._source_document_for(self._active_document())

    def _simplification_log_payload(
        self,
        source_doc: _DocumentState,
        result: dict,
        *,
        output_path: str | None,
    ) -> dict:
        return {
            "mode": "gui",
            "input_path": str(source_doc.file_path or source_doc.filename),
            "output_path": str(output_path or ""),
            "original_node_count": int(result.get("original_node_count", 0)),
            "new_node_count": int(result.get("new_node_count", 0)),
            "reduction_percent": float(result.get("reduction_percent", 0.0)),
            "params_used": dict(result.get("params_used", {})),
            "protected_counts": dict(result.get("protected_counts", {})),
            "removed_node_ids": list(result.get("removed_node_ids", [])),
        }

    def _write_simplification_log(self, payload: dict) -> str:
        input_path = str(payload.get("input_path", "") or "").strip()
        output_path = str(payload.get("output_path", "") or "").strip()

        if input_path:
            log_path = simplification_log_path_for_file(input_path)
        elif output_path:
            log_path = simplification_log_path_for_file(output_path)
        else:
            base = Path.cwd() / "simplified_preview.swc"
            log_path = simplification_log_path_for_file(base)

        return write_text_report(log_path, format_simplification_report_text(payload))

    def _resolve_simplification_context(self) -> tuple[_DocumentState | None, _DocumentState | None, dict | None]:
        active = self._active_document()
        if active is None:
            return None, None, None

        if self._is_simplification_preview(active):
            source_doc = self._source_document_for(active)
            result = self._simplify_result_by_preview.get(active.editor)
            return source_doc, active, result

        preview_editor = self._simplify_preview_by_source.get(active.editor)
        if preview_editor is None:
            return active, None, None

        preview_doc = self._documents.get(preview_editor)
        result = self._simplify_result_by_preview.get(preview_editor)
        return active, preview_doc, result

    def _remove_simplification_preview(self, preview_editor: EditorTab, *, switch_to_source: bool):
        preview_doc = self._documents.get(preview_editor)
        source_editor = self._simplify_source_by_preview.pop(preview_editor, None)
        if source_editor is not None:
            if self._simplify_preview_by_source.get(source_editor) is preview_editor:
                self._simplify_preview_by_source.pop(source_editor, None)
        self._simplify_result_by_preview.pop(preview_editor, None)

        if preview_doc is not None:
            self._documents.pop(preview_editor, None)

        idx = self._canvas_tabs.indexOf(preview_editor)
        if idx >= 0:
            self._canvas_tabs.removeTab(idx)

        float_win = self._detached_windows.pop(preview_editor, None)
        if float_win is not None:
            try:
                float_win.editor_closing.disconnect(self._on_detached_editor_closing)
            except Exception:
                pass
            float_win.close()

        if switch_to_source and source_editor is not None:
            src_idx = self._canvas_tabs.indexOf(source_editor)
            if src_idx >= 0:
                self._canvas_tabs.setCurrentIndex(src_idx)

    def _refresh_simplification_panel_state(self):
        source_doc = self._active_source_document()
        result = None if source_doc is None else source_doc.last_simplification_result
        if source_doc is None or not isinstance(result, dict):
            self._simplification_panel.set_preview_state(False, None, None)
            return
        summary = {
            "original_node_count": int(result.get("original_node_count", 0)),
            "new_node_count": int(result.get("new_node_count", 0)),
            "reduction_percent": float(result.get("reduction_percent", 0.0)),
            "params_used": dict(result.get("params_used", {})),
        }
        self._simplification_panel.set_preview_state(
            False,
            summary,
            None,
        )

    def _on_simplification_process_requested(self, config_overrides: dict):
        source_doc = self._active_source_document()
        if source_doc is None or source_doc.df is None or source_doc.df.empty:
            self._append_log("Simplification: no active SWC document.", "WARN")
            return

        try:
            result = simplify_dataframe(source_doc.df, config_overrides=dict(config_overrides or {}))
        except Exception as e:  # noqa: BLE001
            self._append_log(f"Simplification failed: {e}", "ERROR")
            return

        simplified_df = result.get("dataframe")
        if not isinstance(simplified_df, pd.DataFrame) or simplified_df.empty:
            self._append_log("Simplification produced empty output.", "WARN")
            return

        payload = self._simplification_log_payload(source_doc, result, output_path=None)
        source_doc.last_simplification_result = dict(payload)
        from swcstudio.core.provenance import OpKind  # noqa: PLC0415
        # Effective config = panel-default thresholds merged with whatever
        # the user changed in the panel (epsilon, radius_tolerance,
        # keep_tips, keep_bifurcations, …). Captures every numeric knob
        # that actually controlled this simplification run.
        from swcstudio.core.config import merge_config as _merge_simp  # noqa: PLC0415
        from swcstudio.tools.morphology_editing.features.simplification import (  # noqa: PLC0415
            get_config as _simp_default_config,
        )
        _simp_overrides = dict(config_overrides or {})
        _simp_effective = _merge_simp(_simp_default_config(), _simp_overrides)
        # If the algorithm reports the params it actually used, prefer
        # that (it can include derived/internal values too).
        _simp_actual_params = dict(payload.get("params_used", {}) or {})
        # Only record config_overrides when it's a genuinely smaller
        # delta against effective_config — otherwise it's redundant.
        _params: dict = {"effective_config": _simp_effective}
        if _simp_overrides and _simp_overrides != _simp_effective:
            _params["config_overrides"] = _simp_overrides
        if _simp_actual_params and _simp_actual_params != _simp_effective:
            _params["params_used"] = _simp_actual_params
        _params["original_node_count"] = int(payload.get("original_node_count", 0))
        _params["new_node_count"]      = int(payload.get("new_node_count", 0))
        _params["reduction_percent"]   = float(payload.get("reduction_percent", 0.0))
        self._record_tracked_commit(
            source_doc, simplified_df, kind=OpKind.SIMPLIFICATION,
            params=_params,
            message=(
                f"GUI simplify: {payload.get('original_node_count', 0)} → "
                f"{payload.get('new_node_count', 0)} nodes "
                f"({payload.get('reduction_percent', 0.0):.2f}% reduction)"
            ),
        )
        self._apply_document_dataframe(
            source_doc,
            simplified_df,
            event_title="Simplification",
            event_summary=(
                f"Simplified the current SWC from {payload.get('original_node_count', 0)} "
                f"to {payload.get('new_node_count', 0)} nodes."
            ),
            event_details=[
                f"Reduction (%): {float(payload.get('reduction_percent', 0.0)):.2f}",
                f"Removed nodes: {len(list(payload.get('removed_node_ids', []) or []))}",
                f"Protected counts: {dict(payload.get('protected_counts', {}))}",
                f"Parameters used: {dict(payload.get('params_used', {}))}",
            ],
        )
        self._rerun_active_validation()
        self._append_log(
            "Simplification applied: "
            f"{payload.get('original_node_count', 0)} -> {payload.get('new_node_count', 0)} "
            f"({payload.get('reduction_percent', 0.0):.2f}%).",
            "INFO",
        )
        self._refresh_simplification_panel_state()

    def _is_validation_auto_label_preview(self, doc: _DocumentState | None) -> bool:
        return bool(doc and doc.is_preview and doc.preview_kind == "validation_auto_label")

    def _resolve_validation_auto_label_context(
        self,
    ) -> tuple[_DocumentState | None, _DocumentState | None, dict | None]:
        active = self._active_document()
        if active is None:
            return None, None, None

        if self._is_validation_auto_label_preview(active):
            source_doc = self._source_document_for(active)
            result = self._auto_label_result_by_preview.get(active.editor)
            return source_doc, active, result

        preview_editor = self._auto_label_preview_by_source.get(active.editor)
        if preview_editor is None:
            return active, None, None

        preview_doc = self._documents.get(preview_editor)
        result = self._auto_label_result_by_preview.get(preview_editor)
        return active, preview_doc, result

    def _remove_validation_auto_label_preview(self, preview_editor: EditorTab, *, switch_to_source: bool):
        preview_doc = self._documents.get(preview_editor)
        source_editor = self._auto_label_source_by_preview.pop(preview_editor, None)
        if source_editor is not None:
            if self._auto_label_preview_by_source.get(source_editor) is preview_editor:
                self._auto_label_preview_by_source.pop(source_editor, None)
        self._auto_label_result_by_preview.pop(preview_editor, None)

        if preview_doc is not None:
            self._documents.pop(preview_editor, None)

        idx = self._canvas_tabs.indexOf(preview_editor)
        if idx >= 0:
            self._canvas_tabs.removeTab(idx)

        float_win = self._detached_windows.pop(preview_editor, None)
        if float_win is not None:
            try:
                float_win.editor_closing.disconnect(self._on_detached_editor_closing)
            except Exception:
                pass
            float_win.close()

        if switch_to_source and source_editor is not None:
            src_idx = self._canvas_tabs.indexOf(source_editor)
            if src_idx >= 0:
                self._canvas_tabs.setCurrentIndex(src_idx)

    def _auto_label_result_to_dataframe(self, result: object) -> pd.DataFrame:
        df = auto_label_result_to_dataframe(result)
        if df.empty:
            return pd.DataFrame(columns=SWC_COLS)
        return df.loc[:, SWC_COLS].copy()

    def _merge_auto_label_types_only(self, base_df: pd.DataFrame, labeled_df: pd.DataFrame) -> pd.DataFrame:
        """Keep original geometry/radius columns and only replace type assignments."""
        out = auto_label_merge_types_only(base_df, labeled_df)
        if out.empty:
            return pd.DataFrame(columns=SWC_COLS)
        return out.loc[:, SWC_COLS].copy()

    def _refresh_validation_auto_label_panel_state(self):
        doc = self._active_source_document()
        if doc is None or not isinstance(doc.last_auto_label_result, dict):
            self._validation_auto_label_panel.set_preview_state(False, None)
            return
        self._validation_auto_label_panel.set_preview_state(False, doc.last_auto_label_result)

    def _on_validation_auto_label_process_requested(self, options: object, backend_settings: object | None = None):
        # The panel emits (options, {"model_dir": str|None}). The dict is
        # optional for backward compatibility with any caller still connected
        # to the single-argument variant of the signal.
        if self._validation_auto_label_thread is not None and self._validation_auto_label_thread.isRunning():
            # Defensive: shouldn't fire because the panel disables Run
            # while a worker is in flight, but ignore double-clicks.
            return

        backend_cfg: dict = {}
        if isinstance(backend_settings, dict):
            backend_cfg = dict(backend_settings)

        config_overrides: dict = {}
        if backend_cfg.get("model_dir"):
            config_overrides["model_dir"] = str(backend_cfg["model_dir"])
        for key in ("cell_type", "flag_enabled"):
            if key in backend_cfg:
                config_overrides[key] = backend_cfg[key]

        source_doc = self._active_source_document()
        if source_doc is None or source_doc.df is None or source_doc.df.empty:
            self._append_log("Validation Auto Label Editing: no active SWC document.", "WARN")
            self._validation_auto_label_panel.set_status_text("No active SWC loaded.")
            return

        # Write the active document to a temp SWC the worker will read.
        # The temp file is cleaned up in the worker-finished handler so
        # it survives across thread context switches.
        tmp_fd, tmp_in = tempfile.mkstemp(prefix="swcstudio_auto_label_", suffix=".swc")
        os.close(tmp_fd)
        tmp_path = Path(tmp_in)
        try:
            self._write_swc_file(str(tmp_path), source_doc.df)
        except Exception as e:  # noqa: BLE001
            self._append_log(f"Validation Auto Label Editing failed: {e}", "ERROR")
            self._validation_auto_label_panel.set_status_text(f"Auto Label Editing failed:\n{e}")
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
            return

        # Hand off to the worker thread. The result handlers below pick
        # up the source doc + options + tmp path stashed on `self`.
        self._validation_auto_label_run_id += 1
        run_id = int(self._validation_auto_label_run_id)
        self._validation_auto_label_tmp_path = tmp_path
        self._validation_auto_label_source_doc = source_doc
        self._validation_auto_label_options = options

        self._validation_auto_label_panel.set_running(
            True, status_text="Auto Label Editing — running…"
        )

        self._validation_auto_label_thread = QThread(self)
        self._validation_auto_label_worker = _AutoLabelFileWorker(
            run_id, str(tmp_path), options, config_overrides,
        )
        self._validation_auto_label_worker.moveToThread(self._validation_auto_label_thread)
        self._validation_auto_label_thread.started.connect(self._validation_auto_label_worker.run)
        self._validation_auto_label_worker.finished.connect(self._on_validation_auto_label_finished)
        self._validation_auto_label_worker.failed.connect(self._on_validation_auto_label_failed)
        self._validation_auto_label_worker.finished.connect(self._validation_auto_label_thread.quit)
        self._validation_auto_label_worker.failed.connect(self._validation_auto_label_thread.quit)
        self._validation_auto_label_thread.finished.connect(self._cleanup_validation_auto_label_worker_refs)
        self._validation_auto_label_thread.start()

    @Slot(int, object)
    def _on_validation_auto_label_finished(self, run_id: int, result_obj: object) -> None:
        if int(run_id) != int(self._validation_auto_label_run_id):
            return
        # Clean up the temp file ASAP — the result_obj already holds the
        # parsed rows we need for the preview.
        tmp_path = self._validation_auto_label_tmp_path
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
        self._validation_auto_label_tmp_path = None

        source_doc = self._validation_auto_label_source_doc
        options = self._validation_auto_label_options
        self._validation_auto_label_source_doc = None
        self._validation_auto_label_options = None

        if source_doc is None:
            self._validation_auto_label_panel.set_running(False)
            return

        try:
            self._apply_validation_auto_label_result(source_doc, options, result_obj)
        finally:
            self._validation_auto_label_panel.set_running(False)

    @Slot(int, str)
    def _on_validation_auto_label_failed(self, run_id: int, message: str) -> None:
        if int(run_id) != int(self._validation_auto_label_run_id):
            return
        tmp_path = self._validation_auto_label_tmp_path
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
        self._validation_auto_label_tmp_path = None
        self._validation_auto_label_source_doc = None
        self._validation_auto_label_options = None

        self._append_log(f"Validation Auto Label Editing failed: {message}", "ERROR")
        self._validation_auto_label_panel.set_status_text(
            f"Auto Label Editing failed:\n{message}"
        )
        self._validation_auto_label_panel.set_running(False)

    @Slot()
    def _cleanup_validation_auto_label_worker_refs(self) -> None:
        self._validation_auto_label_worker = None
        self._validation_auto_label_thread = None

    def _apply_validation_auto_label_result(
        self, source_doc: "_DocumentState", options: object, result_obj: object,
    ) -> None:
        preview_df = self._auto_label_result_to_dataframe(result_obj)
        if preview_df.empty:
            self._append_log("Validation Auto Label Editing produced empty output.", "WARN")
            self._validation_auto_label_panel.set_status_text("Auto Label Editing output is empty.")
            return
        preview_df = self._merge_auto_label_types_only(source_doc.df, preview_df)

        opts = options
        opts_dict = {
            "soma": bool(getattr(opts, "soma", False)),
            "axon": bool(getattr(opts, "axon", False)),
            "apic": bool(getattr(opts, "apic", False)),
            "basal": bool(getattr(opts, "basal", False)),
            "rad": bool(getattr(opts, "rad", False)),
            "cell_type": str(getattr(opts, "cell_type", "auto") or "auto"),
            "flag_enabled": bool(getattr(opts, "flag_enabled", True)),
        }
        result_payload = {
            "input_file": str(source_doc.file_path or source_doc.filename),
            "nodes_total": int(getattr(result_obj, "nodes_total", 0)),
            "type_changes": int(getattr(result_obj, "type_changes", 0)),
            "radius_changes": 0,
            "out_type_counts": dict(getattr(result_obj, "out_type_counts", {}) or {}),
            "cell_type": getattr(result_obj, "cell_type", None),
            "cell_type_source": getattr(result_obj, "cell_type_source", "stage1"),
            "stage1_cell_type": getattr(result_obj, "stage1_cell_type", None),
            "stage1_confidence": getattr(result_obj, "stage1_confidence", None),
            "class_mismatch": bool(getattr(result_obj, "class_mismatch", False)),
            "notes": list(getattr(result_obj, "notes", []) or []),
            "labeler_version": getattr(result_obj, "labeler_version", None),
            "qc_result": dict(getattr(result_obj, "qc_result", {}) or {}),
            "flag_result": dict(getattr(result_obj, "flag_result", {}) or {}),
            "change_details": [
                line
                for line in list(getattr(result_obj, "change_details", []) or [])
                if "radius_changes:" not in str(line).lower()
                and "old_radius=" not in str(line).lower()
                and "new_radius=" not in str(line).lower()
            ],
            "options": opts_dict,
            "log_path": "",
        }
        source_doc.last_auto_label_options = dict(opts_dict)
        source_doc.last_auto_label_result = dict(result_payload)

        out_counts = dict(result_payload.get("out_type_counts", {}))
        flag_result = dict(result_payload.get("flag_result", {}) or {})
        from swcstudio.core.auto_typing import cell_class_name  # noqa: PLC0415
        event_details = [
            f"Input: {source_doc.file_path or source_doc.filename}",
            f"Nodes: {int(result_payload.get('nodes_total', 0))}",
            f"Type changes: {int(result_payload.get('type_changes', 0))}",
            (f"Cell class: {cell_class_name(result_payload.get('cell_type'))} "
             f"({'chosen' if result_payload.get('cell_type_source') == 'user' else 'Stage 1'})"),
            f"Flagged: {bool(flag_result.get('flagged', False))}"
            + (f" (predicted per-cell F1 {float(flag_result['predicted_f1']):.3f})"
               if flag_result.get("predicted_f1") is not None else ""),
            (
                "Out types (1/2/3/4): "
                f"{out_counts.get(1, 0)}/{out_counts.get(2, 0)}/{out_counts.get(3, 0)}/{out_counts.get(4, 0)}"
            ),
        ]
        if result_payload.get("class_mismatch"):
            event_details.append(
                f"Class mismatch: Stage 1 predicts {cell_class_name(result_payload.get('stage1_cell_type'))}")
        # Provenance: this is an AI op. tracked_op auto-detects
        # OpKind.AUTO_LABEL as AI, captures the full env fingerprint
        # (every installed package + system info), records an
        # AI-run blob with model params + metrics. After this lands,
        # `swcstudio history reproduce <sha>` produces a reproduce.yaml.
        from swcstudio.core.provenance import OpKind  # noqa: PLC0415
        self._record_tracked_commit(
            source_doc,
            preview_df,
            kind=OpKind.AUTO_LABEL,
            params={
                "options":          opts_dict,
                "labeler_version":  result_payload.get("labeler_version"),
                "nodes_total":      int(result_payload.get("nodes_total", 0)),
                "type_changes":     int(result_payload.get("type_changes", 0)),
                "out_type_counts":  {str(k): int(v) for k, v in out_counts.items()},
                "cell_type_result": result_payload.get("cell_type"),
                "cell_type_source": result_payload.get("cell_type_source"),
                "stage1_cell_type": result_payload.get("stage1_cell_type"),
                "class_mismatch":   bool(result_payload.get("class_mismatch", False)),
                "flagged":          bool(flag_result.get("flagged", False)),
                "predicted_f1":     flag_result.get("predicted_f1"),
            },
            message=(
                f"GUI auto-label: {int(result_payload.get('type_changes', 0))} "
                f"type changes / {int(result_payload.get('nodes_total', 0))} nodes"
            ),
        )
        self._apply_document_dataframe(
            source_doc,
            preview_df.copy(),
            event_title="Validation Auto Label Editing Run",
            event_summary=(
                "Applied auto label editing to current SWC; "
                f"type_changes={int(result_payload.get('type_changes', 0))}"
            ),
            event_details=event_details,
        )
        # The applied dataframe now contains this run's suggested types,
        # so the immediately following validation can reuse the result:
        # there are no "likely wrong label" differences left to infer.
        source_doc.pending_type_suspicion_issues = []
        source_doc.auto_label_preview_base_df = None
        source_doc.auto_label_preview_df = None
        self._validation_auto_label_panel.set_preview_state(False, result_payload)
        self._rerun_active_validation()
        self._append_log(
            "Validation Auto Label Editing applied to current canvas: "
            f"nodes={result_payload['nodes_total']}, "
            f"type_changes={result_payload['type_changes']}, "
            f"radius_changes={result_payload['radius_changes']}",
            "INFO",
        )

    def _write_validation_auto_label_log(
        self,
        source_doc: _DocumentState,
        result: dict,
        *,
        output_path: str,
    ) -> str:
        out_p = Path(output_path)
        out_counts = dict(result.get("out_type_counts", {}))
        per_file = [
            f"{Path(source_doc.filename or 'swc').name}: "
            f"nodes={int(result.get('nodes_total', 0))}, "
            f"type_changes={int(result.get('type_changes', 0))}, "
            f"radius_changes={int(result.get('radius_changes', 0))}, "
            f"cell_class={result.get('cell_type') or 'auto'} "
            f"({'user' if result.get('cell_type_source') == 'user' else 'auto'}), "
            f"flag={bool(dict(result.get('flag_result', {}) or {}).get('flagged', False))}, "
            f"out_types(soma/axon/basal/apic)="
            f"{out_counts.get(1, 0)}/{out_counts.get(2, 0)}/{out_counts.get(3, 0)}/{out_counts.get(4, 0)}"
        ]
        payload = {
            "folder": str(out_p.parent),
            "out_dir": str(out_p.parent),
            "zip_path": None,
            "files_total": 1,
            "files_processed": 1,
            "files_failed": 0,
            "total_nodes": int(result.get("nodes_total", 0)),
            "total_type_changes": int(result.get("type_changes", 0)),
            "total_radius_changes": int(result.get("radius_changes", 0)),
            "failures": [],
            "per_file": per_file,
            "change_details": [],
        }
        source_path = str(source_doc.file_path or "").strip()
        log_path = auto_typing_log_path_for_file(source_path or out_p)
        return write_text_report(log_path, format_auto_typing_report_text(payload))

    def _on_manual_radii_apply_requested(self, node_id: int, new_radius: float):
        doc = self._active_source_document()
        if doc is None or doc.df is None or doc.df.empty:
            self._append_log("Manual Radii: no active SWC document.", "WARN")
            return
        mask = doc.df["id"].astype(int) == int(node_id)
        if not bool(mask.any()):
            self._append_log(f"Manual Radii: node {int(node_id)} is no longer present.", "WARN")
            self._manual_radii_panel.clear_selection()
            return
        old_radius = float(doc.df.loc[mask, "radius"].iloc[0])
        target_radius = float(new_radius)
        if float(old_radius) == float(target_radius):
            self._append_log(f"Manual Radii: node {int(node_id)} already has radius {target_radius:.6g}.", "INFO")
            return

        new_df = doc.df.copy()
        new_df.loc[mask, "radius"] = target_radius

        # Provenance: record this Apply as one SET_RADIUS commit on the
        # document's .history/. Falls through silently if the document
        # has no file path (preview / untitled doc).
        from swcstudio.core.provenance import OpKind  # noqa: PLC0415
        self._record_tracked_commit(
            doc,
            new_df,
            kind=OpKind.SET_RADIUS,
            params={"node_id": int(node_id), "radius": target_radius},
            message=(
                f"GUI manual radii: node {int(node_id)} "
                f"{old_radius:.6g} → {target_radius:.6g}"
            ),
        )

        self._apply_document_dataframe(
            doc,
            new_df,
            event_title="Manual Radius Edit",
            event_summary=f"Updated node {int(node_id)} radius from {old_radius:.6g} to {target_radius:.6g}.",
            event_details=[],
            record_type_changes=False,
        )
        self._manual_radii_panel.set_selected_node(int(node_id))
        self._append_log(
            f"Manual Radii applied: node {int(node_id)} {old_radius:.6g} -> {target_radius:.6g}.",
            "INFO",
        )
        self._rerun_active_validation()

    def _on_validation_radii_apply_requested(self, result: object):
        doc = self._active_source_document()
        if doc is None or doc.df is None or doc.df.empty:
            self._append_log("Auto Radii Editing: no active SWC document.", "WARN")
            return
        payload = dict(result or {})
        new_df = payload.get("dataframe")
        if not isinstance(new_df, pd.DataFrame) or new_df.empty:
            self._append_log("Auto Radii Editing produced no dataframe output.", "WARN")
            return
        total_changes = int(payload.get("changes", 0))
        passes_used = int(payload.get("passes", 0))
        new_df_final = new_df.loc[:, SWC_COLS].copy()
        change_details = list(payload.get("change_details", []) or [])
        change_rows: list[dict] = []
        for row in change_details:
            node_id = int(row.get("node_id", -1))
            if node_id < 0:
                continue
            change_rows.append(
                {
                    "node_id": str(node_id),
                    "changed_keys": ["radius"],
                    "old_values": {"radius": f"{float(row.get('old_radius', 0.0)):.10g}"},
                    "new_values": {"radius": f"{float(row.get('new_radius', 0.0)):.10g}"},
                    "old_parameters": f"radius={float(row.get('old_radius', 0.0)):.10g}",
                    "new_parameters": f"radius={float(row.get('new_radius', 0.0)):.10g}",
                }
            )

        # Provenance: the auto-radii pass that produced `result` ran on
        # the current in-memory doc.df, so this commit records exactly
        # that mutation as one RADII_CLEAN op.
        #
        # NOTE: payload["config_used"] from the panel is ALREADY the
        # full merged effective config (defaults + whatever the panel
        # UI was set to). It is NOT a small user-overrides dict, so
        # recording it under both names would be redundant. We only
        # record effective_config here.
        from swcstudio.core.provenance import OpKind  # noqa: PLC0415
        _radii_effective = dict(payload.get("config_used") or {})
        self._record_tracked_commit(
            doc,
            new_df_final,
            kind=OpKind.RADII_CLEAN,
            params={
                "effective_config": _radii_effective,
                "passes": passes_used,
                "radius_changes": total_changes,
            },
            message=(
                f"GUI auto radii: passes={passes_used}, "
                f"radius_changes={total_changes}"
            ),
        )

        self._apply_document_dataframe(
            doc,
            new_df_final,
            event_title="Auto Radii Editing",
            event_summary=f"Applied automatic radii cleaning to current SWC; passes={passes_used}; radius_changes={total_changes}.",
            event_details=[],
            record_type_changes=False,
            change_rows=change_rows,
        )
        self._append_log(
            f"Auto Radii Editing applied to current SWC: radius_changes={total_changes}.",
            "INFO",
        )
        self._rerun_active_validation()

    def _on_geometry_selection_preview_changed(self, node_ids: object, visibility_mode: str, auto_zoom: bool):
        doc = self._active_document()
        if doc is None:
            return
        ids = [int(v) for v in list(node_ids or [])]
        if ids:
            doc.editor.set_geometry_selection(ids, visibility_mode)
            if auto_zoom:
                doc.editor.zoom_to_node_ids(ids)
        else:
            doc.editor.clear_geometry_selection()

    def _on_geometry_focus_requested(self, swc_id: int):
        doc = self._active_document()
        if doc is None:
            return
        doc.editor.focus_node(int(swc_id))
        self._geometry_panel.set_current_node(int(swc_id))

    def _apply_geometry_dataframe(
        self,
        doc: _DocumentState,
        new_df: pd.DataFrame,
        *,
        event_title: str,
        event_summary: str,
        event_details: list[str],
        focus_node_id: int | None = None,
    ):
        self._apply_document_dataframe(
            doc,
            new_df,
            event_title=event_title,
            event_summary=event_summary,
            event_details=event_details,
            record_type_changes=False,
        )
        if focus_node_id is not None:
            self._geometry_panel.set_current_node(int(focus_node_id))
            doc.editor.focus_node(int(focus_node_id))
        self._rerun_active_validation()

    def _on_geometry_move_selection_requested(self, node_ids: object, anchor_id: int, x: float, y: float, z: float):
        doc = self._active_source_document()
        if doc is None or doc.df is None or doc.df.empty:
            self._append_log("Geometry Editing: no active SWC document.", "WARN")
            return
        try:
            selected_node_ids = [int(v) for v in list(node_ids or [])]
            if not selected_node_ids:
                raise ValueError("No selected nodes to move.")
            row = doc.df.loc[doc.df["id"].astype(int) == int(anchor_id)].iloc[0]
            old_xyz = (float(row["x"]), float(row["y"]), float(row["z"]))
            new_df = move_selection_by_anchor_absolute(doc.df, selected_node_ids, int(anchor_id), float(x), float(y), float(z))
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"Geometry Editing: {exc}", "WARN")
            return
        from swcstudio.core.provenance import OpKind  # noqa: PLC0415
        self._record_tracked_commit(
            doc, new_df, kind=OpKind.GEOMETRY_EDIT,
            params={"op": "move-selection", "anchor_id": int(anchor_id),
                    "selected_node_ids": selected_node_ids,
                    "x": float(x), "y": float(y), "z": float(z)},
            message=f"GUI geometry move-selection anchor={anchor_id} ({len(selected_node_ids)} nodes)",
        )
        self._apply_geometry_dataframe(
            doc,
            new_df,
            event_title="Move Selection",
            event_summary=f"Moved {len(selected_node_ids)} selected node(s) using anchor {int(anchor_id)}.",
            event_details=[
                f"Anchor node ID: {int(anchor_id)}",
                f"Moved selected node count: {len(selected_node_ids)}",
                f"Old XYZ: ({old_xyz[0]:.5g}, {old_xyz[1]:.5g}, {old_xyz[2]:.5g})",
                f"New XYZ: ({float(x):.5g}, {float(y):.5g}, {float(z):.5g})",
            ],
            focus_node_id=int(anchor_id),
        )
        self._append_log(f"Geometry Editing: moved {len(selected_node_ids)} selected node(s).", "INFO")

    def _on_geometry_reconnect_requested(self, source_id: int, target_id: int):
        doc = self._active_source_document()
        if doc is None or doc.df is None or doc.df.empty:
            self._append_log("Geometry Editing: no active SWC document.", "WARN")
            return
        try:
            end_row = doc.df.loc[doc.df["id"].astype(int) == int(target_id)].iloc[0]
            old_parent = int(end_row["parent"])
            new_df = reconnect_branch(doc.df, int(source_id), int(target_id))
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"Geometry Editing: {exc}", "WARN")
            return
        from swcstudio.core.provenance import OpKind  # noqa: PLC0415
        self._record_tracked_commit(
            doc, new_df, kind=OpKind.GEOMETRY_EDIT,
            params={"op": "connect", "start_id": int(source_id), "end_id": int(target_id)},
            message=f"GUI geometry reconnect end={target_id} → parent={source_id}",
        )
        self._geometry_panel.clear_all_selections()
        self._geometry_panel.set_current_node(None)
        self._apply_geometry_dataframe(
            doc,
            new_df,
            event_title="Reconnect Branch",
            event_summary=f"Connected end node {int(target_id)} to start node {int(source_id)}.",
            event_details=[
                f"Start node ID: {int(source_id)}",
                f"End node ID: {int(target_id)}",
                f"End node old parent ID: {old_parent}",
                f"End node new parent ID: {int(source_id)}",
                "Node IDs preserved; no automatic renumbering.",
            ],
            focus_node_id=None,
        )
        self._append_log(
            f"Geometry Editing: connected end node {int(target_id)} to start node {int(source_id)}.",
            "INFO",
        )

    def _on_geometry_disconnect_requested(self, source_id: int, target_id: int):
        doc = self._active_source_document()
        if doc is None or doc.df is None or doc.df.empty:
            self._append_log("Geometry Editing: no active SWC document.", "WARN")
            return
        try:
            parent_by_id = {
                int(row["id"]): int(row["parent"])
                for _, row in doc.df[["id", "parent"]].iterrows()
            }
            path = path_between_nodes(doc.df, int(source_id), int(target_id))
            if len(path) < 2:
                raise ValueError("Start and end nodes are not connected.")
            disconnected_children: list[int] = []
            old_edges: list[str] = []
            for left, right in zip(path[:-1], path[1:]):
                left = int(left)
                right = int(right)
                if int(parent_by_id.get(left, -1)) == right:
                    disconnected_children.append(left)
                    old_edges.append(f"{right} -> {left}")
                elif int(parent_by_id.get(right, -1)) == left:
                    disconnected_children.append(right)
                    old_edges.append(f"{left} -> {right}")
                else:
                    raise ValueError("Encountered a non-parent-child step while disconnecting the selected path.")
            new_df = disconnect_branch(doc.df, int(source_id), int(target_id))
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"Geometry Editing: {exc}", "WARN")
            return
        from swcstudio.core.provenance import OpKind  # noqa: PLC0415
        self._record_tracked_commit(
            doc, new_df, kind=OpKind.GEOMETRY_EDIT,
            params={"op": "disconnect", "start_id": int(source_id), "end_id": int(target_id)},
            message=f"GUI geometry disconnect path {source_id} … {target_id}",
        )
        self._geometry_panel.clear_all_selections()
        self._geometry_panel.set_current_node(None)
        self._apply_geometry_dataframe(
            doc,
            new_df,
            event_title="Disconnect Branch",
            event_summary=f"Disconnected the path between {int(source_id)} and {int(target_id)}.",
            event_details=[
                f"Start node ID: {int(source_id)}",
                f"End node ID: {int(target_id)}",
                f"Path nodes: {', '.join(str(v) for v in path)}",
                f"Disconnected child node IDs: {', '.join(str(v) for v in disconnected_children)}",
                f"Disconnected edges: {', '.join(old_edges)}",
                "New parent IDs on disconnected child nodes: -1",
                "Node IDs preserved; no automatic renumbering.",
            ],
            focus_node_id=None,
        )
        self._append_log(
            f"Geometry Editing: disconnected the path between {int(source_id)} and {int(target_id)}.",
            "INFO",
        )

    def _on_geometry_delete_node_requested(self, node_id: int, reconnect_children: bool):
        doc = self._active_source_document()
        if doc is None or doc.df is None or doc.df.empty:
            self._append_log("Geometry Editing: no active SWC document.", "WARN")
            return
        try:
            row = doc.df.loc[doc.df["id"].astype(int) == int(node_id)].iloc[0]
            child_count = int((doc.df["parent"].astype(int) == int(node_id)).sum())
            new_df = geometry_delete_node(doc.df, int(node_id), reconnect_children=bool(reconnect_children))
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"Geometry Editing: {exc}", "WARN")
            return
        from swcstudio.core.provenance import OpKind  # noqa: PLC0415
        self._record_tracked_commit(
            doc, new_df, kind=OpKind.GEOMETRY_EDIT,
            params={"op": "delete-node", "node_id": int(node_id),
                    "reconnect_children": bool(reconnect_children)},
            message=f"GUI geometry delete-node id={node_id} reconnect={bool(reconnect_children)}",
        )
        self._geometry_panel.clear_all_selections()
        self._geometry_panel.set_current_node(None)
        event_title = "Delete Node" if not reconnect_children else "Delete Node + Reconnect Children"
        self._apply_geometry_dataframe(
            doc,
            new_df,
            event_title=event_title,
            event_summary=f"Deleted node {int(node_id)}.",
            event_details=[
                f"Node ID: {int(node_id)}",
                f"Type: {label_for_type(int(row['type']))} ({int(row['type'])})",
                f"Child count: {child_count}",
                f"Reconnect children: {'yes' if reconnect_children else 'no'}",
                "Remaining node IDs preserved; no automatic renumbering.",
            ],
            focus_node_id=None,
        )
        self._append_log(f"Geometry Editing: deleted node {int(node_id)}.", "INFO")

    def _on_geometry_delete_subtree_requested(self, root_id: int):
        doc = self._active_source_document()
        if doc is None or doc.df is None or doc.df.empty:
            self._append_log("Geometry Editing: no active SWC document.", "WARN")
            return
        try:
            subtree_size = int(len(subtree_node_ids(doc.df, int(root_id))))
            parent_row = doc.df.loc[doc.df["id"].astype(int) == int(root_id)].iloc[0]
            parent_id = int(parent_row["parent"])
            new_df = geometry_delete_subtree(doc.df, int(root_id))
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"Geometry Editing: {exc}", "WARN")
            return
        from swcstudio.core.provenance import OpKind  # noqa: PLC0415
        self._record_tracked_commit(
            doc, new_df, kind=OpKind.GEOMETRY_EDIT,
            params={"op": "delete-subtree", "root_id": int(root_id)},
            message=f"GUI geometry delete-subtree root={root_id}",
        )
        self._geometry_panel.clear_all_selections()
        self._geometry_panel.set_current_node(None)
        self._apply_geometry_dataframe(
            doc,
            new_df,
            event_title="Delete Subtree",
            event_summary=f"Deleted subtree rooted at node {int(root_id)}.",
            event_details=[
                f"Subtree root ID: {int(root_id)}",
                f"Removed node count: {subtree_size}",
                "Remaining node IDs preserved; no automatic renumbering.",
            ],
            focus_node_id=None,
        )
        self._append_log(f"Geometry Editing: deleted subtree rooted at node {int(root_id)}.", "INFO")

    def _on_geometry_insert_node_requested(self, start_id: int, end_id: int, x: float, y: float, z: float):
        doc = self._active_source_document()
        if doc is None or doc.df is None or doc.df.empty:
            self._append_log("Geometry Editing: no active SWC document.", "WARN")
            return
        try:
            end_row = None
            if int(end_id) >= 0:
                end_row = doc.df.loc[doc.df["id"].astype(int) == int(end_id)].iloc[0]
            inserted_node_id = int(doc.df["id"].astype(int).max()) + 1
            new_df = insert_node_between(doc.df, int(start_id), int(end_id), x=float(x), y=float(y), z=float(z))
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"Geometry Editing: {exc}", "WARN")
            return
        from swcstudio.core.provenance import OpKind  # noqa: PLC0415
        self._record_tracked_commit(
            doc, new_df, kind=OpKind.GEOMETRY_EDIT,
            params={"op": "insert", "start_id": int(start_id), "end_id": int(end_id),
                    "x": float(x), "y": float(y), "z": float(z),
                    "inserted_node_id": int(inserted_node_id)},
            message=f"GUI geometry insert between {start_id} and {end_id}",
        )
        self._geometry_panel.clear_all_selections()
        self._geometry_panel.set_current_node(None)
        self._apply_geometry_dataframe(
            doc,
            new_df,
            event_title="Insert Node",
            event_summary=(
                f"Inserted a node between {int(start_id)} and {int(end_id)}."
                if int(end_id) >= 0
                else f"Inserted a child node under {int(start_id)}."
            ),
            event_details=[
                f"Start node ID: {int(start_id)}",
                f"End node ID: {int(end_id)}" if int(end_id) >= 0 else "End node ID: None",
                f"Inserted node ID: {inserted_node_id}",
                (
                    f"End node type: {label_for_type(int(end_row['type']))} ({int(end_row['type'])})"
                    if end_row is not None
                    else "Inserted node has no child; end node was not provided."
                ),
                f"Inserted XYZ: ({float(x):.5g}, {float(y):.5g}, {float(z):.5g})",
                "Existing node IDs preserved; inserted node uses max(existing ID)+1.",
            ],
            focus_node_id=None,
        )
        self._append_log(
            (
                f"Geometry Editing: inserted a node between {int(start_id)} and {int(end_id)}."
                if int(end_id) >= 0
                else f"Geometry Editing: inserted a child node under {int(start_id)}."
            ),
            "INFO",
        )

    def _set_control_tabs_for_feature(self, feature: str):
        """Show only control tabs relevant to the active feature."""
        key = (feature or "").strip().lower()
        previous_label = ""
        if self._control_tabs.count() > 0 and self._control_tabs.currentIndex() >= 0:
            previous_label = self._control_tabs.tabText(self._control_tabs.currentIndex()).strip().lower()
        while self._control_tabs.count() > 0:
            self._control_tabs.removeTab(0)
        self._control_tabs.tabBar().setVisible(False)

        current_idx = -1

        if key in ("", "none"):
            self._refresh_top_feature_buttons()
            return

        if key == "batch":
            self._control_tabs.addTab(self._wrap_control_widget(self._batch_tab.split_tab_widget()), "Split")
            self._control_tabs.addTab(self._wrap_control_widget(self._batch_tab.validation_tab_widget()), "Validation")
            self._control_tabs.addTab(self._wrap_control_widget(self._batch_tab.auto_tab_widget()), "Auto Label Editing")
            self._control_tabs.addTab(self._wrap_control_widget(self._batch_tab.radii_tab_widget()), "Radii Cleaning")
            self._control_tabs.addTab(self._wrap_control_widget(self._batch_tab.simplify_tab_widget()), "Simplification")
            self._control_tabs.addTab(self._wrap_control_widget(self._batch_tab.index_clean_tab_widget()), "Index Clean")
            current_idx = 0
        elif key == "validation":
            self._control_tabs.addTab(self._wrap_control_widget(self._validation_tab), "Validation")
            self._control_tabs.addTab(self._wrap_control_widget(self._validation_index_clean), "Index Clean")
            current_idx = {
                "validation": 0,
                "index clean": 1,
            }.get(previous_label, 0)
        elif key in ("morphology_editing", "dendrogram"):
            doc = self._active_document()
            if doc is None:
                self._refresh_top_feature_buttons()
                self._on_control_tab_changed(-1)
                return
            self._control_tabs.addTab(self._wrap_control_widget(doc.controls), "Manual Label Editing")
            self._control_tabs.addTab(self._wrap_control_widget(self._validation_auto_label_panel), "Auto Label Editing")
            self._control_tabs.addTab(self._wrap_control_widget(self._manual_radii_panel), "Manual Radii Editing")
            self._control_tabs.addTab(self._wrap_control_widget(self._validation_radii_panel), "Auto Radii Editing")
            current_idx = {
                "manual label editing": 0,
                "auto label editing": 1,
                "manual radii editing": 2,
                "auto radii editing": 3,
            }.get(previous_label, 0)
            self._refresh_simplification_panel_state()
        elif key == "geometry_editing":
            self._control_tabs.addTab(self._wrap_control_widget(self._geometry_panel), "Geometry Editing")
            self._control_tabs.addTab(self._wrap_control_widget(self._simplification_panel), "Simplification")
            current_idx = {
                "geometry editing": 0,
                "simplification": 1,
            }.get(previous_label, 0)
            self._refresh_simplification_panel_state()
        else:
            # default: visualization
            self._control_tabs.addTab(self._wrap_control_widget(self._viz_control), "View Controls")
            current_idx = 0

        if self._control_tabs.count() > 0:
            self._control_tabs.tabBar().setVisible(False)
            current_idx = max(0, min(current_idx, self._control_tabs.count() - 1))
            self._control_tabs.setCurrentIndex(current_idx)
            self._on_control_tab_changed(self._control_tabs.currentIndex())
        else:
            self._control_tabs.tabBar().setVisible(False)
            self._on_control_tab_changed(-1)

        self._refresh_top_feature_buttons()

    # --------------------------------------------------------- Feature routing
    def _activate_feature(self, name: str):
        key = (name or "").strip().lower()
        current = (self._active_tool or "").strip().lower()
        if key and key == current:
            return
        if (
            key != "validation"
            and key != current
            and hasattr(self, "_validation_tab")
            and self._validation_tab.is_running()
        ):
            self._append_log("Validation is running. Wait for completion before switching tools.", "WARN")
            return
        if key == "batch":
            self._active_tool = "batch"
            self._sync_tool_tab_selection()
            self._set_control_tabs_for_feature("batch")
            self._control_tabs.setVisible(self._control_tabs.count() > 0)
            self._control_dock.show()
            self._on_control_tab_changed(self._control_tabs.currentIndex())
            self._feature_label.setText("Active feature: Batch Processing")
            self._append_log("Feature switched: Batch Processing", "INFO")
            return
        if key == "validation":
            self._active_tool = "validation"
            self._sync_tool_tab_selection()
            self._set_control_tabs_for_feature("validation")
            self._control_tabs.setVisible(self._control_tabs.count() > 0)
            self._control_dock.show()
            self._precheck_dock.hide()
            self._auto_guide_dock.hide()
            self._apply_editor_modes()
            self._refresh_canvas_surface()
            self._feature_label.setText("Active feature: Validation")
            self._append_log("Feature switched: Validation", "INFO")
            return
        if key == "visualization":
            self._active_tool = "visualization"
            self._sync_tool_tab_selection()
            self._set_control_tabs_for_feature("visualization")
            self._control_tabs.setVisible(self._control_tabs.count() > 0)
            self._control_dock.show()
            self._precheck_dock.hide()
            self._auto_guide_dock.hide()
            self._apply_editor_modes()
            self._refresh_canvas_surface()
            self._feature_label.setText("Active feature: Visualization")
            self._append_log("Feature switched: Visualization", "INFO")
            return
        if key in ("morphology_editing", "dendrogram"):
            self._active_tool = "morphology_editing"
            self._sync_tool_tab_selection()
            self._set_control_tabs_for_feature("morphology_editing")
            self._control_tabs.setVisible(self._control_tabs.count() > 0)
            self._control_dock.show()
            self._precheck_dock.hide()
            self._auto_guide_dock.hide()
            self._apply_editor_modes()
            self._refresh_canvas_surface()
            self._clear_active_editor_selection_if_unfocused()
            self._feature_label.setText("Active feature: Morphology Editing")
            self._append_log("Feature switched: Morphology Editing", "INFO")
            return
        if key == "geometry_editing":
            self._active_tool = "geometry_editing"
            self._sync_tool_tab_selection()
            self._set_control_tabs_for_feature("geometry_editing")
            self._control_tabs.setVisible(self._control_tabs.count() > 0)
            self._control_dock.show()
            self._precheck_dock.hide()
            self._auto_guide_dock.hide()
            self._apply_editor_modes()
            self._refresh_canvas_surface()
            self._clear_active_editor_selection_if_unfocused()
            self._feature_label.setText("Active feature: Geometry Editing")
            self._append_log("Feature switched: Geometry Editing", "INFO")
            return
        self._active_tool = ""
        self._sync_tool_tab_selection()
        self._set_control_tabs_for_feature("")
        self._precheck_dock.hide()
        self._auto_guide_dock.hide()
        self._apply_editor_modes()
        self._refresh_canvas_surface()
        self._feature_label.setText("Active feature: None")
        self._append_log("No active tool selected.", "INFO")

    def _set_camera(self, preset: str):
        ed = self._active_editor()
        if ed is None:
            return
        ed.set_camera_view(preset)
        self._append_log(f"Camera preset: {preset}", "INFO")

    def _reset_camera(self):
        ed = self._active_editor()
        if ed is None:
            return
        ed.reset_camera()
        self._append_log("Camera reset.", "INFO")

    def _on_render_mode_changed(self, index: int):
        mode = self._render_combo.currentData()
        if mode is None:
            return
        ed = self._active_editor()
        if ed is None:
            return
        ed.set_render_mode(int(mode))
        self._append_log(f"Render mode set to {self._render_combo.currentText()}.", "INFO")

    # --------------------------------------------------------- File loading
    def reload_swc_from_disk(self, path: str) -> None:
        """Reload an open document's in-memory dataframe from its
        on-disk source SWC.

        Called by the history panel after a Revert action so the GUI
        immediately reflects the reverted state without requiring the
        user to close and reopen the file.

        Idempotent and safe — if no open document matches ``path``
        the call is a no-op.
        """
        try:
            from swcstudio.core.provenance import current_swc_path_for  # noqa: PLC0415
        except Exception:
            current_swc_path_for = None  # type: ignore[assignment]

        target = Path(path).resolve()
        match_doc = None
        for ed, doc in self._documents.items():
            if doc.is_preview:
                continue
            try:
                if Path(doc.file_path).resolve() == target:
                    match_doc = doc
                    break
            except Exception:
                continue
        if match_doc is None:
            return

        # current_swc_path_for is kept as a compatibility helper; in
        # current builds it resolves to the source SWC itself.
        src_path = Path(path)
        if current_swc_path_for is not None:
            cur = current_swc_path_for(src_path)
            if cur.exists():
                src_path = cur

        try:
            new_text = src_path.read_text(encoding="utf-8", errors="ignore")
            new_df = parse_swc_text_preserve_tokens(new_text)
            if new_df.empty:
                return
            for col in ("id", "type", "parent"):
                new_df[col] = new_df[col].astype(int)
            new_df = new_df.loc[:, SWC_COLS].copy()
        except Exception as e:
            self._append_log(f"Reload failed: {e}", "WARN")
            return

        # Reset in-memory document state to the reloaded df. Push a
        # history snapshot so the user can still Undo if they didn't
        # mean to revert; clear the recovery copy so we don't leak a
        # stale unsaved-changes marker.
        match_doc.df = new_df
        match_doc.validation_report = None
        match_doc.issues = []
        match_doc.pending_type_suspicion_issues = None
        match_doc.edit_history.reset()
        match_doc.editor.load_swc(match_doc.df, match_doc.filename)
        self._clear_recovery_copy(match_doc)
        if match_doc is self._active_document():
            self._sync_from_active_document(auto_run_validation=True)
        self._append_log(
            f"Reloaded {match_doc.filename} from {src_path.name}.",
            "INFO",
        )

    def _on_open(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Open SWC files", "", "SWC Files (*.swc);;All Files (*)"
        )
        if paths:
            self._open_paths(paths)

    def _open_paths(self, paths: list[str]) -> None:
        """Open several SWC files, each in its own tab, and show the first.

        Tabs are added without making each one current, so validation runs
        only for the file that ends up shown (the others validate when their
        tab is first selected). Files that are already open are not opened
        again.
        """
        paths = [str(p) for p in (paths or []) if str(p or "").strip()]
        if not paths:
            return
        new_keys = {_path_key(p) for p in paths if self._find_open_document(p) is None}
        if len(new_keys) > _MANY_FILES_CONFIRM:
            answer = QMessageBox.question(
                self,
                "Open Files",
                f"Open {len(new_keys)} files?\n\n"
                "Each file opens in its own tab with its own 3D views and dendrogram, "
                "so many open files use a lot of memory.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                self._append_log(f"Open cancelled ({len(new_keys)} files selected).", "INFO")
                return
        first_editor = None
        for path in paths:
            editor = self._load_swc(path, activate=len(paths) == 1)
            if first_editor is None and editor is not None:
                first_editor = editor
        if first_editor is not None:
            self._show_document(first_editor)

    def _find_open_document(self, path: str | Path) -> _DocumentState | None:
        """The open (non-preview) document for ``path``, if there is one."""
        key = _path_key(path)
        for doc in self._documents.values():
            if doc.is_preview or not doc.file_path:
                continue
            if _path_key(doc.file_path) == key:
                return doc
        return None

    def _show_document(self, editor: EditorTab) -> None:
        """Bring an open document forward: select its tab or raise its window."""
        float_win = self._detached_windows.get(editor)
        if float_win is not None:
            float_win.show()
            float_win.raise_()
            float_win.activateWindow()
            return
        idx = self._canvas_tabs.indexOf(editor)
        if idx >= 0:
            self._canvas_tabs.setCurrentIndex(idx)

    def _cycle_document(self, step: int) -> None:
        """Select the next (step=1) or previous (step=-1) document tab, wrapping around."""
        count = self._canvas_tabs.count()
        if count < 2:
            return
        current = max(0, self._canvas_tabs.currentIndex())
        self._canvas_tabs.setCurrentIndex((current + int(step)) % count)

    def _load_swc(self, path: str, *, activate: bool = True) -> EditorTab | None:
        """Open ``path`` in a new tab and return its editor.

        If the file is already open, its existing tab is used instead (and
        shown when ``activate``), so no entry point opens a file twice.
        Returns None if the file could not be loaded.
        """
        existing = self._find_open_document(path)
        if existing is not None:
            if activate:
                self._show_document(existing.editor)
            self._append_log(f"{existing.filename} is already open.", "INFO")
            return existing.editor
        try:
            df = parse_swc_text_preserve_tokens(Path(path).read_text(encoding="utf-8", errors="ignore"))
            if df.empty:
                self._append_log(f"Empty file: {path} contains no data rows.", "WARN")
                return None

            for col in ("id", "type", "parent"):
                df[col] = df[col].astype(int)

            filename = os.path.basename(path)
            editor = EditorTab()
            self._connect_editor_signals(editor)
            controls = editor.take_dendrogram_controls_panel()
            editor.set_type_legend_visible(self._type_legend_visible)
            editor.load_swc(df.loc[:, SWC_COLS].copy(), filename)

            doc = _DocumentState(
                editor=editor,
                controls=controls,
                df=df.copy(),
                filename=filename,
                file_path=str(path),
                original_df=df.copy(),
            )
            self._documents[editor] = doc
            self._start_morphology_session(doc)

            idx = self._canvas_tabs.addTab(editor, filename)
            self._canvas_tabs.setTabToolTip(idx, str(path))
            if activate:
                self._canvas_tabs.setCurrentIndex(idx)

            self._update_recent_files(path)

            n_roots = int((df["parent"] == -1).sum())
            n_soma = int((df["type"] == 1).sum())
            self._status.showMessage(
                f"Loaded {filename}: {len(df)} nodes, {n_roots} root(s), {n_soma} soma(s)",
                5000,
            )
            self._append_log(
                f"Loaded {filename}: nodes={len(df)}, roots={n_roots}, soma={n_soma}",
                "INFO",
            )
            self.swc_loaded.emit(df.loc[:, SWC_COLS].copy(), filename)
            return editor
        except Exception as e:
            self._append_log(f"Error loading SWC: {e}", "ERROR")
            return None

    def _on_save(self):
        doc = self._active_document()
        if doc is None or doc.df.empty:
            self._append_log("No SWC loaded. Nothing to save.", "WARN")
            return
        if not doc.file_path:
            self._on_save_as()
            return
        self._write_swc_file(doc.file_path, doc.df)
        self._after_document_write(doc, doc.file_path, action_name="Save")
        self._append_log(f"Saved {doc.file_path}", "INFO")

    def _on_save_as(self):
        doc = self._active_document()
        if doc is None or doc.df.empty:
            self._append_log("No SWC loaded. Nothing to save.", "WARN")
            return

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save SWC As",
            doc.filename or "edited.swc",
            "SWC Files (*.swc);;All Files (*)",
        )
        if not path:
            self._append_log("Save As cancelled.", "INFO")
            return

        self._write_swc_file(path, doc.df)
        doc.file_path = str(path)
        doc.filename = os.path.basename(path)
        tab_idx = self._canvas_tabs.indexOf(doc.editor)
        if tab_idx >= 0:
            self._canvas_tabs.setTabText(tab_idx, doc.filename)
            self._canvas_tabs.setTabToolTip(tab_idx, doc.file_path)
        self._sync_from_active_document(auto_run_validation=False)
        self._after_document_write(doc, path, action_name="Save As")
        self._append_log(f"Saved As {path}", "INFO")
        self._update_recent_files(path)

    def _on_export(self):
        doc = self._active_document()
        if doc is None or doc.df.empty:
            self._append_log("No SWC loaded. Nothing to export.", "WARN")
            return

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export SWC",
            f"export_{doc.filename or 'swc'}.swc",
            "SWC Files (*.swc)",
        )
        if not path:
            self._append_log("Export cancelled.", "INFO")
            return
        self._write_swc_file(path, doc.df)
        self._after_document_write(doc, path, action_name="Export")
        self._append_log(f"Exported {path}", "INFO")

    def _write_swc_file(self, path: str, df: pd.DataFrame):
        out_path = Path(path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_df = df.copy()
        if not getattr(out_df, "attrs", {}).get(SWC_HEADER_ATTR) and out_path.exists():
            try:
                previous = parse_swc_text_preserve_tokens(out_path.read_text(encoding="utf-8", errors="ignore"))
                _copy_swc_metadata(out_df, previous)
            except Exception:
                pass
        payload = write_swc_to_bytes_preserve_tokens(out_df)
        fd, tmp_path = tempfile.mkstemp(prefix=f".{out_path.stem}_", suffix=".tmp", dir=str(out_path.parent))
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, out_path)
        except Exception:
            try:
                os.remove(tmp_path)
            except FileNotFoundError:
                pass
            raise

    def _build_document_diff_summary(self, doc: _DocumentState) -> dict[str, int]:
        original = doc.original_df.copy() if isinstance(doc.original_df, pd.DataFrame) else pd.DataFrame(columns=SWC_COLS)
        current = doc.df.copy() if isinstance(doc.df, pd.DataFrame) else pd.DataFrame(columns=SWC_COLS)
        summary = {
            "original_nodes": int(len(original)),
            "current_nodes": int(len(current)),
            "type_changes": 0,
            "radius_changes": 0,
            "parent_changes": 0,
            "geometry_changes": 0,
        }
        if original.empty or current.empty:
            return summary
        common_ids = sorted(set(int(v) for v in original["id"].tolist()) & set(int(v) for v in current["id"].tolist()))
        if not common_ids:
            return summary
        old_map = original.set_index("id")
        new_map = current.set_index("id")
        for node_id in common_ids:
            old_row = old_map.loc[node_id]
            new_row = new_map.loc[node_id]
            if int(old_row["type"]) != int(new_row["type"]):
                summary["type_changes"] += 1
            if float(old_row["radius"]) != float(new_row["radius"]):
                summary["radius_changes"] += 1
            if int(old_row["parent"]) != int(new_row["parent"]):
                summary["parent_changes"] += 1
            if (
                float(old_row["x"]) != float(new_row["x"])
                or float(old_row["y"]) != float(new_row["y"])
                or float(old_row["z"]) != float(new_row["z"])
            ):
                summary["geometry_changes"] += 1
        return summary

    def _write_correction_summary(self, doc: _DocumentState, output_path: str) -> str:
        summary = self._build_document_diff_summary(doc)
        skipped = sum(1 for item in doc.issues if str(item.get("status", "")) == "skipped")
        remaining_titles = [str(item.get("title", "")) for item in doc.issues[:20]]
        provenance_lines = [
            f"[{event.get('time', '')}] {event.get('title', 'event')}: {event.get('summary', '')}"
            for event in list(doc.session_operations or [])
        ]
        payload = {
            "input_path": str(doc.file_path or doc.filename),
            "output_path": str(output_path),
            "remaining_issues": len(doc.issues),
            "fixed_issues": int(doc.fixed_issue_count),
            "skipped_issues": int(skipped),
            "diff_summary": summary,
            "remaining_issue_titles": remaining_titles,
            "provenance_lines": provenance_lines,
        }
        log_path = correction_summary_log_path_for_file(output_path)
        return write_text_report(log_path, format_correction_summary_report_text(payload))

    def _after_document_write(self, doc: _DocumentState, output_path: str, *, action_name: str):
        self._record_session_event(
            doc,
            kind="export",
            title=f"{action_name} SWC",
            summary=f"Wrote corrected SWC to {output_path}",
            details=[],
        )
        doc.original_df = doc.df.copy()
        doc.fixed_issue_count = 0
        if not doc.is_preview:
            self._clear_recovery_copy(doc)

    # --------------------------------------------------- Drag & drop
    def dragEnterEvent(self, event: QDragEnterEvent):
        event.ignore()

    def dropEvent(self, event: QDropEvent):
        event.ignore()

    # --------------------------------------------------- Sync from editor
    def _on_editor_df_changed(self, editor: EditorTab, df: pd.DataFrame):
        # This fires for any direct in-table edit — labeling a node by
        # changing its type cell, editing a radius cell, dragging a node
        # in the 3D view, etc. The structured diff inside the commit
        # accurately captures whatever fields changed.
        doc = self._documents.get(editor)
        if doc is None:
            return

        old_df = doc.df if isinstance(doc.df, pd.DataFrame) else None
        next_df = df.copy()
        _copy_swc_metadata(next_df, old_df)
        doc.pending_type_suspicion_issues = None
        doc.df = next_df
        doc.validation_report = None
        doc.issues = []
        if not doc.is_preview:
            # Provenance: every direct editor change is one commit. We
            # mark the source so a future filter can pick out "edits
            # made directly in the table" vs panel-driven mutations.
            from swcstudio.core.provenance import OpKind  # noqa: PLC0415
            self._record_tracked_commit(
                doc, doc.df, kind=OpKind.SET_TYPE,
                params={"source": "editor_table", "title": "Manual Labeling"},
                message="GUI manual labeling",
            )
            self._record_session_operation(
                doc,
                title="Manual Labeling",
                summary="Applied labeling edits in Morphology Editing.",
                old_df=old_df,
                new_df=doc.df,
                details=[],
            )
        self._push_document_history(doc, old_df, doc.df)
        self._write_recovery_copy(doc)
        self._refresh_edit_history_state(doc)

        if editor is self._active_editor():
            self._sync_from_active_document(auto_run_validation=not doc.is_preview)
            if not doc.is_preview:
                self._append_log("Dendrogram edits applied to current SWC.", "INFO")
            else:
                self._append_log("Dendrogram edits applied to simplification preview.", "INFO")

    def _on_editor_node_selected(self, editor: EditorTab, swc_id: int):
        doc = self._documents.get(editor)
        if doc is None or doc is not self._active_document():
            return
        self._manual_radii_panel.set_selected_node(int(swc_id))
        self._geometry_panel.set_current_node(int(swc_id))

    def _on_editor_undo_requested(self, editor: EditorTab):
        doc = self._documents.get(editor)
        if doc is None or doc is not self._active_document():
            return
        if self._undo_document(doc):
            self._append_log("Undo.", "INFO")

    def _on_editor_redo_requested(self, editor: EditorTab):
        doc = self._documents.get(editor)
        if doc is None or doc is not self._active_document():
            return
        if self._redo_document(doc):
            self._append_log("Redo.", "INFO")

    # --------------------------------------------------- Document tabs/windows
    def _on_document_tab_changed(self, _index: int):
        self._sync_from_active_document(auto_run_validation=True)
        if self._active_tool in ("morphology_editing", "dendrogram"):
            self._set_control_tabs_for_feature("morphology_editing")
            self._refresh_simplification_panel_state()
        elif self._active_tool == "geometry_editing":
            self._set_control_tabs_for_feature("geometry_editing")
        self._apply_editor_modes()

    def _on_document_tab_close_requested(self, index: int):
        editor = self._canvas_tabs.widget(index)
        if isinstance(editor, EditorTab):
            self._close_document_editor(editor, from_detached_window=False)

    def _on_document_detach_requested(self, index: int, x: int, y: int):
        editor = self._canvas_tabs.widget(index)
        if not isinstance(editor, EditorTab):
            return
        doc = self._documents.get(editor)
        if doc is None:
            return

        self._canvas_tabs.removeTab(index)
        float_win = _DetachedEditorWindow(editor, doc.filename, self)
        float_win.editor_closing.connect(self._on_detached_editor_closing)
        float_win.move(max(0, int(x - 120)), max(0, int(y - 20)))
        float_win.show()
        self._detached_windows[editor] = float_win

        self._append_log(f"Detached tab: {doc.filename}", "INFO")
        self._sync_from_active_document(auto_run_validation=False)
        self._refresh_canvas_surface()
        if self._active_tool in ("morphology_editing", "dendrogram"):
            self._set_control_tabs_for_feature("morphology_editing")
        elif self._active_tool == "geometry_editing":
            self._set_control_tabs_for_feature("geometry_editing")

    def _on_detached_editor_closing(self, editor_widget: QWidget):
        if self._closing_app:
            return
        if isinstance(editor_widget, EditorTab):
            self._close_document_editor(editor_widget, from_detached_window=True)

    def _request_detached_editor_close(self, editor: EditorTab) -> bool:
        if self._closing_app:
            return True
        return self._close_document_editor(editor, from_detached_window=True)

    def _close_document_editor(self, editor: EditorTab, *, from_detached_window: bool):
        doc = self._documents.get(editor)
        if doc is None:
            return True

        # If this source has a simplification preview tab, close preview first.
        preview_editor = self._simplify_preview_by_source.get(editor)
        if preview_editor is not None:
            self._remove_simplification_preview(preview_editor, switch_to_source=False)
        # If this source has an auto-label preview tab, close preview first.
        auto_preview_editor = self._auto_label_preview_by_source.get(editor)
        if auto_preview_editor is not None:
            self._remove_validation_auto_label_preview(auto_preview_editor, switch_to_source=False)

        # If closing a preview tab, just drop preview state and UI tab.
        if self._is_simplification_preview(doc):
            self._remove_simplification_preview(editor, switch_to_source=False)
            self._append_log(f"Closed tab: {doc.filename}", "INFO")
            self._sync_from_active_document(auto_run_validation=False)
            self._refresh_canvas_surface()
            if self._active_tool in ("morphology_editing", "dendrogram"):
                self._set_control_tabs_for_feature("morphology_editing")
                self._refresh_simplification_panel_state()
            elif self._active_tool == "geometry_editing":
                self._set_control_tabs_for_feature("geometry_editing")
            return True
        if self._is_validation_auto_label_preview(doc):
            self._remove_validation_auto_label_preview(editor, switch_to_source=False)
            self._append_log(f"Closed tab: {doc.filename}", "INFO")
            self._sync_from_active_document(auto_run_validation=False)
            self._refresh_canvas_surface()
            return True

        close_plan = self._plan_document_close(doc, app_closing=False)
        if close_plan is None:
            return False
        if not self._apply_document_close_plan(doc, close_plan):
            return False

        self._documents.pop(editor, None)

        if not from_detached_window:
            idx = self._canvas_tabs.indexOf(editor)
            if idx >= 0:
                self._canvas_tabs.removeTab(idx)

        float_win = self._detached_windows.pop(editor, None)
        if float_win is not None and not from_detached_window:
            try:
                float_win.editor_closing.disconnect(self._on_detached_editor_closing)
            except Exception:
                pass
            float_win.close()

        self._append_log(f"Closed tab: {doc.filename}", "INFO")
        self._sync_from_active_document(auto_run_validation=False)
        self._refresh_canvas_surface()
        if self._active_tool in ("morphology_editing", "dendrogram"):
            self._set_control_tabs_for_feature("morphology_editing")
            self._refresh_simplification_panel_state()
        elif self._active_tool == "geometry_editing":
            self._set_control_tabs_for_feature("geometry_editing")
        return True
    # --------------------------------------------------- Helpers
    def _append_log(self, text: str, level: str = "INFO"):
        stamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{stamp}] [{level}] {text}".rstrip()
        self._runtime_log_lines.append(line)
        if len(self._runtime_log_lines) > 500:
            self._runtime_log_lines = self._runtime_log_lines[-500:]
        try:
            self._status.showMessage(text, 5000)
        except Exception:
            pass

    def _set_current_file_label_text(self, filename: str):
        full = f"Current file: {filename or '(none)'}"
        self._current_file_label.setToolTip(full)
        # Keep top ribbon shrinkable when filenames are long.
        fm = QFontMetrics(self._current_file_label.font())
        max_px = max(120, self._current_file_label.maximumWidth())
        self._current_file_label.setText(fm.elidedText(full, Qt.ElideMiddle, max_px))

    def _update_info_label(self, df: pd.DataFrame, n_roots: int, n_soma: int, *, filename: str):
        self._info_label.setText(
            f"File: {filename}\n"
            f"Nodes: {len(df)}\n"
            f"Roots: {n_roots}\n"
            f"Soma nodes: {n_soma}\n"
            f"Type counts:\n"
            f"  Soma (1): {(df['type'] == 1).sum()}\n"
            f"  Axon (2): {(df['type'] == 2).sum()}\n"
            f"  Basal (3): {(df['type'] == 3).sum()}\n"
            f"  Apical (4): {(df['type'] == 4).sum()}"
        )

    def _update_recent_files(self, path: str):
        path = os.path.abspath(path)
        if path in self._recent_paths:
            self._recent_paths.remove(path)
        self._recent_paths.insert(0, path)
        self._recent_paths = self._recent_paths[:10]

        self._recent_menu.clear()
        for p in self._recent_paths:
            act = QAction(p, self)
            act.triggered.connect(lambda _=False, sp=p: self._load_swc(sp))
            self._recent_menu.addAction(act)

    def _open_flag_review(self, path: str) -> None:
        """Load a batch's flag review into the Flag Review window and show it."""
        if path and not self._flag_review_panel.load(path):
            return
        self._flag_review_dock.show()
        self._flag_review_dock.raise_()

    def _on_flag_review_open_requested(self, paths: list[str]) -> None:
        """Open cells chosen in the Flag Review window."""
        # Batch Processing shows its own canvas instead of the document tabs,
        # and a review usually starts right after a batch: switch to label
        # editing so the opened cell is actually on screen.
        if self._active_tool == "batch":
            self._activate_feature("morphology_editing")
        self._open_paths(list(paths or []))

    def _set_type_legend_visible(self, visible: bool) -> None:
        self._type_legend_visible = bool(visible)
        for doc in self._documents.values():
            doc.editor.set_type_legend_visible(self._type_legend_visible)

    def _reset_layout(self):
        self._data_dock.show()
        self._control_dock.show()
        self._precheck_dock.hide()
        self._auto_guide_dock.hide()
        self._flag_review_dock.hide()
        try:
            self.setDockOptions(QMainWindow.AnimatedDocks | QMainWindow.AllowNestedDocks | QMainWindow.AllowTabbedDocks)
            self.addDockWidget(Qt.LeftDockWidgetArea, self._data_dock)
            self.addDockWidget(Qt.RightDockWidgetArea, self._control_dock)
            self.resizeDocks([self._data_dock, self._control_dock], [340, 360], Qt.Horizontal)
        except Exception:
            pass
        self._append_log("Layout reset (docks movable).", "INFO")

    def _apply_issue_state(self, doc: _DocumentState | None):
        issues = list(doc.issues) if doc is not None else []
        self._issue_panel.set_issues(issues)
        self._set_issue_status(issues)
        if doc is None:
            return
        selected_issue = None
        if doc.selected_issue_id:
            selected_issue = next(
                (item for item in issues if str(item.get("issue_id", "")).strip() == str(doc.selected_issue_id).strip()),
                None,
            )
        doc.editor.set_issue_markers([selected_issue] if selected_issue else [])
        if selected_issue is None:
            self._issue_panel.clear_selection()
            doc.editor.clear_selection()

    def _set_issue_status(self, issues: list[dict]):
        critical = sum(1 for item in issues if str(item.get("severity", "")) == "critical")
        warning = sum(1 for item in issues if str(item.get("severity", "")) == "warning")
        info = sum(1 for item in issues if str(item.get("severity", "")) == "info")
        skipped = sum(1 for item in issues if str(item.get("status", "")).strip().lower() in {"muted", "skipped"})
        fixed = 0
        doc = self._active_document()
        if doc is not None:
            fixed = int(doc.fixed_issue_count)
        self._issue_status_label.setText(
            f"Issues: {len(issues)} total · {critical} critical · {warning} warning · "
            f"{info} info · {skipped} muted · {fixed} fixed"
        )

    def _build_all_issues_for_document(
        self,
        doc: _DocumentState,
        report: dict,
        *,
        skip_type_suspicion: bool = False,
        type_suspicious: list[dict] | None = None,
    ) -> list[dict]:
        return build_issue_list(
            doc.df,
            report,
            skip_type_suspicion=skip_type_suspicion,
            type_suspicious=type_suspicious,
        )

    def _on_validation_report_ready(self, report: dict):
        doc = self._active_document()
        if doc is None:
            return
        previous_issue_id = str(doc.selected_issue_id or "").strip()
        previous_issue = self._find_issue_by_id(doc, previous_issue_id)
        doc.validation_report = dict(report)
        reused_type_suspicion = doc.pending_type_suspicion_issues
        doc.pending_type_suspicion_issues = None
        # Fast path: build the issue list WITHOUT type suspicion so the
        # panel paints within ~100 ms instead of blocking on ~1-2 s of
        # auto-typing inference. The ``Likely wrong labels`` rows are
        # filled in by ``_start_type_suspicion_worker`` below.
        if reused_type_suspicion is None:
            issues = self._build_all_issues_for_document(
                doc,
                report,
                skip_type_suspicion=True,
            )
        else:
            issues = self._build_all_issues_for_document(
                doc,
                report,
                type_suspicious=list(reused_type_suspicion),
            )
        issue_ids = {str(item.get("issue_id", "")) for item in issues}
        for item in issues:
            issue_id = str(item.get("issue_id", "")).strip()
            status = str(doc.issue_status_overrides.get(issue_id, "open")).strip().lower()
            if status == "skipped":
                status = "muted"
            if status:
                item["status"] = status
        if doc.pending_resolved_issue_ids:
            resolved_now = [issue_id for issue_id in doc.pending_resolved_issue_ids if issue_id and issue_id not in issue_ids]
            if resolved_now:
                doc.fixed_issue_count += len(resolved_now)
            doc.pending_resolved_issue_ids = set()
        doc.issue_status_overrides = {
            issue_id: status
            for issue_id, status in doc.issue_status_overrides.items()
            if issue_id in issue_ids and status in {"muted", "skipped"}
        }
        doc.issues = issues
        self._apply_issue_state(doc)
        self._data_tabs.setCurrentIndex(0)
        self._data_dock.show()
        self._append_log(f"Issue navigator updated: {len(doc.issues)} actionable findings.", "INFO")
        # Phase 2: only run inference when there is no result from the
        # auto-label operation that produced this exact dataframe.
        if reused_type_suspicion is None:
            self._start_type_suspicion_worker(doc, report)
        else:
            self._append_log(
                "Issue navigator reused the applied auto-label result; "
                "skipped duplicate type-suspicion inference.",
                "INFO",
            )
        if previous_issue_id and self._issue_panel.select_issue(previous_issue_id):
            doc.selected_issue_id = previous_issue_id
            return
        replacement_issue = self._find_matching_issue(doc, previous_issue)
        replacement_issue_id = str((replacement_issue or {}).get("issue_id", "")).strip()
        if replacement_issue_id and self._issue_panel.select_issue(replacement_issue_id):
            doc.selected_issue_id = replacement_issue_id
            return
        doc.selected_issue_id = ""
        self._issue_panel.clear_selection()
        doc.editor.set_issue_markers([])
        self._context_inspector.clear(
            title="No issue selected",
            problem_detail="The previous issue was resolved.",
            suggested_solution="Click another issue in the left panel to continue.",
        )

    # ------------------------------------------------------------------
    # Background type-suspicion worker. The QThread waits on a separate
    # inference process rather than loading XGBoost into Qt's
    # process. This avoids macOS crashes caused by incompatible OpenMP
    # runtimes shipped by native scientific wheels.
    # ------------------------------------------------------------------

    def _start_type_suspicion_worker(self, doc: _DocumentState, report: dict) -> None:
        if doc is None or doc.df is None or doc.df.empty:
            return
        # If a worker is already running on a previous open, cancel it —
        # only the active doc's results matter. Run-id check in the
        # finished slot makes stale results harmless even if a stop
        # races with a finish.
        if self._type_suspicion_thread is not None and self._type_suspicion_thread.isRunning():
            try:
                self._type_suspicion_thread.quit()
                self._type_suspicion_thread.wait(200)
            except Exception:
                pass
            self._cleanup_type_suspicion_worker_refs()

        self._type_suspicion_run_id += 1
        run_id = int(self._type_suspicion_run_id)
        self._type_suspicion_doc = doc

        self._type_suspicion_thread = QThread(self)
        self._type_suspicion_worker = _TypeSuspicionWorker(run_id, doc.df.copy())
        self._type_suspicion_worker.moveToThread(self._type_suspicion_thread)
        self._type_suspicion_thread.started.connect(self._type_suspicion_worker.run)
        self._type_suspicion_worker.finished.connect(self._on_type_suspicion_finished)
        self._type_suspicion_worker.failed.connect(self._on_type_suspicion_failed)
        self._type_suspicion_worker.finished.connect(self._type_suspicion_thread.quit)
        self._type_suspicion_worker.failed.connect(self._type_suspicion_thread.quit)
        self._type_suspicion_thread.finished.connect(self._cleanup_type_suspicion_worker_refs)
        self._type_suspicion_thread.start()

    @Slot(int, object)
    def _on_type_suspicion_finished(self, run_id: int, suspicion_issues: object) -> None:
        if int(run_id) != int(self._type_suspicion_run_id):
            return  # stale — a newer run already started
        doc = self._type_suspicion_doc
        self._type_suspicion_doc = None
        if doc is None or not isinstance(suspicion_issues, list) or not suspicion_issues:
            return
        # Re-build the issue list with the freshly computed type-suspicion
        # rows merged in. We pass them in explicitly so the engine isn't
        # re-run synchronously.
        report = doc.validation_report or {}
        merged = self._build_all_issues_for_document(
            doc, report,
            type_suspicious=list(suspicion_issues),
        )
        # Preserve any user mutations made between the fast-path render
        # and now (status overrides, fixed counts).
        issue_ids = {str(item.get("issue_id", "")) for item in merged}
        for item in merged:
            issue_id = str(item.get("issue_id", "")).strip()
            status = str(doc.issue_status_overrides.get(issue_id, "open")).strip().lower()
            if status == "skipped":
                status = "muted"
            if status:
                item["status"] = status
        doc.issue_status_overrides = {
            issue_id: status
            for issue_id, status in doc.issue_status_overrides.items()
            if issue_id in issue_ids and status in {"muted", "skipped"}
        }
        doc.issues = merged
        # Only refresh the panel if this doc is still the active one;
        # otherwise just update the cached list and the next focus will
        # pick it up.
        if doc is self._active_document():
            self._apply_issue_state(doc)
        self._append_log(
            f"Issue navigator: added {len(suspicion_issues)} likely-wrong-label rows.",
            "INFO",
        )

    @Slot(int, str)
    def _on_type_suspicion_failed(self, run_id: int, message: str) -> None:
        if int(run_id) != int(self._type_suspicion_run_id):
            return
        self._type_suspicion_doc = None
        self._append_log(f"Type suspicion worker failed: {message}", "WARN")

    @Slot()
    def _cleanup_type_suspicion_worker_refs(self) -> None:
        self._type_suspicion_worker = None
        self._type_suspicion_thread = None

    def _on_validation_result_activated(self, row: dict):
        if str(row.get("key", "")).strip() in {"parent_id_less_than_child_id", "no_node_id_gaps"}:
            self._activate_feature("validation")
            self._select_control_tab_by_label("index clean")
        issues = issues_from_validation_report({"results": [row]})
        if not issues:
            return
        self._on_issue_selected(issues[0])

    def _on_validation_index_clean_requested(self, new_df: object, id_map: object):
        doc = self._active_source_document()
        if doc is None or doc.df is None or doc.df.empty:
            self._append_log("Validation: no active SWC document for Index Clean.", "WARN")
            return
        if not isinstance(new_df, pd.DataFrame) or new_df.empty:
            self._append_log("Validation: Index Clean did not produce a valid SWC.", "WARN")
            return
        original_node_count = int(len(doc.df))
        new_node_count = int(len(new_df))
        remapped_id_count = sum(
            1 for old_id, new_id in dict(id_map or {}).items() if int(old_id) != int(new_id)
        )
        details = validation_index_clean_detail_lines(
            input_path=str(doc.file_path or doc.filename or ""),
            output_path="",
            original_node_count=original_node_count,
            new_node_count=new_node_count,
            remapped_id_count=remapped_id_count,
        )
        from swcstudio.core.provenance import OpKind  # noqa: PLC0415
        self._record_tracked_commit(
            doc, new_df, kind=OpKind.INDEX_CLEAN,
            params={"original_node_count": original_node_count,
                    "new_node_count": new_node_count,
                    "remapped_id_count": remapped_id_count},
            message=f"GUI validation index-clean ({remapped_id_count} ids remapped)",
        )
        self._apply_document_dataframe(
            doc,
            new_df,
            event_title="Validation Index Clean",
            event_summary="Reordered and reindexed the SWC for clean parent-before-child indexing.",
            event_details=details,
            record_type_changes=False,
            id_map=dict(id_map or {}),
        )
        self._append_log("Validation: Index Clean applied.", "INFO")
        self._rerun_active_validation()

    def _focus_issue(self, issue: dict):
        doc = self._active_document()
        if doc is None:
            return
        node_ids = [int(v) for v in issue.get("node_ids", [])]
        if node_ids:
            doc.editor.focus_node(node_ids[0])
        elif issue.get("section_ids"):
            self._append_log("Issue has section-level context but no direct node target yet.", "INFO")

    def _select_control_tab_by_label(self, target: str):
        wanted = str(target or "").strip().lower()
        for i in range(self._control_tabs.count()):
            label = (self._control_tabs.tabText(i) or "").strip().lower()
            if label == wanted:
                if self._control_tabs.currentIndex() != i:
                    self._control_tabs.setCurrentIndex(i)
                return True
        return False

    def _issue_uses_popup_only_controls(self, issue: dict | None) -> bool:
        if not isinstance(issue, dict):
            return False
        return str(issue.get("source_key", "")).strip() in {
            "valid_soma_format",
            "multiple_somas",
            "has_soma",
            "has_axon",
            "has_basal_dendrite",
            "has_apical_dendrite",
            "no_invalid_negative_types",
            "custom_types_defined",
        }

    def _route_issue_to_tool(self, issue: dict):
        tool_target = str(issue.get("tool_target", "")).strip().lower()
        if not tool_target:
            return
        if tool_target in {"label_editing", "auto_label", "radii_cleaning", "manual_radii"}:
            self._activate_feature("morphology_editing")
            target_tab = {
                "label_editing": "manual label editing",
                "auto_label": "auto label editing",
                "manual_radii": "manual radii editing",
                "radii_cleaning": "auto radii editing",
            }.get(tool_target, "manual label editing")
            self._select_control_tab_by_label(target_tab)
            return
        if tool_target == "simplification":
            self._activate_feature("geometry_editing")
            self._select_control_tab_by_label("simplification")
            return
        if tool_target == "geometry_editing":
            self._activate_feature("geometry_editing")
            self._select_control_tab_by_label("geometry editing")
            return
        if tool_target == "index_clean":
            self._activate_feature("validation")
            self._select_control_tab_by_label("index clean")
            return
        self._activate_feature("validation")
        self._select_control_tab_by_label("validation")

    def _on_issue_selected(self, issue: dict):
        doc = self._active_document()
        if doc is not None:
            doc.selected_issue_id = str(issue.get("issue_id", "")).strip()
            doc.editor.clear_selection()
            doc.editor.clear_geometry_selection()
            self._manual_radii_panel.clear_selection()
            doc.editor.set_issue_markers([issue])
        self._data_tabs.setCurrentIndex(0)
        if self._issue_uses_popup_only_controls(issue):
            self._control_tabs.setVisible(False)
        else:
            self._control_tabs.setVisible(True)
            self._route_issue_to_tool(issue)
        self._context_inspector.set_issue(issue, self._build_issue_context(issue))
        title = str(issue.get("title", "Issue")).strip() or "Issue"
        self._status.showMessage(f"Selected issue: {title}", 4000)

    def _build_issue_context(self, issue: dict) -> dict:
        node_ids = [int(v) for v in issue.get("node_ids", [])]
        doc = self._active_document()
        node_preview = ", ".join(str(v) for v in node_ids[:25]) if node_ids else "None"
        remaining_nodes = max(0, len(node_ids) - 25)
        section_ids = [int(v) for v in issue.get("section_ids", [])]
        ctx = {
            "node_ids_text": ", ".join(str(v) for v in node_ids) or "None",
            "section_ids_text": ", ".join(str(v) for v in section_ids) or "None",
            "problem_detail": str(issue.get("description", "")).strip() or "No extra detail provided.",
            "suggested_solution": str(issue.get("suggested_fix", "")).strip() or "Inspect the affected morphology and fix the prerequisite issue first.",
            "tool_button_label": {
                "validation": "Validation",
                "index_clean": "Index Clean",
                "radii_cleaning": "Auto Radii Editing",
                "manual_radii": "Manual Radii Editing",
                "auto_label": "Auto Label Editing",
                "label_editing": "Manual Label Editing",
                "simplification": "Simplification",
                "geometry_editing": "Geometry Editing",
            }.get(str(issue.get("tool_target", "")).strip().lower(), "Related Tool"),
            "auto_fix_available": False,
            "auto_fix_label": "",
            "detail_lines": [],
        }
        if node_ids or section_ids:
            ctx["detail_lines"].extend(
                [
                    f"Total affected nodes: {len(node_ids)}",
                    f"Affected node IDs: {node_preview}",
                    *(["Additional nodes not shown here: " + str(remaining_nodes)] if remaining_nodes > 0 else []),
                    f"Sections: {', '.join(str(v) for v in section_ids) or 'None'}",
                ]
            )
        else:
            ctx["detail_lines"].extend(
                [
                    "Affected nodes: not provided by this check",
                    "Affected sections: not provided by this check",
                ]
            )
        if doc is None or doc.df is None or doc.df.empty:
            return ctx

        payload = dict(issue.get("source_payload", {}) or {})
        radii_stats = dict(radii_stats_by_type(doc.df, bins=20))
        type_stats_map = dict(radii_stats.get("type_stats", {}) or {})
        if node_ids:
            ctx["focus_node_id"] = int(node_ids[0])

        if str(issue.get("source_key", "")).strip() == "blocked_validation_checks":
            blocked_checks = list(payload.get("blocked_checks", []) or [])
            blocked_labels = [str(item.get("label", "")).strip() for item in blocked_checks if str(item.get("label", "")).strip()]
            blocked_reason = str(payload.get("blocked_reason", "Unknown prerequisite issue")).strip()
            preface_lines: list[str] = []
            if blocked_reason.lower() == "unsupported section type: 0":
                preface_lines = [
                    "Some dependent checks are still blocked because neurite sections still contain unsupported type 0 labels.",
                    "If you already assigned a soma, that part is fixed; the remaining blocked state comes from unlabeled neurite nodes.",
                    "",
                ]
            ctx["detail_lines"] = [
                *preface_lines,
                f"Blocked reason: {blocked_reason}",
                f"Checks that could not run ({len(blocked_labels)}):",
                *[f"- {label}" for label in blocked_labels],
                "",
                "Suggested next step:",
                str(ctx.get("suggested_solution", "")).strip(),
            ]
            return ctx

        radii_issue_keys = {
            "all_neurite_radii_nonzero",
            "soma_radius_nonzero",
            "no_ultranarrow_sections",
            "no_ultranarrow_starts",
            "no_fat_terminal_ends",
            "radius_upper_bound",
            "radii_outlier_batch",
        }
        if str(issue.get("domain", "")).strip() == "radii" or str(issue.get("source_key", "")).strip() in radii_issue_keys:
            ctx.update(
                {
                    "custom_primary_label": "Manual Radii Editing",
                    "custom_primary_action": "open_manual_radii_tool",
                    "custom_secondary_label": "Auto Radii Editing",
                    "custom_secondary_action": "open_auto_radii_tool",
                    "hide_skip_button": True,
                    "hide_apply_button": True,
                }
            )

        if str(issue.get("source_key", "")).strip() == "valid_soma_format":
            complex_groups = list(payload.get("metrics", {}).get("complex_groups", []) or [])
            preview = []
            for group in complex_groups[:10]:
                preview.append(
                    f"Group anchored at node {int(group.get('anchor_id', -1))}: "
                    f"{len(list(group.get('node_ids', []) or []))} connected soma nodes"
                )
            ctx.update(
                {
                    "problem_detail": str(issue.get("description", "")).strip() or "Complex multi-node soma format detected.",
                    "suggested_solution": (
                        "Consolidate each connected soma group into one mega-node: isolate connected type-1 nodes, "
                        "compute the centroid, set the radius to furthest-distance-plus-radius, and rewire all "
                        "non-soma children to the surviving anchor node."
                    ),
                    "custom_primary_label": "Consolidate Soma",
                    "custom_primary_action": "consolidate_soma",
                    "hide_detail_section": True,
                    "detail_lines": [
                        f"Complex soma groups: {len(complex_groups)}",
                        "",
                        "Consolidation steps:",
                        "1. Group connected soma nodes (type 1) by topology.",
                        "2. Replace each group with one mega-node at the centroid.",
                        "3. Set radius to furthest-node distance plus that node's radius.",
                        "4. Rewire all axon/dendrite children to the new anchor node.",
                        "",
                        *preview,
                    ],
                }
            )
            return ctx

        if str(issue.get("source_key", "")).strip() in {"has_soma", "has_axon", "has_basal_dendrite", "has_apical_dendrite"}:
            missing_key = str(issue.get("source_key", "")).strip()
            missing_label_map = {
                "has_soma": "soma",
                "has_axon": "axon",
                "has_basal_dendrite": "basal dendrite",
                "has_apical_dendrite": "apical dendrite",
            }
            missing_label = missing_label_map.get(missing_key, "neurite type")
            ctx.update(
                {
                    "problem_detail": str(issue.get("description", "")).strip() or f"No {missing_label} node found.",
                    "suggested_solution": (
                        "Choose either Manual Label Editing or Auto Label Editing in Morphology Editing to assign the missing type."
                    ),
                    "custom_primary_label": "Manual Label Editing",
                    "custom_primary_action": "open_manual_label_popup",
                    "custom_secondary_label": "Auto Label Editing",
                    "custom_secondary_action": "open_auto_label_popup",
                    "hide_skip_button": True,
                    "hide_apply_button": True,
                    "hide_detail_section": True,
                }
            )
            return ctx

        if str(issue.get("source_key", "")).strip() == "multiple_somas":
            soma_ids = list(payload.get("metrics", {}).get("soma_ids_after_consolidation", []) or [])
            ctx.update(
                {
                    "problem_detail": str(issue.get("description", "")).strip() or "Multiple disconnected soma groups detected.",
                    "suggested_solution": "This file must be split into one SWC per disconnected tree.",
                    "custom_primary_label": "Split",
                    "custom_primary_action": "split_trees",
                    "hide_detail_section": True,
                    "detail_lines": [
                        f"Disconnected soma groups: {len(soma_ids)}",
                        f"Soma anchor IDs: {', '.join(str(int(v)) for v in soma_ids) if soma_ids else 'None'}",
                        "",
                        "Suggested fix:",
                        "Split each disconnected tree into its own SWC file.",
                    ],
                }
            )
            return ctx

        if str(issue.get("source_key", "")).strip() == "custom_types_defined":
            undefined_custom_types = list(payload.get("metrics", {}).get("undefined_custom_types", []) or [])
            detail_lines = [
                f"Total affected nodes: {len(node_ids)}",
                f"Affected node IDs: {node_preview}",
                *(["Additional nodes not shown here: " + str(remaining_nodes)] if remaining_nodes > 0 else []),
                "",
                "Undefined custom type groups:",
            ]
            if undefined_custom_types:
                for item in undefined_custom_types:
                    type_id = int(item.get("type_id", -1))
                    sample_ids = [int(v) for v in list(item.get("node_ids_sample", []) or [])]
                    detail_lines.append(
                        f"Type {type_id}: {int(item.get('node_count', 0))} node(s)"
                    )
                    if sample_ids:
                        detail_lines.append(
                            f"  sample node IDs: {', '.join(str(v) for v in sample_ids[:25])}"
                        )
            else:
                affected_rows = doc.df.loc[doc.df["id"].astype(int).isin(node_ids)].copy() if node_ids else pd.DataFrame(columns=SWC_COLS)
                for _, row in affected_rows.head(50).iterrows():
                    detail_lines.append(
                        f"Node {int(row['id'])}: current type={int(row['type'])}, "
                        f"label={label_for_type(int(row['type']))}"
                    )
                if len(affected_rows) > 50:
                    detail_lines.extend(["...", f"Only first 50 affected nodes shown here. Remaining nodes: {len(affected_rows) - 50}"])
            ctx.update(
                {
                    "problem_detail": str(issue.get("description", "")).strip() or "Custom SWC type IDs need display definitions.",
                    "suggested_solution": "Use Manual Label Editing and define each custom type with a type ID, name, color, and notes.",
                    "custom_primary_label": "Manual Label Editing",
                    "custom_primary_action": "open_manual_label_popup",
                    "hide_skip_button": True,
                    "hide_apply_button": True,
                    "detail_lines": detail_lines,
                }
            )
            return ctx

        if str(issue.get("source_key", "")).strip() == "no_invalid_negative_types":
            detail_lines = [
                f"Total affected nodes: {len(node_ids)}",
                f"Affected node IDs: {node_preview}",
                *(["Additional nodes not shown here: " + str(remaining_nodes)] if remaining_nodes > 0 else []),
                "",
                "Invalid type values by node:",
            ]
            affected_rows = doc.df.loc[doc.df["id"].astype(int).isin(node_ids)].copy() if node_ids else pd.DataFrame(columns=SWC_COLS)
            affected_rows = affected_rows.sort_values("id")
            for _, row in affected_rows.head(50).iterrows():
                detail_lines.append(
                    f"Node {int(row['id'])}: current type={int(row['type'])}, "
                    f"radius={float(row['radius']):.5g}, parent={int(row['parent'])}"
                )
            if len(affected_rows) > 50:
                detail_lines.extend(["...", f"Only first 50 affected nodes shown here. Remaining nodes: {len(affected_rows) - 50}"])
            ctx.update(
                {
                    "problem_detail": str(issue.get("description", "")).strip() or "One or more nodes use invalid negative SWC type values.",
                    "suggested_solution": "Relabel nodes with invalid negative type values to supported SWC types.",
                    "custom_primary_label": "Manual Label Editing",
                    "custom_primary_action": "open_manual_label_popup",
                    "hide_skip_button": True,
                    "hide_apply_button": True,
                    "detail_lines": detail_lines,
                }
            )
            return ctx

        if str(issue.get("source_key", "")).strip() == "simplification_suggestion":
            ctx.update(
                {
                    "problem_detail": str(issue.get("description", "")).strip() or "This SWC may benefit from simplification.",
                    "suggested_solution": "Open Simplification to preview and apply graph-aware cleanup on the current SWC.",
                    "custom_primary_label": "Simplification",
                    "custom_primary_action": "open_simplification_tool",
                    "hide_detail_section": True,
                    "detail_lines": [],
                }
            )
            return ctx

        if str(issue.get("source_key", "")).strip() in {"parent_id_less_than_child_id", "no_node_id_gaps"}:
            source_key = str(issue.get("source_key", "")).strip()
            metrics = dict(payload.get("metrics", {}) or {})
            detail_lines = [
                f"Total affected nodes: {len(node_ids)}",
                f"Affected node IDs: {node_preview}",
                *(["Additional nodes not shown here: " + str(remaining_nodes)] if remaining_nodes > 0 else []),
            ]
            if source_key == "parent_id_less_than_child_id":
                detail_lines.extend(
                    [
                        "",
                        f"ID order violations: {int(metrics.get('id_order_violation_count', len(node_ids)))}",
                        "Meaning: one or more child nodes appear before their parent in ID order.",
                    ]
                )
            else:
                gap_samples = list(metrics.get("gap_samples", []) or [])
                detail_lines.extend(
                    [
                        "",
                        f"ID gaps found: {int(metrics.get('gap_count', 0))}",
                        f"Missing ID values: {int(metrics.get('missing_id_count', 0))}",
                        "Meaning: sorted node IDs skip one or more integers.",
                    ]
                )
                if gap_samples:
                    detail_lines.append("")
                    detail_lines.append("Gap examples:")
                    for sample in gap_samples[:10]:
                        detail_lines.append(
                            f"After {int(sample.get('after_id', -1))}, next ID is {int(sample.get('before_id', -1))} "
                            f"(missing {int(sample.get('missing_count', 0))})"
                        )
            ctx.update(
                {
                    "problem_detail": str(issue.get("description", "")).strip() or "Index consistency issue detected.",
                    "suggested_solution": "Open Validation -> Index Clean to reorder the SWC and rebuild a continuous parent-before-child ID sequence.",
                    "detail_lines": detail_lines,
                }
            )
            return ctx

        if (
            str(issue.get("source_key", "")).strip() == "no_fat_terminal_ends"
            and not node_ids
            and not section_ids
        ):
            ctx.update(
                {
                    "problem_detail": "This warning means one or more terminal branch endings appear abnormally thick compared with the local branch trend.",
                    "suggested_solution": "Inspect terminal tips in Auto Radii Editing or adjust individual nodes in Manual Radii Editing.",
                    "detail_lines": [
                        "This is a morphology-level NeuroM warning.",
                        "The current backend reports pass/fail for this check but does not return exact terminal node IDs.",
                        "",
                        "Suggested review focus:",
                        "- terminal tips with unusually large radii",
                        "- sudden radius inflation at branch endings",
                    ],
                }
            )
            return ctx

        radii_issue_keys = {
            "all_neurite_radii_nonzero",
            "soma_radius_nonzero",
            "no_ultranarrow_sections",
            "no_ultranarrow_starts",
            "no_fat_terminal_ends",
            "radius_upper_bound",
            "radii_outlier_batch",
        }
        if str(issue.get("domain", "")).strip() == "radii" or str(issue.get("source_key", "")).strip() in radii_issue_keys:
            detail_lines = [
                f"Total affected nodes: {len(node_ids)}",
                f"Affected node IDs: {node_preview}",
                *(["Additional nodes not shown here: " + str(remaining_nodes)] if remaining_nodes > 0 else []),
            ]
            if section_ids:
                detail_lines.append(f"Sections: {', '.join(str(v) for v in section_ids)}")
            detail_lines.append("")
            detail_lines.append("Affected node radii:")

            affected_rows = doc.df.loc[doc.df["id"].astype(int).isin(node_ids)].copy() if node_ids else pd.DataFrame(columns=SWC_COLS)
            affected_rows = affected_rows.sort_values("id")
            for _, row in affected_rows.head(50).iterrows():
                type_id = int(row["type"])
                stats_row = dict(type_stats_map.get(str(type_id), {}) or {})
                summary = []
                if stats_row.get("median") is not None:
                    summary.append(f"median={float(stats_row.get('median', 0.0)):.5g}")
                if stats_row.get("q1") is not None and stats_row.get("q3") is not None:
                    summary.append(
                        f"q1={float(stats_row.get('q1', 0.0)):.5g}"
                    )
                    summary.append(
                        f"q3={float(stats_row.get('q3', 0.0)):.5g}"
                    )
                summary_text = f" | type stats: {', '.join(summary)}" if summary else ""
                detail_lines.append(
                    f"Node {int(row['id'])}: radius={float(row['radius']):.5g}, "
                    f"type={label_for_type(type_id)} ({type_id}){summary_text}"
                )
            if len(affected_rows) > 50:
                detail_lines.extend(["...", f"Only first 50 affected nodes shown here. Remaining nodes: {len(affected_rows) - 50}"])
            ctx["detail_lines"] = detail_lines
            return ctx

        if (
            not node_ids
            and not section_ids
            and str(payload.get("source", "")).strip() == "neuron_morphology"
        ):
            recommended_tool = str(issue.get("tool_target", "") or "").strip()
            ctx.update(
                {
                    "detail_lines": [
                        "This is a morphology-level NeuroM check result.",
                        "The current backend reports pass/fail for this warning but does not return exact node IDs or section IDs.",
                        "",
                        f"Check key: {issue.get('source_key', '') or 'n/a'}",
                        *([f"Recommended tool: {recommended_tool}"] if recommended_tool else []),
                    ],
                }
            )
            return ctx

        if str(issue.get("source_key", "")).strip() == "no_duplicate_3d_points":
            duplicate_groups = list(payload.get("metrics", {}).get("duplicate_groups_sample", []) or [])
            group_lines: list[str] = []
            for idx, group in enumerate(duplicate_groups, start=1):
                ids_in_group = [int(v) for v in list(group.get("ids", []) or [])]
                xyz = list(group.get("xyz", []) or [])
                xyz_text = ", ".join(f"{float(v):.5g}" for v in xyz[:3]) if len(xyz) >= 3 else "unknown"
                group_lines.append(
                    f"Group {idx}: nodes {', '.join(str(v) for v in ids_in_group)} share XYZ ({xyz_text})"
                )
            total_groups = int(payload.get("metrics", {}).get("duplicate_group_count", len(duplicate_groups)))
            ctx.update(
                {
                    "problem_detail": str(issue.get("description", "")).strip() or "Multiple nodes share exactly the same 3D position.",
                    "suggested_solution": "Review each duplicated node group and merge, delete, or simplify the redundant geometry.",
                    "detail_lines": [
                        f"Duplicate groups found: {total_groups}",
                        f"Total affected nodes: {len(node_ids)}",
                        "",
                        "Duplicated node groups:",
                        *group_lines,
                        *(["...", f"Only first {len(duplicate_groups)} duplicate groups shown here."] if total_groups > len(duplicate_groups) else []),
                    ],
                }
            )
            return ctx

        if str(issue.get("source_key", "")).strip() == "radii_outlier_batch":
            changes = list(payload.get("changes", []) or [])
            preview_lines = []
            for item in changes[:50]:
                reasons = ", ".join(str(reason) for reason in list(item.get("reasons", [])) if str(reason).strip())
                preview_lines.append(
                    f"Node {int(item.get('node_id', -1))}: {float(item.get('old_radius', 0.0)):.5g} -> "
                    f"{float(item.get('new_radius', 0.0)):.5g}"
                    + (f" ({reasons})" if reasons else "")
                )
            ctx.update(
                {
                    "auto_fix_available": True,
                    "auto_fix_label": "Apply Suggested Radii",
                    "problem_detail": f"{len(changes)} nodes have suspicious radii that likely need cleanup.",
                    "suggested_solution": "Review the selected nodes in Manual Radii Editing or use Auto Radii Editing for broader cleanup.",
                    "detail_lines": [
                        f"Total affected nodes: {len(changes)}",
                        f"Affected node IDs: {', '.join(str(int(item.get('node_id', -1))) for item in changes[:25])}",
                        *(["Additional nodes not shown here: " + str(max(0, len(changes) - 25))] if len(changes) > 25 else []),
                        "",
                        "Problem details by node:",
                        "Suggested updates:",
                        *preview_lines,
                        *(["...", f"Only first 50 node updates shown here. Remaining nodes: {max(0, len(changes) - 50)}"] if len(changes) > 50 else []),
                    ],
                }
            )
            return ctx

        if str(issue.get("source_key", "")).strip() == "type_suspicion_batch":
            changes = list(payload.get("changes", []) or [])
            preview_lines = []
            for item in changes[:50]:
                preview_lines.append(
                    f"Node {int(item.get('node_id', -1))}: type {int(item.get('old_type', -1))} -> "
                    f"type {int(item.get('new_type', -1))}"
                )
            ctx.update(
                {
                    "custom_primary_label": "Auto Label Editing",
                    "custom_primary_action": "open_auto_label_popup",
                    "hide_apply_button": True,
                    "problem_detail": f"{len(changes)} nodes have likely incorrect neurite labels.",
                    "suggested_solution": "Review the suggested relabeling in Auto Label Editing and apply it from the tool if it looks correct.",
                    "detail_lines": [
                        f"Total affected nodes: {len(changes)}",
                        f"Affected node IDs: {', '.join(str(int(item.get('node_id', -1))) for item in changes[:25])}",
                        *(["Additional nodes not shown here: " + str(max(0, len(changes) - 25))] if len(changes) > 25 else []),
                        "",
                        "Problem details by node:",
                        "Suggested relabeling:",
                        *preview_lines,
                        *(["...", f"Only first 50 relabel suggestions shown here. Remaining nodes: {max(0, len(changes) - 50)}"] if len(changes) > 50 else []),
                    ],
                }
            )
            return ctx

        if node_ids:
            ctx["detail_lines"].extend(["", f"This issue is attached to {len(node_ids)} node(s). Use the highlighted nodes in the viewer to inspect the local morphology."])

        ctx["detail_lines"].append(f"Check key: {issue.get('source_key', '') or 'n/a'}")
        recommended_tool = str(issue.get("tool_target", "") or "").strip()
        if recommended_tool:
            ctx["detail_lines"].append(f"Recommended tool: {recommended_tool}")
        return ctx

    def _apply_document_dataframe(
        self,
        doc: _DocumentState,
        df: pd.DataFrame,
        *,
        event_title: str = "",
        event_summary: str = "",
        event_details: list[str] | None = None,
        push_history: bool = True,
        record_type_changes: bool = True,
        id_map: dict[int, int] | None = None,
        change_rows: list[dict] | None = None,
    ):
        old_df = doc.df if isinstance(doc.df, pd.DataFrame) else None
        next_df = df.copy()
        _copy_swc_metadata(next_df, old_df)
        doc.pending_type_suspicion_issues = None
        doc.df = next_df
        if push_history:
            self._push_document_history(doc, old_df, doc.df)
        self._write_recovery_copy(doc)
        doc.editor.load_swc(doc.df, doc.filename)
        doc.editor.set_mode(self._editor_mode_for_feature())
        self._refresh_edit_history_state(doc)
        if event_title:
            self._record_session_operation(
                doc,
                title=event_title,
                summary=event_summary,
                old_df=old_df,
                new_df=doc.df,
                details=event_details or [],
                id_map=dict(id_map or {}) if isinstance(id_map, dict) else None,
                change_rows=list(change_rows or []) if isinstance(change_rows, list) else None,
            )
        self._sync_from_active_document(auto_run_validation=False)

    def _rerun_active_validation(self, *, resolved_issue_id: str | None = None):
        doc = self._active_document()
        if doc is None:
            return
        self._validation_tab.stop_worker(wait_ms=5000)
        if resolved_issue_id:
            doc.pending_resolved_issue_ids.add(str(resolved_issue_id))
            doc.issue_status_overrides.pop(str(resolved_issue_id), None)
        doc.validation_report = None
        self._issue_panel.clear_issues("Running validation and issue detectors...")
        self._set_issue_status([])
        doc.editor.set_issue_markers([])
        doc.editor.clear_selection()
        self._validation_tab.load_swc(doc.df, doc.filename, file_path=doc.file_path, auto_run=False)
        self._validation_tab.run_validation()

    def _find_issue_by_id(self, doc: _DocumentState | None, issue_id: str) -> dict | None:
        if doc is None:
            return None
        wanted = str(issue_id or "").strip()
        if not wanted:
            return None
        for item in doc.issues:
            if str(item.get("issue_id", "")).strip() == wanted:
                return dict(item)
        return None

    def _find_matching_issue(self, doc: _DocumentState | None, previous_issue: dict | None) -> dict | None:
        if doc is None or not isinstance(previous_issue, dict):
            return None
        candidates = [dict(item) for item in doc.issues if isinstance(item, dict)]
        if not candidates:
            return None

        previous_source_key = str(previous_issue.get("source_key", "")).strip()
        previous_title = str(previous_issue.get("title", "")).strip()
        previous_domain = str(previous_issue.get("domain", "")).strip()
        previous_certainty = str(previous_issue.get("certainty", "")).strip()

        scoped = candidates
        if previous_source_key:
            same_source = [
                item for item in candidates
                if str(item.get("source_key", "")).strip() == previous_source_key
            ]
            if same_source:
                scoped = same_source
        elif previous_title:
            same_title = [
                item for item in candidates
                if str(item.get("title", "")).strip() == previous_title
            ]
            if same_title:
                scoped = same_title

        previous_nodes = {int(v) for v in previous_issue.get("node_ids", [])}
        previous_sections = {int(v) for v in previous_issue.get("section_ids", [])}

        def _score(candidate: dict) -> tuple[int, int, int, int, int, int, str]:
            score = 0
            candidate_source = str(candidate.get("source_key", "")).strip()
            candidate_title = str(candidate.get("title", "")).strip()
            candidate_domain = str(candidate.get("domain", "")).strip()
            candidate_certainty = str(candidate.get("certainty", "")).strip()
            if candidate_source and candidate_source == previous_source_key:
                score += 1000
            if candidate_title and candidate_title == previous_title:
                score += 200
            if candidate_domain and candidate_domain == previous_domain:
                score += 100
            if candidate_certainty and candidate_certainty == previous_certainty:
                score += 50

            candidate_nodes = {int(v) for v in candidate.get("node_ids", [])}
            candidate_sections = {int(v) for v in candidate.get("section_ids", [])}
            node_overlap = len(previous_nodes & candidate_nodes)
            section_overlap = len(previous_sections & candidate_sections)
            score += node_overlap * 10
            score += section_overlap * 5

            return (
                score,
                node_overlap,
                section_overlap,
                -abs(len(candidate_nodes) - len(previous_nodes)),
                -abs(len(candidate_sections) - len(previous_sections)),
                -len(candidate_nodes),
                str(candidate.get("issue_id", "")).strip(),
            )

        best = max(scoped, key=_score, default=None)
        if not isinstance(best, dict):
            return None

        best_source = str(best.get("source_key", "")).strip()
        best_title = str(best.get("title", "")).strip()
        best_nodes = {int(v) for v in best.get("node_ids", [])}
        best_sections = {int(v) for v in best.get("section_ids", [])}
        if previous_source_key and best_source == previous_source_key:
            if (
                len(scoped) == 1
                or bool(previous_nodes & best_nodes)
                or bool(previous_sections & best_sections)
                or (not previous_nodes and not previous_sections)
            ):
                return best
            return None
        if previous_title and best_title == previous_title:
            return best
        return None

    def _on_apply_suggested_fix_requested(self, issue_id: str):
        doc = self._active_document()
        if doc is None or doc.df is None or doc.df.empty:
            return
        issue = self._find_issue_by_id(doc, issue_id)
        if not issue:
            return
        source_key = str(issue.get("source_key", "")).strip()
        payload = dict(issue.get("source_payload", {}) or {})

        # Provenance: both branches below mutate doc.df. Each commits as
        # one AUTO_FIX op carrying the issue_id + applied-node count.
        from swcstudio.core.provenance import OpKind  # noqa: PLC0415

        if source_key == "radii_outlier_batch":
            changes = list(payload.get("changes", []) or [])
            if not changes:
                return
            df = doc.df.copy()
            applied = []
            for item in changes:
                node_id = int(item.get("node_id", -1))
                mask = df["id"] == node_id
                if not bool(mask.any()):
                    continue
                old_radius = float(df.loc[mask, "radius"].iloc[0])
                new_radius = float(item.get("new_radius", old_radius))
                df.loc[mask, "radius"] = new_radius
                applied.append(f"Node {node_id}: {old_radius:.5g} -> {new_radius:.5g}")
            if not applied:
                return
            self._record_tracked_commit(
                doc, df, kind=OpKind.AUTO_FIX,
                params={"issue_id": str(issue_id), "fix_kind": "radii_outlier_batch",
                        "applied_count": len(applied)},
                message=f"GUI apply suggested radii: issue={issue_id} ({len(applied)} nodes)",
            )
            self._apply_document_dataframe(
                doc,
                df,
                event_title="Apply Suggested Radii",
                event_summary=f"Applied suggested radii to {len(applied)} nodes.",
                event_details=applied[:20] + [f"Issue: {issue_id}"],
            )
            self._append_log(f"Applied suggested radii to {len(applied)} nodes.", "INFO")
            self._rerun_active_validation(resolved_issue_id=issue_id)
            return

        if source_key == "type_suspicion_batch":
            changes = list(payload.get("changes", []) or [])
            if not changes:
                return
            df = doc.df.copy()
            applied = []
            for item in changes:
                node_id = int(item.get("node_id", -1))
                mask = df["id"] == node_id
                if not bool(mask.any()):
                    continue
                old_type = int(df.loc[mask, "type"].iloc[0])
                new_type = int(item.get("new_type", old_type))
                df.loc[mask, "type"] = new_type
                applied.append(f"Node {node_id}: {old_type} -> {new_type}")
            if not applied:
                return
            self._record_tracked_commit(
                doc, df, kind=OpKind.AUTO_FIX,
                params={"issue_id": str(issue_id), "fix_kind": "type_suspicion_batch",
                        "applied_count": len(applied)},
                message=f"GUI apply suggested labels: issue={issue_id} ({len(applied)} nodes)",
            )
            self._apply_document_dataframe(
                doc,
                df,
                event_title="Apply Suggested Labels",
                event_summary=f"Applied suggested labels to {len(applied)} nodes.",
                event_details=applied[:20] + [f"Issue: {issue_id}"],
            )
            self._append_log(f"Applied suggested labels to {len(applied)} nodes.", "INFO")
            self._rerun_active_validation(resolved_issue_id=issue_id)

    def _on_skip_issue_requested(self, issue_id: str, skipping: bool):
        doc = self._active_document()
        if doc is None:
            return
        key = str(issue_id or "").strip()
        if not key:
            return
        if skipping:
            doc.issue_status_overrides[key] = "muted"
        else:
            doc.issue_status_overrides.pop(key, None)
        for item in doc.issues:
            if str(item.get("issue_id", "")).strip() == key:
                item["status"] = "muted" if skipping else "open"
        self._apply_issue_state(doc)
        if self._issue_panel.select_issue(key):
            self._on_issue_selected(next((item for item in doc.issues if str(item.get("issue_id", "")).strip() == key), None) or {})

    def _on_context_open_tool_requested(self, tool_target: str):
        target = str(tool_target or "").strip().lower()
        if target in {"label_editing", "auto_label", "radii_cleaning", "manual_radii"}:
            self._activate_feature("morphology_editing")
            target_tab = {
                "label_editing": "manual label editing",
                "auto_label": "auto label editing",
                "manual_radii": "manual radii editing",
                "radii_cleaning": "auto radii editing",
            }.get(target, "manual label editing")
            self._select_control_tab_by_label(target_tab)
            return
        if target == "simplification":
            self._activate_feature("geometry_editing")
            self._select_control_tab_by_label("simplification")
            return
        if target == "geometry_editing":
            self._activate_feature("geometry_editing")
            self._select_control_tab_by_label("geometry editing")
            return
        self._route_issue_to_tool({"tool_target": target})

    def _on_context_custom_action_requested(self, issue_id: str, action_id: str):
        action = str(action_id or "").strip().lower()
        if action == "open_manual_label_popup":
            self._control_tabs.setVisible(True)
            self._activate_feature("morphology_editing")
            self._select_control_tab_by_label("manual label editing")
            return
        if action == "open_auto_label_popup":
            self._control_tabs.setVisible(True)
            self._activate_feature("morphology_editing")
            self._select_control_tab_by_label("auto label editing")
            return
        if action == "open_manual_radii_tool":
            self._control_tabs.setVisible(True)
            self._activate_feature("morphology_editing")
            self._select_control_tab_by_label("manual radii editing")
            return
        if action == "open_auto_radii_tool":
            self._control_tabs.setVisible(True)
            self._activate_feature("morphology_editing")
            self._select_control_tab_by_label("auto radii editing")
            return
        if action == "open_simplification_tool":
            self._control_tabs.setVisible(True)
            self._activate_feature("geometry_editing")
            self._select_control_tab_by_label("simplification")
            return
        if action == "consolidate_soma":
            doc = self._active_document()
            issue = self._find_issue_by_id(doc, issue_id)
            if doc is None or issue is None or doc.df is None or doc.df.empty:
                return
            arr = np.zeros(
                len(doc.df),
                dtype=[
                    ("id", np.int64),
                    ("type", np.int64),
                    ("x", np.float64),
                    ("y", np.float64),
                    ("z", np.float64),
                    ("radius", np.float64),
                    ("parent", np.int64),
                ],
            )
            for col in SWC_COLS:
                arr[col] = doc.df[col].to_numpy()
            result = consolidate_complex_somas_array(arr)
            final_arr = np.array(result.get("array", arr), copy=True)
            if final_arr.size == 0:
                return
            new_df = pd.DataFrame({col: final_arr[col] for col in SWC_COLS}, columns=SWC_COLS)
            new_df["id"] = new_df["id"].astype(int)
            new_df["type"] = new_df["type"].astype(int)
            new_df["parent"] = new_df["parent"].astype(int)
            group_infos = list(result.get("groups", []) or [])
            changed = bool(result.get("changed"))
            removed_nodes = max(0, int(arr.size) - int(final_arr.size))
            event_details = [
                f"Collapsed soma groups: {int(result.get('group_count', 0))}",
                f"Complex groups changed: {len(list(result.get('complex_groups', []) or []))}",
                f"Removed soma nodes: {removed_nodes}",
                "Surviving node IDs are preserved; consolidation does not renumber the SWC.",
            ]
            for group in group_infos[:10]:
                event_details.append(
                    f"Anchor {int(group.get('anchor_id', -1))}: "
                    f"{int(group.get('group_size', 0))} node(s) -> radius {float(group.get('radius', 0.0)):.5g}"
                )
            if not changed:
                event_details.append("No connected multi-node soma groups required consolidation.")
            from swcstudio.core.provenance import OpKind  # noqa: PLC0415
            self._record_tracked_commit(
                doc, new_df, kind=OpKind.PLUGIN_OP,
                params={"plugin": "consolidate_soma", "issue_id": str(issue_id),
                        "group_count": int(result.get("group_count", 0)),
                        "removed_nodes": removed_nodes, "changed": changed},
                message=f"GUI consolidate-soma (issue {issue_id})",
            )
            self._apply_document_dataframe(
                doc,
                new_df,
                event_title="Consolidate Soma",
                event_summary=(
                    f"Collapsed {len(list(result.get('complex_groups', []) or []))} complex soma group(s) "
                    f"into mega-node representation."
                ),
                event_details=event_details,
            )
            self._append_log(
                f"Consolidated soma representation: groups={int(result.get('group_count', 0))}, removed_nodes={removed_nodes}.",
                "INFO",
            )
            self._rerun_active_validation(resolved_issue_id=issue_id)
            return
        if action == "split_trees":
            doc = self._active_document()
            issue = self._find_issue_by_id(doc, issue_id) if doc is not None else None
            saved_paths = list(self._validation_tab._on_save_all() or [])
            if not saved_paths:
                return
            if doc is not None and not doc.is_preview:
                payload = dict((issue or {}).get("source_payload", {}) or {})
                soma_ids = list(payload.get("metrics", {}).get("soma_ids_after_consolidation", []) or [])
                event_details = [
                    f"Original file: {doc.file_path or doc.filename}",
                    f"Disconnected soma groups: {len(soma_ids)}",
                    f"Split output files: {len(saved_paths)}",
                ]
                preview_limit = 12
                for out_path in saved_paths[:preview_limit]:
                    event_details.append(f"Output: {out_path}")
                if len(saved_paths) > preview_limit:
                    event_details.append(f"... and {len(saved_paths) - preview_limit} more output file(s)")
                self._record_session_event(
                    doc,
                    kind="split",
                    title="Split Trees",
                    summary=f"Split {doc.filename or 'SWC'} into {len(saved_paths)} output SWC file(s).",
                    details=event_details,
                )
            first_path = str(saved_paths[0])
            reply = QMessageBox.question(
                self,
                "Open First Split Tree",
                (
                    f"Saved {len(saved_paths)} split SWC file(s).\n\n"
                    f"Do you want to open the first tree now?\n{first_path}"
                ),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if reply == QMessageBox.Yes:
                self._load_swc(first_path)
            return
        if action == "download_validation_report":
            self._validation_tab._on_download_report()
            return
        if action == "define_custom_types":
            doc = self._active_document()
            issue = self._find_issue_by_id(doc, issue_id)
            if not issue:
                return
            payload = dict(issue.get("source_payload", {}) or {})
            missing_defs = list(payload.get("metrics", {}).get("undefined_custom_types", []) or [])
            if not missing_defs:
                return
            dialog = DefineCustomTypesDialog(missing_defs, self)
            if dialog.exec() != QDialog.Accepted:
                return
            if doc is not None:
                doc.editor.load_swc(doc.df, doc.filename)
                self._table_widget.load_dataframe(doc.df, doc.filename)
            self._append_log(f"Saved {len(missing_defs)} custom type definition(s).", "INFO")
            self._rerun_active_validation(resolved_issue_id=issue_id)
            return

    def _on_issue_panel_export_swc_requested(self):
        self._on_export()

    def _toggle_data_panel(self, checked: bool):
        self._data_dock.setVisible(bool(checked))

    def _toggle_control_panel(self, checked: bool):
        self._control_dock.setVisible(bool(checked))

    def _toggle_precheck_panel(self, checked: bool):
        if checked:
            self._show_precheck_floating()
        else:
            self._precheck_dock.hide()

    def _toggle_auto_typing_guide_panel(self, checked: bool):
        if checked:
            if self._is_auto_label_control_active():
                self._show_auto_typing_guide_floating()
            else:
                self._auto_guide_dock.hide()
                self._append_log(
                    "Auto Typing Guide opens when an Auto Label Editing control tab is active.",
                    "INFO",
                )
        else:
            self._auto_guide_dock.hide()

    def _toggle_log_panel(self, checked: bool):
        self._bottom_log_title.setVisible(bool(checked))
        self._edit_log_text.setVisible(bool(checked))

    def _show_precheck_floating(self):
        self._precheck_dock.show()
        self._precheck_dock.setFloating(True)
        g = self.geometry()
        w = max(760, int(g.width() * 0.62))
        h = max(360, int(g.height() * 0.36))
        x = g.x() + max(40, int((g.width() - w) * 0.5))
        y = g.y() + 120
        self._precheck_dock.setGeometry(x, y, w, h)
        self._precheck_dock.raise_()

    def _show_auto_typing_guide_floating(self):
        self._auto_typing_guide.refresh()
        self._auto_guide_dock.show()
        self._auto_guide_dock.setFloating(True)
        g = self.geometry()
        w = max(760, int(g.width() * 0.62))
        h = max(360, int(g.height() * 0.36))
        x = g.x() + max(40, int((g.width() - w) * 0.5))
        y = g.y() + 160
        self._auto_guide_dock.setGeometry(x, y, w, h)
        self._auto_guide_dock.raise_()

    def _is_auto_label_control_active(self) -> bool:
        if self._active_tool not in ("batch", "validation"):
            return False
        idx = self._control_tabs.currentIndex()
        if idx < 0:
            return False
        label = self._control_tabs.tabText(idx).strip().lower()
        return label == "auto label editing"

    def _is_batch_validation_control_active(self) -> bool:
        if self._active_tool != "batch":
            return False
        idx = self._control_tabs.currentIndex()
        if idx < 0:
            return False
        label = self._control_tabs.tabText(idx).strip().lower()
        return label == "validation"

    def _on_control_tab_changed(self, _index: int):
        # Do not auto-popup guide docks on tab/tool switches.
        # Guides are opened only by explicit user actions.
        if hasattr(self, "_auto_guide_dock"):
            self._auto_guide_dock.hide()
        if hasattr(self, "_precheck_dock"):
            self._precheck_dock.hide()
        if self._active_tool in ("morphology_editing", "dendrogram"):
            self._refresh_simplification_panel_state()
            idx = self._control_tabs.currentIndex()
            if idx >= 0:
                label = self._control_tabs.tabText(idx).strip().lower()
                if label == "manual radii editing":
                    self._manual_radii_panel.ensure_stats_loaded()
                elif label == "auto radii editing":
                    self._validation_radii_panel.ensure_stats_loaded()
        self._refresh_canvas_surface()
        self._sync_top_feature_button_selection()

    def _on_precheck_requested(self):
        self._show_precheck_floating()

    def _on_batch_validation_ready(self, report: dict):
        self._batch_canvas.show_batch_validation_results(report)
        self._batch_canvas.set_mode(EditorTab.MODE_BATCH)
        self._batch_has_results = True
        self._refresh_canvas_surface()
        if self._active_tool == "batch":
            self._feature_label.setText("Active feature: Batch Processing")
        totals = dict(report.get("summary_total", {}))
        self._append_log(
            "Batch validation results loaded to canvas: "
            f"files={report.get('files_validated', 0)}/{report.get('files_total', 0)}, "
            f"pass={totals.get('pass', 0)}, warn={totals.get('warning', 0)}, "
            f"fail={totals.get('fail', 0)}",
            "INFO",
        )

    def _undo_edit(self):
        if self._undo_document(self._active_source_document()):
            self._append_log("Undo.", "INFO")

    def _redo_edit(self):
        if self._redo_document(self._active_source_document()):
            self._append_log("Redo.", "INFO")

    def closeEvent(self, event):
        close_plans: list[tuple[_DocumentState, dict]] = []
        self._closing_app = True

        if hasattr(self, "_validation_tab"):
            self._validation_tab.stop_worker(wait_ms=5000)

        for doc in list(self._documents.values()):
            close_plan = self._plan_document_close(doc, app_closing=True)
            if close_plan is None:
                self._closing_app = False
                event.ignore()
                return
            close_plans.append((doc, close_plan))

        for doc, close_plan in close_plans:
            if not self._apply_document_close_plan(doc, close_plan):
                self._closing_app = False
                event.ignore()
                return

        super().closeEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._set_current_file_label_text(self._filename)

    # ---------------- Help ----------------
    def _show_help_text_dialog(self, title: str, text: str):
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setIcon(QMessageBox.Information)
        box.setText(title)
        box.setInformativeText(text.replace("\n", "<br>"))
        box.exec()

    def _show_quick_manual(self):
        text = (
            "1) Top menu row: File/Edit/View/Window/Help dropdown menus.\n"
            "2) Tools bar: choose a tool, then choose its feature row under it.\n"
            "3) Issue Navigator and Inspector are dock windows (close, float, resize, move).\n"
            "4) You can open multiple SWC files as canvas tabs (select several in File -> Open); "
            "Window -> Next File / Previous File (Ctrl+Tab / Ctrl+Shift+Tab) moves between them.\n"
            "5) Drag a canvas tab outside the tab bar to detach it into a floating window.\n"
            "6) Closing a SWC tab/window always asks for confirmation; edited SWC copies are only written when you confirm the close-save dialog.\n"
            "7) Opening an SWC runs checks and populates the Issue Navigator automatically.\n"
            "8) Clicking an issue focuses the viewer and opens the related repair workflow.\n"
            "9) Bottom panel shows all logs and warnings."
        )
        self._show_help_text_dialog("Quick Manual", text)
        self._append_log(text, "HELP")

    def _show_shortcuts(self):
        next_keys = QKeySequence(QKeySequence.NextChild).toString(QKeySequence.NativeText)
        # Qt names Shift+Tab "Backtab"; users know it as Shift+Tab.
        previous_keys = (
            QKeySequence(QKeySequence.PreviousChild).toString(QKeySequence.NativeText).replace("Backtab", "Tab")
        )
        text = (
            "Ctrl+O: Open (select several files to open them all)\n"
            "Ctrl+S: Save\n"
            "Ctrl+Shift+S: Save As\n"
            "Ctrl+Z: Undo\n"
            "Ctrl+Shift+Z: Redo\n"
            f"{next_keys}: Next open file\n"
            f"{previous_keys}: Previous open file"
        )
        self._show_help_text_dialog("Shortcuts", text)
        self._append_log(text, "HELP")

    def _show_about_dialog(self):
        try:
            from swcstudio.core import updater
            versions = updater.current_versions()
        except Exception:  # noqa: BLE001
            versions = {"app": "unknown", "models": "unknown"}
        install_flavor = "bundled executable" if getattr(sys, "frozen", False) else "pip/source environment"
        try:
            from swcstudio.core.model_paths import labeler_info
            labeler = labeler_info()
            labeler_text = f"SWC auto-labeler {labeler['version']} ({labeler['source_commit'][:10]})"
        except Exception:  # noqa: BLE001
            labeler_text = "unknown"
        text = (
            "SWC-Studio is an issue-driven workspace for inspecting, repairing, "
            "and exporting neuron morphology files in SWC format.\n\n"
            "It provides a shared desktop GUI, CLI, and Python backend so the "
            "same validation and editing logic can be used interactively or in scripts.\n\n"
            f"App code version: {versions.get('app', 'unknown')}\n"
            f"Models version: {versions.get('models', 'unknown')}\n"
            f"Auto-labeler: {labeler_text}\n"
            f"Install flavor: {install_flavor}"
        )
        self._show_help_text_dialog("About SWC-Studio", text)
        self._append_log("Opened About dialog.", "INFO")

    # ----- History menu handlers (PROVENANCE_SPEC §14) -----
    def _on_open_history_browser(self):
        """Open the history browser dialog for the active SWC file."""
        path = self._file_path
        if not path:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(
                self,
                "History Browser",
                "Open an SWC file first, then choose History → Open History Browser.",
            )
            return
        try:
            from swcstudio.gui.history_panel import open_history_dialog
            open_history_dialog(self, path)
            self._append_log(f"Opened history browser for {path}", "INFO")
        except Exception as e:
            self._append_log(f"Could not open history browser: {e}", "ERROR")

    def _on_init_history_for_current_file(self):
        """Initialize .history/ for the active file; import legacy if present."""
        from PySide6.QtWidgets import QMessageBox
        path = self._file_path
        if not path:
            QMessageBox.information(
                self,
                "Initialize History",
                "Open an SWC file first, then choose History → Initialize History.",
            )
            return
        try:
            from swcstudio.core.provenance import (
                archive_history_dir,
                archive_path_for,
                history_dir_for,
                history_archive_exists,
                init_history,
                migrate_legacy_output_dir,
                needs_migration,
            )
            hist = history_dir_for(path)
            archive = archive_path_for(path)
            if hist.exists() or history_archive_exists(path):
                if hist.exists():
                    archive_history_dir(hist, path, remove_dir=True)
                QMessageBox.information(
                    self,
                    "Initialize History",
                    f"History is already initialized at {archive}.",
                )
                return
            if needs_migration(path):
                outcome = migrate_legacy_output_dir(path)
                if hist.exists():
                    archive_history_dir(hist, path, remove_dir=True)
                msg = f"Initialized history archive:\n{archive}"
                if outcome.imported_commit:
                    msg += f"\nImported state from {outcome.imported_from.name}."
                if outcome.legacy_files_kept:
                    msg += f"\nKept {outcome.legacy_files_kept} legacy file(s) in place."
                QMessageBox.information(self, "Initialize History", msg)
                self._append_log(msg.replace("\n", " "), "INFO")
            else:
                init_history(path)
                archive_history_dir(hist, path, remove_dir=True)
                QMessageBox.information(
                    self, "Initialize History",
                    f"Initialized history archive at {archive}.",
                )
                self._append_log(f"Initialized history for {path}", "INFO")
        except Exception as e:
            self._append_log(f"Could not initialize history: {e}", "ERROR")
            QMessageBox.warning(self, "Initialize History", str(e))

    def _check_for_updates(self):
        """Check GitHub Releases for a newer version and offer to install it.

        Probes the modular update manifest at
        https://github.com/Mio0v0/SWC-Studio/releases/latest/download/update_manifest.json
        and reports any newer code/models layers. The user is offered a
        choice: install in-place (the modular updater downloads only the
        changed layer), or open the release page in a browser for a manual
        full download.
        """
        try:
            from swcstudio.core import updater
        except ImportError:
            QMessageBox.warning(
                self, "Check for Updates",
                "Update module not available in this build.",
            )
            return

        self._append_log("Checking for updates...", "INFO")
        manifest = updater.fetch_manifest()
        if manifest is None:
            QMessageBox.information(
                self, "Check for Updates",
                "Could not reach the update server. Check your internet connection "
                "or try again later.",
            )
            self._append_log("Update check failed (no manifest).", "WARN")
            return

        updates = updater.available_updates(manifest)
        cur = updater.current_versions()
        if not updates:
            QMessageBox.information(
                self, "Check for Updates",
                f"You're up to date.\n\n"
                f"App code: {cur['app']}\n"
                f"Models:   {cur['models']}",
            )
            self._append_log(
                f"No updates available (app={cur['app']}, models={cur['models']}).",
                "INFO",
            )
            return

        # Build a friendly description of what's new
        lines = [f"Release {manifest.release_version} is available.", ""]
        if "app" in updates:
            size_mb = (manifest.app.size or 0) / (1024 * 1024)
            lines.append(f"  • App code: {cur['app']} → {updates['app']}  (~{size_mb:.0f} MB)")
        if "models" in updates:
            size_mb = (manifest.models.size or 0) / (1024 * 1024)
            lines.append(f"  • Models:   {cur['models']} → {updates['models']}  (~{size_mb:.0f} MB)")
        lines.append("")
        lines.append("Updates are downloaded into your user data directory and applied on")
        lines.append("next launch. Your desktop bundle is left untouched, so you can revert")
        lines.append("by deleting the override directory.")

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle("Check for Updates")
        box.setText("\n".join(lines))
        install_btn = box.addButton("Install Update", QMessageBox.AcceptRole)
        browser_btn = box.addButton("Open Release Page", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Cancel)
        box.exec()

        clicked = box.clickedButton()
        if clicked is install_btn:
            self._apply_updates(manifest, updates)
        elif clicked is browser_btn:
            from PySide6.QtGui import QDesktopServices
            from PySide6.QtCore import QUrl
            QDesktopServices.openUrl(QUrl(
                "https://github.com/Mio0v0/SWC-Studio/releases/latest"
            ))
            self._append_log("Opened release page in browser.", "INFO")
        # else: user cancelled

    def _apply_updates(self, manifest, updates):
        """Download + extract each update layer the user agreed to install."""
        from swcstudio.core import updater
        applied: list[str] = []
        for layer in ("app", "models"):
            if layer not in updates:
                continue
            package = manifest.app if layer == "app" else manifest.models
            self._append_log(
                f"Downloading {layer} update {package.version}...", "INFO",
            )
            try:
                target = updater.apply_update(layer, package)
            except Exception as exc:
                self._append_log(f"Update failed for {layer}: {exc}", "ERROR")
                QMessageBox.warning(
                    self, "Update Failed",
                    f"Could not install {layer} update:\n{exc}",
                )
                return
            applied.append(f"{layer} {package.version}  ->  {target}")
            self._append_log(f"Installed {layer} update at {target}", "INFO")

        msg_lines = ["Update installed:"] + ["  " + a for a in applied]
        if "app" in updates:
            msg_lines += ["", "Restart SWC-Studio to load the new code."]
        else:
            msg_lines += ["", "Models will be loaded on the next auto-label run."]
        QMessageBox.information(
            self, "Update Installed", "\n".join(msg_lines),
        )
