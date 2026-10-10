"""Floating guide widget describing the auto-typing engine."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QScrollArea, QVBoxLayout, QWidget

_TITLE = "Auto-Label Engine"

_BODY = (
    "The SWC auto-labeler labels every node of an SWC as soma, axon,\n"
    "basal dendrite or apical dendrite from geometry alone. Existing\n"
    "labels are not needed.\n"
    "\n"
    "Stage 0 - Structural checks\n"
    "  A file must hold one connected cell with a soma and finite\n"
    "  coordinates and radii. A file without a soma label takes its\n"
    "  root as the soma. Files that fail are not labeled.\n"
    "\n"
    "Stage 1 - Cell class\n"
    "  3-class cells get axon, basal and apical labels; 2-class cells\n"
    "  get axon and dendrite. On Auto, Stage 1 decides from whole-cell\n"
    "  shape. If you choose a class and Stage 1 disagrees, your class\n"
    "  is used and the cell is flagged as a class mismatch.\n"
    "\n"
    "Stage 2 - Subtree classifier\n"
    "  Each primary subtree leaving the soma is labeled as a whole by a\n"
    "  gradient-boosted classifier, so labels stay consistent along each\n"
    "  track from the soma.\n"
    "\n"
    "Stage 3 - Topology rules and branch correction\n"
    "  Rules enforce the expected anatomy, such as a single axon tree\n"
    "  and, for 3-class cells, a single apical tree. For 3-class cells\n"
    "  a learned correction then re-decides single branches near the\n"
    "  soma (within 150 µm of path length).\n"
    "\n"
    "Stage 4 - Quality flag\n"
    "  One flag per cell class predicts the cell's per-cell F1, how well\n"
    "  it was labeled. A single file is flagged below the flag's cut-off;\n"
    "  a batch flags the riskiest share of each class (10% by default)\n"
    "  and lists every cell in the Flag Review window, riskiest first.\n"
    "\n"
    "Model files\n"
    "  Shipped with SWC-Studio. Use the Model folder picker for models\n"
    "  you trained with `swcstudio train auto-typing`; leave it blank\n"
    "  for the shipped ones.\n"
)


class AutoTypingGuideWidget(QWidget):
    """Static guide panel shown as a floating dock for Auto Label Editing."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self._title = QLabel(_TITLE)
        self._title.setStyleSheet("font-size: 14px; font-weight: 700; color: #222;")
        layout.addWidget(self._title)

        self._body = QLabel(_BODY)
        self._body.setWordWrap(True)
        self._body.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self._body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._body.setStyleSheet(
            "QLabel {"
            "  font-size: 12px;"
            "  color: #444;"
            "  background: #f6f6f6;"
            "  border: 1px solid #ddd;"
            "  padding: 8px;"
            "}"
        )

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(self._body)
        layout.addWidget(scroll, stretch=1)

    def refresh(self):
        """No-op: the guide is static now that there are no tunable rule weights."""
        return None
