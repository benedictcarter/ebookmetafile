"""Tests for epub_build — is_our_epub, detect_format_state, build_epub."""

import io
import zipfile
from pathlib import Path

import pytest

from ebookmetafile.epub_build import (
    EpubBook,
    EpubChapter,
    EpubImage,
    build_epub,
    detect_format_state,
    is_our_epub,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_third_party_epub(path: Path, epub_version: str = "3.0") -> None:
    """Write a minimal EPUB without our generator tag."""
    with zipfile.ZipFile(path, "w") as zf:
        mi = zipfile.ZipInfo("mimetype")
        mi.compress_type = zipfile.ZIP_STORED
        zf.writestr(mi, "application/epub+zip")
        zf.writestr("META-INF/container.xml", (
            '<?xml version="1.0"?>'
            '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles>'
            '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
            '</rootfiles></container>'
        ))
        zf.writestr("OEBPS/content.opf", (
            f'<?xml version="1.0"?>'
            f'<package version="{epub_version}" xmlns="http://www.idpf.org/2007/opf">'
            f'<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            f'<dc:title>Test</dc:title>'
            f'</metadata>'
            f'<manifest/><spine/></package>'
        ))


def _build_simple(tmp_path: Path, title: str = "Test Book",
                  author: str = "Author", n_chapters: int = 1) -> Path:
    """Build a simple EPUB using build_epub and return its path."""
    dst = tmp_path / "book.epub"
    book = EpubBook(
        title=title,
        author=author,
        chapters=[EpubChapter(title=f"Chapter {i + 1}", body_html=f"<p>Content {i + 1}.</p>")
                  for i in range(n_chapters)],
    )
    err = build_epub(book, dst)
    assert err is None, f"build_epub failed: {err}"
    return dst


# ---------------------------------------------------------------------------
# is_our_epub
# ---------------------------------------------------------------------------

class TestIsOurEpub:
    def test_our_epub_returns_true(self, tmp_path):
        dst = _build_simple(tmp_path)
        assert is_our_epub(dst) is True

    def test_third_party_epub_returns_false(self, tmp_path):
        path = tmp_path / "third.epub"
        _make_third_party_epub(path)
        assert is_our_epub(path) is False

    def test_non_epub_zip_returns_false(self, tmp_path):
        path = tmp_path / "notepub.zip"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("hello.txt", "world")
        assert is_our_epub(path) is False

    def test_corrupt_file_returns_false(self, tmp_path):
        path = tmp_path / "corrupt.epub"
        path.write_bytes(b"not a zip file at all")
        assert is_our_epub(path) is False

    def test_nonexistent_file_returns_false(self, tmp_path):
        path = tmp_path / "missing.epub"
        assert is_our_epub(path) is False


# ---------------------------------------------------------------------------
# detect_format_state
# ---------------------------------------------------------------------------

class TestDetectFormatState:
    def test_mobi(self, tmp_path):
        p = tmp_path / "book.mobi"
        p.touch()
        assert detect_format_state(p) == "MOBI"

    def test_prc(self, tmp_path):
        p = tmp_path / "book.prc"
        p.touch()
        assert detect_format_state(p) == "MOBI"

    def test_azw3(self, tmp_path):
        p = tmp_path / "book.azw3"
        p.touch()
        assert detect_format_state(p) == "AZW3"

    def test_azw(self, tmp_path):
        p = tmp_path / "book.azw"
        p.touch()
        assert detect_format_state(p) == "AZW"

    def test_pdf(self, tmp_path):
        p = tmp_path / "book.pdf"
        p.touch()
        assert detect_format_state(p) == "PDF"

    def test_our_epub(self, tmp_path):
        dst = _build_simple(tmp_path)
        assert detect_format_state(dst) == "EPUB 3.3 ✓"

    def test_third_party_epub3(self, tmp_path):
        path = tmp_path / "third.epub"
        _make_third_party_epub(path, epub_version="3.0")
        assert detect_format_state(path) == "EPUB 3"

    def test_third_party_epub2(self, tmp_path):
        path = tmp_path / "third.epub"
        _make_third_party_epub(path, epub_version="2.0")
        assert detect_format_state(path) == "EPUB 2"

    def test_epub_no_version(self, tmp_path):
        path = tmp_path / "noversionepub.epub"
        with zipfile.ZipFile(path, "w") as zf:
            mi = zipfile.ZipInfo("mimetype")
            mi.compress_type = zipfile.ZIP_STORED
            zf.writestr(mi, "application/epub+zip")
            zf.writestr("META-INF/container.xml", (
                '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                '<rootfiles>'
                '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
                '</rootfiles></container>'
            ))
            zf.writestr("OEBPS/content.opf", (
                '<package xmlns="http://www.idpf.org/2007/opf">'
                '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
                '<dc:title>Test</dc:title></metadata><manifest/><spine/></package>'
            ))
        assert detect_format_state(path) == "EPUB"

    def test_corrupt_epub_returns_epub(self, tmp_path):
        path = tmp_path / "corrupt.epub"
        path.write_bytes(b"garbage")
        assert detect_format_state(path) == "EPUB"

    def test_unknown_extension(self, tmp_path):
        path = tmp_path / "book.lit"
        path.touch()
        assert detect_format_state(path) == "LIT"


# ---------------------------------------------------------------------------
# build_epub — round-trip ZIP structure
# ---------------------------------------------------------------------------

class TestBuildEpub:
    def test_creates_file(self, tmp_path):
        dst = _build_simple(tmp_path)
        assert dst.exists()

    def test_valid_zip(self, tmp_path):
        dst = _build_simple(tmp_path)
        assert zipfile.is_zipfile(dst)

    def test_mimetype_entry(self, tmp_path):
        dst = _build_simple(tmp_path)
        with zipfile.ZipFile(dst) as zf:
            assert "mimetype" in zf.namelist()
            assert zf.read("mimetype") == b"application/epub+zip"

    def test_mimetype_stored_uncompressed(self, tmp_path):
        dst = _build_simple(tmp_path)
        with zipfile.ZipFile(dst) as zf:
            info = zf.getinfo("mimetype")
            assert info.compress_type == zipfile.ZIP_STORED

    def test_container_xml_present(self, tmp_path):
        dst = _build_simple(tmp_path)
        with zipfile.ZipFile(dst) as zf:
            assert "META-INF/container.xml" in zf.namelist()

    def test_opf_present(self, tmp_path):
        dst = _build_simple(tmp_path)
        with zipfile.ZipFile(dst) as zf:
            assert "OEBPS/content.opf" in zf.namelist()

    def test_ncx_present(self, tmp_path):
        dst = _build_simple(tmp_path)
        with zipfile.ZipFile(dst) as zf:
            assert "OEBPS/toc.ncx" in zf.namelist()

    def test_nav_present(self, tmp_path):
        dst = _build_simple(tmp_path)
        with zipfile.ZipFile(dst) as zf:
            assert "OEBPS/nav.xhtml" in zf.namelist()

    def test_chapter_file_present(self, tmp_path):
        dst = _build_simple(tmp_path, n_chapters=1)
        with zipfile.ZipFile(dst) as zf:
            assert "OEBPS/chapter001.xhtml" in zf.namelist()

    def test_multiple_chapters(self, tmp_path):
        dst = _build_simple(tmp_path, n_chapters=3)
        with zipfile.ZipFile(dst) as zf:
            names = zf.namelist()
        assert "OEBPS/chapter001.xhtml" in names
        assert "OEBPS/chapter002.xhtml" in names
        assert "OEBPS/chapter003.xhtml" in names

    def test_opf_contains_title_and_author(self, tmp_path):
        dst = _build_simple(tmp_path, title="My Book", author="Jane Doe")
        with zipfile.ZipFile(dst) as zf:
            opf = zf.read("OEBPS/content.opf").decode()
        assert "My Book" in opf
        assert "Jane Doe" in opf

    def test_opf_contains_generator_tag(self, tmp_path):
        dst = _build_simple(tmp_path)
        with zipfile.ZipFile(dst) as zf:
            opf = zf.read("OEBPS/content.opf").decode()
        assert 'content="ebookmetafile"' in opf

    def test_is_our_epub_after_build(self, tmp_path):
        dst = _build_simple(tmp_path)
        assert is_our_epub(dst) is True

    def test_cover_image_in_manifest_when_provided(self, tmp_path):
        dst = tmp_path / "withcover.epub"
        book = EpubBook(
            title="Cover Book",
            author="Auth",
            chapters=[EpubChapter(title="Ch1", body_html="<p>Hi</p>")],
            cover_data=b"\xff\xd8\xff\xe0" + b"\x00" * 100,  # minimal JPEG-ish bytes
            cover_mime="image/jpeg",
        )
        err = build_epub(book, dst)
        assert err is None
        with zipfile.ZipFile(dst) as zf:
            names = zf.namelist()
            opf = zf.read("OEBPS/content.opf").decode()
        assert "OEBPS/images/cover.jpg" in names
        assert "cover.jpg" in opf

    def test_empty_chapters(self, tmp_path):
        dst = tmp_path / "empty.epub"
        book = EpubBook(title="Empty", author="No One", chapters=[])
        err = build_epub(book, dst)
        assert err is None
        assert dst.exists()
        with zipfile.ZipFile(dst) as zf:
            names = zf.namelist()
        assert "chapter001.xhtml" not in str(names)

    def test_xml_special_chars_in_title(self, tmp_path):
        dst = tmp_path / "special.epub"
        book = EpubBook(
            title="A & B <> C",
            author='Jane "Doc" Smith',
            chapters=[EpubChapter(title="Ch", body_html="<p>x</p>")],
        )
        err = build_epub(book, dst)
        assert err is None
        with zipfile.ZipFile(dst) as zf:
            opf = zf.read("OEBPS/content.opf").decode()
        assert "A &amp; B &lt;&gt; C" in opf
        assert "Jane &quot;Doc&quot; Smith" in opf

    def test_error_returned_on_invalid_path(self, tmp_path):
        dst = tmp_path / "no_such_dir" / "sub" / "book.epub"
        # build_epub creates parent dirs, so this should still succeed
        book = EpubBook(title="T", chapters=[])
        err = build_epub(book, dst)
        assert err is None
        assert dst.exists()
