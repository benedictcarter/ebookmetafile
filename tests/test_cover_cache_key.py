"""Tests for the embedded cover cache key helper."""

import unittest
from pathlib import Path

from ebookmetafile.gui_constants import embedded_cover_cache_key


class TestEmbeddedCoverCacheKey(unittest.TestCase):

    def test_different_books_same_internal_path_differ(self):
        """The collision that caused the original bug: two books sharing OEBPS/cover.jpg."""
        url = "embedded:OEBPS/cover.jpg"
        k1 = embedded_cover_cache_key(Path(r"S:\books\book_a.epub"), url)
        k2 = embedded_cover_cache_key(Path(r"S:\books\book_b.epub"), url)
        self.assertNotEqual(k1, k2)

    def test_same_book_same_url_is_stable(self):
        p = Path(r"S:\books\book.epub")
        url = "embedded:OEBPS/cover.jpg"
        self.assertEqual(
            embedded_cover_cache_key(p, url),
            embedded_cover_cache_key(p, url),
        )

    def test_same_book_different_urls_differ(self):
        p = Path(r"S:\books\book.epub")
        k1 = embedded_cover_cache_key(p, "embedded:OEBPS/cover.jpg")
        k2 = embedded_cover_cache_key(p, "embedded:OEBPS/images/cover.png")
        self.assertNotEqual(k1, k2)

    def test_http_url_still_works_as_key(self):
        """HTTP cover URLs are also used as keys (no collision risk there)."""
        p = Path(r"S:\books\book.epub")
        url = "https://books.google.com/thumbnail?id=abc"
        key = embedded_cover_cache_key(p, url)
        self.assertIn(url, key)

    def test_key_contains_filepath(self):
        p = Path(r"S:\books\mybook.epub")
        key = embedded_cover_cache_key(p, "embedded:cover.jpg")
        self.assertIn("mybook.epub", key)

    def test_key_contains_url(self):
        key = embedded_cover_cache_key(Path(r"S:\b.epub"), "embedded:OEBPS/cover.jpg")
        self.assertIn("OEBPS/cover.jpg", key)

    def test_books_in_different_directories_differ(self):
        url = "embedded:cover.jpg"
        k1 = embedded_cover_cache_key(Path(r"S:\lib_a\book.epub"), url)
        k2 = embedded_cover_cache_key(Path(r"S:\lib_b\book.epub"), url)
        self.assertNotEqual(k1, k2)


if __name__ == "__main__":
    unittest.main()
