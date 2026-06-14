import unittest
from pathlib import Path

from PyQt5 import QtCore

from ebookmetafile.models import BookRecord
from ebookmetafile.columns import COLUMNS
from ebookmetafile.gui_table import BookTableModel
from ebookmetafile.gui_constants import ROWS_PER_BOOK


class TestBookRecord(unittest.TestCase):
    def test_empty_series_no_double_hyphen(self):
        rec = BookRecord(id=5, filepath=Path(r"S:\ebooks\file.epub"))
        rec.chosen_metadata = {"author": "Test Author", "title": "Test Title", "series": "", "series_index": ""}
        rec.dir_out = r"S:\ebooks"
        rec.recompute_new_filepath()

        name = rec.new_filepath.stem
        self.assertNotIn("- -", name)
        self.assertIn("Test Author", name)
        self.assertIn("Test Title", name)

    def test_dots_in_author_name_preserved(self):
        rec = BookRecord(id=4, filepath=Path(r"S:\ebooks\file.epub"))
        rec.chosen_metadata = {"author": "A.B.C. Author", "title": "Test Title"}
        rec.output_pattern = "{author} - {title}"
        rec.recompute_new_filepath()

        self.assertEqual(rec.new_filepath.suffix, ".epub")
        self.assertIn("A.B.C. Author", rec.new_filepath.stem)
        self.assertIn("Test Title", rec.new_filepath.stem)

    def test_recompute_relative_resolves_to_input_dir(self):
        rec = BookRecord(id=1, filepath=Path(r"S:\ebooks\scifi\file.epub"))
        rec.chosen_metadata = {"author": "Auth", "title": "The Title"}
        rec.output_pattern = "{author} - {title}"
        rec.recompute_new_filepath()

        self.assertEqual(rec.new_filepath.parent, rec.filepath.parent)
        self.assertTrue(rec.new_filepath.name.startswith("Auth - The Title"))

    def test_get_source_row_values_chosen(self):
        rec = BookRecord(id=2, filepath=Path(r"S:\ebooks\scifi\f2.epub"))
        rec.metadata_file = {"author": "A", "title": "T"}
        rec.metadata_pattern = {"author": "P_A", "title": "P_T"}
        rec.chosen_metadata = {"author": "A", "title": "T"}
        rec.output_pattern = "{author} - {title}"
        rec.recompute_new_filepath()

        row = rec.get_source_row_values(0, COLUMNS)
        self.assertEqual(row[COLUMNS.index("author")], "A")
        self.assertEqual(row[COLUMNS.index("new_filepath")], str(rec.new_filepath))
        self.assertEqual(row[COLUMNS.index("source_label")], "Chosen")

    def test_get_source_row_values_filename(self):
        rec = BookRecord(id=2, filepath=Path(r"S:\ebooks\scifi\f2.epub"))
        rec.metadata_pattern = {"author": "P_A", "title": "P_T"}
        rec.pattern = r"{dir_in}\{author} - {title}"

        row = rec.get_source_row_values(1, COLUMNS)
        self.assertEqual(row[COLUMNS.index("author")], "P_A")
        self.assertEqual(row[COLUMNS.index("title")], "P_T")
        self.assertEqual(row[COLUMNS.index("source_label")], "Filename")
        self.assertEqual(row[COLUMNS.index("pattern")], rec.pattern)
        self.assertEqual(row[COLUMNS.index("new_filepath")], "")

    def test_get_source_row_values_filemeta(self):
        rec = BookRecord(id=2, filepath=Path(r"S:\ebooks\scifi\f2.epub"))
        rec.metadata_file = {"author": "A", "title": "T"}

        row = rec.get_source_row_values(2, COLUMNS)
        self.assertEqual(row[COLUMNS.index("author")], "A")
        self.assertEqual(row[COLUMNS.index("source_label")], "File meta")
        self.assertEqual(row[COLUMNS.index("filepath")], "")

    def test_get_source_row_values_google(self):
        rec = BookRecord(id=2, filepath=Path(r"S:\ebooks\scifi\f2.epub"))
        rec.metadata_google = {"author": "G_Auth", "title": "G_Title"}

        row = rec.get_source_row_values(3, COLUMNS)
        self.assertEqual(row[COLUMNS.index("author")], "G_Auth")
        self.assertEqual(row[COLUMNS.index("title")], "G_Title")
        self.assertEqual(row[COLUMNS.index("source_label")], "Google")
        self.assertEqual(row[COLUMNS.index("filepath")], "")

    def test_get_source_row_values_openlibrary(self):
        rec = BookRecord(id=2, filepath=Path(r"S:\ebooks\scifi\f2.epub"))
        rec.metadata_openlibrary = {"author": "OL_Auth", "title": "OL_Title", "subject": "Fiction"}

        row = rec.get_source_row_values(4, COLUMNS)
        self.assertEqual(row[COLUMNS.index("author")], "OL_Auth")
        self.assertEqual(row[COLUMNS.index("title")], "OL_Title")
        self.assertEqual(row[COLUMNS.index("subject")], "Fiction")
        self.assertEqual(row[COLUMNS.index("source_label")], "Open Library")

    def test_set_chosen_from_google(self):
        rec = BookRecord(id=3, filepath=Path(r"S:\ebooks\f3.epub"))
        rec.metadata_google = {"author": "Google Author", "title": "Google Title"}
        rec.set_chosen_from_source("author", "google")
        self.assertEqual(rec.chosen_metadata.get("author"), "Google Author")

    def test_set_chosen_from_openlibrary(self):
        rec = BookRecord(id=3, filepath=Path(r"S:\ebooks\f3.epub"))
        rec.metadata_openlibrary = {"author": "OL Author", "title": "OL Title"}
        rec.set_chosen_from_source("title", "openlibrary")
        self.assertEqual(rec.chosen_metadata.get("title"), "OL Title")



class TestBookTableModel(unittest.TestCase):
    def test_row_count_is_five_per_book(self):
        records = [
            BookRecord(id=1, filepath=Path(r"S:\a\one.epub")),
            BookRecord(id=2, filepath=Path(r"S:\a\two.epub")),
        ]
        model = BookTableModel(records)
        self.assertEqual(model.rowCount(), 2 * ROWS_PER_BOOK)

    def test_sorting_by_title(self):
        r1 = BookRecord(id=1, filepath=Path(r"S:\a\one.epub"))
        r1.chosen_metadata = {"title": "banana"}
        r2 = BookRecord(id=2, filepath=Path(r"S:\a\two.epub"))
        r2.chosen_metadata = {"title": "apple"}
        r3 = BookRecord(id=3, filepath=Path(r"S:\a\three.epub"))
        r3.chosen_metadata = {"title": "cherry"}

        model = BookTableModel([r1, r2, r3])
        idx = COLUMNS.index("title")
        model.sort(idx, QtCore.Qt.AscendingOrder)

        titles = [r.chosen_metadata.get("title") for r in model.records]
        self.assertEqual(titles, ["apple", "banana", "cherry"])

    def test_data_chosen_row_returns_filepath(self):
        rec = BookRecord(id=1, filepath=Path(r"S:\a\one.epub"))
        rec.chosen_metadata = {"author": "Auth", "title": "Title"}
        model = BookTableModel([rec])

        # Row 0 = chosen sub-row
        idx = model.index(0, COLUMNS.index("filepath"))
        self.assertEqual(model.data(idx, QtCore.Qt.DisplayRole), str(rec.filepath))

        # Row 1 = filename sub-row — filepath should be blank
        idx = model.index(1, COLUMNS.index("filepath"))
        self.assertEqual(model.data(idx, QtCore.Qt.DisplayRole), "")

    def test_chosen_row_is_bold(self):
        rec = BookRecord(id=1, filepath=Path(r"S:\a\one.epub"))
        model = BookTableModel([rec])

        from PyQt5.QtGui import QFont
        idx = model.index(0, COLUMNS.index("author"))
        font = model.data(idx, QtCore.Qt.FontRole)
        self.assertIsNotNone(font)
        self.assertTrue(font.bold())

        # Source rows not bold
        idx = model.index(1, COLUMNS.index("author"))
        font = model.data(idx, QtCore.Qt.FontRole)
        self.assertIsNone(font)


if __name__ == "__main__":
    unittest.main()
