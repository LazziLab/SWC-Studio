"""Central visualization workspace for 3D, dendrogram, and multiview layouts."""

import numpy as np
import pandas as pd
import pyqtgraph as pg

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QBrush, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QSplitter,
    QStackedWidget,
    QSizePolicy,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .constants import color_for_type
from .dendrogram_widget import DendrogramWidget
from .font_utils import bold_font
from .neuron_3d_widget import Neuron3DWidget
from swcstudio.core.validation_catalog import CHECK_ORDER


def _tree_bold_font(widget: QWidget) -> QFont:
    return bold_font(widget.font(), point_size=11)


class _Projection2DWidget(QWidget):
    """Simple 2D projected view of SWC segments."""

    def __init__(self, title: str, x_col: str, y_col: str, parent=None):
        super().__init__(parent)
        self._title = title
        self._x_col = x_col
        self._y_col = y_col
        self._df: pd.DataFrame | None = None
        self._highlight_id: int | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        label = QLabel(title)
        label.setAlignment(Qt.AlignCenter)
        label.setStyleSheet("font-size: 12px; color: #333;")
        layout.addWidget(label)

        self._plot = pg.PlotWidget(background="white")
        self._plot.setMouseEnabled(x=True, y=True)
        self._plot.showGrid(x=False, y=False, alpha=0.1)
        self._plot.getAxis("bottom").setLabel(x_col)
        self._plot.getAxis("left").setLabel(y_col)
        layout.addWidget(self._plot, stretch=1)

    def load_swc(self, df: pd.DataFrame):
        self._df = df.copy()
        self._draw()

    def refresh(self, df: pd.DataFrame):
        self._df = df.copy()
        self._draw()

    def highlight_node(self, swc_id: int):
        self._highlight_id = swc_id
        self._draw()

    def clear_highlight(self):
        self._highlight_id = None
        self._draw()

    def _draw(self):
        self._plot.clear()
        if self._df is None or self._df.empty:
            return

        df = self._df
        ids = df["id"].to_numpy(dtype=int)
        types = df["type"].to_numpy(dtype=int)
        parents = df["parent"].to_numpy(dtype=int)
        x = df[self._x_col].to_numpy(dtype=float)
        y = df[self._y_col].to_numpy(dtype=float)
        id2idx = {int(ids[i]): i for i in range(len(ids))}

        lines_by_type: dict[int, list[float]] = {}
        for i in range(len(ids)):
            pid = int(parents[i])
            if pid < 0 or pid not in id2idx:
                continue
            p_idx = id2idx[pid]
            type_id = int(types[i])
            lines = lines_by_type.setdefault(type_id, [])
            lines.extend([x[p_idx], x[i], np.nan])

        y_by_type: dict[int, list[float]] = {}
        for i in range(len(ids)):
            pid = int(parents[i])
            if pid < 0 or pid not in id2idx:
                continue
            p_idx = id2idx[pid]
            type_id = int(types[i])
            rows = y_by_type.setdefault(type_id, [])
            rows.extend([y[p_idx], y[i], np.nan])

        for type_id, xs in lines_by_type.items():
            ys = y_by_type.get(type_id)
            if not xs or not ys:
                continue
            self._plot.plot(
                np.asarray(xs, dtype=float),
                np.asarray(ys, dtype=float),
                pen=pg.mkPen(color=color_for_type(int(type_id)), width=1.2),
                connect="finite",
            )

        if self._highlight_id is not None:
            row = df[df["id"] == int(self._highlight_id)]
            if not row.empty:
                hx = float(row.iloc[0][self._x_col])
                hy = float(row.iloc[0][self._y_col])
                self._plot.addItem(
                    pg.ScatterPlotItem(
                        [hx], [hy], size=10, symbol="o",
                        pen=pg.mkPen("#e11", width=1.5),
                        brush=pg.mkBrush("#ff444466"),
                    )
                )


class EditorTab(QWidget):
    """Workspace with mode switching: canvas, dendrogram, and visualization."""

    df_changed = Signal(pd.DataFrame)
    node_selected = Signal(int)

    MODE_CANVAS = "canvas"
    MODE_EMPTY = "empty"
    MODE_BATCH = "batch"
    MODE_DENDRO = "dendrogram"
    MODE_VIS = "visualization"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self._df: pd.DataFrame | None = None
        self._mode = self.MODE_CANVAS
        self._has_data = False
        self._issue_marker_signature: tuple[tuple[int, ...], ...] = ()

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        self._stack = QStackedWidget()
        self._stack.setMinimumWidth(0)
        self._stack.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        root_layout.addWidget(self._stack)

        self._page_empty = QWidget()
        self._page_empty.setMinimumWidth(0)
        self._page_empty.setStyleSheet("background: #000;")
        self._stack.addWidget(self._page_empty)

        self._page_batch = QWidget()
        self._page_batch.setMinimumWidth(0)
        self._page_batch.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        batch_layout = QVBoxLayout(self._page_batch)
        batch_layout.setContentsMargins(8, 8, 8, 8)
        batch_layout.setSpacing(6)
        batch_title = QLabel("Batch Validation Results")
        batch_title.setStyleSheet("font-size: 14px; font-weight: 600; color: #333;")
        batch_layout.addWidget(batch_title)
        self._batch_summary = QLabel("Run Batch Processing -> Validation to populate results.")
        self._batch_summary.setWordWrap(True)
        self._batch_summary.setStyleSheet("font-size: 12px; color: #555;")
        batch_layout.addWidget(self._batch_summary)
        self._batch_tree = QTreeWidget()
        self._batch_tree.setMinimumWidth(0)
        self._batch_tree.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self._batch_tree.setHeaderLabels(["Status", "Label"])
        self._batch_tree.setRootIsDecorated(True)
        self._batch_tree.setItemsExpandable(True)
        self._batch_tree.setAlternatingRowColors(True)
        self._batch_tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self._batch_tree.setStyleSheet(
            "QTreeWidget { font-size: 12px; gridline-color: #ddd; }"
            "QHeaderView::section { font-weight: 600; padding: 4px; }"
        )
        batch_header = self._batch_tree.header()
        batch_header.setSectionResizeMode(0, QHeaderView.Interactive)
        batch_header.setSectionResizeMode(1, QHeaderView.Interactive)
        batch_header.setStretchLastSection(False)
        self._batch_tree.setColumnWidth(0, 360)
        self._batch_tree.setColumnWidth(1, 420)
        batch_layout.addWidget(self._batch_tree, stretch=1)
        self._stack.addWidget(self._page_batch)

        self._view3d_canvas = Neuron3DWidget()
        self._page_canvas = QWidget()
        canvas_layout = QVBoxLayout(self._page_canvas)
        canvas_layout.setContentsMargins(0, 0, 0, 0)
        canvas_layout.addWidget(self._view3d_canvas)
        self._stack.addWidget(self._page_canvas)

        self._view3d_dendro = Neuron3DWidget()
        self._dendro = DendrogramWidget()
        self._page_dendro = QWidget()
        dendro_layout = QVBoxLayout(self._page_dendro)
        dendro_layout.setContentsMargins(0, 0, 0, 0)
        split = QSplitter(Qt.Horizontal)
        split.addWidget(self._view3d_dendro)
        split.addWidget(self._dendro)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 1)
        dendro_layout.addWidget(split)
        self._stack.addWidget(self._page_dendro)

        self._view3d_visual = Neuron3DWidget()
        self._proj_xy = _Projection2DWidget("Top View (X-Y)", "x", "y")
        self._proj_xz = _Projection2DWidget("Front View (X-Z)", "x", "z")
        self._proj_yz = _Projection2DWidget("Side View (Y-Z)", "y", "z")

        proj_row = QWidget()
        proj_layout = QHBoxLayout(proj_row)
        proj_layout.setContentsMargins(0, 0, 0, 0)
        proj_layout.setSpacing(6)
        proj_layout.addWidget(self._proj_xy)
        proj_layout.addWidget(self._proj_xz)
        proj_layout.addWidget(self._proj_yz)

        self._page_visual = QWidget()
        visual_layout = QVBoxLayout(self._page_visual)
        visual_layout.setContentsMargins(0, 0, 0, 0)
        visual_split = QSplitter(Qt.Vertical)
        visual_split.addWidget(self._view3d_visual)
        visual_split.addWidget(proj_row)
        visual_split.setStretchFactor(0, 3)
        visual_split.setStretchFactor(1, 2)
        visual_layout.addWidget(visual_split)
        self._stack.addWidget(self._page_visual)

        self._dendro.df_changed.connect(self._on_df_changed)
        self._dendro.node_selected.connect(self._on_node_selected)
        self._view3d_canvas.node_clicked.connect(self._on_view_node_clicked)
        self._view3d_dendro.node_clicked.connect(self._on_view_node_clicked)
        self._view3d_visual.node_clicked.connect(self._on_view_node_clicked)

        self._show_current_mode()

    # --------------------------------------------------------- Public API
    def load_swc(self, df: pd.DataFrame, filename: str = ""):
        self._df = df.copy()
        self._has_data = True
        self._dendro.load_swc(df, filename)
        self._view3d_canvas.load_swc(df, filename)
        self._view3d_dendro.load_swc(df, filename)
        self._view3d_visual.load_swc(df, filename)
        self._proj_xy.load_swc(df)
        self._proj_xz.load_swc(df)
        self._proj_yz.load_swc(df)
        self._show_current_mode()

    def set_mode(self, mode: str):
        if mode in (self.MODE_CANVAS, self.MODE_EMPTY, self.MODE_BATCH, self.MODE_DENDRO, self.MODE_VIS):
            self._mode = mode
            self._show_current_mode()

    def show_batch_validation_results(self, batch_report: dict):
        self._batch_tree.clear()
        bold_item_font = _tree_bold_font(self)
        totals = dict(batch_report.get("summary_total", {}))
        self._batch_summary.setText(
            f"Folder: {batch_report.get('folder', '')}\n"
            f"Files validated: {batch_report.get('files_validated', 0)}/{batch_report.get('files_total', 0)}  "
            f"Failed files: {batch_report.get('files_failed', 0)}\n"
            f"Checks: total={totals.get('total', 0)} pass={totals.get('pass', 0)} "
            f"warn={totals.get('warning', 0)} fail={totals.get('fail', 0)}"
        )

        for fr in batch_report.get("results", []):
            fname = str(fr.get("file", ""))
            report = dict(fr.get("report", {}))
            summary = dict(report.get("summary", {}))
            pass_n = int(summary.get("pass", 0))
            fail_n = int(summary.get("fail", 0))
            warn_n = int(summary.get("warning", 0))
            top = QTreeWidgetItem(["", fname])
            top.setTextAlignment(0, Qt.AlignLeft | Qt.AlignVCenter)
            top.setTextAlignment(1, Qt.AlignLeft | Qt.AlignVCenter)
            top.setForeground(1, QBrush(QColor("#000000")))
            top.setToolTip(
                1,
                (
                    f"pass={pass_n} "
                    f"fail={fail_n} "
                    f"warning={warn_n}"
                ),
            )
            top.setExpanded(False)
            self._batch_tree.addTopLevelItem(top)
            self._batch_tree.setItemWidget(
                top,
                0,
                self._make_batch_status_counts_widget(pass_n, fail_n, warn_n),
            )

            rows = list(report.get("results", []))
            rows.sort(key=self._result_sort_key)
            for row in rows:
                status = str(row.get("status", "")).lower()
                tag, color = self._status_cell(status)
                label = str(row.get("label", row.get("key", "")))
                detail = str(row.get("message", "")).strip()
                child = QTreeWidgetItem([tag, label])
                child.setTextAlignment(0, Qt.AlignLeft | Qt.AlignVCenter)
                child.setTextAlignment(1, Qt.AlignLeft | Qt.AlignVCenter)
                child.setFont(0, bold_item_font)
                child.setForeground(0, QBrush(QColor(color)))
                if detail:
                    child.setToolTip(1, detail)
                top.addChild(child)

        if batch_report.get("failures"):
            fail_top = QTreeWidgetItem(["FAIL", "File errors"])
            fail_top.setFont(0, bold_item_font)
            fail_top.setForeground(0, QBrush(QColor("#d62728")))
            fail_top.setExpanded(False)
            self._batch_tree.addTopLevelItem(fail_top)
            for err in batch_report.get("failures", []):
                child = QTreeWidgetItem(["FAIL", str(err)])
                child.setFont(0, bold_item_font)
                child.setForeground(0, QBrush(QColor("#d62728")))
                fail_top.addChild(child)

        self._batch_tree.expandToDepth(0)

    def _status_cell(self, status: str) -> tuple[str, str]:
        s = (status or "").lower()
        if s == "pass":
            return "PASS", "#2ca02c"
        if s == "info":
            return "INFO", "#1f77b4"
        if s == "warning":
            return "WARN", "#ff9900"
        return "FAIL", "#d62728"

    def _make_batch_status_counts_widget(self, pass_n: int, fail_n: int, warn_n: int) -> QLabel:
        w = QLabel()
        w.setTextFormat(Qt.RichText)
        w.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        w.setStyleSheet(
            "QLabel { background: transparent; font-size: 11pt; font-weight: 700; }"
        )
        w.setText(
            (
                f"<span style='color:#2ca02c'>PASS #{int(pass_n)}</span>"
                f" / <span style='color:#d62728'>FAIL #{int(fail_n)}</span>"
                f" / <span style='color:#ffcc00'>WARNING #{int(warn_n)}</span>"
            )
        )
        return w

    def _result_sort_key(self, row: dict) -> tuple[int, str]:
        key = str(row.get("key", ""))
        label = str(row.get("label", ""))
        return (CHECK_ORDER.get(key, 1000), label.lower())

    def take_dendrogram_controls_panel(self) -> QWidget:
        return self._dendro.take_controls_panel()

    def set_render_mode(self, mode_id: int):
        self._view3d_canvas.set_render_mode(mode_id)
        self._view3d_dendro.set_render_mode(mode_id)
        self._view3d_visual.set_render_mode(mode_id)

    def set_edit_history_state(self, can_undo: bool, can_redo: bool):
        self._dendro.set_history_state(can_undo, can_redo)

    def set_issue_markers(self, issues: list[dict]):
        signature = tuple(
            tuple(sorted(int(node_id) for node_id in issue.get("node_ids", [])))
            for issue in issues
        )
        if signature == self._issue_marker_signature:
            return
        self._issue_marker_signature = signature
        self._view3d_canvas.set_issue_markers(issues)
        self._view3d_dendro.set_issue_markers(issues)
        self._view3d_visual.set_issue_markers(issues)

    def set_geometry_selection(self, node_ids: list[int] | set[int], visibility_mode: str = "show"):
        self._view3d_canvas.set_geometry_selection(node_ids, visibility_mode)
        self._view3d_dendro.set_geometry_selection(node_ids, visibility_mode)
        self._view3d_visual.set_geometry_selection(node_ids, visibility_mode)
        self._dendro.set_geometry_selection(node_ids, visibility_mode)

    def clear_geometry_selection(self):
        self._view3d_canvas.clear_geometry_selection()
        self._view3d_dendro.clear_geometry_selection()
        self._view3d_visual.clear_geometry_selection()
        self._dendro.clear_geometry_selection()

    def zoom_to_node_ids(self, node_ids: list[int] | set[int]):
        self._view3d_canvas.zoom_to_node_ids(node_ids)
        self._view3d_dendro.zoom_to_node_ids(node_ids)
        self._view3d_visual.zoom_to_node_ids(node_ids)
        self._dendro.zoom_to_node_ids(node_ids)

    def clear_selection(self):
        self._view3d_canvas.clear_selection()
        self._view3d_dendro.clear_selection()
        self._view3d_visual.clear_selection()
        self._dendro.clear_selection()
        self._proj_xy.clear_highlight()
        self._proj_xz.clear_highlight()
        self._proj_yz.clear_highlight()

    def focus_node(self, swc_id: int):
        self._view3d_canvas.focus_node(swc_id)
        self._view3d_dendro.focus_node(swc_id)
        self._view3d_visual.focus_node(swc_id)
        self._dendro.select_node_by_id(swc_id, emit_signal=False)
        self._proj_xy.highlight_node(swc_id)
        self._proj_xz.highlight_node(swc_id)
        self._proj_yz.highlight_node(swc_id)

    def set_camera_view(self, preset: str):
        self._active_view().set_camera_view(preset)

    def reset_camera(self):
        self._active_view().reset_camera()

    def _active_view(self) -> Neuron3DWidget:
        if self._mode == self.MODE_DENDRO:
            return self._view3d_dendro
        if self._mode == self.MODE_VIS:
            return self._view3d_visual
        return self._view3d_canvas

    # ------------------------------------------------- Sync
    def _on_df_changed(self, df: pd.DataFrame):
        self._df = df.copy()
        self._issue_marker_signature = ()
        self._view3d_canvas.refresh(df)
        self._view3d_dendro.refresh(df)
        self._view3d_visual.refresh(df)
        self._proj_xy.refresh(df)
        self._proj_xz.refresh(df)
        self._proj_yz.refresh(df)
        self.df_changed.emit(df)

    def _on_node_selected(self, swc_id: int, node_type: int, level: int):
        self._view3d_canvas.highlight_node(swc_id)
        self._view3d_dendro.highlight_node(swc_id)
        self._view3d_visual.highlight_node(swc_id)
        self._proj_xy.highlight_node(swc_id)
        self._proj_xz.highlight_node(swc_id)
        self._proj_yz.highlight_node(swc_id)
        self.node_selected.emit(int(swc_id))

    def _on_view_node_clicked(self, swc_id: int):
        self._view3d_canvas.highlight_node(swc_id)
        self._view3d_dendro.highlight_node(swc_id)
        self._view3d_visual.highlight_node(swc_id)
        self._dendro.select_node_by_id(swc_id, emit_signal=False)
        self._proj_xy.highlight_node(swc_id)
        self._proj_xz.highlight_node(swc_id)
        self._proj_yz.highlight_node(swc_id)
        self.node_selected.emit(int(swc_id))

    def _show_current_mode(self):
        if self._mode == self.MODE_EMPTY:
            self._stack.setCurrentWidget(self._page_empty)
            return
        if self._mode == self.MODE_BATCH:
            self._stack.setCurrentWidget(self._page_batch)
            return
        if not self._has_data:
            self._stack.setCurrentWidget(self._page_empty)
            return
        if self._mode == self.MODE_DENDRO:
            self._stack.setCurrentWidget(self._page_dendro)
            return
        if self._mode == self.MODE_VIS:
            self._stack.setCurrentWidget(self._page_visual)
            return
        self._stack.setCurrentWidget(self._page_canvas)
