import io
import unittest
import zipfile
from pathlib import Path

from ebookmetafile.models import BookRecord
from ebookmetafile.file_apply import make_unique_path, _write_epub_metadata, apply_record


def _make_epub(tmp_path: Path, title: str, author: str, series: str = "") -> Path:
    """Create a minimal valid EPUB file for testing."""
    epub_path = tmp_path / "test.epub"

    container_xml = b"""\
<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf"
              media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>"""

    series_meta = (
        f'    <meta name="calibre:series" content="{series}"/>\n'
        f'    <meta name="calibre:series_index" content="0"/>\n'
        if series
        else ""
    )

    opf_xml = f"""\
<?xml version='1.0' encoding='utf-8'?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"
            xmlns:opf="http://www.idpf.org/2007/opf">
    <dc:title>{title}</dc:title>
    <dc:creator opf:role="aut">{author}</dc:creator>
    <dc:language>en</dc:language>
    <dc:identifier id="uid">test-uid</dc:identifier>
{series_meta}  </metadata>
  <manifest/>
  <spine/>
</package>""".encode("utf-8")

    with zipfile.ZipFile(epub_path, "w") as zf:
        info = zipfile.ZipInfo("mimetype")
        info.compress_type = zipfile.ZIP_STORED
        zf.writestr(info, b"application/epub+zip")
        zf.writestr("META-INF/container.xml", container_xml)
        zf.writestr("OEBPS/content.opf", opf_xml)

    return epub_path


def _read_opf(epub_path: Path) -> str:
    with zipfile.ZipFile(epub_path, "r") as zf:
        return zf.read("OEBPS/content.opf").decode("utf-8")


class TestMakeUniquePath(unittest.TestCase):
    def test_no_clash(self, tmp_path=None):
        p = Path("S:/nonexistent/file.epub")
        self.assertEqual(make_unique_path(p), p)

    def test_clash_appends_suffix(self):
        import tempfile, os
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "book.epub"
            p.touch()
            unique = make_unique_path(p)
            self.assertEqual(unique.name, "book (1).epub")
            self.assertFalse(unique.exists())

    def test_clash_increments(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "book.epub"
            p.touch()
            (Path(td) / "book (1).epub").touch()
            unique = make_unique_path(p)
            self.assertEqual(unique.name, "book (2).epub")


class TestWriteEpubMetadata(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_updates_title_and_author(self):
        epub = _make_epub(self.tmp, "Old Title", "Old Author")
        err = _write_epub_metadata(epub, {"title": "New Title", "author": "New Author"})
        self.assertIsNone(err)
        opf = _read_opf(epub)
        self.assertIn("New Title", opf)
        self.assertIn("New Author", opf)
        self.assertNotIn("Old Title", opf)
        self.assertNotIn("Old Author", opf)

    def test_sets_calibre_series(self):
        epub = _make_epub(self.tmp, "Test Title", "Test Author")
        err = _write_epub_metadata(epub, {"series": "Test Series", "series_index": "1"})
        self.assertIsNone(err)
        opf = _read_opf(epub)
        self.assertIn('calibre:series', opf)
        self.assertIn('Test Series', opf)
        self.assertIn('"1"', opf)

    def test_updates_existing_calibre_series(self):
        epub = _make_epub(self.tmp, "Book", "Author", series="OldSeries")
        err = _write_epub_metadata(epub, {"series": "NewSeries", "series_index": "2"})
        self.assertIsNone(err)
        opf = _read_opf(epub)
        self.assertIn("NewSeries", opf)
        self.assertNotIn("OldSeries", opf)

    def test_epub_remains_valid_zip(self):
        epub = _make_epub(self.tmp, "Title", "Author")
        _write_epub_metadata(epub, {"title": "Updated"})
        # Should still be a readable ZIP with the expected files
        with zipfile.ZipFile(epub, "r") as zf:
            self.assertIn("META-INF/container.xml", zf.namelist())
            self.assertIn("OEBPS/content.opf", zf.namelist())

    def test_empty_chosen_is_noop(self):
        epub = _make_epub(self.tmp, "Original", "Author")
        err = _write_epub_metadata(epub, {})
        self.assertIsNone(err)
        opf = _read_opf(epub)
        self.assertIn("Original", opf)


class TestApplyRecord(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_copy_clash_append_creates_numbered_file(self):
        src1_dir = self.tmp / "s1"
        src2_dir = self.tmp / "s2"
        src1_dir.mkdir()
        src2_dir.mkdir()
        dst_dir = self.tmp / "dst"
        dst = dst_dir / "Author - Title.epub"

        src1 = _make_epub(src1_dir, "Book A", "Author")
        src2 = _make_epub(src2_dir, "Book B", "Author")

        rec1 = BookRecord(id=1, filepath=src1, chosen_metadata={"title": "Title", "author": "Author"})
        rec1.new_filepath = dst
        rec2 = BookRecord(id=2, filepath=src2, chosen_metadata={"title": "Title", "author": "Author"})
        rec2.new_filepath = dst

        ok1, msg1 = apply_record(rec1, "copy", "append", )
        ok2, msg2 = apply_record(rec2, "copy", "append", )

        self.assertTrue(ok1, msg1)
        self.assertTrue(ok2, msg2)
        self.assertTrue(dst.exists(), "first file should be at dst")
        self.assertTrue((dst_dir / "Author - Title (1).epub").exists(), "second file should get (1) suffix")

    def test_copy_same_src_dst_creates_numbered_copy(self):
        epub = _make_epub(self.tmp, "Old Title", "Author")
        rec = BookRecord(id=1, filepath=epub, chosen_metadata={"title": "New Title", "author": "Author"})
        rec.new_filepath = epub  # same path

        ok, msg = apply_record(rec, "copy", "append", )
        self.assertTrue(ok, msg)
        # Original must be untouched
        opf_orig = _read_opf(epub)
        self.assertIn("Old Title", opf_orig)
        # Numbered copy must exist with new metadata
        copy_path = epub.parent / (epub.stem + " (1)" + epub.suffix)
        self.assertTrue(copy_path.exists(), "numbered copy should have been created")
        opf_copy = _read_opf(copy_path)
        self.assertIn("New Title", opf_copy)

    def test_move_same_src_dst_writes_metadata_in_place(self):
        epub = _make_epub(self.tmp, "Old Title", "Author")
        rec = BookRecord(id=1, filepath=epub, chosen_metadata={"title": "New Title", "author": "Author"})
        rec.new_filepath = epub  # same path

        ok, msg = apply_record(rec, "move", "append", )
        self.assertTrue(ok, msg)
        opf = _read_opf(epub)
        self.assertIn("New Title", opf)


if __name__ == "__main__":
    unittest.main()
