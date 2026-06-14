"""Tests for BookTableModel editing — setData, apply_row_updates, flags, filtering."""

import sys
import unittest
from pathlib import Path

from PyQt5 import QtCore
from PyQt5.QtWidgets import QApplication

from ebookmetafile.columns import COLUMNS
from ebookmetafile.gui_constants import ROWS_PER_BOOK, ROW_CHOSEN, ROW_FILENAME, ROW_FILEMETA
from ebookmetafile.gui_table import BookTableModel
from ebookmetafile.models import BookRecord

# QApplication must exist before any Qt model operations
_app = QApplication.instance() or QApplication(sys.argv)


def _rec(id=1, filepath=r"S:\books\book.epub", **meta) -> BookRecord:
    r = BookRecord(id=id, filepath=Path(filepath))
    r.chosen_metadata = dict(meta)
    r.dir_out = r"S:\out"
    return r


def _model(*records) -> BookTableModel:
    return BookTableModel(list(records))


def _idx(model, row, col_key):
    return model.index(row, COLUMNS.index(col_key))


# ---------------------------------------------------------------------------
# flags — editability
# ---------------------------------------------------------------------------

class TestFlags(unittest.TestCase):

    def test_chosen_row_metadata_is_editable(self):
        model = _model(_rec())
        for field in ("author", "title", "series", "series_index", "subject", "tags",
                      "publisher", "description", "isbn"):
            with self.subTest(field=field):
                idx = _idx(model, ROW_CHOSEN, field)
                self.assertTrue(model.flags(idx) & QtCore.Qt.ItemIsEditable,
                                f"{field} on chosen row should be editable")

    def test_chosen_row_output_pattern_editable(self):
        model = _model(_rec())
        idx = _idx(model, ROW_CHOSEN, "output_pattern")
        self.assertTrue(model.flags(idx) & QtCore.Qt.ItemIsEditable)

    def test_chosen_row_dir_out_editable(self):
        model = _model(_rec())
        idx = _idx(model, ROW_CHOSEN, "dir_out")
        self.assertTrue(model.flags(idx) & QtCore.Qt.ItemIsEditable)

    def test_chosen_row_filepath_not_editable(self):
        model = _model(_rec())
        idx = _idx(model, ROW_CHOSEN, "filepath")
        self.assertFalse(model.flags(idx) & QtCore.Qt.ItemIsEditable)

    def test_filename_row_pattern_editable(self):
        model = _model(_rec())
        idx = _idx(model, ROW_FILENAME, "pattern")
        self.assertTrue(model.flags(idx) & QtCore.Qt.ItemIsEditable)

    def test_filename_row_author_not_editable(self):
        model = _model(_rec())
        idx = _idx(model, ROW_FILENAME, "author")
        self.assertFalse(model.flags(idx) & QtCore.Qt.ItemIsEditable)

    def test_filemeta_row_nothing_editable(self):
        model = _model(_rec())
        for field in ("author", "title", "pattern", "output_pattern"):
            with self.subTest(field=field):
                if field not in COLUMNS:
                    continue
                idx = _idx(model, ROW_FILEMETA, field)
                self.assertFalse(model.flags(idx) & QtCore.Qt.ItemIsEditable,
                                 f"{field} on filemeta row should NOT be editable")

    def test_invalid_index_returns_no_flags(self):
        model = _model(_rec())
        self.assertEqual(model.flags(QtCore.QModelIndex()), QtCore.Qt.NoItemFlags)


# ---------------------------------------------------------------------------
# setData — chosen row
# ---------------------------------------------------------------------------

class TestSetDataChosenRow(unittest.TestCase):

    def test_edit_author(self):
        rec = _rec(author="Old")
        model = _model(rec)
        idx = _idx(model, ROW_CHOSEN, "author")
        ok = model.setData(idx, "New Author", QtCore.Qt.EditRole)
        self.assertTrue(ok)
        self.assertEqual(rec.chosen_metadata["author"], "New Author")

    def test_edit_title(self):
        rec = _rec(title="Old Title")
        model = _model(rec)
        idx = _idx(model, ROW_CHOSEN, "title")
        model.setData(idx, "New Title", QtCore.Qt.EditRole)
        self.assertEqual(rec.chosen_metadata["title"], "New Title")

    def test_edit_output_pattern(self):
        rec = _rec()
        model = _model(rec)
        idx = _idx(model, ROW_CHOSEN, "output_pattern")
        model.setData(idx, r"{dir_out}\{title}", QtCore.Qt.EditRole)
        self.assertEqual(rec.output_pattern, r"{dir_out}\{title}")

    def test_edit_dir_out(self):
        rec = _rec()
        model = _model(rec)
        idx = _idx(model, ROW_CHOSEN, "dir_out")
        model.setData(idx, r"S:\new_out", QtCore.Qt.EditRole)
        self.assertEqual(rec.dir_out, r"S:\new_out")

    def test_edit_triggers_recompute(self):
        rec = _rec(author="Auth", title="Title")
        rec.output_pattern = r"{author} - {title}"
        rec.recompute_new_filepath()
        old_path = rec.new_filepath

        model = _model(rec)
        idx = _idx(model, ROW_CHOSEN, "title")
        model.setData(idx, "New Title", QtCore.Qt.EditRole)
        self.assertNotEqual(rec.new_filepath, old_path)
        self.assertIn("New Title", str(rec.new_filepath))

    def test_edit_read_only_cell_returns_false(self):
        rec = _rec()
        model = _model(rec)
        idx = _idx(model, ROW_CHOSEN, "filepath")
        ok = model.setData(idx, "anything", QtCore.Qt.EditRole)
        self.assertFalse(ok)

    def test_wrong_role_returns_false(self):
        rec = _rec()
        model = _model(rec)
        idx = _idx(model, ROW_CHOSEN, "author")
        ok = model.setData(idx, "X", QtCore.Qt.DisplayRole)
        self.assertFalse(ok)


# ---------------------------------------------------------------------------
# setData — filename row (pattern editing)
# ---------------------------------------------------------------------------

class TestSetDataFilenameRow(unittest.TestCase):

    def test_edit_pattern_parses_metadata(self):
        rec = BookRecord(id=1, filepath=Path(r"S:\books\Test Author - Test Title.epub"))
        model = _model(rec)
        idx = _idx(model, ROW_FILENAME, "pattern")
        model.setData(idx, "{author} - {title}", QtCore.Qt.EditRole)
        self.assertEqual(rec.pattern, "{author} - {title}")
        self.assertEqual(rec.metadata_pattern.get("author"), "Test Author")
        self.assertEqual(rec.metadata_pattern.get("title"), "Test Title")

    def test_edit_pattern_status_ok_on_match(self):
        rec = BookRecord(id=1, filepath=Path(r"S:\books\Test Author - Test Title.epub"))
        model = _model(rec)
        idx = _idx(model, ROW_FILENAME, "pattern")
        model.setData(idx, "{author} - {title}", QtCore.Qt.EditRole)
        self.assertEqual(rec.pattern_status, "OK")

    def test_edit_pattern_status_error_on_no_match(self):
        rec = BookRecord(id=1, filepath=Path(r"S:\books\nodashes.epub"))
        model = _model(rec)
        idx = _idx(model, ROW_FILENAME, "pattern")
        model.setData(idx, "{author} - {series} {series_index} - {title}", QtCore.Qt.EditRole)
        self.assertNotEqual(rec.pattern_status, "OK")

    def test_edit_non_pattern_cell_on_filename_row_returns_false(self):
        rec = _rec()
        model = _model(rec)
        idx = _idx(model, ROW_FILENAME, "author")
        ok = model.setData(idx, "X", QtCore.Qt.EditRole)
        self.assertFalse(ok)


# ---------------------------------------------------------------------------
# apply_row_updates — paste support
# ---------------------------------------------------------------------------

class TestApplyRowUpdates(unittest.TestCase):

    def test_paste_single_metadata_field(self):
        rec = _rec(author="Old")
        model = _model(rec)
        model.apply_row_updates(ROW_CHOSEN, {"author": "Pasted Author"})
        self.assertEqual(rec.chosen_metadata["author"], "Pasted Author")

    def test_paste_multiple_fields_at_once(self):
        rec = _rec()
        model = _model(rec)
        model.apply_row_updates(ROW_CHOSEN, {"author": "A", "title": "T", "series": "S"})
        self.assertEqual(rec.chosen_metadata["author"], "A")
        self.assertEqual(rec.chosen_metadata["title"], "T")
        self.assertEqual(rec.chosen_metadata["series"], "S")

    def test_paste_output_pattern(self):
        rec = _rec()
        model = _model(rec)
        model.apply_row_updates(ROW_CHOSEN, {"output_pattern": r"{author} - {title}"})
        self.assertEqual(rec.output_pattern, r"{author} - {title}")

    def test_paste_dir_out(self):
        rec = _rec()
        model = _model(rec)
        model.apply_row_updates(ROW_CHOSEN, {"dir_out": r"S:\pasted_out"})
        self.assertEqual(rec.dir_out, r"S:\pasted_out")

    def test_paste_pattern_on_filename_row(self):
        rec = BookRecord(id=1, filepath=Path(r"S:\books\Test Author - Test Title.epub"))
        model = _model(rec)
        model.apply_row_updates(ROW_FILENAME, {"pattern": "{author} - {title}"})
        self.assertEqual(rec.pattern, "{author} - {title}")
        self.assertEqual(rec.metadata_pattern.get("author"), "Test Author")

    def test_paste_to_second_book(self):
        rec1 = _rec(id=1, author="A1")
        rec2 = _rec(id=2, author="A2")
        model = _model(rec1, rec2)
        # Second book's chosen row is at absolute row ROWS_PER_BOOK + ROW_CHOSEN
        model.apply_row_updates(ROWS_PER_BOOK + ROW_CHOSEN, {"author": "Updated"})
        self.assertEqual(rec1.chosen_metadata["author"], "A1")  # untouched
        self.assertEqual(rec2.chosen_metadata["author"], "Updated")

    def test_paste_out_of_bounds_is_ignored(self):
        rec = _rec()
        model = _model(rec)
        # Should not raise
        model.apply_row_updates(9999, {"author": "X"})
        self.assertNotEqual(rec.chosen_metadata.get("author"), "X")

    def test_paste_strips_whitespace(self):
        rec = _rec()
        model = _model(rec)
        model.apply_row_updates(ROW_CHOSEN, {"author": "  Whitespace  "})
        self.assertEqual(rec.chosen_metadata["author"], "Whitespace")


# ---------------------------------------------------------------------------
# set_chosen_only / set_book_filter
# ---------------------------------------------------------------------------

class TestViewState(unittest.TestCase):

    def test_set_chosen_only_collapses_to_one_row_per_book(self):
        recs = [_rec(id=i) for i in range(3)]
        model = _model(*recs)
        self.assertEqual(model.rowCount(), 3 * ROWS_PER_BOOK)
        model.set_chosen_only(True)
        self.assertEqual(model.rowCount(), 3)

    def test_set_chosen_only_false_restores_all_rows(self):
        recs = [_rec(id=i) for i in range(2)]
        model = _model(*recs)
        model.set_chosen_only(True)
        model.set_chosen_only(False)
        self.assertEqual(model.rowCount(), 2 * ROWS_PER_BOOK)

    def test_set_book_filter_limits_visible_records(self):
        r1 = _rec(id=1)
        r2 = _rec(id=2)
        r3 = _rec(id=3)
        model = _model(r1, r2, r3)
        model.set_book_filter({1, 3})
        self.assertEqual(model.rowCount(), 2 * ROWS_PER_BOOK)
        visible_ids = {r.id for r in model._display_records}
        self.assertEqual(visible_ids, {1, 3})

    def test_set_book_filter_none_shows_all(self):
        r1 = _rec(id=1)
        r2 = _rec(id=2)
        model = _model(r1, r2)
        model.set_book_filter({1})
        model.set_book_filter(None)
        self.assertEqual(model.rowCount(), 2 * ROWS_PER_BOOK)

    def test_filter_empty_set_shows_nothing(self):
        model = _model(_rec(id=1), _rec(id=2))
        model.set_book_filter(set())
        self.assertEqual(model.rowCount(), 0)


# ---------------------------------------------------------------------------
# records_for_rows
# ---------------------------------------------------------------------------

class TestRecordsForRows(unittest.TestCase):

    def test_chosen_row_maps_to_correct_record(self):
        r1 = _rec(id=1)
        r2 = _rec(id=2)
        model = _model(r1, r2)
        recs = model.records_for_rows([ROW_CHOSEN])
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0].id, 1)

    def test_all_sub_rows_of_same_book_deduped(self):
        r1 = _rec(id=1)
        model = _model(r1)
        all_rows = list(range(ROWS_PER_BOOK))
        recs = model.records_for_rows(all_rows)
        self.assertEqual(len(recs), 1)

    def test_rows_from_two_books(self):
        r1 = _rec(id=1)
        r2 = _rec(id=2)
        model = _model(r1, r2)
        recs = model.records_for_rows([ROW_CHOSEN, ROWS_PER_BOOK + ROW_CHOSEN])
        self.assertEqual({r.id for r in recs}, {1, 2})

    def test_out_of_range_row_ignored(self):
        model = _model(_rec(id=1))
        recs = model.records_for_rows([9999])
        self.assertEqual(recs, [])


# ---------------------------------------------------------------------------
# headerData
# ---------------------------------------------------------------------------

class TestHeaderData(unittest.TestCase):

    def test_horizontal_headers_match_columns(self):
        model = _model(_rec())
        for i, key in enumerate(COLUMNS):
            label = model.headerData(i, QtCore.Qt.Horizontal, QtCore.Qt.DisplayRole)
            self.assertIsNotNone(label, f"header for column {key} is None")

    def test_vertical_header_chosen_row_shows_c(self):
        model = _model(_rec())
        label = model.headerData(ROW_CHOSEN, QtCore.Qt.Vertical, QtCore.Qt.DisplayRole)
        self.assertIn("C", str(label))

    def test_vertical_header_second_book(self):
        model = _model(_rec(id=1), _rec(id=2))
        label = model.headerData(ROWS_PER_BOOK + ROW_CHOSEN, QtCore.Qt.Vertical, QtCore.Qt.DisplayRole)
        self.assertIn("2", str(label))

    def test_wrong_role_returns_none(self):
        model = _model(_rec())
        result = model.headerData(0, QtCore.Qt.Horizontal, QtCore.Qt.DecorationRole)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
