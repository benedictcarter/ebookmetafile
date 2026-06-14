"""Tests for metadata_read — EPUB, MOBI, PDF metadata extraction."""

import io
import struct
import zipfile
from pathlib import Path

import pytest

from ebookmetafile.metadata_read import read_metadata_for_files


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_epub(path: Path, opf_text: str, epub_version: str = "3.0") -> None:
    """Write a minimal valid EPUB to *path* with the given OPF content."""
    container = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles>'
        '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
        '</rootfiles></container>'
    )
    with zipfile.ZipFile(path, "w") as zf:
        mi = zipfile.ZipInfo("mimetype")
        mi.compress_type = zipfile.ZIP_STORED
        zf.writestr(mi, "application/epub+zip")
        zf.writestr("META-INF/container.xml", container)
        zf.writestr("OEBPS/content.opf", opf_text)


def _minimal_opf(title: str = "My Title", author: str = "My Author",
                 isbn: str = "", publisher: str = "", language: str = "",
                 series: str = "", series_index: str = "",
                 epub_version: str = "3.0") -> str:
    lines = [
        f'<?xml version="1.0"?>',
        f'<package version="{epub_version}" xmlns="http://www.idpf.org/2007/opf"'
        f' xmlns:opf="http://www.idpf.org/2007/opf">',
        f'<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">',
        f'<dc:title>{title}</dc:title>',
        f'<dc:creator>{author}</dc:creator>',
    ]
    if publisher:
        lines.append(f'<dc:publisher>{publisher}</dc:publisher>')
    if language:
        lines.append(f'<dc:language>{language}</dc:language>')
    if isbn:
        lines.append(f'<dc:identifier opf:scheme="ISBN">{isbn}</dc:identifier>')
    if series:
        lines.append(f'<meta name="calibre:series" content="{series}"/>')
    if series_index:
        lines.append(f'<meta name="calibre:series_index" content="{series_index}"/>')
    lines += ['</metadata>', '<manifest/>', '<spine/>', '</package>']
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# EPUB reading
# ---------------------------------------------------------------------------

class TestReadEpubMetadata:
    def test_reads_title_and_author(self, tmp_path):
        p = tmp_path / "book.epub"
        _write_epub(p, _minimal_opf(title="Great Book", author="John Smith"))
        records = read_metadata_for_files([p])
        assert len(records) == 1
        r = records[0]
        assert r.metadata_file["title"] == "Great Book"
        assert r.metadata_file["author"] == "John Smith"

    def test_reads_publisher(self, tmp_path):
        p = tmp_path / "book.epub"
        _write_epub(p, _minimal_opf(publisher="Acme Press"))
        records = read_metadata_for_files([p])
        assert records[0].metadata_file["publisher"] == "Acme Press"

    def test_reads_isbn(self, tmp_path):
        p = tmp_path / "book.epub"
        _write_epub(p, _minimal_opf(isbn="9781234567890"))
        records = read_metadata_for_files([p])
        assert records[0].metadata_file["isbn"] == "9781234567890"

    def test_reads_language(self, tmp_path):
        p = tmp_path / "book.epub"
        _write_epub(p, _minimal_opf(language="fr"))
        records = read_metadata_for_files([p])
        assert records[0].metadata_file["language"] == "fr"

    def test_reads_calibre_series(self, tmp_path):
        p = tmp_path / "book.epub"
        _write_epub(p, _minimal_opf(series="Test Series", series_index="1"))
        records = read_metadata_for_files([p])
        assert records[0].metadata_file["series"] == "Test Series"
        assert records[0].metadata_file["series_index"] == "1"

    def test_missing_optional_fields_return_none(self, tmp_path):
        p = tmp_path / "book.epub"
        _write_epub(p, _minimal_opf())
        records = read_metadata_for_files([p])
        meta = records[0].metadata_file
        assert meta["isbn"] is None
        assert meta["series"] is None
        assert meta["description"] is None
        assert meta["rights"] is None

    def test_format_state_epub3(self, tmp_path):
        p = tmp_path / "book.epub"
        _write_epub(p, _minimal_opf(epub_version="3.0"))
        records = read_metadata_for_files([p])
        assert records[0].format_state == "EPUB 3"

    def test_format_state_epub2(self, tmp_path):
        p = tmp_path / "book.epub"
        _write_epub(p, _minimal_opf(epub_version="2.0"))
        records = read_metadata_for_files([p])
        assert records[0].format_state == "EPUB 2"

    def test_epub_no_container_xml(self, tmp_path):
        p = tmp_path / "nocontainer.epub"
        with zipfile.ZipFile(p, "w") as zf:
            mi = zipfile.ZipInfo("mimetype")
            mi.compress_type = zipfile.ZIP_STORED
            zf.writestr(mi, "application/epub+zip")
            zf.writestr("OEBPS/content.opf", _minimal_opf())
        records = read_metadata_for_files([p])
        assert len(records) == 1
        meta = records[0].metadata_file
        assert meta["title"] is None

    def test_corrupt_epub_does_not_raise(self, tmp_path):
        p = tmp_path / "corrupt.epub"
        p.write_bytes(b"not a zip at all")
        records = read_metadata_for_files([p])
        assert len(records) == 1

    def test_filepath_stored_on_record(self, tmp_path):
        p = tmp_path / "book.epub"
        _write_epub(p, _minimal_opf(title="T"))
        records = read_metadata_for_files([p])
        assert records[0].filepath == p

    def test_id_assigned(self, tmp_path):
        files = []
        for i in range(3):
            p = tmp_path / f"book{i}.epub"
            _write_epub(p, _minimal_opf(title=f"Book {i}"))
            files.append(p)
        records = read_metadata_for_files(files)
        ids = [r.id for r in records]
        assert ids == [1, 2, 3]

    def test_xml_entity_decoded(self, tmp_path):
        p = tmp_path / "book.epub"
        _write_epub(p, _minimal_opf(title="Tom &amp; Jerry", author="A &lt;B&gt;"))
        records = read_metadata_for_files([p])
        assert records[0].metadata_file["title"] == "Tom & Jerry"
        assert records[0].metadata_file["author"] == "A <B>"


# ---------------------------------------------------------------------------
# PDF reading
# ---------------------------------------------------------------------------

class TestReadPdfMetadata:
    def test_reads_title_and_author(self, tmp_path):
        pytest.importorskip("pypdf")
        import pypdf
        from pypdf import PdfWriter
        p = tmp_path / "book.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        writer.add_metadata({"/Title": "PDF Title", "/Author": "PDF Author"})
        with open(p, "wb") as f:
            writer.write(f)
        records = read_metadata_for_files([p])
        assert records[0].metadata_file["title"] == "PDF Title"
        assert records[0].metadata_file["author"] == "PDF Author"

    def test_corrupt_pdf_does_not_raise(self, tmp_path):
        p = tmp_path / "corrupt.pdf"
        p.write_bytes(b"%PDF-1.4\ngarbage")
        records = read_metadata_for_files([p])
        assert len(records) == 1

    def test_format_state_is_pdf(self, tmp_path):
        p = tmp_path / "book.pdf"
        p.write_bytes(b"%PDF-1.4")
        records = read_metadata_for_files([p])
        assert "PDF" in records[0].format_state


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestReadMetadataEdgeCases:
    def test_empty_file_list(self):
        records = read_metadata_for_files([])
        assert records == []

    def test_nonexistent_file_does_not_raise(self, tmp_path):
        p = tmp_path / "missing.epub"
        records = read_metadata_for_files([p])
        assert len(records) == 1

    def test_progress_callback_called(self, tmp_path):
        p = tmp_path / "book.epub"
        _write_epub(p, _minimal_opf())
        calls = []
        read_metadata_for_files([p], progress_callback=lambda c, t: calls.append((c, t)))
        assert calls == [(1, 1)]

    def test_unknown_extension_no_crash(self, tmp_path):
        p = tmp_path / "book.lit"
        p.write_bytes(b"some bytes")
        records = read_metadata_for_files([p])
        assert len(records) == 1
        assert records[0].format_state == "LIT"
