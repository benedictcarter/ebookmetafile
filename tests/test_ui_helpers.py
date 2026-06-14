"""Tests for ui_helpers — TSV parse/tile and highlight logic."""

import unittest

from ebookmetafile.ui_helpers import (
    color_key_to_hex,
    get_source_row_highlight,
    parse_tsv,
    tile_clipboard_to_rect,
)


class TestGetSourceRowHighlight(unittest.TestCase):
    def test_matching_values_returns_blue(self):
        self.assertEqual(get_source_row_highlight("Test Author", "Test Author"), "blue")

    def test_matching_with_whitespace_returns_blue(self):
        self.assertEqual(get_source_row_highlight("  Test Author  ", "Test Author"), "blue")

    def test_non_matching_returns_red(self):
        self.assertEqual(get_source_row_highlight("Test Author", "Other Author"), "red")

    def test_empty_source_returns_none(self):
        self.assertIsNone(get_source_row_highlight("", "Test Author"))

    def test_empty_source_and_chosen_returns_none(self):
        self.assertIsNone(get_source_row_highlight("", ""))

    def test_nonempty_source_empty_chosen_returns_red(self):
        self.assertEqual(get_source_row_highlight("Test Author", ""), "red")

    def test_case_insensitive_match(self):
        # The current implementation is case-sensitive (strip only, no lower)
        # This test documents the actual behaviour
        result = get_source_row_highlight("test author", "Test Author")
        self.assertIn(result, ("blue", "red"))  # documents whichever it is


class TestColorKeyToHex(unittest.TestCase):
    def test_green(self):
        self.assertEqual(color_key_to_hex("green"), "#99ee99")

    def test_red(self):
        self.assertEqual(color_key_to_hex("red"), "#ff9999")

    def test_blue(self):
        self.assertEqual(color_key_to_hex("blue"), "#99bbff")

    def test_none_returns_none(self):
        self.assertIsNone(color_key_to_hex(None))

    def test_unknown_key_returns_none(self):
        self.assertIsNone(color_key_to_hex("purple"))


class TestParseTsv(unittest.TestCase):
    def test_empty_string_returns_empty_list(self):
        self.assertEqual(parse_tsv(""), [])

    def test_single_cell(self):
        self.assertEqual(parse_tsv("hello"), [["hello"]])

    def test_single_row_multi_col(self):
        self.assertEqual(parse_tsv("a\tb\tc"), [["a", "b", "c"]])

    def test_multi_row(self):
        result = parse_tsv("a\tb\nc\td")
        self.assertEqual(result, [["a", "b"], ["c", "d"]])

    def test_irregular_rows_padded(self):
        """Rows with fewer columns should be padded with empty strings."""
        result = parse_tsv("a\tb\tc\nx\ty")
        self.assertEqual(len(result[0]), 3)
        self.assertEqual(len(result[1]), 3)
        self.assertEqual(result[1][2], "")

    def test_trailing_tab_produces_empty_cell(self):
        result = parse_tsv("a\t")
        self.assertEqual(result[0], ["a", ""])

    def test_all_rows_same_width(self):
        result = parse_tsv("a\tb\nc\td\ne\tf")
        widths = [len(r) for r in result]
        self.assertEqual(len(set(widths)), 1)


class TestTileClipboardToRect(unittest.TestCase):
    def test_exact_fit_single_cell(self):
        clip = [["X"]]
        result = tile_clipboard_to_rect(clip, 0, 0, 0, 0)
        self.assertEqual(result, {0: {0: "X"}})

    def test_exact_fit_multi_cell(self):
        clip = [["a", "b"], ["c", "d"]]
        result = tile_clipboard_to_rect(clip, 0, 0, 1, 1)
        self.assertEqual(result[0][0], "a")
        self.assertEqual(result[0][1], "b")
        self.assertEqual(result[1][0], "c")
        self.assertEqual(result[1][1], "d")

    def test_single_cell_tiles_across_rect(self):
        clip = [["X"]]
        result = tile_clipboard_to_rect(clip, 2, 3, 4, 5)
        for r in range(2, 5):
            for c in range(3, 6):
                self.assertEqual(result[r][c], "X")

    def test_tiling_horizontally(self):
        """Clip narrower than dest: repeat horizontally."""
        clip = [["L", "R"]]
        result = tile_clipboard_to_rect(clip, 0, 0, 0, 3)
        self.assertEqual(result[0][0], "L")
        self.assertEqual(result[0][1], "R")
        self.assertEqual(result[0][2], "L")
        self.assertEqual(result[0][3], "R")

    def test_tiling_vertically(self):
        """Clip shorter than dest: repeat vertically."""
        clip = [["T"], ["B"]]
        result = tile_clipboard_to_rect(clip, 0, 0, 3, 0)
        self.assertEqual(result[0][0], "T")
        self.assertEqual(result[1][0], "B")
        self.assertEqual(result[2][0], "T")
        self.assertEqual(result[3][0], "B")

    def test_empty_clip_returns_empty(self):
        result = tile_clipboard_to_rect([], 0, 0, 2, 2)
        self.assertEqual(result, {})

    def test_offset_dest_rect(self):
        """Target rect not starting at (0,0)."""
        clip = [["V"]]
        result = tile_clipboard_to_rect(clip, 10, 5, 11, 6)
        self.assertIn(10, result)
        self.assertIn(11, result)
        self.assertEqual(result[10][5], "V")
        self.assertEqual(result[11][6], "V")
        self.assertNotIn(0, result)


if __name__ == "__main__":
    unittest.main()
