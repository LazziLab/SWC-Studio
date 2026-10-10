"""The GUI never silently overwrites or loses data on disk.

Each document remembers the file as SWC-Studio last read or wrote it. When
something else changes the file (a GUI or CLI batch run, another program),
an open tab is reloaded, or, if it has changes of its own, nothing is written
over the file without asking. Also covered: Save As onto a file open in
another tab, number text kept through Index Clean, the auto-label provenance
record, and validation that never blocks or misreports a tab.

Like test_gui_documents.py, each check drives the real main window offscreen
in a subprocess.
"""
from __future__ import annotations

import unittest

from .test_gui_documents import _run_gui_script

_HELPERS = """
import time
from types import SimpleNamespace
from swcstudio.gui.constants import SWC_COLS

def data_lines(path):
    return [line for line in Path(path).read_text(encoding='utf-8').splitlines()
            if line.strip() and not line.lstrip().startswith('#')]

def types_on_disk(path):
    return [int(line.split()[1]) for line in data_lines(path)]

def radii_on_disk(path):
    return [float(line.split()[5]) for line in data_lines(path)]

def wait_until(condition, timeout=30.0):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.02)
    return False

RELABELED = '1 1 0 0 0 1 -1\\n2 4 2 0 0 0.5 1\\n3 4 3 0 0 0.5 2\\n4 4 4 0 0 0.5 3\\n'
"""


class ChangedOnDiskTests(unittest.TestCase):
    def test_reopening_a_relabeled_file_shows_and_keeps_the_new_labels(self) -> None:
        _run_gui_script(self, _HELPERS, """
            from swcstudio.core.provenance import OpKind, tracked_op
            path = write_swc('cell.swc', types=(1, 3, 3, 3))
            window._load_swc(path)
            other = write_swc('other.swc')
            window._load_swc(other)

            # A batch run relabels cell.swc in place (and records it in its history).
            with tracked_op(path, kind=OpKind.SET_TYPE, params={}, message='batch relabel') as op:
                op.set_output(RELABELED.encode('utf-8'))
            assert types_on_disk(path) == [1, 4, 4, 4]

            # The reviewer opens it again from the Flag Review.
            review = TMP / 'r_flag_review_1.json'
            review.write_text(json.dumps({
                'kind': 'swcstudio_flag_review', 'folder': str(TMP), 'timestamp': '1', 'flag_budget': 0.1,
                'records': [{'file': 'cell.swc', 'input_path': path, 'output_path': path, 'labeled': True,
                             'cell_type': 'pyramidal', 'predicted_f1': 0.4, 'flagged': True,
                             'class_mismatch': False, 'review_status': 'unreviewed'}],
            }), encoding='utf-8')
            panel = window._flag_review_panel
            assert panel.load(review)
            panel._btn_next.click()
            assert tab_names(window) == ['cell.swc', 'other.swc'], tab_names(window)
            doc = window._active_document()
            assert doc.filename == 'cell.swc'
            assert doc.df['type'].tolist() == [1, 4, 4, 4], doc.df['type'].tolist()
            assert doc.editor._df['type'].tolist() == [1, 4, 4, 4]

            # ...and fixes one node: the batch's other labels stay on disk.
            df = doc.df.loc[:, SWC_COLS].copy()
            df.loc[df['id'] == 2, 'type'] = 2
            doc.editor._on_df_changed(df)
            assert types_on_disk(path) == [1, 2, 4, 4], types_on_disk(path)

            # File -> Open of an unchanged file just switches to it.
            window._open_paths([other])
            assert current_name(window) == 'other.swc'
        """)

    def test_reopening_asks_when_the_tab_has_its_own_changes(self) -> None:
        _run_gui_script(self, _HELPERS, """
            path = write_swc('cell.swc', types=(1, 3, 3, 3))
            window._load_swc(path)
            doc = window._active_document()
            # An edit (written to the file), then Undo: the tab now differs from the file.
            df = doc.df.loc[:, SWC_COLS].copy()
            df.loc[df['id'] == 3, 'radius'] = 0.9
            doc.editor._on_df_changed(df)
            assert window._undo_document(doc)
            assert window._document_has_unsaved_edits(doc)
            Path(path).write_text(RELABELED, encoding='utf-8')

            asked = []
            answer = ['keep']
            def ask(d, *, action):
                asked.append(action)
                return answer[0]
            window._ask_disk_conflict = ask
            window._load_swc(path)
            assert asked == [None], asked
            assert doc.df['type'].tolist() == [1, 3, 3, 3]
            assert tab_names(window) == ['cell.swc [changed on disk]'], tab_names(window)
            assert Path(path).read_text(encoding='utf-8') == RELABELED

            answer[0] = 'reload'
            window._load_swc(path)
            assert doc.df['type'].tolist() == [1, 4, 4, 4]
            assert tab_names(window) == ['cell.swc']
            assert not window._document_has_unsaved_edits(doc)
        """)

    def test_edits_and_saves_never_overwrite_a_file_changed_on_disk_silently(self) -> None:
        _run_gui_script(self, _HELPERS, """
            path = write_swc('cell.swc', types=(1, 3, 3, 3))
            window._load_swc(path)
            doc = window._active_document()
            asked = []
            answer = ['cancel']
            def ask(d, *, action):
                asked.append(action)
                return answer[0]
            window._ask_disk_conflict = ask
            Path(path).write_text(RELABELED, encoding='utf-8')  # someone else relabels it

            # Cancel: nothing written, the edit is not applied, the tab is marked.
            window._manual_radii_panel.apply_requested.emit(2, 0.9)
            assert len(asked) == 1 and asked[0], asked
            assert Path(path).read_text(encoding='utf-8') == RELABELED
            assert float(doc.df.loc[doc.df['id'] == 2, 'radius'].iloc[0]) == 0.5
            assert tab_names(window) == ['cell.swc [changed on disk]'], tab_names(window)
            # File -> Save asks too.
            window._on_save()
            assert len(asked) == 2 and Path(path).read_text(encoding='utf-8') == RELABELED
            # So does a direct edit in the dendrogram; the editor goes back to the tab's contents.
            df = doc.df.loc[:, SWC_COLS].copy()
            df.loc[df['id'] == 4, 'type'] = 2
            doc.editor._on_df_changed(df)
            assert len(asked) == 3 and Path(path).read_text(encoding='utf-8') == RELABELED
            assert doc.df['type'].tolist() == [1, 3, 3, 3]
            assert doc.editor._df['type'].tolist() == [1, 3, 3, 3]

            # Overwrite: the tab's version, with the edit, replaces the file.
            answer[0] = 'overwrite'
            window._manual_radii_panel.apply_requested.emit(2, 0.9)
            assert types_on_disk(path) == [1, 3, 3, 3]
            assert radii_on_disk(path)[1] == 0.9
            assert tab_names(window) == ['cell.swc']

            # Reload: the tab shows the file; the edit is dropped, nothing is written.
            Path(path).write_text(RELABELED, encoding='utf-8')
            answer[0] = 'reload'
            window._manual_radii_panel.apply_requested.emit(3, 0.7)
            assert Path(path).read_text(encoding='utf-8') == RELABELED
            assert doc.df['type'].tolist() == [1, 4, 4, 4]
            assert float(doc.df.loc[doc.df['id'] == 3, 'radius'].iloc[0]) == 0.5

            # The file is as the tab last saw it: no question, the edit is written.
            window._manual_radii_panel.apply_requested.emit(3, 0.7)
            assert len(asked) == 5, asked
            assert types_on_disk(path) == [1, 4, 4, 4] and radii_on_disk(path)[2] == 0.7

            # Switching back to a tab whose file changed reloads it.
            other = write_swc('other.swc')
            window._load_swc(other)
            Path(path).write_text(RELABELED.replace('2 4 2', '2 2 2'), encoding='utf-8')
            window._canvas_tabs.setCurrentIndex(0)
            assert doc.df['type'].tolist() == [1, 2, 4, 4], doc.df['type'].tolist()
            assert len(asked) == 5
        """)

    def test_batch_runs_reload_open_files_and_keep_tabs_with_their_own_changes(self) -> None:
        _run_gui_script(self, _HELPERS, """
            folder = TMP / 'batch'
            folder.mkdir()
            unordered = '10 1 0 0 0 1 -1\\n5 3 1 0 0 0.5 10\\n7 3 2 0 0 0.5 5\\n'
            a = folder / 'a.swc'
            b = folder / 'b.swc'
            a.write_text(unordered, encoding='utf-8')
            b.write_text(unordered, encoding='utf-8')
            window._open_paths([str(a), str(b)])
            doc_a, doc_b = list(window._documents.values())
            # b gets changes of its own that are not in its file (an edit, then Undo).
            df = doc_b.df.loc[:, SWC_COLS].copy()
            df.loc[df['id'] == 7, 'type'] = 4
            doc_b.editor._on_df_changed(df)
            assert window._undo_document(doc_b)
            assert window._document_has_unsaved_edits(doc_b)

            warned = []
            QMessageBox.warning = staticmethod(lambda *args, **kw: warned.append(args[2]) or QMessageBox.Ok)
            window._batch_tab._show_report_popup = lambda *args, **kw: None
            window._batch_tab._index_clean_input_dir = str(folder)
            window._batch_tab._on_run_batch_index_clean()  # renumbers both files in place
            assert [int(line.split()[0]) for line in data_lines(a)] == [1, 2, 3]

            assert doc_a.df['id'].tolist() == [1, 2, 3], doc_a.df['id'].tolist()
            assert doc_a.editor._df['id'].tolist() == [1, 2, 3]
            assert doc_b.df['id'].tolist() == [10, 5, 7]
            assert tab_names(window) == ['a.swc', 'b.swc [changed on disk]'], tab_names(window)
            assert len(warned) == 1 and 'b.swc' in warned[0] and 'a.swc' not in warned[0], warned

            # An auto-label batch finishing does the same.
            a.write_text(RELABELED, encoding='utf-8')
            window._batch_tab._on_batch_finished(window._batch_tab._batch_run_id, SimpleNamespace(folder=str(folder)))
            assert doc_a.df['type'].tolist() == [1, 4, 4, 4], doc_a.df['type'].tolist()
        """)

    def test_save_as_onto_a_file_open_in_another_tab_is_refused(self) -> None:
        _run_gui_script(self, _HELPERS, """
            from PySide6.QtWidgets import QFileDialog
            a = write_swc('a.swc', types=(1, 3, 3))
            b = write_swc('b.swc', types=(1, 4, 4))
            window._open_paths([a, b])
            window._canvas_tabs.setCurrentIndex(1)
            warned = []
            QMessageBox.warning = staticmethod(lambda *args, **kw: warned.append(args[2]) or QMessageBox.Ok)
            target = [a]
            QFileDialog.getSaveFileName = staticmethod(lambda *args, **kw: (target[0], ''))
            before = Path(a).read_bytes()

            window._on_save_as()
            assert Path(a).read_bytes() == before
            assert tab_names(window) == ['a.swc', 'b.swc'], tab_names(window)
            assert window._active_document().file_path == b
            assert len(warned) == 1 and 'open in another tab' in warned[0], warned
            window._on_export()
            assert Path(a).read_bytes() == before and len(warned) == 2
            # Another spelling of the same file (a hard link) is refused too.
            alias = TMP / 'link_to_a.swc'
            os.link(a, alias)
            target[0] = str(alias)
            window._on_save_as()
            assert Path(a).read_bytes() == before and len(warned) == 3

            # A new name works; the tab then edits the new file.
            target[0] = str(TMP / 'b_copy.swc')
            window._on_save_as()
            assert tab_names(window) == ['a.swc', 'b_copy.swc'], tab_names(window)
            assert types_on_disk(target[0]) == [1, 4, 4]
            window._manual_radii_panel.apply_requested.emit(2, 0.8)
            assert radii_on_disk(target[0])[1] == 0.8 and radii_on_disk(b)[1] == 0.5
        """)


class NumberTextTests(unittest.TestCase):
    _CELL = """
        src = TMP / 'cell.swc'
        src.write_text(
            '# test cell\\n'
            '10 1 0.0000e+00 0 0 1.0000e+00 -1\\n'
            '5 3 1.0000e+01 0 0 5.0000e-01 10\\n'
            '7 3 2.5000e+00 1.0000e+00 0 2.5000e-01 5\\n',
            encoding='utf-8',
        )
        window._load_swc(str(src))
        doc = window._active_document()
    """

    def test_index_clean_keeps_each_nodes_number_text(self) -> None:
        _run_gui_script(self, _HELPERS, self._CELL, """
            window._validation_index_clean._on_apply()
            assert data_lines(src) == [
                '1 1 0.0000e+00 0 0 1.0000e+00 -1',
                '2 3 1.0000e+01 0 0 5.0000e-01 1',
                '3 3 2.5000e+00 1.0000e+00 0 2.5000e-01 2',
            ], data_lines(src)
            assert doc.df['id'].tolist() == [1, 2, 3]
        """)

    def test_index_clean_after_an_edit_keeps_each_nodes_number_text(self) -> None:
        _run_gui_script(self, _HELPERS, self._CELL, """
            # A dendrogram edit leaves the document without its token columns.
            df = doc.df.loc[:, SWC_COLS].copy()
            df.loc[df['id'] == 7, 'type'] = 4
            doc.editor._on_df_changed(df)
            assert '7 4 2.5000e+00 1.0000e+00 0 2.5000e-01 5' in data_lines(src), data_lines(src)
            assert 'x_str' not in doc.df.columns
            window._validation_index_clean._on_apply()
            assert data_lines(src) == [
                '1 1 0.0000e+00 0 0 1.0000e+00 -1',
                '2 3 1.0000e+01 0 0 5.0000e-01 1',
                '3 4 2.5000e+00 1.0000e+00 0 2.5000e-01 2',
            ], data_lines(src)
        """)

    def test_label_and_radius_edits_change_only_those_fields_of_the_file(self) -> None:
        _run_gui_script(self, _HELPERS, """
            from swcstudio.core.provenance.header import strip_prov_lines
            src = TMP / 'rich.swc'
            original = ('\\ufeff# my header\\r\\n'
                        '1 1 0 0 0 1 -1 extra1 # soma\\r\\n'
                        '# mid-file note\\r\\n'
                        '2 3 1.0000e+01 0 0 5.0000e-01 1 extra2\\r\\n'
                        '3 3 2.0 0 0 0.25 2\\r\\n')
            src.write_bytes(original.encode('utf-8'))
            window._load_swc(str(src))
            doc = window._active_document()
            window._manual_radii_panel.apply_requested.emit(3, 0.75)
            df = doc.df.loc[:, SWC_COLS].copy()
            df.loc[df['id'] == 2, 'type'] = 4
            doc.editor._on_df_changed(df)
            expected = original.replace('2 3 1.0000e+01', '2 4 1.0000e+01').replace('0.25 2', '0.75 2')
            assert strip_prov_lines(src.read_bytes()) == expected.encode('utf-8'), src.read_bytes()
            # Saving an unchanged document leaves the file as it is.
            before = src.read_bytes()
            window._on_save()
            assert src.read_bytes() == before
        """)

    def test_a_utf8_byte_order_mark_does_not_corrupt_the_first_line(self) -> None:
        _run_gui_script(self, _HELPERS, """
            from swcstudio.core.swc_io import SWC_HEADER_ATTR
            with_header = TMP / 'bom_header.swc'
            with_header.write_bytes('\\ufeff# made on Windows\\n1 1 0 0 0 1 -1\\n2 3 1 0 0 0.5 1\\n'.encode('utf-8'))
            window._load_swc(str(with_header))
            doc = window._active_document()
            assert doc.df['id'].tolist() == [1, 2]
            assert doc.df.attrs[SWC_HEADER_ATTR] == ['# made on Windows'], doc.df.attrs
            first_is_data = TMP / 'bom_data.swc'
            first_is_data.write_bytes('\\ufeff1 1 0 0 0 1 -1\\n2 3 1 0 0 0.5 1\\n'.encode('utf-8'))
            window._load_swc(str(first_is_data))
            doc = window._active_document()
            assert doc.df['id'].tolist() == [1, 2], doc.df['id'].tolist()
            assert doc.df['parent'].tolist() == [-1, 1]
        """)


class RefusedEditTests(unittest.TestCase):
    def test_index_clean_and_geometry_refusals_are_shown_and_change_nothing(self) -> None:
        _run_gui_script(self, _HELPERS, """
            src = TMP / 'dupes.swc'
            src.write_text('1 1 0 0 0 1 -1\\n2 3 1 0 0 0.5 1\\n2 3 2 0 0 0.5 1\\n', encoding='utf-8')
            before = src.read_bytes()
            window._load_swc(str(src))
            warned = []
            QMessageBox.warning = staticmethod(lambda *args, **kw: warned.append((args[1], args[2])) or QMessageBox.Ok)
            window._validation_index_clean._on_apply()
            assert len(warned) == 1 and warned[0][0] == 'Index Clean', warned
            window._on_geometry_delete_node_requested(2, False)
            assert len(warned) == 2 and warned[1][0] == 'Geometry Editing', warned
            assert src.read_bytes() == before
            assert window._active_document().df['id'].tolist() == [1, 2, 2]
        """)

    def test_save_all_trees_refuses_a_split_that_would_drop_nodes_and_asks_before_replacing(self) -> None:
        _run_gui_script(self, _HELPERS, """
            from PySide6.QtWidgets import QFileDialog
            out = TMP / 'trees'
            out.mkdir()
            QFileDialog.getExistingDirectory = staticmethod(lambda *args, **kw: str(out))
            warned, asked = [], []
            QMessageBox.warning = staticmethod(lambda *args, **kw: warned.append(args[2]) or QMessageBox.Ok)
            answer = [QMessageBox.No]
            QMessageBox.question = staticmethod(lambda *args, **kw: asked.append(args[2]) or answer[0])
            two_cells = ('1 1 0 0 0 1 -1\\n2 3 1 0 0 0.5 1\\n'
                         '10 1 50 0 0 1 -1\\n11 3 51 0 0 0.5 10\\n')
            # Nodes 20 and 21 are each other's parent: they reach no soma.
            cyclic = TMP / 'cyclic.swc'
            cyclic.write_text(two_cells + '20 3 9 9 0 0.5 21\\n21 3 9 8 0 0.5 20\\n', encoding='utf-8')
            window._load_swc(str(cyclic))
            assert window._validation_tab._on_save_all() == []
            assert len(warned) == 1 and 'cycle' in warned[0].lower(), warned
            assert list(out.iterdir()) == []

            ok = TMP / 'two.swc'
            ok.write_text(two_cells, encoding='utf-8')
            window._load_swc(str(ok))
            saved = window._validation_tab._on_save_all()
            assert [Path(p).name for p in saved] == ['two_tree1.swc', 'two_tree2.swc'], saved
            first = (out / 'two_tree1.swc').read_bytes()
            (out / 'two_tree1.swc').write_text('# keep me\\n', encoding='utf-8')
            # Saving again over existing files asks first; No keeps them.
            assert window._validation_tab._on_save_all() == []
            assert len(asked) == 1 and (out / 'two_tree1.swc').read_text(encoding='utf-8') == '# keep me\\n'
            answer[0] = QMessageBox.Yes
            assert len(window._validation_tab._on_save_all()) == 2
            assert (out / 'two_tree1.swc').read_bytes() == first
        """)


class AutoLabelResultTests(unittest.TestCase):
    _FAKE_LABELER = """
        import swcstudio.gui.main_window as main_window_module
        from PySide6.QtCore import QObject, Signal, Slot
        from swcstudio.core.auto_typing import BatchOptions
        from swcstudio.core.provenance import ensure_schema, history_dir_for, open_history_for_read, open_index

        MODEL_RECORD = {'model_dir': str(TMP / 'my_models'), 'model_source': 'custom',
                        'model_version': '9.9', 'model_sha': 'abc123', 'model_files': ['stage1.json']}
        record = [MODEL_RECORD]

        class FakeLabeler(QObject):
            finished = Signal(int, object)
            failed = Signal(int, str)
            def __init__(self, run_id, path, options, overrides):
                super().__init__()
                self._run_id = run_id
                self._path = path
            @Slot()
            def run(self):
                rows = [{'id': 1, 'type': 1, 'x': 0.0, 'y': 0.0, 'z': 0.0, 'radius': 1.0, 'parent': -1},
                        {'id': 2, 'type': 3, 'x': 2.0, 'y': 0.0, 'z': 0.0, 'radius': 0.5, 'parent': 1},
                        {'id': 3, 'type': 3, 'x': 3.0, 'y': 0.0, 'z': 0.0, 'radius': 0.5, 'parent': 2}]
                result = SimpleNamespace(
                    rows=rows, types=[1, 2, 2], radii=[1.0, 0.5, 0.5], nodes_total=3, type_changes=2,
                    out_type_counts={1: 1, 2: 2}, cell_type='pyramidal', cell_type_source='user',
                    stage1_cell_type='pyramidal', stage1_confidence=0.9, class_mismatch=False, notes=[],
                    labeler_version='1.0.0', qc_result={}, flag_result={'flagged': False, 'predicted_f1': 0.8},
                    change_details=[])
                if record[0] is not None:
                    result.model_record = dict(record[0])
                self.finished.emit(self._run_id, result)

        main_window_module._AutoLabelFileWorker = FakeLabeler
        window._start_type_suspicion_worker = lambda *args, **kwargs: None
        OPTIONS = BatchOptions(soma=True, axon=True, apic=True, basal=True, rad=False, zip_output=False,
                               cell_type='pyramidal', flag_enabled=True, flag_budget=0.1)

        def run_labeler(doc):
            done = lambda: window._validation_auto_label_thread is None and doc.last_auto_label_result is not None
            doc.last_auto_label_result = None
            window._on_validation_auto_label_process_requested(OPTIONS, {})
            assert wait_until(done), 'auto-label result never arrived'

        def auto_label_params(path):
            with open_history_for_read(path, history_dir_for(path)) as hist:
                conn = open_index(hist)
                ensure_schema(conn)
                rows = conn.execute("SELECT params_json FROM ops WHERE kind = 'auto_label' ORDER BY op_id").fetchall()
                conn.close()
            return [json.loads(row[0]) for row in rows]
    """

    def test_single_file_auto_label_records_the_model_folder(self) -> None:
        _run_gui_script(self, _HELPERS, self._FAKE_LABELER, """
            path = write_swc('cell.swc', types=(1, 3, 3))
            window._load_swc(path)
            doc = window._active_document()
            run_labeler(doc)
            assert types_on_disk(path) == [1, 2, 2]
            params = auto_label_params(path)
            assert len(params) == 1, params
            for key, value in MODEL_RECORD.items():
                assert params[0][key] == value, (key, params[0])
            assert params[0]['labeler_version'] == '1.0.0'

            # A result without a model record adds nothing.
            record[0] = None
            run_labeler(doc)
            params = auto_label_params(path)
            assert len(params) == 2 and 'model_dir' not in params[1], params
        """)

    def test_result_arriving_after_a_tab_switch_drops_the_labeled_files_old_issues(self) -> None:
        _run_gui_script(self, _HELPERS, self._FAKE_LABELER, """
            a = write_swc('a.swc', types=(1, 3, 3))
            b = write_swc('b.swc')
            window._open_paths([a, b])
            doc_a, doc_b = list(window._documents.values())
            assert window._active_document() is doc_a
            # a has issues from a validation of its old labels.
            doc_a.validation_report = {'results': []}
            doc_a.issues = [{'issue_id': 'old', 'title': 'Likely wrong labels', 'severity': 'warning'}]
            b_report = {'results': []}
            doc_b.validation_report = b_report

            window._on_validation_auto_label_process_requested(OPTIONS, {})
            window._canvas_tabs.setCurrentIndex(1)  # before the result arrives
            assert wait_until(lambda: doc_a.last_auto_label_result is not None)
            assert types_on_disk(a) == [1, 2, 2]
            assert doc_a.df['type'].tolist() == [1, 2, 2]
            assert doc_a.validation_report is None and doc_a.issues == [], doc_a.issues
            assert window._active_document() is doc_b and doc_b.validation_report is b_report
            # Showing a again validates its new labels.
            runs = len(validation_runs)
            window._canvas_tabs.setCurrentIndex(0)
            assert validation_runs[runs:] == ['a.swc'], validation_runs
        """)

    def test_result_for_a_file_reloaded_meanwhile_is_not_applied(self) -> None:
        _run_gui_script(self, _HELPERS, self._FAKE_LABELER, """
            path = write_swc('cell.swc', types=(1, 3, 3))
            window._load_swc(path)
            doc = window._active_document()
            window._on_validation_auto_label_process_requested(OPTIONS, {})
            # Before the result arrives, a batch run rewrites the file and the tab reloads.
            Path(path).write_text('1 1 0 0 0 1 -1\\n2 4 2 0 0 0.5 1\\n3 4 3 0 0 0.5 2\\n', encoding='utf-8')
            window._batch_tab.source_files_changed.emit(str(TMP))
            assert doc.df['type'].tolist() == [1, 4, 4]
            assert wait_until(lambda: window._validation_auto_label_thread is None)
            app.processEvents()
            assert types_on_disk(path) == [1, 4, 4], types_on_disk(path)
            assert doc.df['type'].tolist() == [1, 4, 4]
        """)


class ValidationWhileSwitchingTests(unittest.TestCase):
    def test_switching_tabs_during_a_long_validation_neither_blocks_nor_misreports(self) -> None:
        _run_gui_script(self, _HELPERS, """
            import threading
            import swcstudio.gui.validation_tab as validation_tab_module
            del window._validation_tab.run_validation  # the real background validation
            window._start_type_suspicion_worker = lambda *args, **kwargs: None
            real_checks = validation_tab_module.run_checks_text
            gate = threading.Event()
            def checks(text):
                if len(text.strip().splitlines()) == 6:  # slow.swc: header + 5 nodes
                    gate.wait(60)
                return real_checks(text)
            validation_tab_module.run_checks_text = checks

            window._load_swc(write_swc('slow.swc', types=(1, 3, 3, 3, 3)))
            doc_slow = window._active_document()
            assert window._validation_tab.is_running()
            assert 'Running validation' in window._issue_panel._summary.text()

            started = time.monotonic()
            window._load_swc(write_swc('fast.swc', types=(1, 3, 4)))
            elapsed = time.monotonic() - started
            assert elapsed < 2.0, elapsed  # it used to wait up to 5 s for slow.swc
            doc_fast = window._active_document()
            assert doc_fast.filename == 'fast.swc'
            assert 'Running validation' in window._issue_panel._summary.text(), window._issue_panel._summary.text()
            assert 'validation' in window._issue_status_label.text().lower(), window._issue_status_label.text()

            # fast.swc's issues fill in when its validation finishes.
            assert wait_until(lambda: doc_fast.validation_report is not None), 'fast.swc was never validated'
            assert 'Running' not in window._issue_panel._summary.text()
            report = doc_fast.validation_report

            # slow.swc's validation finishing late changes nothing on screen.
            gate.set()
            wait_until(lambda: False, timeout=1.0)
            assert doc_slow.validation_report is None
            assert doc_fast.validation_report is report

            # Shown again, slow.swc is validated.
            window._canvas_tabs.setCurrentIndex(0)
            assert wait_until(lambda: doc_slow.validation_report is not None), 'slow.swc was never validated'
        """)

    def test_a_failed_validation_says_so(self) -> None:
        _run_gui_script(self, _HELPERS, """
            import swcstudio.gui.validation_tab as validation_tab_module
            del window._validation_tab.run_validation
            window._start_type_suspicion_worker = lambda *args, **kwargs: None
            def broken(text):
                raise RuntimeError('checker exploded')
            validation_tab_module.run_checks_text = broken
            window._load_swc(write_swc('cell.swc'))
            doc = window._active_document()
            assert wait_until(lambda: doc.validation_error != ''), 'failure never reported'
            assert 'Validation failed: checker exploded' in window._issue_panel._summary.text()
            assert not window._validation_tab.is_running()
        """)


if __name__ == "__main__":
    unittest.main()
