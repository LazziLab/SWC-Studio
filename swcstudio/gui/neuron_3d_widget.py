"""3D neuron visualization widget using VisPy."""

import html

import numpy as np
import pandas as pd

from vispy import scene
from vispy.scene import visuals
from vispy.color import Color
from vispy.app import use_app

# Ensure VisPy uses PySide6
use_app("pyside6")

from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QRadioButton, QButtonGroup, QLabel, QGridLayout
from PySide6.QtCore import Qt, Signal

from .constants import color_for_type, label_for_type, type_legend_entries, SWC_COLS

# Number of facets around each frustum cross-section
_N_FACETS = 8
_THETA = np.linspace(0, 2 * np.pi, _N_FACETS, endpoint=False, dtype=np.float32)
_COS = np.cos(_THETA)
_SIN = np.sin(_THETA)
_ISSUE_HIGHLIGHT_HEX = "#18e0cf"
_GEOMETRY_HIGHLIGHT_HEX = "#ff006e"


def _hex_to_rgba(hex_color: str) -> np.ndarray:
    """Convert '#rrggbb' to [r, g, b, a] float array."""
    h = hex_color.lstrip("#")
    return np.array([int(h[i:i+2], 16) / 255.0 for i in (0, 2, 4)] + [1.0], dtype=np.float32)


def _frustum_mesh(p0, r0, p1, r1, color_rgba):
    """Build a truncated-cone mesh between two points with radii r0 and r1.

    Returns (vertices, faces, colors) arrays ready to be concatenated.
    Each frustum has 2*N_FACETS vertices and 2*N_FACETS triangular faces.
    """
    n = _N_FACETS
    # Direction vector
    d = p1 - p0
    length = np.linalg.norm(d)
    if length < 1e-12:
        return None, None, None

    # Build orthonormal basis (d, u, v)
    d_hat = d / length
    # Find a vector not parallel to d_hat
    if abs(d_hat[0]) < 0.9:
        ref = np.array([1, 0, 0], dtype=np.float32)
    else:
        ref = np.array([0, 1, 0], dtype=np.float32)
    u = np.cross(d_hat, ref)
    u /= np.linalg.norm(u)
    v = np.cross(d_hat, u)

    # Circle at p0 (radius r0) and p1 (radius r1)
    verts = np.empty((2 * n, 3), dtype=np.float32)
    for i in range(n):
        offset = _COS[i] * u + _SIN[i] * v
        verts[i] = p0 + r0 * offset       # ring 0
        verts[n + i] = p1 + r1 * offset   # ring 1

    # Triangular faces: two triangles per quad
    faces = np.empty((2 * n, 3), dtype=np.uint32)
    for i in range(n):
        j = (i + 1) % n
        faces[2 * i]     = [i, n + i, n + j]
        faces[2 * i + 1] = [i, n + j, j]

    colors = np.tile(color_rgba, (2 * n, 1))
    return verts, faces, colors


class Neuron3DWidget(QWidget):
    """3D neuron structure rendered with VisPy, embeddable as a Qt widget."""

    node_clicked = Signal(int)

    MODE_LINES = 0
    MODE_SPHERES = 1
    MODE_FRUSTUM = 2

    def __init__(self, parent=None):
        super().__init__(parent)
        self._df: pd.DataFrame | None = None
        self._mode = self.MODE_LINES
        self._highlight_id: int | None = None
        self._issue_markers_data: list[dict] = []
        self._issue_marker_signature: tuple[tuple[int, ...], ...] = ()
        self._selectable_node_ids: set[int] | None = None
        self._node_position_by_id: dict[int, np.ndarray] = {}
        self._geometry_node_ids: set[int] = set()
        self._geometry_visibility_mode: str = "show"
        self._highlight_marker = None
        self._issue_markers = None
        self._type_legend_enabled = True
        self._type_legend_entries: list[tuple[int, str, str]] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Toggle bar
        toggle_bar = QHBoxLayout()
        toggle_bar.addWidget(QLabel("Render:"))
        self._mode_group = QButtonGroup(self)

        rb_lines = QRadioButton("Lines")
        rb_lines.setChecked(True)
        self._mode_group.addButton(rb_lines, self.MODE_LINES)
        toggle_bar.addWidget(rb_lines)

        rb_spheres = QRadioButton("Spheres")
        self._mode_group.addButton(rb_spheres, self.MODE_SPHERES)
        toggle_bar.addWidget(rb_spheres)

        rb_frustum = QRadioButton("Frustum")
        self._mode_group.addButton(rb_frustum, self.MODE_FRUSTUM)
        toggle_bar.addWidget(rb_frustum)

        self._mode_group.idToggled.connect(self._on_mode_change)
        toggle_bar.addStretch()
        layout.addLayout(toggle_bar)

        # Create VisPy canvas
        self._canvas = scene.SceneCanvas(keys="interactive", bgcolor="white")
        self._view = self._canvas.central_widget.add_view()
        self._view.camera = scene.TurntableCamera(
            distance=500, elevation=30, azimuth=45, fov=45,
        )
        self._canvas.events.mouse_press.connect(self._on_mouse_press)

        canvas_host = QWidget()
        canvas_layout = QGridLayout(canvas_host)
        canvas_layout.setContentsMargins(0, 0, 0, 0)
        canvas_layout.setSpacing(0)
        canvas_layout.addWidget(self._canvas.native, 0, 0)

        self._node_overlay = QLabel("")
        self._node_overlay.setWordWrap(True)
        self._node_overlay.setVisible(False)
        self._node_overlay.setMaximumWidth(240)
        self._node_overlay.setStyleSheet(
            "QLabel {"
            " background: rgba(255,255,255,235);"
            " border: 1px solid #d8e0eb;"
            " border-radius: 10px;"
            " padding: 8px 10px;"
            " color: #132238;"
            " font-size: 12px;"
            "}"
        )
        canvas_layout.addWidget(self._node_overlay, 0, 0, alignment=Qt.AlignTop | Qt.AlignLeft)

        # Colour key for the node types in this file (View -> Show Node-Type
        # Legend). It lets mouse events through so the corner stays usable.
        self._type_legend = QLabel("")
        self._type_legend.setTextFormat(Qt.RichText)
        self._type_legend.setVisible(False)
        self._type_legend.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._type_legend.setStyleSheet(
            "QLabel {"
            " background: rgba(255,255,255,220);"
            " border: 1px solid #d8e0eb;"
            " border-radius: 8px;"
            " padding: 4px 8px;"
            " color: #132238;"
            " font-size: 11px;"
            "}"
        )
        canvas_layout.addWidget(self._type_legend, 0, 0, alignment=Qt.AlignTop | Qt.AlignRight)
        layout.addWidget(canvas_host)

    # --------------------------------------------------------- Public API
    def load_swc(self, df: pd.DataFrame, filename: str = ""):
        self._df = df.copy()
        self._rebuild_node_cache()
        self._issue_markers_data = []
        self._issue_marker_signature = ()
        self._selectable_node_ids = None
        self._draw()
        self._update_type_legend()

    def refresh(self, df: pd.DataFrame):
        self._df = df.copy()
        self._rebuild_node_cache()
        self._issue_markers_data = []
        self._issue_marker_signature = ()
        self._selectable_node_ids = None
        self._draw()
        self._update_type_legend()

    def set_type_legend_visible(self, visible: bool):
        """Show or hide the node-type colour legend."""
        self._type_legend_enabled = bool(visible)
        self._type_legend.setVisible(self._type_legend_enabled and bool(self._type_legend_entries))

    def type_legend_entries(self) -> list[tuple[int, str, str]]:
        """The ``(type id, label, colour)`` rows the legend currently lists."""
        return list(self._type_legend_entries)

    def set_render_mode(self, mode_id: int):
        """Set rendering mode programmatically."""
        if mode_id not in (self.MODE_LINES, self.MODE_SPHERES, self.MODE_FRUSTUM):
            return
        self._mode = int(mode_id)
        btn = self._mode_group.button(self._mode)
        if btn is not None:
            btn.setChecked(True)
        self._draw()

    def set_camera_view(self, preset: str):
        """Apply a camera preset: iso, top, front, side."""
        p = (preset or "").strip().lower()
        if p == "top":
            self._view.camera.elevation = 90
            self._view.camera.azimuth = 0
        elif p == "front":
            self._view.camera.elevation = 0
            self._view.camera.azimuth = 90
        elif p == "side":
            self._view.camera.elevation = 0
            self._view.camera.azimuth = 0
        else:  # iso
            self._view.camera.elevation = 30
            self._view.camera.azimuth = 45
        self._fit_camera()
        self._canvas.update()

    def reset_camera(self):
        """Reset camera and fit to current data."""
        self._view.camera.fov = 45
        self._view.camera.elevation = 30
        self._view.camera.azimuth = 45
        self._fit_camera()
        self._canvas.update()

    def highlight_node(self, swc_id: int):
        """Highlight a specific node by SWC ID."""
        self._highlight_id = swc_id
        self._update_node_overlay(swc_id)
        self._draw_highlight()

    def focus_node(self, swc_id: int):
        """Center the camera on a node and highlight it."""
        self.highlight_node(swc_id)
        if self._df is None or self._df.empty:
            return
        row = self._df[self._df["id"] == int(swc_id)]
        if row.empty:
            return
        pos = row.iloc[0][["x", "y", "z"]].to_numpy(dtype=np.float32)
        self._view.camera.center = tuple(float(v) for v in pos)
        self._canvas.update()

    def zoom_to_node(self, swc_id: int):
        """Center, highlight, and zoom in toward a node."""
        self.focus_node(swc_id)
        current_distance = float(getattr(self._view.camera, "distance", 120.0) or 120.0)
        self._view.camera.distance = max(20.0, min(current_distance * 0.2, 220.0))
        self._canvas.update()

    def set_issue_markers(self, issues: list[dict]):
        new_signature = tuple(
            tuple(sorted(int(node_id) for node_id in issue.get("node_ids", [])))
            for issue in issues
        )
        if new_signature == self._issue_marker_signature:
            return
        self._issue_marker_signature = new_signature
        self._issue_markers_data = [dict(item) for item in issues]
        selectable_ids: set[int] = set()
        for issue in self._issue_markers_data:
            selectable_ids.update(int(node_id) for node_id in issue.get("node_ids", []))
        self._selectable_node_ids = selectable_ids if selectable_ids else None
        self._draw_issue_markers()

    def clear_selection(self):
        if self._highlight_id is None and not self._node_overlay.isVisible():
            return
        self._highlight_id = None
        self._node_overlay.clear()
        self._node_overlay.setVisible(False)
        self._draw_highlight()

    def set_geometry_selection(self, node_ids: list[int] | set[int], visibility_mode: str = "show"):
        wanted_ids = {int(v) for v in list(node_ids or [])}
        wanted_mode = str(visibility_mode or "show").strip().lower() or "show"
        if wanted_ids == self._geometry_node_ids and wanted_mode == self._geometry_visibility_mode:
            return
        self._geometry_node_ids = wanted_ids
        self._geometry_visibility_mode = wanted_mode
        self._draw()

    def clear_geometry_selection(self):
        if not self._geometry_node_ids and self._geometry_visibility_mode == "show":
            return
        self._geometry_node_ids = set()
        self._geometry_visibility_mode = "show"
        self._draw()

    def zoom_to_node_ids(self, node_ids: list[int] | set[int]):
        if self._df is None or self._df.empty:
            return
        wanted = {int(v) for v in list(node_ids or [])}
        if not wanted:
            return
        rows = self._df.loc[self._df["id"].astype(int).isin(sorted(wanted)), ["x", "y", "z"]]
        if rows.empty:
            return
        xyz = rows.to_numpy(dtype=np.float32)
        center = xyz.mean(axis=0)
        extent = xyz.max(axis=0) - xyz.min(axis=0)
        dist = float(np.linalg.norm(extent)) * 1.2
        self._view.camera.center = tuple(float(v) for v in center)
        self._view.camera.distance = max(dist, 25.0)
        self._canvas.update()

    # ------------------------------------------------- Toggle
    def _on_mode_change(self, btn_id, checked):
        if checked:
            self._mode = btn_id
            self._draw()

    # ------------------------------------------------- Rendering dispatch
    def _draw(self):
        for child in list(self._view.scene.children):
            if child is not self._view.camera:
                child.parent = None

        df = self._df
        if df is None or df.empty:
            self._canvas.update()
            return

        ids = df["id"].to_numpy()
        types = df["type"].to_numpy(dtype=int)
        parents = df["parent"].to_numpy(dtype=int)
        radii = df["radius"].to_numpy(dtype=np.float32)
        xyz = df[["x", "y", "z"]].to_numpy(dtype=np.float32)
        id2idx = {int(ids[i]): i for i in range(len(ids))}

        if self._mode == self.MODE_LINES:
            self._draw_lines(ids, types, parents, radii, xyz, id2idx)
        elif self._mode == self.MODE_SPHERES:
            self._draw_spheres(ids, types, parents, radii, xyz, id2idx)
        else:
            self._draw_frustum(ids, types, parents, radii, xyz, id2idx)

        self._fit_camera()

        # Draw highlight if set
        self._draw_issue_markers()
        self._draw_highlight()
        self._canvas.update()

    def _rebuild_node_cache(self):
        self._node_position_by_id = {}
        if self._df is None or self._df.empty:
            return
        ids = self._df["id"].to_numpy(dtype=int)
        xyz = self._df[["x", "y", "z"]].to_numpy(dtype=np.float32)
        self._node_position_by_id = {
            int(ids[i]): xyz[i]
            for i in range(len(ids))
        }

    def _fit_camera(self):
        if self._df is None or self._df.empty:
            return
        xyz = self._df[["x", "y", "z"]].to_numpy(dtype=np.float32)
        all_xyz = xyz[np.isfinite(xyz).all(axis=1)]
        if all_xyz.size == 0:
            return
        center = all_xyz.mean(axis=0)
        extent = all_xyz.max(axis=0) - all_xyz.min(axis=0)
        dist = float(np.linalg.norm(extent)) * 0.8
        self._view.camera.center = tuple(center)
        self._view.camera.distance = max(dist, 50)

    def _draw_highlight(self):
        """Draw a bright marker at the highlighted node's 3D position."""
        # Remove old highlight
        if self._highlight_marker is not None:
            self._highlight_marker.parent = None
            self._highlight_marker = None

        if self._highlight_id is None or self._df is None:
            self._node_overlay.clear()
            self._node_overlay.setVisible(False)
            self._canvas.update()
            return

        df = self._df
        row = df[df["id"] == self._highlight_id]
        if row.empty:
            self._node_overlay.clear()
            self._node_overlay.setVisible(False)
            self._canvas.update()
            return

        pos = row[["x", "y", "z"]].to_numpy(dtype=np.float32)
        self._highlight_marker = visuals.Markers(parent=self._view.scene)
        self._highlight_marker.set_data(
            pos, size=15,
            face_color=Color("#ff000060"),
            edge_color=Color("#ee0000"),
            edge_width=2.5,
            symbol="o",
        )
        self._canvas.update()

    def _update_node_overlay(self, swc_id: int):
        if self._df is None or self._df.empty:
            self._node_overlay.clear()
            self._node_overlay.setVisible(False)
            return
        row = self._df[self._df["id"] == int(swc_id)]
        if row.empty:
            self._node_overlay.clear()
            self._node_overlay.setVisible(False)
            return
        node = row.iloc[0]
        type_id = int(node["type"])
        self._node_overlay.setText(
            f"ID: {int(node['id'])}\n"
            f"Type: {label_for_type(type_id)} ({type_id})\n"
            f"Radius: {float(node['radius']):.5g}"
        )
        self._node_overlay.setVisible(True)

    def _update_type_legend(self):
        """List the colour of each node type present in the current file."""
        if self._df is None or self._df.empty or "type" not in self._df.columns:
            entries = []
        else:
            entries = type_legend_entries(pd.unique(self._df["type"]))
        self._type_legend_entries = entries
        self._type_legend.setText("<br>".join(
            f"<span style='color:{color}; font-size:13px;'>&#9632;</span>&nbsp;"
            f"{html.escape(label)} ({type_id})"
            for type_id, label, color in entries
        ))
        self._type_legend.setVisible(self._type_legend_enabled and bool(entries))

    def _draw_issue_markers(self):
        if self._issue_markers is not None:
            self._issue_markers.parent = None
            self._issue_markers = None

        if self._df is None or self._df.empty or not self._issue_markers_data:
            self._canvas.update()
            return

        positions: list[list[float]] = []
        colors: list[np.ndarray] = []
        seen_ids: set[int] = set()
        for issue in self._issue_markers_data:
            for node_id in issue.get("node_ids", []):
                node_id = int(node_id)
                if node_id in seen_ids:
                    continue
                if node_id in self._geometry_node_ids:
                    continue
                pos = self._node_position_by_id.get(node_id)
                if pos is None:
                    continue
                positions.append([float(pos[0]), float(pos[1]), float(pos[2])])
                colors.append(_hex_to_rgba(_ISSUE_HIGHLIGHT_HEX))
                seen_ids.add(node_id)

        if not positions:
            self._canvas.update()
            return

        self._issue_markers = visuals.Markers(parent=self._view.scene)
        self._issue_markers.set_data(
            np.asarray(positions, dtype=np.float32),
            size=13,
            face_color=np.asarray(colors, dtype=np.float32),
            edge_color=Color("#ffffff"),
            edge_width=1.8,
            symbol="disc",
        )
        self._canvas.update()

    def _project_to_canvas(self, xyz: np.ndarray) -> np.ndarray | None:
        if xyz.size == 0:
            return None
        try:
            transform = self._view.scene.node_transform(self._canvas.scene)
            mapped = np.asarray(transform.map(xyz.astype(np.float32)))
        except Exception:
            return None
        if mapped.ndim == 1:
            mapped = mapped.reshape(1, -1)
        if mapped.shape[1] >= 4:
            w = mapped[:, 3]
            valid = np.abs(w) > 1e-6
            if np.any(valid):
                mapped[valid, 0] = mapped[valid, 0] / w[valid]
                mapped[valid, 1] = mapped[valid, 1] / w[valid]
        if mapped.shape[1] < 2:
            return None
        return mapped[:, :2]

    def _point_to_segment_distance(self, point: np.ndarray, a: np.ndarray, b: np.ndarray) -> float:
        ab = b - a
        denom = float(np.dot(ab, ab))
        if denom <= 1e-9:
            return float(np.linalg.norm(point - a))
        t = float(np.dot(point - a, ab) / denom)
        t = max(0.0, min(1.0, t))
        proj = a + t * ab
        return float(np.linalg.norm(point - proj))

    def _nearest_node_id(self, canvas_pos) -> int | None:
        if self._df is None or self._df.empty:
            return None
        full_df = self._df
        df = full_df
        if self._selectable_node_ids:
            df = full_df[full_df["id"].isin(sorted(self._selectable_node_ids))]
        if df.empty:
            return None
        xyz = df[["x", "y", "z"]].to_numpy(dtype=np.float32)
        projected = self._project_to_canvas(xyz)
        if projected is None or len(projected) != len(df):
            return None
        target = np.asarray(canvas_pos[:2], dtype=np.float32)
        node_pick_threshold = 30.0 if len(df) <= 4 else 22.0
        segment_pick_threshold = 18.0 if len(df) <= 4 else 12.0
        deltas = projected - target
        dist2 = np.einsum("ij,ij->i", deltas, deltas)
        if dist2.size == 0:
            return None
        idx = int(np.argmin(dist2))
        if float(dist2[idx]) <= node_pick_threshold ** 2:
            return int(df.iloc[idx]["id"])

        full_xyz = full_df[["x", "y", "z"]].to_numpy(dtype=np.float32)
        full_projected = self._project_to_canvas(full_xyz)
        if full_projected is None or len(full_projected) != len(full_df):
            return None
        id_to_proj = {
            int(full_df.iloc[i]["id"]): full_projected[i]
            for i in range(len(full_df))
        }
        best_child_id = None
        best_dist = float("inf")
        for _, row in df.iterrows():
            child_id = int(row["id"])
            parent_id = int(row["parent"])
            if parent_id < 0:
                continue
            child_pos = id_to_proj.get(child_id)
            parent_pos = id_to_proj.get(parent_id)
            if child_pos is None or parent_pos is None:
                continue
            dist = self._point_to_segment_distance(target, parent_pos.astype(np.float32), child_pos.astype(np.float32))
            if dist < best_dist:
                best_dist = dist
                best_child_id = child_id
        if best_child_id is not None and best_dist <= segment_pick_threshold:
            return int(best_child_id)
        return None

    def _on_mouse_press(self, event):
        button = getattr(event, "button", None)
        if button not in (1, "left", None):
            return
        node_id = self._nearest_node_id(getattr(event, "pos", (0, 0)))
        if node_id is None:
            return
        self.zoom_to_node(node_id)
        self.node_clicked.emit(int(node_id))
        try:
            event.handled = True
        except Exception:
            pass


    # ------------------------------------------------- Line mode
    def _draw_lines(self, ids, types, parents, radii, xyz, id2idx):
        type_edges: dict[int, list] = {}
        selected_edges: list[tuple[np.ndarray, np.ndarray]] = []
        soma_positions, soma_radii = [], []
        selected_ids = set(self._geometry_node_ids)
        dim_mode = bool(selected_ids) and self._geometry_visibility_mode == "dim"
        hide_mode = bool(selected_ids) and self._geometry_visibility_mode == "hide"
        dim_edges: list[tuple[np.ndarray, np.ndarray]] = []
        highlight_positions: list[np.ndarray] = []

        for i in range(len(ids)):
            t = int(types[i])
            pid = int(parents[i])
            if t == 1:
                if not hide_mode or int(ids[i]) in selected_ids:
                    soma_positions.append(xyz[i])
                    soma_radii.append(max(float(radii[i]), 1.0))
            if int(ids[i]) in selected_ids:
                highlight_positions.append(xyz[i])
            if pid >= 0 and pid in id2idx:
                p_idx = id2idx[pid]
                child_id = int(ids[i])
                parent_id = int(ids[p_idx])
                edge_selected = child_id in selected_ids and parent_id in selected_ids
                if hide_mode and not edge_selected:
                    continue
                if edge_selected:
                    selected_edges.append((xyz[p_idx], xyz[i]))
                    continue
                if dim_mode:
                    dim_edges.append((xyz[p_idx], xyz[i]))
                    continue
                type_edges.setdefault(t, []).append((xyz[p_idx], xyz[i]))

        if dim_edges:
            n = len(dim_edges)
            pos = np.empty((n * 2, 3), dtype=np.float32)
            connect = np.empty((n, 2), dtype=np.uint32)
            for j, (p, c) in enumerate(dim_edges):
                pos[j * 2] = p
                pos[j * 2 + 1] = c
                connect[j] = [j * 2, j * 2 + 1]
            visuals.Line(
                pos=pos,
                connect=connect,
                color=Color("#c9ced6"),
                width=0.9,
                parent=self._view.scene,
                antialias=True,
            )

        for type_id, edges in type_edges.items():
            n = len(edges)
            pos = np.empty((n * 2, 3), dtype=np.float32)
            connect = np.empty((n, 2), dtype=np.uint32)
            for j, (p, c) in enumerate(edges):
                pos[j * 2] = p; pos[j * 2 + 1] = c
                connect[j] = [j * 2, j * 2 + 1]
            visuals.Line(pos=pos, connect=connect,
                         color=Color(color_for_type(int(type_id))),
                         width=2.2 if selected_ids else 1.5, parent=self._view.scene, antialias=True)

        if selected_edges:
            n = len(selected_edges)
            pos = np.empty((n * 2, 3), dtype=np.float32)
            connect = np.empty((n, 2), dtype=np.uint32)
            for j, (p, c) in enumerate(selected_edges):
                pos[j * 2] = p
                pos[j * 2 + 1] = c
                connect[j] = [j * 2, j * 2 + 1]
            visuals.Line(
                pos=pos,
                connect=connect,
                color=Color(_GEOMETRY_HIGHLIGHT_HEX),
                width=3.0,
                parent=self._view.scene,
                antialias=True,
            )

        if soma_positions:
            markers = visuals.Markers(parent=self._view.scene)
            markers.set_data(
                np.array(soma_positions, dtype=np.float32),
                size=np.array(soma_radii, dtype=np.float32) * 2.5,
                face_color=Color("#2ca02c"), edge_color=Color("#333"),
                edge_width=1.0, symbol="o")
        if highlight_positions:
            markers = visuals.Markers(parent=self._view.scene)
            markers.set_data(
                np.array(highlight_positions, dtype=np.float32),
                size=11,
                face_color=Color(_GEOMETRY_HIGHLIGHT_HEX),
                edge_color=Color("#ffffff"),
                edge_width=1.5,
                symbol="disc",
            )

    # ------------------------------------------------- Sphere mode
    def _draw_spheres(self, ids, types, parents, radii, xyz, id2idx):
        type_groups: dict[int, tuple] = {}
        all_edges_pos, all_edges_connect = [], []
        idx_offset = 0
        selected_edges_pos, selected_edges_connect = [], []
        selected_idx_offset = 0
        selected_positions: list[np.ndarray] = []
        selected_ids = set(self._geometry_node_ids)
        dim_mode = bool(selected_ids) and self._geometry_visibility_mode == "dim"
        hide_mode = bool(selected_ids) and self._geometry_visibility_mode == "hide"
        dim_edges_pos, dim_edges_connect = [], []
        dim_idx_offset = 0

        for i in range(len(ids)):
            node_id = int(ids[i])
            t = int(types[i])
            r = max(float(radii[i]), 0.3)
            if node_id in selected_ids:
                selected_positions.append(xyz[i])
            if not hide_mode or node_id in selected_ids:
                type_groups.setdefault(t, ([], []))[0].append(xyz[i])
                type_groups[t][1].append(6.0 if selected_ids else r * 2.0)

            pid = int(parents[i])
            if pid >= 0 and pid in id2idx:
                parent_id = int(ids[id2idx[pid]])
                edge_selected = node_id in selected_ids and parent_id in selected_ids
                if hide_mode and not edge_selected:
                    continue
                if edge_selected:
                    selected_edges_pos.extend([xyz[id2idx[pid]], xyz[i]])
                    selected_edges_connect.append([selected_idx_offset, selected_idx_offset + 1])
                    selected_idx_offset += 2
                elif dim_mode:
                    dim_edges_pos.extend([xyz[id2idx[pid]], xyz[i]])
                    dim_edges_connect.append([dim_idx_offset, dim_idx_offset + 1])
                    dim_idx_offset += 2
                else:
                    all_edges_pos.extend([xyz[id2idx[pid]], xyz[i]])
                    all_edges_connect.append([idx_offset, idx_offset + 1])
                    idx_offset += 2

        if dim_edges_pos:
            visuals.Line(pos=np.array(dim_edges_pos, dtype=np.float32),
                         connect=np.array(dim_edges_connect, dtype=np.uint32),
                         color=Color("#c9ced6"), width=0.5,
                         parent=self._view.scene, antialias=True)
        if all_edges_pos:
            visuals.Line(pos=np.array(all_edges_pos, dtype=np.float32),
                         connect=np.array(all_edges_connect, dtype=np.uint32),
                         color=Color("#cccccc"), width=0.5,
                         parent=self._view.scene, antialias=True)
        if selected_edges_pos:
            visuals.Line(pos=np.array(selected_edges_pos, dtype=np.float32),
                         connect=np.array(selected_edges_connect, dtype=np.uint32),
                         color=Color(_GEOMETRY_HIGHLIGHT_HEX), width=2.3,
                         parent=self._view.scene, antialias=True)

        for type_id, (positions, rad_list) in type_groups.items():
            markers = visuals.Markers(parent=self._view.scene, scaling="scene")
            markers.set_data(
                np.array(positions, dtype=np.float32),
                size=np.array(rad_list, dtype=np.float32),
                face_color=Color(color_for_type(int(type_id))),
                edge_color=Color(color_for_type(int(type_id))),
                edge_width=0.0, symbol="disc")
        if selected_positions:
            markers = visuals.Markers(parent=self._view.scene, scaling="scene")
            markers.set_data(
                np.array(selected_positions, dtype=np.float32),
                size=np.full((len(selected_positions),), 9.0, dtype=np.float32),
                face_color=Color(_GEOMETRY_HIGHLIGHT_HEX),
                edge_color=Color("#ffffff"),
                edge_width=1.0,
                symbol="disc",
            )

    # ------------------------------------------------- Frustum mode
    def _draw_frustum(self, ids, types, parents, radii, xyz, id2idx):
        """Render each segment as a truncated cone (frustum) from parent to child."""
        all_verts = []
        all_faces = []
        all_colors = []
        vert_offset = 0
        selected_ids = set(self._geometry_node_ids)
        hide_mode = bool(selected_ids) and self._geometry_visibility_mode == "hide"

        for i in range(len(ids)):
            pid = int(parents[i])
            if pid < 0 or pid not in id2idx:
                continue
            if hide_mode:
                child_id = int(ids[i])
                parent_id = int(ids[id2idx[pid]])
                if child_id not in selected_ids or parent_id not in selected_ids:
                    continue

            p_idx = id2idx[pid]
            t = int(types[i])
            child_id = int(ids[i])
            parent_id = int(ids[p_idx])
            edge_selected = child_id in selected_ids and parent_id in selected_ids
            color_rgba = _hex_to_rgba(_GEOMETRY_HIGHLIGHT_HEX if edge_selected else color_for_type(t))

            p0 = xyz[p_idx].astype(np.float64)
            p1 = xyz[i].astype(np.float64)
            r0 = max(float(radii[p_idx]), 0.1)
            r1 = max(float(radii[i]), 0.1)

            v, f, c = _frustum_mesh(p0, r0, p1, r1, color_rgba)
            if v is None:
                continue

            all_verts.append(v)
            all_faces.append(f + vert_offset)
            all_colors.append(c)
            vert_offset += len(v)

        if not all_verts:
            return

        verts = np.concatenate(all_verts, axis=0)
        faces = np.concatenate(all_faces, axis=0)
        colors = np.concatenate(all_colors, axis=0)

        mesh = visuals.Mesh(
            vertices=verts, faces=faces, vertex_colors=colors,
            parent=self._view.scene,
            shading="smooth",
        )
