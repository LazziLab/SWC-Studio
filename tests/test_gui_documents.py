"""Working with several open files in the GUI: opening, switching, Flag Review
navigation, and the node-type colour legend.

The GUI checks run the real main window offscreen in a subprocess (as in
test_gui_smoke.py) and leave with os._exit so Qt/VisPy teardown cannot hang
or crash the test run.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Shared start of every GUI script: a temp folder with a private custom-type
# registry, tiny SWC files, the main window, and validation stubbed out (these
# tests are about tabs, not checks) but counted.
_PRELUDE = textwrap.dedent(
    """
    import json
    import os
    import sys
    import tempfile
    from pathlib import Path

    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    TMP = Path(tempfile.mkdtemp(prefix='swcstudio-gui-docs-'))
    os.environ['SWCSTUDIO_CUSTOM_TYPES_PATH'] = str(TMP / 'custom_types.json')

    from PySide6.QtWidgets import QApplication, QMessageBox
    from swcstudio.gui.main_window import SWCMainWindow

    def write_swc(name, types=(1, 3, 4)):
        lines = ['1 %d 0 0 0 1 -1' % types[0]]
        for i, t in enumerate(types[1:], start=2):
            lines.append('%d %d %d 0 0 0.5 %d' % (i, t, i, i - 1))
        path = TMP / name
        path.write_text('\\n'.join(lines) + '\\n', encoding='utf-8')
        return str(path)

    def tab_names(window):
        return [window._canvas_tabs.tabText(i) for i in range(window._canvas_tabs.count())]

    def current_name(window):
        return window._canvas_tabs.tabText(window._canvas_tabs.currentIndex())

    app = QApplication([])
    window = SWCMainWindow()
    (TMP / 'recovery').mkdir()
    window._recovery_directory = lambda: TMP / 'recovery'  # keep edits out of ~/.swc_studio
    validation_runs = []
    window._validation_tab.run_validation = lambda: validation_runs.append(window._filename)
    """
)


def _run_gui_script(test: unittest.TestCase, *bodies: str) -> None:
    script = _PRELUDE + "".join(textwrap.dedent(body) for body in bodies) + textwrap.dedent(
        """
        sys.stdout.write('GUI_DOCS_OK\\n')
        sys.stdout.flush()
        os._exit(0)
        """
    )
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    test.assertTrue(
        result.returncode == 0 and "GUI_DOCS_OK" in result.stdout,
        msg=f"GUI subprocess failed.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
    )


class OpenDocumentsTests(unittest.TestCase):
    def test_same_path_opens_one_tab(self) -> None:
        _run_gui_script(self, """
            a = write_swc('a.swc')
            b = write_swc('b.swc')
            window._load_swc(a)
            window._load_swc(b)
            assert tab_names(window) == ['a.swc', 'b.swc'], tab_names(window)
            # Same file again, also spelled differently: switch to its tab instead.
            window._load_swc(a)
            assert tab_names(window) == ['a.swc', 'b.swc'], tab_names(window)
            assert current_name(window) == 'a.swc'
            window._load_swc(str(TMP / '.' / 'sub' / '..' / 'b.swc'))
            assert tab_names(window) == ['a.swc', 'b.swc'], tab_names(window)
            assert current_name(window) == 'b.swc'
            # Recent Files goes through the same path.
            window._recent_menu.actions()[-1].trigger()
            assert len(window._documents) == 2, len(window._documents)
        """)

    def test_multi_open_opens_each_file_and_shows_the_first(self) -> None:
        _run_gui_script(self, """
            paths = [write_swc(f'cell{i}.swc') for i in range(3)]
            window._open_paths(paths)
            assert tab_names(window) == ['cell0.swc', 'cell1.swc', 'cell2.swc'], tab_names(window)
            assert current_name(window) == 'cell0.swc'
            # Only the file that ends up shown is validated on open.
            assert validation_runs == ['cell0.swc'], validation_runs
            # Reopening a selection that overlaps the open files adds only the new one.
            paths.append(write_swc('cell3.swc'))
            window._open_paths(paths[1:])
            assert tab_names(window) == ['cell0.swc', 'cell1.swc', 'cell2.swc', 'cell3.swc'], tab_names(window)
            assert current_name(window) == 'cell1.swc'
        """)

    def test_opening_many_new_files_asks_first(self) -> None:
        _run_gui_script(self, """
            asked = []
            answer = [QMessageBox.No]
            def fake_question(*args, **kwargs):
                asked.append(args[2] if len(args) > 2 else '')
                return answer[0]
            QMessageBox.question = staticmethod(fake_question)

            paths = [write_swc(f'c{i:02d}.swc') for i in range(11)]
            window._open_paths(paths[:10])
            assert asked == [] and window._canvas_tabs.count() == 10, (asked, window._canvas_tabs.count())
            for doc in list(window._documents.values()):
                window._documents.pop(doc.editor)
                window._canvas_tabs.removeTab(window._canvas_tabs.indexOf(doc.editor))

            window._open_paths(paths)
            assert len(asked) == 1 and '11 files' in asked[0], asked
            assert window._canvas_tabs.count() == 0, window._canvas_tabs.count()
            answer[0] = QMessageBox.Yes
            window._open_paths(paths)
            assert len(asked) == 2 and window._canvas_tabs.count() == 11, (asked, window._canvas_tabs.count())
            # Nothing new to open, so no question.
            window._open_paths(paths)
            assert len(asked) == 2 and window._canvas_tabs.count() == 11
        """)

    def test_next_and_previous_file_cycle_tabs(self) -> None:
        _run_gui_script(self, """
            from PySide6.QtGui import QKeySequence
            window._open_paths([write_swc(f'n{i}.swc') for i in range(3)])
            assert current_name(window) == 'n0.swc'
            nxt, prev = window._next_document_action, window._previous_document_action
            assert QKeySequence(QKeySequence.NextChild) in nxt.shortcuts()
            assert QKeySequence(QKeySequence.PreviousChild) in prev.shortcuts()
            # No other action in the menus may claim these keys.
            claimed = {}
            for menu_action in window._home_menu_bar.actions():
                for action in menu_action.menu().actions():
                    for seq in action.shortcuts():
                        claimed.setdefault(seq.toString(), []).append(action.text())
            for seq in list(nxt.shortcuts()) + list(prev.shortcuts()):
                assert len(claimed[seq.toString()]) == 1, (seq.toString(), claimed[seq.toString()])

            seen = []
            for _ in range(3):
                nxt.trigger()
                seen.append(current_name(window))
            assert seen == ['n1.swc', 'n2.swc', 'n0.swc'], seen
            prev.trigger()
            assert current_name(window) == 'n2.swc'
            assert window._active_document().filename == 'n2.swc'
        """)


class FlagReviewNavigationTests(unittest.TestCase):
    _REVIEW = """
        def write_review(records, name='x_flag_review_1.json'):
            path = TMP / name
            path.write_text(json.dumps({
                'kind': 'swcstudio_flag_review', 'folder': str(TMP), 'timestamp': '1',
                'flag_budget': 0.10, 'records': records,
            }), encoding='utf-8')
            return str(path)

        def rec(name, f1, *, flagged=False, warnings=None):
            out = {'file': name, 'input_path': write_swc(name), 'labeled': True,
                   'cell_type': 'pyramidal', 'predicted_f1': f1, 'flagged': flagged,
                   'class_mismatch': False, 'review_status': 'unreviewed'}
            if warnings is not None:
                out['warnings'] = warnings
            return out

        panel = window._flag_review_panel
        table = panel._table
        def table_files():
            return [table.item(r, 1).text() for r in range(table.rowCount())]
    """

    def test_open_cell_opens_every_selected_row_and_next_steps_down(self) -> None:
        _run_gui_script(self, self._REVIEW, """
            from PySide6.QtCore import QItemSelectionModel
            from swcstudio.gui.flag_review_window import COLUMNS
            # Stored out of order; the table ranks them riskiest first.
            review = write_review([rec('c.swc', 0.90), rec('a.swc', 0.40, flagged=True),
                                   rec('b.swc', 0.60, warnings=['no axon labeled'])])
            window._activate_feature('batch')
            window._open_flag_review(review)
            assert table_files() == ['a.swc', 'b.swc', 'c.swc'], table_files()

            # Next with nothing selected opens the riskiest cell, then steps down.
            assert panel._btn_next.isEnabled() and panel._btn_previous.isEnabled()
            panel._btn_next.click()
            assert tab_names(window) == ['a.swc'], tab_names(window)
            # The batch canvas hides document tabs; opening a cell switches to label editing.
            assert window._active_tool == 'morphology_editing', window._active_tool
            assert window._canvas_stack.currentWidget() is window._canvas_tabs
            assert not panel._btn_previous.isEnabled()
            panel._btn_next.click()
            assert tab_names(window) == ['a.swc', 'b.swc'] and current_name(window) == 'b.swc'
            assert [ix.row() for ix in table.selectionModel().selectedRows()] == [1]
            panel._btn_next.click()
            assert current_name(window) == 'c.swc' and not panel._btn_next.isEnabled()
            panel.step(1)  # past the end: nothing happens
            assert current_name(window) == 'c.swc'
            panel._btn_previous.click()
            assert current_name(window) == 'b.swc', current_name(window)
            assert window._canvas_tabs.count() == 3

            # Open Cell opens every selected row, each in its own (de-duplicated) tab.
            write_swc('d.swc')
            records = list(panel._records) + [rec('d.swc', 0.95)]
            panel.load(write_review(records, 'y_flag_review_1.json'))
            table.clearSelection()
            for row in (1, 3):
                table.selectionModel().select(table.model().index(row, 0),
                                              QItemSelectionModel.Select | QItemSelectionModel.Rows)
            panel._btn_open_cell.click()
            assert tab_names(window) == ['a.swc', 'b.swc', 'c.swc', 'd.swc'], tab_names(window)
            assert current_name(window) == 'b.swc', current_name(window)

            # The Warnings column shows the record's warnings.
            col = COLUMNS.index('Warnings')
            assert not table.isColumnHidden(col)
            assert [table.item(r, col).text() for r in range(4)] == ['', 'no axon labeled', '', '']
        """)

    def test_review_without_warnings_hides_the_column(self) -> None:
        _run_gui_script(self, self._REVIEW, """
            from swcstudio.gui.flag_review_window import COLUMNS, warnings_text
            review = write_review([rec('a.swc', 0.4), rec('b.swc', 0.6)])
            assert panel.load(review)
            assert table.rowCount() == 2
            assert table.isColumnHidden(COLUMNS.index('Warnings'))
            # Double-click opens the clicked row.
            panel._on_row_double_clicked(1)
            assert tab_names(window) == ['b.swc'], tab_names(window)

            assert warnings_text({}) == ''
            assert warnings_text({'warnings': None}) == ''
            assert warnings_text({'warnings': []}) == ''
            assert warnings_text({'warnings': ['no axon labeled', 'no soma']}) == 'no axon labeled; no soma'
            assert warnings_text({'warnings': 'no axon labeled'}) == 'no axon labeled'
        """)


class TypeLegendTests(unittest.TestCase):
    def test_legend_entries_order_and_helpers(self) -> None:
        from swcstudio.gui.constants import color_for_type, label_for_type, type_legend_entries

        entries = type_legend_entries([0, 3, 1, 4, 3, 2, 9, -1])
        self.assertEqual([t for t, _label, _color in entries], [1, 2, 3, 4, 9, 0, -1])
        for type_id, label, color in entries:
            self.assertEqual(label, label_for_type(type_id))
            self.assertEqual(color, color_for_type(type_id))
        self.assertEqual(type_legend_entries([]), [])

    def test_legend_lists_the_file_types_and_follows_edits(self) -> None:
        _run_gui_script(self, """
            from swcstudio.core.custom_types import save_custom_type_definitions
            from swcstudio.gui.constants import color_for_type
            save_custom_type_definitions({7: {'name': 'spine', 'color': '#00aaaa'}})

            window._load_swc(write_swc('typed.swc', types=(1, 3, 3, 4, 7, 0)))
            doc = window._active_document()
            editor = doc.editor
            views = (editor._view3d_canvas, editor._view3d_dendro, editor._view3d_visual)
            expected = [(1, 'soma'), (3, 'basal dendrite'), (4, 'apical dendrite'), (7, 'spine'), (0, 'undefined')]
            assert [(t, label) for t, label, _c in editor.type_legend_entries()] == expected, editor.type_legend_entries()
            assert all(color == color_for_type(t) for t, _l, color in editor.type_legend_entries())
            assert dict((t, c) for t, _l, c in editor.type_legend_entries())[7] == '#00aaaa'
            for view in views:
                assert view.type_legend_entries() == editor.type_legend_entries()
                assert not view._type_legend.isHidden()
                assert 'spine (7)' in view._type_legend.text()

            # A whole-document change (the path auto-label results take) updates it.
            df = doc.df.copy()
            df.loc[df['type'] == 3, 'type'] = 2
            window._apply_document_dataframe(doc, df, event_title='Auto Label', event_summary='test')
            assert [t for t, _l, _c in editor.type_legend_entries()] == [1, 2, 4, 7, 0], editor.type_legend_entries()
            # So does an edit made in the dendrogram.
            df = doc.df.copy()
            df.loc[df['type'].isin([0, 7]), 'type'] = 4
            editor._on_df_changed(df)
            assert [t for t, _l, _c in editor.type_legend_entries()] == [1, 2, 4], editor.type_legend_entries()

            # View -> Show Node-Type Legend hides it everywhere, including files opened later.
            assert window._type_legend_action.isChecked()
            window._type_legend_action.setChecked(False)
            assert all(view._type_legend.isHidden() for view in views)
            window._load_swc(write_swc('later.swc'))
            later = window._active_document().editor
            assert later._view3d_canvas._type_legend.isHidden()
            window._type_legend_action.setChecked(True)
            assert not later._view3d_canvas._type_legend.isHidden()
            assert not editor._view3d_visual._type_legend.isHidden()
        """)


if __name__ == "__main__":
    unittest.main()
