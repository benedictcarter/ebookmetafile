"""Tests for the OPF title/creator upsert fix.

Before the fix, _update_opf_text used replace-only regex for title and author,
so EPUBs missing those elements would silently not have them written.
"""

import unittest
import zipfile
from pathlib import Path
import tempfile

from ebookmetafile.file_apply import _update_opf_text, _write_epub_metadata


def _make_epub_no_title(tmp_path: Path) -> Path:
    """EPUB whose OPF has a creator but no dc:title element."""
    epub = tmp_path / "no_title.epub"
    opf = b"""\
<?xml version='1.0' encoding='utf-8'?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"
            xmlns:opf="http://www.idpf.org/2007/opf">
    <dc:creator opf:role="aut">Existing Author</dc:creator>
    <dc:language>en</dc:language>
    <dc:identifier id="uid">uid-1</dc:identifier>
  </metadata>
  <manifest/>
  <spine/>
</package>"""
    with zipfile.ZipFile(epub, "w") as zf:
        info = zipfile.ZipInfo("mimetype")
        info.compress_type = zipfile.ZIP_STORED
        zf.writestr(info, b"application/epub+zip")
        zf.writestr("META-INF/container.xml", b"""\
<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf"
              media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>""")
        zf.writestr("OEBPS/content.opf", opf)
    return epub


def _make_epub_no_creator(tmp_path: Path) -> Path:
    """EPUB whose OPF has a title but no dc:creator element."""
    epub = tmp_path / "no_creator.epub"
    opf = b"""\
<?xml version='1.0' encoding='utf-8'?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Existing Title</dc:title>
    <dc:language>en</dc:language>
    <dc:identifier id="uid">uid-2</dc:identifier>
  </metadata>
  <manifest/>
  <spine/>
</package>"""
    with zipfile.ZipFile(epub, "w") as zf:
        info = zipfile.ZipInfo("mimetype")
        info.compress_type = zipfile.ZIP_STORED
        zf.writestr(info, b"application/epub+zip")
        zf.writestr("META-INF/container.xml", b"""\
<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf"
              media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>""")
        zf.writestr("OEBPS/content.opf", opf)
    return epub


def _read_opf(epub: Path) -> str:
    with zipfile.ZipFile(epub, "r") as zf:
        return zf.read("OEBPS/content.opf").decode("utf-8")


class TestUpdateOpfTextUpsert(unittest.TestCase):
    """Unit tests directly against _update_opf_text."""

    def _no_title_opf(self):
        return """\
<?xml version='1.0'?>
<package xmlns="http://www.idpf.org/2007/opf">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:creator>Author</dc:creator>
  </metadata>
</package>"""

    def _no_creator_opf(self):
        return """\
<?xml version='1.0'?>
<package xmlns="http://www.idpf.org/2007/opf">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Title</dc:title>
  </metadata>
</package>"""

    def test_title_inserted_when_element_absent(self):
        result = _update_opf_text(self._no_title_opf(), {"title": "New Title"})
        self.assertIn("New Title", result)
        self.assertIn("dc:title", result)

    def test_author_inserted_when_element_absent(self):
        result = _update_opf_text(self._no_creator_opf(), {"author": "New Author"})
        self.assertIn("New Author", result)
        self.assertIn("dc:creator", result)

    def test_title_updated_when_element_present(self):
        opf = """\
<?xml version='1.0'?>
<package xmlns="http://www.idpf.org/2007/opf">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Old Title</dc:title>
    <dc:creator>Author</dc:creator>
  </metadata>
</package>"""
        result = _update_opf_text(opf, {"title": "New Title"})
        self.assertIn("New Title", result)
        self.assertNotIn("Old Title", result)

    def test_author_updated_when_element_present_with_attributes(self):
        """dc:creator with opf:role attribute must be updated, not duplicated."""
        opf = """\
<?xml version='1.0'?>
<package xmlns="http://www.idpf.org/2007/opf">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"
            xmlns:opf="http://www.idpf.org/2007/opf">
    <dc:title>Title</dc:title>
    <dc:creator opf:role="aut">Old Author</dc:creator>
  </metadata>
</package>"""
        result = _update_opf_text(opf, {"author": "New Author"})
        self.assertIn("New Author", result)
        self.assertNotIn("Old Author", result)
        # Should not have created a second creator element
        self.assertEqual(result.lower().count("<dc:creator"), 1)

    def test_both_title_and_author_inserted_in_empty_metadata(self):
        opf = """\
<?xml version='1.0'?>
<package xmlns="http://www.idpf.org/2007/opf">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
  </metadata>
</package>"""
        result = _update_opf_text(opf, {"title": "The Title", "author": "The Author"})
        self.assertIn("The Title", result)
        self.assertIn("The Author", result)


class TestWriteEpubMissingElements(unittest.TestCase):
    """Integration tests: write to real EPUB files missing title/creator."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_title_written_to_epub_missing_dc_title(self):
        epub = _make_epub_no_title(self.tmp)
        err = _write_epub_metadata(epub, {"title": "Written Title"})
        self.assertIsNone(err)
        opf = _read_opf(epub)
        self.assertIn("Written Title", opf)

    def test_author_written_to_epub_missing_dc_creator(self):
        epub = _make_epub_no_creator(self.tmp)
        err = _write_epub_metadata(epub, {"author": "Written Author"})
        self.assertIsNone(err)
        opf = _read_opf(epub)
        self.assertIn("Written Author", opf)

    def test_epub_still_valid_after_inserting_missing_elements(self):
        epub = _make_epub_no_title(self.tmp)
        _write_epub_metadata(epub, {"title": "T", "author": "A"})
        with zipfile.ZipFile(epub, "r") as zf:
            self.assertIn("OEBPS/content.opf", zf.namelist())


if __name__ == "__main__":
    unittest.main()
