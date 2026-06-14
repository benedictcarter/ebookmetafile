"""Extended tests for file_apply — XML escaping, OPF field coverage, MOBI,
cover cache integration, move/replace operations."""

import io
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path

from ebookmetafile.file_apply import (
    _embed_cover_in_epub,
    _escape_xml,
    _patch_mobi,
    _update_opf_text,
    _write_epub_metadata,
    apply_record,
    make_unique_path,
    write_metadata,
)
from ebookmetafile.models import BookRecord


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_epub(
    tmp_path: Path,
    *,
    title: str = "Title",
    author: str = "Author",
    series: str = "",
    subject: str = "",
    publisher: str = "",
    name: str = "test.epub",
) -> Path:
    epub_path = tmp_path / name

    series_meta = (
        f'    <meta name="calibre:series" content="{series}"/>\n'
        f'    <meta name="calibre:series_index" content="1"/>\n'
        if series else ""
    )
    subject_meta = f"    <dc:subject>{subject}</dc:subject>\n" if subject else ""
    publisher_meta = f"    <dc:publisher>{publisher}</dc:publisher>\n" if publisher else ""

    opf = f"""\
<?xml version='1.0' encoding='utf-8'?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"
            xmlns:opf="http://www.idpf.org/2007/opf">
    <dc:title>{title}</dc:title>
    <dc:creator opf:role="aut">{author}</dc:creator>
    <dc:language>en</dc:language>
    <dc:identifier id="uid">uid-1</dc:identifier>
{series_meta}{subject_meta}{publisher_meta}  </metadata>
  <manifest/>
  <spine/>
</package>""".encode("utf-8")

    with zipfile.ZipFile(epub_path, "w") as zf:
        info = zipfile.ZipInfo("mimetype")
        info.compress_type = zipfile.ZIP_STORED
        zf.writestr(info, b"application/epub+zip")
        zf.writestr("META-INF/container.xml", b"""\
<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf"
              media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>""")
        zf.writestr("OEBPS/content.opf", opf)
    return epub_path


def _read_opf(epub_path: Path) -> str:
    with zipfile.ZipFile(epub_path, "r") as zf:
        return zf.read("OEBPS/content.opf").decode("utf-8")


def _make_minimal_mobi(title: str = "Test Book", author: str = "Test Author") -> bytes:
    """Build a minimal valid MOBI binary for testing the MOBI writer."""
    # Layout:
    #   [0:78]   PalmDB header (name in first 32 bytes, num_records at 76)
    #   [78:86]  record list entry (offset=86, attrs=0)
    #   [86:118] record 0: 32-byte PalmDOC header (zeros)
    #   [118:254]  MOBI header (136 bytes, no EXTH)
    #   [254:]   full title
    rec0_start = 86
    palmdoc_len = 32
    mobi_hdr_len = 136

    full_title_bytes = title.encode("utf-8")
    full_title_off = palmdoc_len + mobi_hdr_len   # offset from rec0_start
    full_title_len = len(full_title_bytes)

    mobi_hdr = bytearray(mobi_hdr_len)
    mobi_hdr[0:4] = b"MOBI"
    struct.pack_into(">I", mobi_hdr, 4, mobi_hdr_len)
    struct.pack_into(">I", mobi_hdr, 84, full_title_off)
    struct.pack_into(">I", mobi_hdr, 88, full_title_len)
    struct.pack_into(">I", mobi_hdr, 128, 0)          # EXTH flags = 0 (no EXTH)

    record0 = bytes(palmdoc_len) + bytes(mobi_hdr) + full_title_bytes

    prefix = bytearray(78)
    name_bytes = title.encode("latin-1", errors="replace")[:31].ljust(32, b"\x00")
    prefix[0:32] = name_bytes
    struct.pack_into(">H", prefix, 76, 1)             # num_records

    rec_entry = struct.pack(">II", rec0_start, 0)
    return bytes(prefix) + rec_entry + record0


# ---------------------------------------------------------------------------
# _escape_xml
# ---------------------------------------------------------------------------

class TestEscapeXml(unittest.TestCase):
    def test_ampersand(self):
        self.assertEqual(_escape_xml("A&B"), "A&amp;B")

    def test_less_than(self):
        self.assertEqual(_escape_xml("a<b"), "a&lt;b")

    def test_greater_than(self):
        self.assertEqual(_escape_xml("a>b"), "a&gt;b")

    def test_double_quote(self):
        self.assertEqual(_escape_xml('"quoted"'), "&quot;quoted&quot;")

    def test_plain_text_unchanged(self):
        self.assertEqual(_escape_xml("Hello World"), "Hello World")

    def test_combined(self):
        result = _escape_xml('Title: A & "B" <C>')
        self.assertIn("&amp;", result)
        self.assertIn("&lt;", result)
        self.assertIn("&gt;", result)
        self.assertIn("&quot;", result)


# ---------------------------------------------------------------------------
# _update_opf_text — field coverage and XML safety
# ---------------------------------------------------------------------------

class TestUpdateOpfText(unittest.TestCase):

    def _base_opf(self, title="T", author="A", subject=""):
        subj = f"<dc:subject>{subject}</dc:subject>" if subject else ""
        return f"""\
<?xml version='1.0' encoding='utf-8'?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>{title}</dc:title>
    <dc:creator>{author}</dc:creator>
    {subj}
  </metadata>
</package>"""

    def test_special_chars_in_title_escaped(self):
        opf = _update_opf_text(self._base_opf(), {"title": "A & B < C > D"})
        self.assertIn("A &amp; B &lt; C &gt; D", opf)
        self.assertNotIn("A & B", opf)

    def test_special_chars_in_author_escaped(self):
        opf = _update_opf_text(self._base_opf(), {"author": 'Smith "John"'})
        self.assertIn("Smith &quot;John&quot;", opf)

    def test_publisher_inserted_when_absent(self):
        opf = _update_opf_text(self._base_opf(), {"publisher": "Test Publisher"})
        self.assertIn("Test Publisher", opf)
        self.assertIn("dc:publisher", opf)

    def test_publisher_updated_when_present(self):
        base = self._base_opf() .replace("</metadata>",
            "    <dc:publisher>OldPub</dc:publisher>\n  </metadata>")
        opf = _update_opf_text(base, {"publisher": "NewPub"})
        self.assertIn("NewPub", opf)
        self.assertNotIn("OldPub", opf)

    def test_language_written(self):
        opf = _update_opf_text(self._base_opf(), {"language": "fr"})
        self.assertIn("fr", opf)
        self.assertIn("dc:language", opf)

    def test_rights_written(self):
        opf = _update_opf_text(self._base_opf(), {"rights": "CC BY 4.0"})
        self.assertIn("CC BY 4.0", opf)

    def test_description_written(self):
        opf = _update_opf_text(self._base_opf(), {"description": "A great book."})
        self.assertIn("A great book.", opf)

    def test_isbn_inserted(self):
        opf = _update_opf_text(self._base_opf(), {"isbn": "978-3-16-148410-0"})
        self.assertIn("978-3-16-148410-0", opf)
        self.assertIn('scheme="isbn"', opf.lower())

    def test_isbn_updated_when_present(self):
        base = self._base_opf().replace("</metadata>",
            '    <dc:identifier opf:scheme="isbn">000-0</dc:identifier>\n  </metadata>')
        opf = _update_opf_text(base, {"isbn": "111-1"})
        self.assertIn("111-1", opf)
        self.assertNotIn("000-0", opf)

    def test_tags_written_as_keywords_meta(self):
        opf = _update_opf_text(self._base_opf(), {"tags": "scifi, dystopia"})
        self.assertIn("scifi, dystopia", opf)
        self.assertIn('"keywords"', opf)

    def test_subject_replaces_existing(self):
        opf = _update_opf_text(self._base_opf(subject="OldSubject"), {"subject": "NewSubject"})
        self.assertIn("NewSubject", opf)
        self.assertNotIn("OldSubject", opf)

    def test_subject_not_accumulated_on_double_write(self):
        """Writing subject twice must not result in two dc:subject elements."""
        base = self._base_opf()
        once = _update_opf_text(base, {"subject": "Fantasy"})
        twice = _update_opf_text(once, {"subject": "Fantasy"})
        count = twice.lower().count("dc:subject")
        # Each pair of open+close tags = 2 occurrences; we expect exactly one element
        self.assertLessEqual(count, 2, "subject element duplicated on second write")

    def test_series_inserted_when_absent(self):
        opf = _update_opf_text(self._base_opf(), {"series": "Test Series", "series_index": "1"})
        self.assertIn("Test Series", opf)
        self.assertIn("calibre:series", opf)
        self.assertIn('"1"', opf)

    def test_empty_fields_not_written(self):
        opf = _update_opf_text(self._base_opf(), {"publisher": "", "language": None})
        self.assertNotIn("dc:publisher", opf)

    def test_contributor_written(self):
        opf = _update_opf_text(self._base_opf(), {"contributor": "Editor Joe"})
        self.assertIn("Editor Joe", opf)

    def test_pub_date_written(self):
        opf = _update_opf_text(self._base_opf(), {"pub_date": "2001-01-01"})
        self.assertIn("2001-01-01", opf)


# ---------------------------------------------------------------------------
# Regression: regex-replacement backreference injection (security)
#
# Metadata values can arrive from untrusted web sources or a crafted ebook's
# own fields. Such a value containing a regex-replacement sequence (\1, \g<n>)
# must NOT be interpreted by re.sub when inserting a new OPF/NCX element —
# previously this either crashed the write (re.error) or injected the matched
# </metadata> text into the document.
# ---------------------------------------------------------------------------

class TestOpfReplacementInjection(unittest.TestCase):

    def _empty_metadata_opf(self):
        # No dc:title / dc:subject / calibre:series elements present, so the
        # INSERT branches (the formerly-vulnerable code paths) are exercised.
        return (
            "<package xmlns=\"http://www.idpf.org/2007/opf\" version=\"2.0\">"
            "<metadata xmlns:dc=\"http://purl.org/dc/elements/1.1/\"></metadata>"
            "</package>"
        )

    def test_group_reference_in_title_does_not_crash(self):
        opf = _update_opf_text(self._empty_metadata_opf(), {"title": "Foo \\g<99>"})
        self.assertIn("dc:title", opf)
        # The literal sequence is preserved (XML-escaped), not interpreted.
        self.assertIn("\\g", opf)

    def test_numeric_backreference_in_subject_not_expanded(self):
        opf = _update_opf_text(self._empty_metadata_opf(), {"subject": "Sci \\1 Fi"})
        # The match (</metadata>) must NOT have been spliced into the value.
        self.assertNotIn("Sci </metadata>", opf)
        self.assertIn("Sci \\1 Fi", opf)
        # Document still has exactly one closing metadata tag.
        self.assertEqual(opf.count("</metadata>"), 1)

    def test_backreference_in_series_does_not_crash(self):
        opf = _update_opf_text(self._empty_metadata_opf(), {"series": "S \\g<0>", "series_index": "1"})
        self.assertIn("calibre:series", opf)

    def test_backreference_in_isbn_does_not_crash(self):
        opf = _update_opf_text(self._empty_metadata_opf(), {"isbn": "123 \\7"})
        self.assertIn("opf:scheme=\"isbn\"", opf)
        self.assertEqual(opf.count("</metadata>"), 1)


# ---------------------------------------------------------------------------
# Regression: cover download scheme allowlist (SSRF / local file disclosure)
# ---------------------------------------------------------------------------

class TestDownloadImageSchemeGuard(unittest.TestCase):

    def test_file_scheme_rejected(self):
        from ebookmetafile.file_apply import _download_image
        with self.assertRaises(ValueError):
            _download_image("file:///etc/passwd")

    def test_ftp_scheme_rejected(self):
        from ebookmetafile.file_apply import _download_image
        with self.assertRaises(ValueError):
            _download_image("ftp://example.com/cover.jpg")

    def test_data_scheme_rejected(self):
        from ebookmetafile.file_apply import _download_image
        with self.assertRaises(ValueError):
            _download_image("data:image/png;base64,AAAA")


# ---------------------------------------------------------------------------
# _embed_cover_in_epub
# ---------------------------------------------------------------------------

class TestEmbedCoverInEpub(unittest.TestCase):

    def _make_files(self) -> tuple:
        opf_path = "OEBPS/content.opf"
        opf = b"""\
<?xml version='1.0' encoding='utf-8'?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Test</dc:title>
  </metadata>
  <manifest>
  </manifest>
  <spine/>
</package>"""
        return {"META-INF/container.xml": b"", opf_path: opf}, opf_path

    def test_cover_file_added_to_epub_dict(self):
        files, opf_path = self._make_files()
        cover_bytes = b"FAKEIMAGE"
        _embed_cover_in_epub(files, opf_path, cover_bytes, "image/jpeg")
        self.assertIn("OEBPS/cover.jpg", files)
        self.assertEqual(files["OEBPS/cover.jpg"], cover_bytes)

    def test_manifest_item_added(self):
        files, opf_path = self._make_files()
        _embed_cover_in_epub(files, opf_path, b"IMG", "image/jpeg")
        opf = files[opf_path].decode("utf-8")
        self.assertIn("cover-image", opf)
        self.assertIn("cover.jpg", opf)

    def test_png_uses_png_extension(self):
        files, opf_path = self._make_files()
        _embed_cover_in_epub(files, opf_path, b"IMG", "image/png")
        self.assertIn("OEBPS/cover.png", files)

    def test_duplicate_cover_item_removed_before_insert(self):
        files, opf_path = self._make_files()
        # Pre-insert a cover item that should be replaced
        opf = files[opf_path].decode("utf-8").replace(
            "<manifest>",
            '<manifest>\n    <item id="cover-image" href="old.jpg" media-type="image/jpeg" properties="cover-image"/>'
        )
        files[opf_path] = opf.encode("utf-8")
        _embed_cover_in_epub(files, opf_path, b"NEW", "image/jpeg")
        result = files[opf_path].decode("utf-8")
        self.assertNotIn("old.jpg", result)
        self.assertIn("cover.jpg", result)
        self.assertEqual(result.count("cover-image"), result.lower().count("cover-image"))


# ---------------------------------------------------------------------------
# Cover cache integration in _write_epub_metadata
# ---------------------------------------------------------------------------

class TestWriteEpubCoverCache(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_cover_cache_used_instead_of_downloading(self):
        """When cover_cache has the URL's bytes, no network call should be made."""
        epub = _make_epub(self.tmp)
        # Tiny valid 1x1 JPEG
        fake_jpeg = (
            b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
            b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t"
            b"\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a"
            b"\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342\x1e"
            b"\x1b\x1b\x1b\x1b\x1b\x1b\x1b\x1b\x1b\x1b\x1b\x1b\x1b\x1b\x1b"
            b"\x1b\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4"
            b"\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00"
            b"\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xc4"
            b"\x00\xb5\x10\x00\x02\x01\x03\x03\x02\x04\x03\x05\x05\x04\x04\x00"
            b"\x00\x01}\x01\x02\x03\x00\x04\x11\x05\x12!1A\x06\x13Qa\x07\x14q"
            b"\x82\x91\xa1\x08#B\xb1\xc1\x15R\xd1\xf0$3br\x82\t\n\x16\x17\x18"
            b"\x19\x1a%&'()*456789:CDEFGHIJSTUVWXYZ\xff\xda\x00\x08\x01\x01\x00"
            b"\x00?\x00\xfb\xd1\xff\xd9"
        )
        cache = {"https://example.com/cover.jpg": (fake_jpeg, "image/jpeg")}

        # _download_image should never be called (no network in tests)
        err = _write_epub_metadata(
            epub,
            {"title": "T", "cover": "https://example.com/cover.jpg"},
            cover_cache=cache,
        )
        self.assertIsNone(err)

        # Cover file should be embedded in the EPUB
        with zipfile.ZipFile(epub, "r") as zf:
            names = zf.namelist()
        self.assertTrue(
            any("cover" in n for n in names),
            f"No cover file found in {names}"
        )

    def test_uncached_cover_url_is_skipped_not_downloaded(self):
        """If the cover URL is not in the cache, the write still succeeds and
        no network request is made — cover embedding is simply skipped."""
        epub = _make_epub(self.tmp)
        err = _write_epub_metadata(
            epub,
            {"title": "No Network", "cover": "https://0.0.0.0/nocover.jpg"},
            cover_cache=None,
        )
        self.assertIsNone(err)
        opf = _read_opf(epub)
        self.assertIn("No Network", opf)
        # No cover file added when URL was not cached
        with zipfile.ZipFile(epub, "r") as zf:
            names = zf.namelist()
        self.assertFalse(any("cover" in n.lower() for n in names))

    def test_uncached_cover_skipped_even_with_empty_cache_dict(self):
        """Same as above but with an empty dict rather than None."""
        epub = _make_epub(self.tmp)
        err = _write_epub_metadata(
            epub,
            {"title": "Empty Cache", "cover": "https://0.0.0.0/nocover.jpg"},
            cover_cache={},
        )
        self.assertIsNone(err)
        with zipfile.ZipFile(epub, "r") as zf:
            names = zf.namelist()
        self.assertFalse(any("cover" in n.lower() for n in names))


# ---------------------------------------------------------------------------
# MOBI writer
# ---------------------------------------------------------------------------

class TestMobiWriter(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def _write_mobi(self, chosen: dict, name: str = "book.mobi") -> Path:
        path = self.tmp / name
        path.write_bytes(_make_minimal_mobi())
        from ebookmetafile.file_apply import _write_mobi_metadata
        err = _write_mobi_metadata(path, chosen)
        self.assertIsNone(err, f"_write_mobi_metadata returned error: {err}")
        return path

    def test_title_written_to_full_title_field(self):
        path = self._write_mobi({"title": "New Title"})
        data = path.read_bytes()
        self.assertIn(b"New Title", data)

    def test_author_written_in_exth(self):
        path = self._write_mobi({"author": "Jane Doe"})
        data = path.read_bytes()
        self.assertIn(b"Jane Doe", data)

    def test_series_written_in_exth(self):
        path = self._write_mobi({"series": "Test Series", "series_index": "2"})
        data = path.read_bytes()
        self.assertIn(b"Test Series", data)
        self.assertIn(b"2", data)

    def test_multiple_fields_written(self):
        path = self._write_mobi({
            "title": "Test Title",
            "author": "Test Author",
            "publisher": "Test Publisher",
        })
        data = path.read_bytes()
        self.assertIn(b"Test Title", data)
        self.assertIn(b"Test Author", data)
        self.assertIn(b"Test Publisher", data)

    def test_empty_chosen_returns_none(self):
        """Empty chosen dict should return None (nothing to write)."""
        raw = _make_minimal_mobi()
        result = _patch_mobi(raw, {})
        self.assertIsNone(result)

    def test_output_is_valid_palmdb(self):
        """After patching, file must still look like a PalmDB (MOBI marker present)."""
        raw = _make_minimal_mobi()
        patched = _patch_mobi(raw, {"title": "T", "author": "A"})
        self.assertIsNotNone(patched)
        self.assertIsInstance(patched, bytes)
        self.assertIn(b"MOBI", patched)
        self.assertIn(b"EXTH", patched)

    def test_double_write_does_not_corrupt(self):
        """Writing metadata twice must still produce a valid result."""
        raw = _make_minimal_mobi()
        patched1 = _patch_mobi(raw, {"title": "Round One", "author": "A"})
        self.assertIsInstance(patched1, bytes)
        patched2 = _patch_mobi(patched1, {"title": "Round Two", "author": "A"})
        self.assertIsInstance(patched2, bytes)
        self.assertIn(b"Round Two", patched2)
        self.assertNotIn(b"Round One", patched2)


# ---------------------------------------------------------------------------
# apply_record — move, replace, no new_filepath
# ---------------------------------------------------------------------------

class TestApplyRecordExtended(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_no_new_filepath_returns_false(self):
        epub = _make_epub(self.tmp)
        rec = BookRecord(id=1, filepath=epub, chosen_metadata={"title": "T"})
        # new_filepath not set
        ok, msg = apply_record(rec, "copy", "append")
        self.assertFalse(ok)
        self.assertIn("filepath", msg.lower())

    def test_move_updates_rec_filepath(self):
        src = _make_epub(self.tmp, name="src.epub")
        dst = self.tmp / "dst" / "moved.epub"
        rec = BookRecord(id=1, filepath=src, chosen_metadata={"title": "Moved"})
        rec.new_filepath = dst
        ok, msg = apply_record(rec, "move", "append")
        self.assertTrue(ok, msg)
        self.assertFalse(src.exists(), "source should be gone after move")
        self.assertTrue(dst.exists(), "destination should exist after move")
        self.assertEqual(rec.filepath, dst, "rec.filepath should be updated to dst")
        self.assertIsNone(rec.new_filepath)

    def test_copy_leaves_source_intact(self):
        src = _make_epub(self.tmp, name="src.epub")
        dst = self.tmp / "out" / "copy.epub"
        rec = BookRecord(id=1, filepath=src, chosen_metadata={"title": "Copied"})
        rec.new_filepath = dst
        ok, msg = apply_record(rec, "copy", "append")
        self.assertTrue(ok, msg)
        self.assertTrue(src.exists(), "source must survive a copy")
        self.assertTrue(dst.exists())

    def test_replace_clash_overwrites_destination(self):
        src = _make_epub(self.tmp, title="New", name="src.epub")
        dst_dir = self.tmp / "out"
        dst_dir.mkdir()
        dst = dst_dir / "book.epub"
        # Pre-create destination
        _make_epub(dst_dir, title="Old", name="book.epub")

        rec = BookRecord(id=1, filepath=src, chosen_metadata={"title": "New"})
        rec.new_filepath = dst
        ok, msg = apply_record(rec, "copy", "replace")
        self.assertTrue(ok, msg)
        opf = _read_opf(dst)
        self.assertIn("New", opf)

    def test_cover_cache_passed_through_to_write(self):
        """apply_record passes cover_cache down to write_metadata."""
        epub = _make_epub(self.tmp, name="cover_test.epub")
        fake_jpeg = b"\xff\xd8\xff\xd9"  # minimal 'JPEG'
        cache = {"https://example.com/c.jpg": (fake_jpeg, "image/jpeg")}

        dst = self.tmp / "out" / "result.epub"
        rec = BookRecord(
            id=1,
            filepath=epub,
            chosen_metadata={"title": "T", "cover": "https://example.com/c.jpg"},
        )
        rec.new_filepath = dst
        # Should not raise (no network needed because cache is provided)
        ok, msg = apply_record(rec, "copy", "append", cover_cache=cache)
        self.assertTrue(ok, msg)


# ---------------------------------------------------------------------------
# write_metadata routing
# ---------------------------------------------------------------------------

class TestWriteMetadataRouting(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_epub_routed_to_epub_writer(self):
        epub = _make_epub(self.tmp)
        err = write_metadata(epub, {"title": "Routed"})
        self.assertIsNone(err)
        self.assertIn("Routed", _read_opf(epub))

    def test_mobi_routed_to_mobi_writer(self):
        mobi = self.tmp / "book.mobi"
        mobi.write_bytes(_make_minimal_mobi())
        err = write_metadata(mobi, {"title": "MobiRouted"})
        self.assertIsNone(err)
        self.assertIn(b"MobiRouted", mobi.read_bytes())

    def test_azw3_routed_to_mobi_writer(self):
        azw3 = self.tmp / "book.azw3"
        azw3.write_bytes(_make_minimal_mobi())
        err = write_metadata(azw3, {"title": "AZW3"})
        self.assertIsNone(err)

    def test_unsupported_extension_returns_none(self):
        txt = self.tmp / "book.txt"
        txt.write_text("hello")
        err = write_metadata(txt, {"title": "Ignored"})
        self.assertIsNone(err)


if __name__ == "__main__":
    unittest.main()
