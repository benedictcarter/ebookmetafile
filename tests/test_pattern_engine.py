import unittest
from pathlib import Path

from ebookmetafile.pattern_engine import parse_filename


class TestFilenameOnlyPatterns(unittest.TestCase):
    """Patterns with no directory components — matches against the file stem."""

    def _parse(self, pattern, stem):
        return parse_filename(pattern, Path(r"S:\ebooks") / (stem + ".epub"))

    def test_author_title(self):
        fields, status = self._parse(
            "{author} - {title}",
            "Smith, John - The Test Book",
        )
        self.assertEqual(status, "OK")
        self.assertEqual(fields["author"], "Smith, John")
        self.assertEqual(fields["title"], "The Test Book")

    def test_author_series_index_title(self):
        fields, status = self._parse(
            "{author} - {series} {series_index} - {title}",
            "Smith, John - Test Series 06 - The Sixth Book",
        )
        self.assertEqual(status, "OK")
        self.assertEqual(fields["author"], "Smith, John")
        self.assertEqual(fields["series"], "Test Series")
        self.assertEqual(fields["series_index"], "06")
        self.assertEqual(fields["title"], "The Sixth Book")

    def test_long_series_name_with_numeric_index(self):
        """Non-greedy series must not steal the numeric index token."""
        fields, status = self._parse(
            "{author} - {series} {series_index} - {title}",
            "Smith, Jane - The Long Series Name 10 - The Tenth Book",
        )
        self.assertEqual(status, "OK")
        self.assertEqual(fields["series"], "The Long Series Name")
        self.assertEqual(fields["series_index"], "10")
        self.assertEqual(fields["title"], "The Tenth Book")

    def test_decimal_series_index(self):
        fields, status = self._parse(
            "{author} - {series} {series_index} - {title}",
            "Jones, Bob - Test Series 6.5 - The Point Five Book",
        )
        self.assertEqual(status, "OK")
        self.assertEqual(fields["series_index"], "6.5")

    def test_author_only_title(self):
        """Pattern without series fields still works."""
        fields, status = self._parse(
            "{author} - {title}",
            "Doe, Jane - A Test Title",
        )
        self.assertEqual(status, "OK")
        self.assertEqual(fields["author"], "Doe, Jane")
        self.assertEqual(fields["title"], "A Test Title")

    def test_no_pattern_returns_error(self):
        fields, status = self._parse("", "Whatever")
        self.assertNotEqual(status, "OK")

    def test_unmatched_pattern_returns_error(self):
        fields, status = self._parse(
            "{author} - {series} {series_index} - {title}",
            "NoDashesHereAtAll",
        )
        self.assertNotEqual(status, "OK")


class TestDirectoryPatterns(unittest.TestCase):
    """Patterns that include directory segments."""

    def test_lib_subject_filename(self):
        path = Path(r"S:\ebooks\scifi\Test Author - Test Title.epub")
        fields, status = parse_filename(r"{dir_in}\{subject}\{author} - {title}", path)
        self.assertEqual(status, "OK")
        self.assertEqual(fields["dir_in"], r"S:\ebooks")
        self.assertEqual(fields["subject"], "scifi")
        self.assertEqual(fields["author"], "Test Author")
        self.assertEqual(fields["title"], "Test Title")

    def test_dir_in_alias_for_lib(self):
        """{dir_in} in the pattern behaves identically to {dir_in}."""
        path = Path(r"S:\ebooks\scifi\Test Author - Test Title.epub")
        fields, status = parse_filename(r"{dir_in}\{subject}\{author} - {title}", path)
        self.assertEqual(status, "OK")
        self.assertEqual(fields["dir_in"], r"S:\ebooks")
        self.assertEqual(fields["subject"], "scifi")

    def test_dir_in_updates_when_subject_added_to_pattern(self):
        """Adding {subject} to the pattern should shift what {dir_in} absorbs."""
        path = Path(r"S:\ebooks\scifi\Test Author - Test Title.epub")

        # Without subject in the pattern, {dir_in} absorbs all dirs above the file
        fields1, _ = parse_filename(r"{dir_in}\{author} - {title}", path)
        self.assertEqual(fields1["dir_in"], r"S:\ebooks\scifi")

        # Adding {subject} means one fewer dir is absorbed by {dir_in}
        fields2, _ = parse_filename(r"{dir_in}\{subject}\{author} - {title}", path)
        self.assertEqual(fields2["dir_in"], r"S:\ebooks")
        self.assertEqual(fields2["subject"], "scifi")

    def test_lib_subject_full(self):
        path = Path(
            r"S:\ebooks\scifi\Smith, John - Test Series 06 - The Sixth Book.epub"
        )
        pattern = r"{dir_in}\{subject}\{author} - {series} {series_index} - {title}"
        fields, status = parse_filename(pattern, path)
        self.assertEqual(status, "OK")
        self.assertEqual(fields["subject"], "scifi")
        self.assertEqual(fields["author"], "Smith, John")
        self.assertEqual(fields["series"], "Test Series")
        self.assertEqual(fields["series_index"], "06")
        self.assertEqual(fields["title"], "The Sixth Book")

    def test_lib_subject_author_title_deep_library(self):
        """dir_in absorbs multiple leading segments."""
        path = Path(r"C:\Users\user\Documents\Books\scifi\Test Author - Test Title.epub")
        fields, status = parse_filename(r"{dir_in}\{subject}\{author} - {title}", path)
        self.assertEqual(status, "OK")
        self.assertEqual(fields["subject"], "scifi")
        self.assertEqual(fields["author"], "Test Author")
        self.assertEqual(fields["title"], "Test Title")

    def test_no_lib_matches_rightmost_segments(self):
        """Without {dir_in}, the rightmost N path segments are matched."""
        path = Path(r"S:\ebooks\fantasy\Test Author - Test Title.epub")
        fields, status = parse_filename(r"{subject}\{author} - {title}", path)
        self.assertEqual(status, "OK")
        self.assertEqual(fields["subject"], "fantasy")
        self.assertEqual(fields["author"], "Test Author")
        self.assertEqual(fields["title"], "Test Title")

    def test_forward_slash_separator(self):
        path = Path(r"S:\ebooks\scifi\Test Author - Test Title.epub")
        fields, status = parse_filename("{dir_in}/{subject}/{author} - {title}", path)
        self.assertEqual(status, "OK")
        self.assertEqual(fields["subject"], "scifi")
        self.assertEqual(fields["author"], "Test Author")
        self.assertEqual(fields["title"], "Test Title")

    def test_too_few_path_segments_returns_error(self):
        path = Path(r"S:\file.epub")
        fields, status = parse_filename(
            r"{dir_in}\{subject}\{author} - {title}", path
        )
        self.assertNotEqual(status, "OK")


class TestPatternEdgeCases(unittest.TestCase):
    """Edge cases not covered by the main test classes."""

    def test_title_only_pattern_no_directory(self):
        """A pattern with just {title} and no directory component works."""
        path = Path(r"S:\ebooks\Test Title.epub")
        fields, status = parse_filename("{title}", path)
        self.assertEqual(status, "OK")
        self.assertEqual(fields["title"], "Test Title")

    def test_no_dir_in_with_multiple_subdirectories(self):
        """Without {dir_in}, only the rightmost N segments are used."""
        path = Path(r"S:\a\b\c\d\author - title.epub")
        fields, status = parse_filename(r"{subject}\{author} - {title}", path)
        self.assertEqual(status, "OK")
        self.assertEqual(fields["subject"], "d")
        self.assertEqual(fields["author"], "author")
        self.assertEqual(fields["title"], "title")

    def test_duplicate_placeholder_returns_error(self):
        """A pattern with a repeated placeholder returns an error, not a crash."""
        path = Path(r"S:\ebooks\Smith - Smith - A Tale.epub")
        fields, status = parse_filename("{author} - {author} - {title}", path)
        self.assertNotEqual(status, "OK")
        self.assertIn("author", status.lower())

    def test_dir_in_with_single_segment_library(self):
        """{dir_in} with a single-segment library path works."""
        path = Path(r"C:\Test Author - Test Title.epub")
        fields, status = parse_filename(r"{dir_in}\{author} - {title}", path)
        self.assertEqual(status, "OK")
        self.assertEqual(fields["author"], "Test Author")
        self.assertEqual(fields["title"], "Test Title")

    def test_pattern_needs_more_segments_than_path_has(self):
        """Error returned when pattern requires more segments than path provides."""
        path = Path(r"S:\book.epub")
        fields, status = parse_filename(
            r"{dir_in}\{subject}\{genre}\{author} - {title}", path
        )
        self.assertNotEqual(status, "OK")

    def test_dir_in_absorbs_deep_library_path(self):
        """{dir_in} greedily absorbs all but the matched segments."""
        path = Path(r"S:\ebooks\2024\sci-fi\Test Author - Test Title.epub")
        fields, status = parse_filename(r"{dir_in}\{subject}\{author} - {title}", path)
        self.assertEqual(status, "OK")
        # dir_in should absorb S:\ebooks\2024, leaving sci-fi as subject
        self.assertIn("ebooks", fields["dir_in"])
        self.assertEqual(fields["subject"], "sci-fi")
        self.assertEqual(fields["title"], "Test Title")


if __name__ == "__main__":
    unittest.main()
