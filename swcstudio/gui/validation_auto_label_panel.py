"""Validation control panel: single-file auto label editing applied in-place.

The panel runs the SWC auto-labeler on the current canvas. The user chooses the
cell class (automatic, 3-class or 2-class), whether the quality flag runs, and
optionally a model folder of custom-trained models.
"""

from __future__ import annotations

import json

from PySide6.QtCore import Qt, Signal
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
    QVBoxLayout,
    QWidget,
)

from swcstudio.core.auto_typing import (
    BatchOptions,
    cell_class_name,
    get_config as get_auto_typing_config,
    is_available,
    save_config as save_auto_typing_config,
)
from swcstudio.core.config import feature_config_path
from swcstudio.core.model_paths import labeler_info

_CFG_PATH = feature_config_path("batch_processing", "auto_typing")

# (shown text, value passed to the engine)
CELL_CLASS_CHOICES = (
    ("Auto (Stage 1 decides)", "auto"),
    ("3-class (axon, basal, apical)", "pyramidal"),
    ("2-class (axon, dendrite)", "interneuron"),
)

FLAG_REASONS = {
    "below_cutoff": "its predicted per-cell F1 is below the flag's cut-off",
    "riskiest_share": "it is among the riskiest cells of its class in this batch",
    "class_mismatch": "the chosen cell class differs from Stage 1's prediction",
    "no_flag_model": "this model folder has no quality flag",
    "no_flag_for_class": "this model folder has no quality flag for this cell class",
}


def add_cell_class_items(combo: QComboBox) -> None:
    """Fill ``combo`` with the cell-class choices."""
    for text, value in CELL_CLASS_CHOICES:
        combo.addItem(text, value)
    combo.setToolTip(
        "Auto lets Stage 1 decide. A chosen class that Stage 1 disputes is still used,\n"
        "and the cell is flagged as a class mismatch for review.")


def select_cell_class(combo: QComboBox, value: str | None) -> None:
    """Select the combo entry for a stored cell class (old names included)."""
    v = str(value or "auto").strip().lower()
    v = {"unknown": "auto", "3-class": "pyramidal", "2-class": "interneuron"}.get(v, v)
    idx = combo.findData(v)
    combo.setCurrentIndex(idx if idx >= 0 else 0)


def flag_summary_lines(flag_result: dict, *, class_mismatch: bool = False,
                       stage1_cell_type: str | None = None) -> list[str]:
    """Human-readable lines for one file's quality-flag result."""
    if not flag_result:
        return ["Quality flag: off"]
    lines = []
    f1 = flag_result.get("predicted_f1")
    if f1 is not None:
        lines.append(f"Predicted per-cell F1: {float(f1):.3f}")
    flagged = bool(flag_result.get("flagged", False))
    reason = FLAG_REASONS.get(str(flag_result.get("reason") or ""), "")
    if flagged:
        lines.append("Quality flag: FLAGGED for review" + (f" ({reason})" if reason else ""))
    elif flag_result.get("enabled", True):
        lines.append("Quality flag: ok")
    else:
        lines.append("Quality flag: not scored" + (f" ({reason})" if reason else ""))
    if class_mismatch and stage1_cell_type:
        lines.append(f"Stage 1 predicts {cell_class_name(stage1_cell_type)}; "
                     "check the cell class before accepting these labels.")
    return lines


class _AutoTypingConfigDialog(QDialog):
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


class ValidationAutoLabelPanel(QWidget):
    # Emit (BatchOptions, settings_dict). The dict carries
    # ``{"model_dir": str|None, ...}`` so the main window can pass it as
    # ``config_overrides`` to the auto-label entry point.
    process_requested = Signal(object, object)
    guide_requested = Signal()
    log_message = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._config_dialog: _AutoTypingConfigDialog | None = None
        self._build_ui()
        self.set_preview_state(False, None)
        self._refresh_backend_status()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)
        root.setAlignment(Qt.AlignTop)

        try:
            version = f" {labeler_info()['version']}"
        except Exception:  # noqa: BLE001
            version = ""
        desc = QLabel(
            f"Auto-label every node of the current SWC with the SWC auto-labeler{version}: "
            "structural checks, the cell class (3-class or 2-class), subtree labeling, "
            "topology rules with a correction of the branches near the soma, and a "
            "quality flag that predicts how well the cell was labeled."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("font-size: 12px; color: #555;")
        root.addWidget(desc)

        # ---- model dir picker (optional override)
        self._model_row = QHBoxLayout()
        self._model_row.setSpacing(6)
        model_lbl = QLabel("Model folder (optional):")
        model_lbl.setStyleSheet("font-size: 12px; color: #333;")
        self._model_row.addWidget(model_lbl)
        self._edit_model_dir = QLineEdit()
        self._edit_model_dir.setPlaceholderText("Leave blank for the shipped models")
        self._edit_model_dir.setToolTip("A folder written by `swcstudio train auto-typing`.\n"
                                        "Leave blank to use the shipped or updated models.")
        self._edit_model_dir.setFixedWidth(260)
        self._edit_model_dir.editingFinished.connect(self._refresh_backend_status)
        self._model_row.addWidget(self._edit_model_dir)
        self._btn_browse_model = QPushButton("Browse...")
        self._btn_browse_model.setFixedWidth(96)
        self._btn_browse_model.clicked.connect(self._on_browse_model_dir)
        self._model_row.addWidget(self._btn_browse_model)
        self._backend_status_lbl = QLabel("")
        self._backend_status_lbl.setStyleSheet("font-size: 11px; color: #888;")
        self._model_row.addWidget(self._backend_status_lbl)
        self._model_row.addStretch()
        root.addLayout(self._model_row)

        option_row = QHBoxLayout()
        option_row.setSpacing(8)
        cell_lbl = QLabel("Cell class:")
        cell_lbl.setStyleSheet("font-size: 12px; color: #333;")
        option_row.addWidget(cell_lbl)
        self._cell_type_combo = QComboBox()
        add_cell_class_items(self._cell_type_combo)
        try:
            select_cell_class(self._cell_type_combo, get_auto_typing_config().get("cell_type"))
        except Exception:  # noqa: BLE001
            pass
        option_row.addWidget(self._cell_type_combo)
        self._flag_enabled = QCheckBox("Quality flag")
        self._flag_enabled.setChecked(True)
        self._flag_enabled.setToolTip("Predict the cell's per-cell F1 and flag it for review\n"
                                      "when that falls below the flag's cut-off.")
        option_row.addWidget(self._flag_enabled)
        option_row.addStretch()
        root.addLayout(option_row)

        top_btns = QHBoxLayout()
        self._btn_run = QPushButton("Run")
        self._btn_run.clicked.connect(
            lambda: self.process_requested.emit(
                self.current_options(), self.current_settings()
            )
        )
        top_btns.addWidget(self._btn_run)
        self._btn_edit_cfg = QPushButton("Show JSON")
        self._btn_edit_cfg.clicked.connect(self._on_edit_auto_typing_json)
        top_btns.addWidget(self._btn_edit_cfg)
        top_btns.addStretch()
        root.addLayout(top_btns)

        # Busy indicator: indeterminate progress bar (range 0-0) shown
        # only while a worker is running.
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        self._progress.setTextVisible(False)
        self._progress.setMaximumHeight(8)
        root.addWidget(self._progress)

        self._summary = QPlainTextEdit()
        self._summary.setReadOnly(True)
        self._summary.setMinimumHeight(160)
        self._summary.setStyleSheet(
            "QPlainTextEdit {"
            "  background: #fafafa; border: 1px solid #ddd; color: #333;"
            "  font-family: Menlo, Consolas, monospace; font-size: 12px;"
            "}"
        )
        root.addWidget(self._summary, stretch=1)

    def current_options(self) -> BatchOptions:
        return BatchOptions(
            soma=True,
            axon=True,
            apic=True,
            basal=True,
            rad=False,
            zip_output=False,
            cell_type=self._cell_type_combo.currentData() or "auto",
            flag_enabled=self._flag_enabled.isChecked(),
        )

    def current_settings(self) -> dict:
        """Return ``{"model_dir": str|None, "cell_type": str, "flag_enabled": bool}``."""
        model_dir = (self._edit_model_dir.text() or "").strip() or None
        return {
            "model_dir": model_dir,
            "cell_type": self._cell_type_combo.currentData() or "auto",
            "flag_enabled": self._flag_enabled.isChecked(),
        }

    def _on_browse_model_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select a labeler model folder")
        if path:
            self._edit_model_dir.setText(path)
            self._refresh_backend_status()

    def _refresh_backend_status(self) -> None:
        md = (self._edit_model_dir.text() or "").strip() or None
        # A status probe, run while the panel is built: never download here.
        ok, reason = is_available(model_dir=md, auto_download=False)
        if ok:
            self._backend_status_lbl.setText("ready")
            self._backend_status_lbl.setStyleSheet("font-size: 11px; color: #2a7;")
            self._backend_status_lbl.setToolTip("The labeler's model folder was found.")
        else:
            self._backend_status_lbl.setText("unavailable — see details")
            self._backend_status_lbl.setStyleSheet("font-size: 11px; color: #c33;")
            self._backend_status_lbl.setToolTip(str(reason))

    def _on_edit_auto_typing_json(self):
        if self._config_dialog is None:
            self._config_dialog = _AutoTypingConfigDialog(self)
            self._config_dialog.saved.connect(self.log_message.emit)
        self._config_dialog.reload_from_source()
        self._config_dialog.show()
        self._config_dialog.raise_()
        self._config_dialog.activateWindow()

    def set_preview_state(self, has_preview: bool, summary: dict | None):
        _ = has_preview

        if not summary:
            self._summary.setPlainText(
                "No auto-label result yet.\n"
                "Use Run to apply auto-label editing to the current canvas."
            )
            return

        out_counts = dict(summary.get("out_type_counts", {}))
        flag_result = dict(summary.get("flag_result", {}) or {})
        source = "chosen" if summary.get("cell_type_source") == "user" else "Stage 1"
        lines = [
            "Auto Label Editing Result",
            "-------------------------",
            f"Nodes processed: {summary.get('nodes_total', 0)}",
            f"Type changes: {summary.get('type_changes', 0)}",
            f"Radius changes: {summary.get('radius_changes', 0)}",
            f"Cell class: {cell_class_name(summary.get('cell_type'))} ({source})",
            (
                "Output types (soma/axon/basal/apic): "
                f"{out_counts.get(1, 0)}/{out_counts.get(2, 0)}/{out_counts.get(3, 0)}/{out_counts.get(4, 0)}"
            ),
        ]
        lines.extend(flag_summary_lines(flag_result,
                                        class_mismatch=bool(summary.get("class_mismatch")),
                                        stage1_cell_type=summary.get("stage1_cell_type")))
        for note in summary.get("notes") or []:
            lines.append(f"Note: {note}")
        log_path = str(summary.get("log_path", "") or "").strip()
        if log_path:
            lines.extend(["", f"Log file: {log_path}"])
        self._summary.setPlainText("\n".join(lines))

    def set_status_text(self, text: str):
        self._summary.setPlainText(str(text or ""))

    def set_running(self, running: bool, status_text: str | None = None) -> None:
        """Toggle the running state — disables interactive controls and
        shows a busy progress bar while a worker is in flight."""
        self._progress.setVisible(bool(running))
        self._btn_run.setEnabled(not running)
        self._btn_edit_cfg.setEnabled(not running)
        self._edit_model_dir.setEnabled(not running)
        self._btn_browse_model.setEnabled(not running)
        self._cell_type_combo.setEnabled(not running)
        self._flag_enabled.setEnabled(not running)
        if running and status_text:
            self._summary.setPlainText(str(status_text))
