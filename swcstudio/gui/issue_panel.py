"""Issue list panel for the issue-driven SWC repair workflow."""

from __future__ import annotations

from collections import Counter
from typing import Any

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QBrush, QPalette, QPen
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTreeWidgetItemIterator,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)


_SEVERITY_META = {
    "critical": ("Critical", "#d14343"),
    "warning": ("Warning", "#d98a00"),
    "info": ("Info", "#4460d8"),
    "muted": ("Muted", "#6b7280"),
}
_BLOCKING_ROLE = Qt.UserRole + 10


class _BlockingIssueDelegate(QStyledItemDelegate):
    """Paint blocking soma issues as full red cells without losing text."""

    def paint(self, painter, option: QStyleOptionViewItem, index):
        if not bool(index.data(_BLOCKING_ROLE)):
            super().paint(painter, option, index)
            return

        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        painter.save()
        rect = opt.rect.adjusted(1, 1, -1, -1)
        painter.fillRect(rect, QColor("#f4c7c3"))
        if opt.state & QStyle.State_Selected:
            painter.setPen(QPen(QColor("#b42318"), 2.0))
        else:
            painter.setPen(QPen(QColor("#d92d20"), 1.0))
        painter.drawRect(rect)
        painter.setPen(QColor("#7a1712"))
        painter.setFont(opt.font)
        text_rect = rect.adjusted(10, 0, -8, 0)
        painter.drawText(
            text_rect,
            Qt.AlignLeft | Qt.AlignVCenter | Qt.TextWordWrap,
            str(opt.text or index.data(Qt.DisplayRole) or ""),
        )
        painter.restore()


class IssuePanelWidget(QWidget):
    """Searchable grouped issue list with selection callbacks."""

    issue_selected = Signal(dict)
    rule_guide_requested = Signal()
    export_swc_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._issues: list[dict[str, Any]] = []
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        title = QLabel("Issues")
        title.setStyleSheet("font-size: 16px; font-weight: 700; color: #132238;")
        root.addWidget(title)

        self._summary = QLabel("Open an SWC to populate issues automatically.")
        self._summary.setWordWrap(True)
        self._summary.setStyleSheet("font-size: 12px; color: #5f6b7a;")
        root.addWidget(self._summary)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Filter by issue text, node id, or category")
        self._search.textChanged.connect(self._rebuild_tree)
        root.addWidget(self._search)

        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Issue"])
        self._tree.setHeaderHidden(True)
        self._tree.setAlternatingRowColors(False)
        self._tree.setUniformRowHeights(False)
        self._tree.setRootIsDecorated(True)
        self._tree.setItemsExpandable(True)
        self._tree.itemSelectionChanged.connect(self._emit_current_issue)
        self._tree.setItemDelegate(_BlockingIssueDelegate(self._tree))
        self._tree.setStyleSheet(
            "QTreeWidget { background: transparent; border: 1px solid #d8e0eb; border-radius: 12px; padding: 6px; }"
            "QTreeWidget::item { padding: 8px 4px; background: transparent; }"
            "QTreeWidget::item:selected { background: transparent; }"
        )
        self._tree.viewport().setStyleSheet("background: transparent;")
        palette = self._tree.palette()
        palette.setColor(QPalette.Highlight, QColor(0, 0, 0, 0))
        palette.setColor(QPalette.HighlightedText, palette.color(QPalette.Text))
        self._tree.setPalette(palette)
        root.addWidget(self._tree, stretch=1)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(8)
        self._btn_rule_guide = QPushButton("Rule Guide")
        self._btn_rule_guide.clicked.connect(self.rule_guide_requested.emit)
        footer.addWidget(self._btn_rule_guide)
        self._btn_export_swc = QPushButton("Export SWC")
        self._btn_export_swc.clicked.connect(self.export_swc_requested.emit)
        footer.addWidget(self._btn_export_swc)
        root.addLayout(footer)

    def set_issues(self, issues: list[dict[str, Any]]):
        self._issues = [dict(item) for item in issues]
        self._rebuild_tree()

    def clear_issues(self, message: str = "No issues loaded."):
        self._issues = []
        self._tree.clear()
        self._summary.setText(message)

    def _issue_matches(self, issue: dict[str, Any], query: str) -> bool:
        if not query:
            return True
        hay = " ".join(
            [
                str(issue.get("title", "")),
                str(issue.get("description", "")),
                str(issue.get("source_category", "")),
                " ".join(str(v) for v in issue.get("node_ids", [])),
            ]
        ).lower()
        return query in hay

    def _rebuild_tree(self):
        query = self._search.text().strip().lower()
        filtered = [item for item in self._issues if self._issue_matches(item, query)]
        counts = Counter(str(item.get("severity", "info")) for item in filtered)
        muted = sum(1 for item in filtered if str(item.get("status", "")).strip().lower() in {"muted", "skipped"})
        total = len(filtered)
        self._summary.setText(
            f"{total} visible issue(s) · "
            f"{counts.get('critical', 0)} critical · "
            f"{counts.get('warning', 0)} warning · "
            f"{counts.get('info', 0)} info · "
            f"{muted} muted"
        )

        self._tree.clear()
        groups: dict[str, QTreeWidgetItem] = {}
        for key in ("critical", "warning", "info", "muted"):
            label, color = _SEVERITY_META[key]
            top = QTreeWidgetItem([f"{label}", ""])
            top.setFirstColumnSpanned(True)
            top.setExpanded(True)
            top.setForeground(0, Qt.GlobalColor.black)
            top.setData(0, Qt.UserRole, None)
            if key == "muted":
                top.setText(0, f"{label} ({muted})")
            else:
                top.setText(0, f"{label} ({counts.get(key, 0)})")
            top.setBackground(0, Qt.transparent)
            top.setForeground(0, self.palette().text())
            groups[key] = top
            self._tree.addTopLevelItem(top)

        for issue in filtered:
            severity = str(issue.get("severity", "info"))
            status = str(issue.get("status", "")).strip().lower()
            item = QTreeWidgetItem([str(issue.get("title", "Issue"))])
            item.setData(0, Qt.UserRole, dict(issue))
            item.setToolTip(0, "")
            item.setForeground(0, self.palette().text())
            source_key = str(issue.get("source_key", "")).strip()
            title = str(issue.get("title", "Issue")).strip() or "Issue"
            if source_key in {"multiple_somas", "has_soma"}:
                item.setText(0, title)
                item.setSizeHint(0, QSize(0, 60))
                item.setData(0, _BLOCKING_ROLE, True)
            else:
                item.setText(0, title)
                item.setSizeHint(0, QSize(0, 36))
                item.setData(0, _BLOCKING_ROLE, False)
            if status in {"muted", "skipped"}:
                item.setForeground(0, Qt.gray)
                groups["muted"].addChild(item)
            else:
                groups.get(severity, groups["info"]).addChild(item)

        for key, top in groups.items():
            if top.childCount() == 0:
                top.setHidden(True)
            else:
                top.setHidden(False)
        self._tree.expandAll()

    def _emit_current_issue(self):
        items = self._tree.selectedItems()
        if not items:
            return
        issue = items[0].data(0, Qt.UserRole)
        if isinstance(issue, dict):
            self.issue_selected.emit(dict(issue))

    def select_issue(self, issue_id: str) -> bool:
        wanted = str(issue_id or "").strip()
        if not wanted:
            return False
        it = QTreeWidgetItemIterator(self._tree)
        while it.value():
            item = it.value()
            issue = item.data(0, Qt.UserRole)
            if isinstance(issue, dict) and str(issue.get("issue_id", "")).strip() == wanted:
                self._tree.clearSelection()
                item.setSelected(True)
                self._tree.setCurrentItem(item)
                self._tree.scrollToItem(item, QTreeWidget.PositionAtCenter)
                self._tree.setFocus(Qt.OtherFocusReason)
                return True
            it += 1
        return False

    def clear_selection(self):
        self._tree.clearSelection()

    def first_issue_id(self) -> str:
        it = QTreeWidgetItemIterator(self._tree)
        while it.value():
            item = it.value()
            issue = item.data(0, Qt.UserRole)
            if isinstance(issue, dict):
                return str(issue.get("issue_id", "")).strip()
            it += 1
        return ""
