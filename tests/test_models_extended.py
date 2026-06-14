"""Extended BookRecord tests — defaults, sanitisation, sync, edge cases."""

import unittest
from pathlib import Path

from ebookmetafile.models import BookRecord


class TestEnsureChosenDefaults(unittest.TestCase):
    def _rec(self):
        return BookRecord(id=1, filepath=Path(r"S:\books\f.epub"))

    def test_file_meta_preferred_over_pattern(self):
        rec = self._rec()
        rec.metadata_file = {"author": "FileAuthor"}
        rec.metadata_pattern = {"author": "PatternAuthor"}
        rec.ensure_chosen_defaults(["author"])
        self.assertEqual(rec.chosen_metadata["author"], "FileAuthor")

    def test_pattern_used_when_file_empty(self):
        rec = self._rec()
        rec.metadata_file = {"author": ""}
        rec.metadata_pattern = {"author": "PatternAuthor"}
        rec.ensure_chosen_defaults(["author"])
        self.assertEqual(rec.chosen_metadata["author"], "PatternAuthor")

    def test_existing_chosen_not_overwritten(self):
        rec = self._rec()
        rec.chosen_metadata = {"author": "AlreadyChosen"}
        rec.metadata_file = {"author": "FileAuthor"}
        rec.ensure_chosen_defaults(["author"])
        self.assertEqual(rec.chosen_metadata["author"], "AlreadyChosen")

    def test_empty_file_and_pattern_gives_empty_string(self):
        rec = self._rec()
        rec.ensure_chosen_defaults(["author"])
        self.assertEqual(rec.chosen_metadata.get("author", ""), "")

    def test_multiple_fields_at_once(self):
        rec = self._rec()
        rec.metadata_file = {"author": "A", "title": "T"}
        rec.ensure_chosen_defaults(["author", "title", "series"])
        self.assertEqual(rec.chosen_metadata["author"], "A")
        self.assertEqual(rec.chosen_metadata["title"], "T")
        self.assertEqual(rec.chosen_metadata.get("series", ""), "")


class TestSyncDirOut(unittest.TestCase):
    def test_seeds_from_parsed_dir_in(self):
        rec = BookRecord(id=1, filepath=Path(r"S:\books\scifi\f.epub"))
        rec.metadata_pattern = {"dir_in": r"S:\books"}
        rec.sync_dir_out()
        self.assertEqual(rec.dir_out, r"S:\books")

    def test_does_not_overwrite_existing_dir_out(self):
        rec = BookRecord(id=1, filepath=Path(r"S:\books\f.epub"))
        rec.dir_out = r"S:\output"
        rec.metadata_pattern = {"dir_in": r"S:\books"}
        rec.sync_dir_out()
        self.assertEqual(rec.dir_out, r"S:\output")

    def test_falls_back_to_parent_when_no_dir_in(self):
        rec = BookRecord(id=1, filepath=Path(r"S:\books\scifi\f.epub"))
        rec.sync_dir_out()
        self.assertEqual(rec.dir_out, str(Path(r"S:\books\scifi")))


class TestRecomputeNewFilepath(unittest.TestCase):
    def _rec(self, filepath=r"S:\books\f.epub") -> BookRecord:
        return BookRecord(id=1, filepath=Path(filepath))

    def test_windows_invalid_chars_removed_from_title(self):
        rec = self._rec()
        rec.chosen_metadata = {"author": "Author", "title": "Title: A/B*C?"}
        rec.dir_out = r"S:\out"
        rec.output_pattern = r"{dir_out}\{author} - {title}"
        rec.recompute_new_filepath()
        stem = rec.new_filepath.stem
        self.assertNotIn(":", stem)
        self.assertNotIn("/", stem)
        self.assertNotIn("*", stem)
        self.assertNotIn("?", stem)
        self.assertIn("Author", stem)

    def test_colon_replaced_with_hyphen(self):
        rec = self._rec()
        rec.chosen_metadata = {"author": "A", "title": "The Title: A Subtitle"}
        rec.output_pattern = r"{author} - {title}"
        rec.recompute_new_filepath()
        # colon → hyphen per _sanitize
        self.assertIn("-", rec.new_filepath.stem)
        self.assertNotIn(":", rec.new_filepath.stem)

    def test_double_hyphen_collapsed_with_empty_series(self):
        rec = self._rec()
        rec.chosen_metadata = {"author": "A", "title": "T", "series": "", "series_index": ""}
        rec.dir_out = r"S:\out"
        rec.output_pattern = r"{dir_out}\{author} - {series} {series_index} - {title}"
        rec.recompute_new_filepath()
        stem = rec.new_filepath.stem
        self.assertNotIn("- -", stem)
        self.assertIn("A", stem)
        self.assertIn("T", stem)

    def test_extension_preserved(self):
        rec = self._rec(r"S:\books\file.mobi")
        rec.chosen_metadata = {"author": "A", "title": "T"}
        rec.output_pattern = r"{author} - {title}"
        rec.recompute_new_filepath()
        self.assertEqual(rec.new_filepath.suffix, ".mobi")

    def test_unknown_placeholder_sets_error_message(self):
        rec = self._rec()
        rec.chosen_metadata = {"author": "A"}
        rec.output_pattern = r"{author} - {nonexistent_field}"
        rec.recompute_new_filepath()
        self.assertIsNotNone(rec.error_message)
        self.assertIn("nonexistent_field", rec.error_message)
        # new_filepath should not be set when pattern is invalid
        self.assertIsNone(rec.new_filepath)

    def test_absolute_output_path_used_as_is(self):
        rec = self._rec()
        rec.chosen_metadata = {"author": "Author", "title": "Title"}
        rec.dir_out = r"S:\output"
        rec.output_pattern = r"{dir_out}\{author} - {title}"
        rec.recompute_new_filepath()
        self.assertEqual(rec.new_filepath.parent, Path(r"S:\output"))

    def test_relative_pattern_resolves_to_source_dir(self):
        rec = self._rec(r"S:\books\scifi\book.epub")
        rec.chosen_metadata = {"author": "A", "title": "T"}
        rec.output_pattern = r"{author} - {title}"
        rec.recompute_new_filepath()
        self.assertEqual(rec.new_filepath.parent, Path(r"S:\books\scifi"))

    def test_dir_in_placeholder_uses_source_parent(self):
        rec = self._rec(r"S:\lib\genre\book.epub")
        rec.chosen_metadata = {"author": "A", "title": "T"}
        rec.output_pattern = r"{dir_in}\{author} - {title}"
        rec.recompute_new_filepath()
        self.assertEqual(rec.new_filepath.parent, Path(r"S:\lib\genre"))


class TestGetDisplayValue(unittest.TestCase):
    def _rec(self) -> BookRecord:
        rec = BookRecord(id=1, filepath=Path(r"S:\f.epub"))
        rec.metadata_file = {"author": "FileAuth"}
        rec.metadata_pattern = {"author": "PatAuth"}
        rec.metadata_google = {"author": "GoogAuth"}
        rec.metadata_openlibrary = {"author": "OLAuth"}
        rec.chosen_metadata = {"author": "ChosenAuth"}
        return rec

    def test_file_source(self):
        self.assertEqual(self._rec().get_display_value("file", "author"), "FileAuth")

    def test_pattern_source(self):
        self.assertEqual(self._rec().get_display_value("pattern", "author"), "PatAuth")

    def test_google_source(self):
        self.assertEqual(self._rec().get_display_value("google", "author"), "GoogAuth")

    def test_openlibrary_source(self):
        self.assertEqual(self._rec().get_display_value("openlibrary", "author"), "OLAuth")

    def test_chosen_source(self):
        self.assertEqual(self._rec().get_display_value("chosen", "author"), "ChosenAuth")

    def test_missing_field_returns_empty_string(self):
        rec = BookRecord(id=1, filepath=Path(r"S:\f.epub"))
        self.assertEqual(rec.get_display_value("file", "author"), "")

    def test_unknown_source_returns_empty_string(self):
        self.assertEqual(self._rec().get_display_value("unknown_source", "author"), "")

    def test_strips_whitespace(self):
        rec = BookRecord(id=1, filepath=Path(r"S:\f.epub"))
        rec.metadata_file = {"author": "  Padded  "}
        self.assertEqual(rec.get_display_value("file", "author"), "Padded")


class TestSetChosenFromSource(unittest.TestCase):
    def test_sets_from_file(self):
        rec = BookRecord(id=1, filepath=Path(r"S:\f.epub"))
        rec.metadata_file = {"title": "FileTitle"}
        rec.set_chosen_from_source("title", "file")
        self.assertEqual(rec.chosen_metadata["title"], "FileTitle")

    def test_sets_from_pattern(self):
        rec = BookRecord(id=1, filepath=Path(r"S:\f.epub"))
        rec.metadata_pattern = {"title": "PatTitle"}
        rec.set_chosen_from_source("title", "pattern")
        self.assertEqual(rec.chosen_metadata["title"], "PatTitle")

    def test_unknown_source_does_not_raise(self):
        rec = BookRecord(id=1, filepath=Path(r"S:\f.epub"))
        rec.set_chosen_from_source("title", "unknown")
        self.assertNotIn("title", rec.chosen_metadata)

    def test_missing_field_in_source_sets_none(self):
        rec = BookRecord(id=1, filepath=Path(r"S:\f.epub"))
        rec.metadata_google = {}
        rec.set_chosen_from_source("author", "google")
        self.assertIsNone(rec.chosen_metadata.get("author"))


if __name__ == "__main__":
    unittest.main()
