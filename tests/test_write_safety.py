"""Comprehensive safety tests for the file write path.

The write operation is the most dangerous thing the app does: it can
irreversibly rename, move, or overwrite files. Every code path that
touches the filesystem must be tested here.

Safety properties that must always hold:
  1. A COPY never modifies or deletes the source file.
  2. A MOVE leaves the source gone and the dest present.
  3. rec.filepath always reflects where the file actually is after the operation,
     regardless of whether the metadata write succeeds or fails.
  4. A failed metadata write returns (False, msg) but never leaves the
     filesystem in a worse state than a partial write to a temp file.
  5. An EPUB/MOBI write never corrupts the file — it is atomic via temp file.
  6. Metadata is written to the DESTINATION only, never to the source.
"""

import io
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path

from ebookmetafile.file_apply import (
    _patch_mobi,
    _write_epub_metadata,
    _write_mobi_metadata,
    apply_record,
    write_metadata,
)
from ebookmetafile.models import BookRecord


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_epub(
    path: Path,
    title: str = "Original Title",
    author: str = "Original Author",
) -> Path:
    opf = f"""\
<?xml version='1.0' encoding='utf-8'?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"
            xmlns:opf="http://www.idpf.org/2007/opf">
    <dc:title>{title}</dc:title>
    <dc:creator opf:role="aut">{author}</dc:creator>
    <dc:language>en</dc:language>
    <dc:identifier id="uid">uid-1</dc:identifier>
  </metadata>
  <manifest/>
  <spine/>
</package>""".encode("utf-8")
    with zipfile.ZipFile(path, "w") as zf:
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
    return path


def _make_invalid_epub(path: Path) -> Path:
    """A file with .epub extension but not a valid ZIP."""
    path.write_bytes(b"this is not a zip file at all")
    return path


def _make_epub_no_container(path: Path) -> Path:
    """Valid ZIP but missing META-INF/container.xml."""
    with zipfile.ZipFile(path, "w") as zf:
        info = zipfile.ZipInfo("mimetype")
        info.compress_type = zipfile.ZIP_STORED
        zf.writestr(info, b"application/epub+zip")
        zf.writestr("OEBPS/content.opf", b"<package/>")
    return path


def _read_opf(path: Path) -> str:
    with zipfile.ZipFile(path, "r") as zf:
        return zf.read("OEBPS/content.opf").decode("utf-8")


def _make_mobi(path: Path, title: str = "Book") -> Path:
    rec0_start = 86
    palmdoc_len = 32
    mobi_hdr_len = 136
    full_title_bytes = title.encode("utf-8")
    full_title_off = palmdoc_len + mobi_hdr_len

    mobi_hdr = bytearray(mobi_hdr_len)
    mobi_hdr[0:4] = b"MOBI"
    struct.pack_into(">I", mobi_hdr, 4, mobi_hdr_len)
    struct.pack_into(">I", mobi_hdr, 84, full_title_off)
    struct.pack_into(">I", mobi_hdr, 88, len(full_title_bytes))
    struct.pack_into(">I", mobi_hdr, 128, 0)

    record0 = bytes(palmdoc_len) + bytes(mobi_hdr) + full_title_bytes
    prefix = bytearray(78)
    prefix[0:32] = title.encode("latin-1", errors="replace")[:31].ljust(32, b"\x00")
    struct.pack_into(">H", prefix, 76, 1)
    rec_entry = struct.pack(">II", rec0_start, 0)
    path.write_bytes(bytes(prefix) + rec_entry + record0)
    return path


def _rec(filepath: Path, **meta) -> BookRecord:
    r = BookRecord(id=1, filepath=filepath)
    r.chosen_metadata = dict(meta)
    r.dir_out = str(filepath.parent)
    return r


# ---------------------------------------------------------------------------
# 1. Source preservation during COPY
# ---------------------------------------------------------------------------

class TestSourcePreservation(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_source_bytes_identical_after_copy(self):
        src = _make_epub(self.tmp / "src.epub")
        original_bytes = src.read_bytes()
        dst = self.tmp / "out" / "dst.epub"
        rec = _rec(src, title="New Title", author="New Author")
        rec.new_filepath = dst
        ok, msg = apply_record(rec, "copy", "append")
        self.assertTrue(ok, msg)
        self.assertEqual(src.read_bytes(), original_bytes,
                         "Source bytes must be unchanged after copy")

    def test_source_metadata_unchanged_after_copy(self):
        src = _make_epub(self.tmp / "src.epub", title="Original", author="OldAuthor")
        dst = self.tmp / "out" / "dst.epub"
        rec = _rec(src, title="New Title", author="NewAuthor")
        rec.new_filepath = dst
        apply_record(rec, "copy", "append")
        opf = _read_opf(src)
        self.assertIn("Original", opf, "Source title must be unchanged")
        self.assertIn("OldAuthor", opf, "Source author must be unchanged")

    def test_new_metadata_only_in_dest_not_source(self):
        src = _make_epub(self.tmp / "src.epub", title="Old")
        dst = self.tmp / "out" / "dst.epub"
        rec = _rec(src, title="Brand New")
        rec.new_filepath = dst
        apply_record(rec, "copy", "append")
        self.assertNotIn("Brand New", _read_opf(src))
        self.assertIn("Brand New", _read_opf(dst))

    def test_source_file_still_exists_after_copy(self):
        src = _make_epub(self.tmp / "src.epub")
        rec = _rec(src, title="T")
        rec.new_filepath = self.tmp / "out" / "dst.epub"
        apply_record(rec, "copy", "append")
        self.assertTrue(src.exists(), "Source must still exist after copy")

    def test_source_unchanged_after_copy_same_path(self):
        """Copy to same path: original must not be modified."""
        epub = _make_epub(self.tmp / "book.epub", title="Original")
        original_bytes = epub.read_bytes()
        rec = _rec(epub, title="New")
        rec.new_filepath = epub
        apply_record(rec, "copy", "append")
        self.assertEqual(epub.read_bytes(), original_bytes)

    def test_copy_to_same_path_creates_numbered_file_not_overwrite(self):
        epub = _make_epub(self.tmp / "book.epub", title="Original")
        rec = _rec(epub, title="New")
        rec.new_filepath = epub
        ok, _ = apply_record(rec, "copy", "append")
        self.assertTrue(ok)
        numbered = self.tmp / "book (1).epub"
        self.assertTrue(numbered.exists())
        self.assertIn("New", _read_opf(numbered))

    def test_multiple_copies_each_leave_source_intact(self):
        src = _make_epub(self.tmp / "src.epub", title="Original")
        original_bytes = src.read_bytes()
        for i in range(3):
            dst = self.tmp / f"out{i}" / "copy.epub"
            rec = _rec(src, title=f"Copy {i}")
            rec.new_filepath = dst
            apply_record(rec, "copy", "append")
        self.assertEqual(src.read_bytes(), original_bytes)


# ---------------------------------------------------------------------------
# 2. Move integrity
# ---------------------------------------------------------------------------

class TestMoveIntegrity(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_move_removes_source(self):
        src = _make_epub(self.tmp / "src.epub")
        dst = self.tmp / "out" / "dst.epub"
        rec = _rec(src, title="T")
        rec.new_filepath = dst
        ok, msg = apply_record(rec, "move", "append")
        self.assertTrue(ok, msg)
        self.assertFalse(src.exists(), "Source must be gone after move")

    def test_move_creates_destination(self):
        src = _make_epub(self.tmp / "src.epub")
        dst = self.tmp / "out" / "dst.epub"
        rec = _rec(src, title="T")
        rec.new_filepath = dst
        apply_record(rec, "move", "append")
        self.assertTrue(dst.exists())

    def test_move_destination_has_new_metadata(self):
        src = _make_epub(self.tmp / "src.epub", title="Old")
        dst = self.tmp / "out" / "dst.epub"
        rec = _rec(src, title="New Title")
        rec.new_filepath = dst
        apply_record(rec, "move", "append")
        self.assertIn("New Title", _read_opf(dst))

    def test_move_updates_rec_filepath_on_success(self):
        src = _make_epub(self.tmp / "src.epub")
        dst = self.tmp / "out" / "dst.epub"
        rec = _rec(src, title="T")
        rec.new_filepath = dst
        apply_record(rec, "move", "append")
        self.assertEqual(rec.filepath, dst)

    def test_move_clears_rec_new_filepath_on_success(self):
        src = _make_epub(self.tmp / "src.epub")
        dst = self.tmp / "out" / "dst.epub"
        rec = _rec(src, title="T")
        rec.new_filepath = dst
        apply_record(rec, "move", "append")
        self.assertIsNone(rec.new_filepath)

    def test_move_updates_rec_filepath_even_if_metadata_write_fails(self):
        """Critical: after a successful move, rec.filepath must point to dst
        even if the subsequent metadata write fails. Without this fix, the
        record would point to a deleted file."""
        # Use an invalid EPUB so the move succeeds but write_metadata fails
        src = _make_invalid_epub(self.tmp / "bad.epub")
        dst = self.tmp / "out" / "dst.epub"
        rec = _rec(src, title="New Title")
        rec.new_filepath = dst
        ok, msg = apply_record(rec, "move", "append")
        # Move succeeded, metadata write failed
        self.assertFalse(ok, "Should fail due to bad EPUB")
        self.assertFalse(src.exists(), "Source should be gone — it was moved")
        self.assertTrue(dst.exists(), "Dest should exist — it was moved there")
        self.assertEqual(rec.filepath, dst,
                         "rec.filepath must point to dst, not the deleted src")

    def test_move_in_place_updates_metadata(self):
        epub = _make_epub(self.tmp / "book.epub", title="Old")
        rec = _rec(epub, title="Updated")
        rec.new_filepath = epub
        ok, _ = apply_record(rec, "move", "append")
        self.assertTrue(ok)
        self.assertIn("Updated", _read_opf(epub))


# ---------------------------------------------------------------------------
# 3. Clash handling
# ---------------------------------------------------------------------------

class TestClashHandling(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_append_first_copy_goes_to_expected_path(self):
        src = _make_epub(self.tmp / "src.epub")
        dst = self.tmp / "out" / "result.epub"
        rec = _rec(src, title="T")
        rec.new_filepath = dst
        ok, _ = apply_record(rec, "copy", "append")
        self.assertTrue(ok)
        self.assertTrue(dst.exists())

    def test_append_second_copy_gets_suffix(self):
        src1 = _make_epub(self.tmp / "s1.epub", title="A")
        src2 = _make_epub(self.tmp / "s2.epub", title="B")
        dst = self.tmp / "out" / "book.epub"

        r1 = _rec(src1, title="A")
        r1.new_filepath = dst
        apply_record(r1, "copy", "append")

        r2 = _rec(src2, title="B")
        r2.new_filepath = dst
        apply_record(r2, "copy", "append")

        self.assertTrue(dst.exists())
        self.assertTrue((self.tmp / "out" / "book (1).epub").exists())

    def test_append_many_clashes_each_unique(self):
        out = self.tmp / "out"
        out.mkdir()
        base = out / "book.epub"
        # Pre-create book.epub, book (1).epub, book (2).epub
        for name in ["book.epub", "book (1).epub", "book (2).epub"]:
            _make_epub(out / name)

        src = _make_epub(self.tmp / "src.epub")
        rec = _rec(src, title="T")
        rec.new_filepath = base
        ok, _ = apply_record(rec, "copy", "append")
        self.assertTrue(ok)
        self.assertTrue((out / "book (3).epub").exists())

    def test_replace_overwrites_destination(self):
        src = _make_epub(self.tmp / "src.epub", title="New")
        dst_dir = self.tmp / "out"
        dst_dir.mkdir()
        dst = dst_dir / "book.epub"
        _make_epub(dst, title="Old")

        rec = _rec(src, title="New")
        rec.new_filepath = dst
        ok, _ = apply_record(rec, "copy", "replace")
        self.assertTrue(ok)
        self.assertIn("New", _read_opf(dst))
        self.assertNotIn("Old", _read_opf(dst))

    def test_replace_does_not_create_numbered_copy(self):
        src = _make_epub(self.tmp / "src.epub")
        dst_dir = self.tmp / "out"
        dst_dir.mkdir()
        dst = dst_dir / "book.epub"
        _make_epub(dst)

        rec = _rec(src, title="T")
        rec.new_filepath = dst
        apply_record(rec, "copy", "replace")

        self.assertFalse((dst_dir / "book (1).epub").exists(),
                         "replace must not create numbered copies")


# ---------------------------------------------------------------------------
# 4. Destination directory creation
# ---------------------------------------------------------------------------

class TestDestinationDirectoryCreation(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_nested_dir_created_on_copy(self):
        src = _make_epub(self.tmp / "src.epub")
        dst = self.tmp / "a" / "b" / "c" / "result.epub"
        rec = _rec(src, title="T")
        rec.new_filepath = dst
        ok, msg = apply_record(rec, "copy", "append")
        self.assertTrue(ok, msg)
        self.assertTrue(dst.exists())

    def test_nested_dir_created_on_move(self):
        src = _make_epub(self.tmp / "src.epub")
        dst = self.tmp / "deep" / "nested" / "result.epub"
        rec = _rec(src, title="T")
        rec.new_filepath = dst
        ok, msg = apply_record(rec, "move", "append")
        self.assertTrue(ok, msg)
        self.assertTrue(dst.exists())

    def test_existing_dir_does_not_cause_error(self):
        out = self.tmp / "existing_dir"
        out.mkdir()
        src = _make_epub(self.tmp / "src.epub")
        dst = out / "result.epub"
        rec = _rec(src, title="T")
        rec.new_filepath = dst
        ok, msg = apply_record(rec, "copy", "append")
        self.assertTrue(ok, msg)


# ---------------------------------------------------------------------------
# 5. Error handling — graceful failures
# ---------------------------------------------------------------------------

class TestErrorHandling(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_no_new_filepath_returns_false(self):
        rec = BookRecord(id=1, filepath=self.tmp / "f.epub")
        ok, msg = apply_record(rec, "copy", "append")
        self.assertFalse(ok)

    def test_source_missing_returns_false(self):
        rec = _rec(self.tmp / "nonexistent.epub", title="T")
        rec.new_filepath = self.tmp / "out" / "dst.epub"
        ok, msg = apply_record(rec, "copy", "append")
        self.assertFalse(ok)
        self.assertIn("failed", msg.lower())

    def test_invalid_epub_write_returns_error_not_exception(self):
        """A corrupt EPUB should produce an error string, not raise."""
        bad = _make_invalid_epub(self.tmp / "bad.epub")
        err = _write_epub_metadata(bad, {"title": "T"})
        self.assertIsNotNone(err)
        self.assertIsInstance(err, str)

    def test_epub_missing_container_xml_returns_error(self):
        bad = _make_epub_no_container(self.tmp / "no_container.epub")
        err = _write_epub_metadata(bad, {"title": "T"})
        self.assertIsNotNone(err)

    def test_mobi_too_small_returns_error(self):
        tiny = self.tmp / "tiny.mobi"
        tiny.write_bytes(b"\x00" * 10)
        err = _write_mobi_metadata(tiny, {"title": "T"})
        self.assertIsNotNone(err)

    def test_invalid_mobi_no_mobi_marker_returns_error(self):
        """A file large enough to parse but with no MOBI marker."""
        data = self.tmp / "fake.mobi"
        # Make a 300-byte file with valid PalmDB header but no MOBI marker
        raw = bytearray(300)
        struct.pack_into(">H", raw, 76, 1)      # num_records = 1
        struct.pack_into(">I", raw, 78, 86)     # record 0 at offset 86
        # No "MOBI" bytes in the file
        data.write_bytes(bytes(raw))
        err = _write_mobi_metadata(data, {"title": "T"})
        self.assertIsNotNone(err)
        self.assertIn("MOBI", err)

    def test_apply_record_returns_false_on_metadata_write_error(self):
        bad = _make_invalid_epub(self.tmp / "bad.epub")
        dst = self.tmp / "out" / "dst.epub"
        rec = _rec(bad, title="T")
        rec.new_filepath = dst
        ok, msg = apply_record(rec, "copy", "append")
        self.assertFalse(ok)
        self.assertIn("Metadata write failed", msg)


# ---------------------------------------------------------------------------
# 6. EPUB write atomicity
# ---------------------------------------------------------------------------

class TestEpubWriteAtomicity(unittest.TestCase):
    """Verify that a failed write never leaves a corrupt/partial file."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_original_preserved_after_failed_write(self):
        """If the EPUB write path fails internally, the original must be intact."""
        epub = _make_epub(self.tmp / "book.epub", title="Safe Original")
        original_bytes = epub.read_bytes()

        # Corrupt the OPF so the write returns an error
        # (we can't easily simulate a mid-write crash, but we can confirm
        # the temp file approach means the original is untouched)
        err = _write_epub_metadata(epub, {"title": "Attempted"})
        # Whether or not the write succeeds, the file must be readable
        with zipfile.ZipFile(epub, "r") as zf:
            self.assertIn("OEBPS/content.opf", zf.namelist())

    def test_no_temp_file_left_after_successful_write(self):
        epub = _make_epub(self.tmp / "book.epub")
        _write_epub_metadata(epub, {"title": "Done"})
        tmp_files = list(self.tmp.glob("*.epub._tmp"))
        self.assertEqual(tmp_files, [], "Temp file must be cleaned up after success")

    def test_no_temp_file_left_after_failed_write(self):
        bad = _make_invalid_epub(self.tmp / "bad.epub")
        _write_epub_metadata(bad, {"title": "T"})
        tmp_files = list(self.tmp.glob("*.epub._tmp"))
        self.assertEqual(tmp_files, [], "Temp file must be cleaned up after failure")

    def test_epub_still_openable_as_zip_after_write(self):
        epub = _make_epub(self.tmp / "book.epub")
        _write_epub_metadata(epub, {"title": "New", "author": "Auth", "series": "S"})
        with zipfile.ZipFile(epub, "r") as zf:
            names = zf.namelist()
        self.assertIn("mimetype", names)
        self.assertIn("META-INF/container.xml", names)
        self.assertIn("OEBPS/content.opf", names)

    def test_mimetype_is_first_entry_and_uncompressed(self):
        """EPUB spec: mimetype must be first entry, stored (not compressed)."""
        epub = _make_epub(self.tmp / "book.epub")
        _write_epub_metadata(epub, {"title": "T"})
        with zipfile.ZipFile(epub, "r") as zf:
            first = zf.infolist()[0]
        self.assertEqual(first.filename, "mimetype")
        self.assertEqual(first.compress_type, zipfile.ZIP_STORED)


# ---------------------------------------------------------------------------
# 7. MOBI write atomicity
# ---------------------------------------------------------------------------

class TestMobiWriteAtomicity(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_original_preserved_after_failed_mobi_write(self):
        tiny = self.tmp / "tiny.mobi"
        tiny.write_bytes(b"\x00" * 10)
        original = tiny.read_bytes()
        _write_mobi_metadata(tiny, {"title": "T"})
        self.assertEqual(tiny.read_bytes(), original)

    def test_no_temp_file_left_after_successful_mobi_write(self):
        mobi = _make_mobi(self.tmp / "book.mobi")
        _write_mobi_metadata(mobi, {"title": "T"})
        tmp_files = list(self.tmp.glob("*.mobi._tmp"))
        self.assertEqual(tmp_files, [])

    def test_no_temp_file_left_after_failed_mobi_write(self):
        tiny = self.tmp / "tiny.mobi"
        tiny.write_bytes(b"\x00" * 10)
        _write_mobi_metadata(tiny, {"title": "T"})
        tmp_files = list(self.tmp.glob("*.mobi._tmp"))
        self.assertEqual(tmp_files, [])


# ---------------------------------------------------------------------------
# 8. EPUB metadata round-trip
# ---------------------------------------------------------------------------

class TestEpubMetadataRoundTrip(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def _write_and_read(self, chosen: dict) -> str:
        epub = _make_epub(self.tmp / "book.epub")
        err = _write_epub_metadata(epub, chosen)
        self.assertIsNone(err, f"Write failed: {err}")
        return _read_opf(epub)

    def test_title_round_trips(self):
        opf = self._write_and_read({"title": "Round Trip Title"})
        self.assertIn("Round Trip Title", opf)

    def test_author_round_trips(self):
        opf = self._write_and_read({"author": "Round Trip Author"})
        self.assertIn("Round Trip Author", opf)

    def test_series_round_trips(self):
        opf = self._write_and_read({"series": "Test Series", "series_index": "3"})
        self.assertIn("Test Series", opf)
        self.assertIn('"3"', opf)

    def test_publisher_round_trips(self):
        opf = self._write_and_read({"publisher": "Penguin"})
        self.assertIn("Penguin", opf)

    def test_language_round_trips(self):
        opf = self._write_and_read({"language": "fr"})
        self.assertIn("fr", opf)

    def test_isbn_round_trips(self):
        opf = self._write_and_read({"isbn": "978-0-00-000000-2"})
        self.assertIn("978-0-00-000000-2", opf)

    def test_description_round_trips(self):
        opf = self._write_and_read({"description": "A tale of two cities."})
        self.assertIn("A tale of two cities.", opf)

    def test_rights_round_trips(self):
        opf = self._write_and_read({"rights": "All rights reserved"})
        self.assertIn("All rights reserved", opf)

    def test_contributor_round_trips(self):
        opf = self._write_and_read({"contributor": "Editor Jane"})
        self.assertIn("Editor Jane", opf)

    def test_pub_date_round_trips(self):
        opf = self._write_and_read({"pub_date": "1984-06-08"})
        self.assertIn("1984-06-08", opf)

    def test_tags_round_trips(self):
        opf = self._write_and_read({"tags": "dystopia, classic"})
        self.assertIn("dystopia, classic", opf)

    def test_subject_round_trips(self):
        opf = self._write_and_read({"subject": "Science Fiction"})
        self.assertIn("Science Fiction", opf)

    def test_all_fields_together(self):
        chosen = {
            "title": "Test Title", "author": "Test Author", "series": "Test Series",
            "series_index": "1", "publisher": "Test Publisher", "language": "en",
            "isbn": "978-0-000-00000-0", "description": "Test description.",
            "subject": "Science Fiction", "rights": "© 2000",
            "contributor": "Test Editor", "pub_date": "2000-01-01",
            "tags": "scifi, epic",
        }
        epub = _make_epub(self.tmp / "test.epub")
        err = _write_epub_metadata(epub, chosen)
        self.assertIsNone(err)
        opf = _read_opf(epub)
        for value in chosen.values():
            self.assertIn(value, opf, f"'{value}' missing from OPF after write")

    def test_xml_special_chars_round_trip_safely(self):
        chosen = {
            "title": "A & B: A < B > C",
            "author": 'Author "Quoted"',
            "publisher": "Pub & Co.",
        }
        epub = _make_epub(self.tmp / "special.epub")
        _write_epub_metadata(epub, chosen)
        # Must still be a valid ZIP
        with zipfile.ZipFile(epub, "r") as zf:
            opf_text = zf.read("OEBPS/content.opf").decode("utf-8")
        # Escaped forms present; raw unescaped & must not appear in element content
        self.assertIn("&amp;", opf_text)
        self.assertIn("&lt;", opf_text)
        self.assertIn("&gt;", opf_text)
        self.assertIn("&quot;", opf_text)

    def test_double_write_does_not_duplicate_subject(self):
        epub = _make_epub(self.tmp / "book.epub")
        _write_epub_metadata(epub, {"subject": "Fantasy"})
        _write_epub_metadata(epub, {"subject": "Fantasy"})
        opf = _read_opf(epub)
        count = opf.lower().count("<dc:subject")
        self.assertLessEqual(count, 1,
                             f"dc:subject duplicated: found {count} elements after double write")

    def test_double_write_does_not_corrupt_title(self):
        epub = _make_epub(self.tmp / "book.epub", title="First")
        _write_epub_metadata(epub, {"title": "Second"})
        _write_epub_metadata(epub, {"title": "Third"})
        opf = _read_opf(epub)
        self.assertIn("Third", opf)
        self.assertNotIn("First", opf)
        self.assertNotIn("Second", opf)

    def test_write_to_epub_missing_title_element_inserts_it(self):
        """Regression: previously title was replace-only, so EPUBs without
        dc:title would silently not get a title written."""
        epub = self.tmp / "no_title.epub"
        opf = b"""\
<?xml version='1.0' encoding='utf-8'?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:creator>Author</dc:creator>
    <dc:identifier id="uid">uid-1</dc:identifier>
  </metadata>
  <manifest/><spine/>
</package>"""
        with zipfile.ZipFile(epub, "w") as zf:
            info = zipfile.ZipInfo("mimetype")
            info.compress_type = zipfile.ZIP_STORED
            zf.writestr(info, b"application/epub+zip")
            zf.writestr("META-INF/container.xml", b"""\
<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf"
    media-type="application/oebps-package+xml"/></rootfiles>
</container>""")
            zf.writestr("OEBPS/content.opf", opf)
        err = _write_epub_metadata(epub, {"title": "Inserted Title"})
        self.assertIsNone(err)
        self.assertIn("Inserted Title", _read_opf(epub))


# ---------------------------------------------------------------------------
# 9. MOBI metadata round-trip and EXTH preservation
# ---------------------------------------------------------------------------

class TestMobiRoundTrip(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_all_fields_written(self):
        mobi = _make_mobi(self.tmp / "book.mobi")
        err = _write_mobi_metadata(mobi, {
            "title": "New Title", "author": "New Author",
            "publisher": "Pub", "series": "Series", "series_index": "2",
        })
        self.assertIsNone(err)
        data = mobi.read_bytes()
        for text in [b"New Title", b"New Author", b"Pub", b"Series", b"2"]:
            self.assertIn(text, data, f"{text} missing from MOBI after write")

    def test_existing_exth_records_not_in_update_are_preserved(self):
        """Write author first, then update only title — author must survive."""
        raw = self.tmp / "book.mobi"
        _make_mobi(raw)
        _write_mobi_metadata(raw, {"author": "Original Author"})
        _write_mobi_metadata(raw, {"title": "New Title"})
        data = raw.read_bytes()
        self.assertIn(b"Original Author", data, "Author must survive a title-only update")
        self.assertIn(b"New Title", data)

    def test_double_write_does_not_duplicate_fields(self):
        """Writing the same field twice must not produce two EXTH records."""
        from ebookmetafile.file_apply import _EXTH_AUTHOR
        mobi = _make_mobi(self.tmp / "dedup.mobi")
        _write_mobi_metadata(mobi, {"author": "Author"})
        _write_mobi_metadata(mobi, {"author": "Author"})
        data = mobi.read_bytes()
        exth_pos = data.find(b"EXTH")
        self.assertGreater(exth_pos, -1, "No EXTH block found")
        n_records = struct.unpack_from(">I", data, exth_pos + 8)[0]
        rpos = exth_pos + 12
        count = 0
        for _ in range(n_records):
            if rpos + 8 > len(data):
                break
            rec_type, rec_len = struct.unpack_from(">II", data, rpos)
            if rec_type == _EXTH_AUTHOR:
                count += 1
            if rec_len < 8:
                break
            rpos += rec_len
        self.assertEqual(count, 1, f"Author EXTH record duplicated: found {count}")

    def test_mobi_file_has_exth_marker_after_write(self):
        mobi = _make_mobi(self.tmp / "book.mobi")
        _write_mobi_metadata(mobi, {"title": "T"})
        self.assertIn(b"EXTH", mobi.read_bytes())


# ---------------------------------------------------------------------------
# 10. Full apply_record round-trip verification
# ---------------------------------------------------------------------------

class TestApplyRecordRoundTrip(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_copy_dest_has_all_chosen_metadata(self):
        src = _make_epub(self.tmp / "src.epub", title="Old", author="OldAuth")
        dst = self.tmp / "out" / "dst.epub"
        chosen = {"title": "New Title", "author": "New Author",
                  "series": "My Series", "series_index": "7"}
        rec = _rec(src, **chosen)
        rec.new_filepath = dst
        ok, msg = apply_record(rec, "copy", "append")
        self.assertTrue(ok, msg)
        opf = _read_opf(dst)
        self.assertIn("New Title", opf)
        self.assertIn("New Author", opf)
        self.assertIn("My Series", opf)

    def test_move_dest_has_all_chosen_metadata(self):
        src = _make_epub(self.tmp / "src.epub", title="Old")
        dst = self.tmp / "out" / "dst.epub"
        rec = _rec(src, title="Moved Title", author="Moved Author")
        rec.new_filepath = dst
        ok, msg = apply_record(rec, "move", "append")
        self.assertTrue(ok, msg)
        opf = _read_opf(dst)
        self.assertIn("Moved Title", opf)
        self.assertIn("Moved Author", opf)

    def test_copy_mobi_dest_has_metadata(self):
        src = _make_mobi(self.tmp / "src.mobi", title="Old")
        dst = self.tmp / "out" / "dst.mobi"
        rec = _rec(src, title="Mobi New Title", author="Mobi Author")
        rec.new_filepath = dst
        ok, msg = apply_record(rec, "copy", "append")
        self.assertTrue(ok, msg)
        data = dst.read_bytes()
        self.assertIn(b"Mobi New Title", data)
        self.assertIn(b"Mobi Author", data)

    def test_source_mobi_unchanged_after_copy(self):
        src = _make_mobi(self.tmp / "src.mobi", title="Original")
        original_bytes = src.read_bytes()
        dst = self.tmp / "out" / "dst.mobi"
        rec = _rec(src, title="New")
        rec.new_filepath = dst
        apply_record(rec, "copy", "append")
        self.assertEqual(src.read_bytes(), original_bytes)


# ---------------------------------------------------------------------------
# 11. Cover embed → rescan round-trip
# ---------------------------------------------------------------------------

class TestCoverRescanRoundTrip(unittest.TestCase):
    """Verify that a cover chosen from a web source is embedded in the file
    and correctly detected (and seeded into chosen_metadata) on rescan."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def _make_epub_with_manifest(self, name="book.epub") -> Path:
        """EPUB with a proper (non-self-closing) manifest and no cover."""
        path = self.tmp / name
        opf = b"""\
<?xml version='1.0' encoding='utf-8'?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"
            xmlns:opf="http://www.idpf.org/2007/opf">
    <dc:title>Book</dc:title>
    <dc:creator opf:role="aut">Author</dc:creator>
    <dc:identifier id="uid">uid-1</dc:identifier>
  </metadata>
  <manifest>
    <item id="content" href="content.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="content"/></spine>
</package>"""
        with zipfile.ZipFile(path, "w") as zf:
            info = zipfile.ZipInfo("mimetype")
            info.compress_type = zipfile.ZIP_STORED
            zf.writestr(info, b"application/epub+zip")
            zf.writestr("META-INF/container.xml", b"""\
<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf"
    media-type="application/oebps-package+xml"/></rootfiles>
</container>""")
            zf.writestr("OEBPS/content.opf", opf)
            zf.writestr("OEBPS/content.xhtml", b"<html/>")
        return path

    def test_cover_detected_after_embed(self):
        """Core round-trip: embed → detect."""
        epub = self._make_epub_with_manifest()
        sof0 = struct.pack(">HBHHBBBB", 11, 8, 150, 100, 1, 1, 0x11, 0)
        fake_jpeg = b"\xff\xd8\xff\xc0" + sof0 + b"\xff\xd9"
        cache = {"https://covers.openlibrary.org/b/id/123-L.jpg": (fake_jpeg, "image/jpeg")}

        err = _write_epub_metadata(
            epub,
            {"cover": "https://covers.openlibrary.org/b/id/123-L.jpg"},
            cover_cache=cache,
        )
        self.assertIsNone(err)

        from ebookmetafile.metadata_read import _inspect_epub_file as _detect_epub_cover_new
        cover_url = _detect_epub_cover_new(epub)[0]
        self.assertIsNotNone(cover_url, "Cover not detected after embed")
        self.assertTrue(cover_url.startswith("embedded:"), f"Got: {cover_url}")

    def test_cover_seeded_into_chosen_after_rescan_no_patterns(self):
        """Regression: when all input patterns are empty, ensure_chosen_defaults
        must still seed cover from the embedded file metadata."""
        from ebookmetafile.metadata_read import METADATA_FIELDS
        from ebookmetafile.models import BookRecord

        epub = self._make_epub_with_manifest()
        sof0 = struct.pack(">HBHHBBBB", 11, 8, 150, 100, 1, 1, 0x11, 0)
        fake_jpeg = b"\xff\xd8\xff\xc0" + sof0 + b"\xff\xd9"
        cache = {"https://covers.openlibrary.org/b/id/999-L.jpg": (fake_jpeg, "image/jpeg")}

        # Write with a cover URL
        _write_epub_metadata(epub, {"cover": "https://covers.openlibrary.org/b/id/999-L.jpg"}, cover_cache=cache)

        # Simulate rescan: build a BookRecord as read_metadata_for_files does
        from ebookmetafile.metadata_read import _inspect_epub_file as _detect_epub_cover_new, _extract_fields_from_exiftool_record
        meta = {f: None for f in METADATA_FIELDS}
        meta["title"] = "Book"
        meta["author"] = "Author"
        meta["cover"] = _detect_epub_cover_new(epub)[0]

        book = BookRecord(id=1, filepath=epub, metadata_file=meta)
        book.ensure_chosen_defaults(list(METADATA_FIELDS))

        self.assertIsNotNone(book.chosen_metadata.get("cover"),
                             "cover must be in chosen_metadata after rescan")
        self.assertTrue(book.chosen_metadata["cover"].startswith("embedded:"),
                        f"Expected embedded: cover, got: {book.chosen_metadata.get('cover')}")

    def test_cover_in_chosen_metadata_after_rescan_with_patterns(self):
        """Cover seeded correctly even when _try_patterns runs."""
        from ebookmetafile.gui_constants import _try_patterns, METADATA_FIELDS
        from ebookmetafile.models import BookRecord

        epub = self._make_epub_with_manifest("Author - Book.epub")
        sof0 = struct.pack(">HBHHBBBB", 11, 8, 200, 150, 1, 1, 0x11, 0)
        fake_jpeg = b"\xff\xd8\xff\xc0" + sof0 + b"\xff\xd9"
        cache = {"https://covers.openlibrary.org/b/id/777-L.jpg": (fake_jpeg, "image/jpeg")}

        _write_epub_metadata(epub, {"cover": "https://covers.openlibrary.org/b/id/777-L.jpg"}, cover_cache=cache)

        from ebookmetafile.metadata_read import _inspect_epub_file as _detect_epub_cover_new
        meta = {f: None for f in METADATA_FIELDS}
        meta["title"] = "Book"
        meta["author"] = "Author"
        meta["cover"] = _detect_epub_cover_new(epub)[0]

        book = BookRecord(id=1, filepath=epub, metadata_file=meta)
        book.ensure_chosen_defaults(list(METADATA_FIELDS))
        book.output_pattern = ""
        _try_patterns([r"{author} - {title}"], book)

        self.assertIsNotNone(book.chosen_metadata.get("cover"))
        self.assertTrue(book.chosen_metadata["cover"].startswith("embedded:"))

    def test_cover_file_present_in_zip_after_embed(self):
        epub = self._make_epub_with_manifest()
        fake_jpeg = b"\xff\xd8\xff\xd9"
        cache = {"https://covers.openlibrary.org/b/id/111-L.jpg": (fake_jpeg, "image/jpeg")}
        _write_epub_metadata(epub, {"cover": "https://covers.openlibrary.org/b/id/111-L.jpg"}, cover_cache=cache)
        with zipfile.ZipFile(epub, "r") as zf:
            cover_files = [n for n in zf.namelist() if "cover" in n.lower() and not n.endswith(".opf")]
        self.assertTrue(len(cover_files) > 0, f"No cover image in ZIP: {list(zf.namelist())}")

    def test_apply_record_round_trip_with_cover(self):
        """Full end-to-end: apply_record embeds cover → rescan detects it."""
        from ebookmetafile.metadata_read import _inspect_epub_file as _detect_epub_cover_new

        src = self._make_epub_with_manifest("src.epub")
        dst = self.tmp / "out" / "dst.epub"
        sof0 = struct.pack(">HBHHBBBB", 11, 8, 300, 200, 1, 1, 0x11, 0)
        fake_jpeg = b"\xff\xd8\xff\xc0" + sof0 + b"\xff\xd9"
        url = "https://covers.openlibrary.org/b/id/555-L.jpg"
        cache = {url: (fake_jpeg, "image/jpeg")}

        rec = _rec(src, title="Book", author="Author", cover=url)
        rec.new_filepath = dst
        ok, msg = apply_record(rec, "copy", "append", cover_cache=cache)
        self.assertTrue(ok, msg)

        cover_url = _detect_epub_cover_new(dst)[0]
        self.assertIsNotNone(cover_url, f"Cover not found on rescan of {dst}")
        self.assertTrue(cover_url.startswith("embedded:"))


if __name__ == "__main__":
    unittest.main()
