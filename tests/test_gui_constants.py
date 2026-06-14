"""Tests for gui_constants helpers — _format_cover_display, _cover_source_name, _try_patterns."""

import struct
import unittest
from pathlib import Path
from unittest.mock import patch

from ebookmetafile.gui_constants import (
    _cover_source_name,
    _format_cover_display,
    _image_dimensions,
    _try_patterns,
)
from ebookmetafile.models import BookRecord


# Minimal valid 1×1 PNG (smallest possible PNG)
_PNG_1X1 = (
    b'\x89PNG\r\n\x1a\n'                       # magic
    b'\x00\x00\x00\rIHDR'                      # IHDR chunk length + type
    b'\x00\x00\x00\x01'                        # width = 1
    b'\x00\x00\x00\x01'                        # height = 1
    b'\x08\x02\x00\x00\x00\x90wS\xde'         # bit depth, colour type, crc
    b'\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N'
    b'\x00\x00\x00\x00IEND\xaeB`\x82'
)

def _make_jpeg_bytes(w: int, h: int) -> bytes:
    """Construct a minimal JPEG with a SOF0 marker encoding w×h."""
    # SOF0 payload: length(2) precision(1) height(2) width(2) ncomp(1) id(1) sampling(1) quant(1)
    sof0 = struct.pack('>HBHHBBBB', 11, 8, h, w, 1, 1, 0x11, 0)
    return b'\xff\xd8\xff\xc0' + sof0 + b'\xff\xd9'


class TestImageDimensions(unittest.TestCase):
    def test_png_1x1(self):
        dims = _image_dimensions(_PNG_1X1)
        self.assertEqual(dims, (1, 1))

    def test_png_custom_size(self):
        # Build a PNG header with custom width/height
        data = bytearray(_PNG_1X1)
        struct.pack_into('>I', data, 16, 320)
        struct.pack_into('>I', data, 20, 480)
        self.assertEqual(_image_dimensions(bytes(data)), (320, 480))

    def test_jpeg_dimensions(self):
        self.assertEqual(_image_dimensions(_make_jpeg_bytes(128, 192)), (128, 192))

    def test_jpeg_landscape(self):
        self.assertEqual(_image_dimensions(_make_jpeg_bytes(640, 400)), (640, 400))

    def test_empty_bytes_returns_none(self):
        self.assertIsNone(_image_dimensions(b""))

    def test_too_short_returns_none(self):
        self.assertIsNone(_image_dimensions(b"\x89PNG"))

    def test_unknown_format_returns_none(self):
        self.assertIsNone(_image_dimensions(b"GIF89a\x01\x00\x01\x00"))


class TestCoverSourceName(unittest.TestCase):
    """_cover_source_name — used for the preview window label (no dimensions)."""

    def test_empty_returns_empty(self):
        self.assertEqual(_cover_source_name(""), "")

    def test_embedded(self):
        self.assertEqual(_cover_source_name("embedded:OEBPS/cover.jpg"), "Embedded · cover.jpg")

    def test_google_books(self):
        self.assertEqual(_cover_source_name("https://books.google.com/x"), "Google Books")

    def test_googleapis(self):
        self.assertEqual(_cover_source_name("https://books.googleapis.com/x"), "Google Books")

    def test_openlibrary(self):
        self.assertEqual(_cover_source_name("https://covers.openlibrary.org/b/id/1-L.jpg"), "Open Library")

    def test_unknown_url_returned_as_is(self):
        url = "https://example.com/img.jpg"
        self.assertEqual(_cover_source_name(url), url)

    def test_long_unknown_url_truncated(self):
        url = "https://example.com/" + "x" * 80
        result = _cover_source_name(url)
        self.assertTrue(result.endswith("…"))
        self.assertLessEqual(len(result), 62)


class TestFormatCoverDisplay(unittest.TestCase):
    """_format_cover_display — used for table cells; shows dimensions when loaded."""

    def test_empty_url_returns_empty_string(self):
        self.assertEqual(_format_cover_display(""), "")

    # --- Without bytes: shows source name only (no size descriptor) ---

    def test_embedded_no_bytes_shows_filename(self):
        self.assertEqual(_format_cover_display("embedded:OEBPS/images/cover.jpg"), "Embedded · cover.jpg")

    def test_google_no_bytes_shows_source_name(self):
        result = _format_cover_display("https://books.google.com/books/content?id=abc")
        self.assertEqual(result, "Google Books")

    def test_openlibrary_no_bytes_shows_source_name(self):
        result = _format_cover_display("https://covers.openlibrary.org/b/id/123456-L.jpg")
        self.assertEqual(result, "Open Library")

    def test_no_size_descriptor_without_bytes(self):
        """Regression: 'large', 'medium', 'thumbnail' must not appear without bytes."""
        for url in [
            "https://covers.openlibrary.org/b/id/123-L.jpg",
            "https://covers.openlibrary.org/b/id/123-M.jpg",
            "https://books.google.com/thumbnail?id=x",
        ]:
            result = _format_cover_display(url)
            for banned in ("large", "medium", "thumbnail", "cover"):
                self.assertNotIn(banned, result.lower(),
                                 f"'{banned}' found in '{result}' for {url}")

    # --- With bytes: shows source name + dimensions ---

    def test_google_with_jpeg_bytes_shows_dimensions(self):
        result = _format_cover_display(
            "https://books.google.com/x", _make_jpeg_bytes(128, 192)
        )
        self.assertEqual(result, "Google Books · 128×192")

    def test_openlibrary_with_jpeg_bytes_shows_dimensions(self):
        result = _format_cover_display(
            "https://covers.openlibrary.org/b/id/1-L.jpg", _make_jpeg_bytes(500, 750)
        )
        self.assertEqual(result, "Open Library · 500×750")

    def test_embedded_with_png_bytes_shows_dimensions(self):
        result = _format_cover_display("embedded:cover.jpg", _PNG_1X1)
        self.assertEqual(result, "Embedded · 1×1")

    def test_unreadable_bytes_falls_back_to_source_name(self):
        result = _format_cover_display("https://books.google.com/x", b"not an image")
        self.assertEqual(result, "Google Books")

    def test_long_unknown_url_truncated(self):
        url = "https://example.com/" + "x" * 80
        result = _format_cover_display(url)
        self.assertTrue(result.endswith("…"))
        self.assertLessEqual(len(result), 62)


class TestTryPatterns(unittest.TestCase):
    def _rec(self, filepath: str = r"S:\books\Author - Title.epub") -> BookRecord:
        return BookRecord(id=1, filepath=Path(filepath))

    def test_first_matching_pattern_used(self):
        rec = self._rec(r"S:\books\Test Author - Test Title.epub")
        patterns = ["{author} - {title}", "{title} - {author}"]
        _try_patterns(patterns, rec)
        self.assertEqual(rec.pattern_status, "OK")
        self.assertEqual(rec.metadata_pattern.get("author"), "Test Author")

    def test_falls_back_to_second_pattern(self):
        rec = self._rec(r"S:\books\Test Title.epub")
        # First pattern requires " - " separator; won't match "Test Title"
        # Second pattern just captures title
        patterns = ["{author} - {title}", "{title}"]
        _try_patterns(patterns, rec)
        self.assertEqual(rec.pattern_status, "OK")
        self.assertEqual(rec.metadata_pattern.get("title"), "Test Title")

    def test_all_fail_keeps_last_error(self):
        rec = self._rec(r"S:\books\file.epub")
        patterns = ["{author} - {series} {series_index} - {title}"]
        # "file" has no " - " separators, so will fail
        _try_patterns(patterns, rec)
        self.assertNotEqual(rec.pattern_status, "OK")
        self.assertEqual(rec.pattern, patterns[-1])

    def test_empty_patterns_no_op(self):
        rec = self._rec()
        original_meta = dict(rec.metadata_pattern)
        _try_patterns([], rec)
        self.assertEqual(rec.metadata_pattern, original_meta)
        self.assertEqual(rec.pattern, "")

    def test_whitespace_only_patterns_no_op(self):
        rec = self._rec()
        _try_patterns(["  ", ""], rec)
        self.assertEqual(rec.pattern, "")

    def test_first_match_stops_processing(self):
        rec = self._rec(r"S:\books\Test Author - Test Title.epub")
        call_log = []

        with patch("ebookmetafile.gui_constants.pattern_engine.parse_filename") as mock_parse:
            def side_effect(pat, path):
                call_log.append(pat)
                if pat == "{author} - {title}":
                    return {"author": "Test Author", "title": "Test Title"}, "OK"
                return {}, "no match"
            mock_parse.side_effect = side_effect

            _try_patterns(["{author} - {title}", "{title}"], rec)

        self.assertEqual(len(call_log), 1, "second pattern should not be tried after first succeeds")

    def test_sets_chosen_defaults_after_parse(self):
        """_try_patterns should call ensure_chosen_defaults so chosen_metadata is seeded."""
        rec = self._rec(r"S:\books\Test Author - Test Title.epub")
        _try_patterns(["{author} - {title}"], rec)
        # After _try_patterns, chosen_metadata should have been seeded
        self.assertIn("author", rec.chosen_metadata)

    def test_pattern_field_updated(self):
        rec = self._rec(r"S:\books\Test Author - Test Title.epub")
        _try_patterns(["{author} - {title}"], rec)
        self.assertEqual(rec.pattern, "{author} - {title}")


if __name__ == "__main__":
    unittest.main()
