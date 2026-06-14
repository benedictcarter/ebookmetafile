"""Integration test: fetch web metadata + cover → write to EPUB → verify.

Requires network access.  Run with the rest of the suite or in isolation:
    python -m pytest tests/test_integration_fetch_write.py -v
"""

import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from ebookmetafile.file_apply import _download_image, _write_epub_metadata
from ebookmetafile.metadata_fetch import fetch_for_record


def _make_epub(tmp_path: Path, title: str = "Placeholder", author: str = "Unknown") -> Path:
    opf = f"""\
<?xml version='1.0' encoding='utf-8'?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"
            xmlns:opf="http://www.idpf.org/2007/opf">
    <dc:title>{title}</dc:title>
    <dc:creator opf:role="aut">{author}</dc:creator>
    <dc:language>en</dc:language>
    <dc:identifier id="uid">test-uid</dc:identifier>
  </metadata>
  <manifest/>
  <spine/>
</package>""".encode("utf-8")

    path = tmp_path / "book.epub"
    with zipfile.ZipFile(path, "w") as zf:
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
    return path


def _read_opf(epub_path: Path) -> str:
    with zipfile.ZipFile(epub_path, "r") as zf:
        return zf.read("OEBPS/content.opf").decode("utf-8")


class TestFetchAndWriteConsiderPhlebas(unittest.TestCase):
    """End-to-end: look up 'Consider Phlebas', download cover, write to EPUB."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_fetch_write_consider_phlebas(self):
        # 1. Fetch metadata from the web
        result = fetch_for_record(1, "Consider Phlebas", "Iain Banks")

        candidate = result.google_top or result.openlibrary_top
        self.assertIsNotNone(
            candidate,
            f"No metadata returned for 'Consider Phlebas' (fetch error: {result.error})",
        )

        # 2. Download the cover image
        self.assertIsNotNone(candidate.cover, "Expected a cover URL in the fetched metadata")
        cover_bytes, cover_mime = _download_image(candidate.cover)
        self.assertGreater(len(cover_bytes), 0, "Cover image download returned empty bytes")
        self.assertIn("image", cover_mime, f"Unexpected MIME type: {cover_mime}")

        # 3. Build chosen metadata and cover cache
        chosen = {
            "title": candidate.title or "Consider Phlebas",
            "author": candidate.author or "Iain M. Banks",
            "cover": candidate.cover,
        }
        cover_cache = {candidate.cover: (cover_bytes, cover_mime)}

        # 4. Create a blank EPUB and write the fetched metadata into it
        epub = _make_epub(self.tmp)
        err = _write_epub_metadata(epub, chosen, cover_cache=cover_cache)
        self.assertIsNone(err, f"Write failed: {err}")

        # 5. Verify text metadata was written
        opf = _read_opf(epub)
        self.assertIn(
            "Consider Phlebas", opf,
            f"Title not found in OPF after write:\n{opf}",
        )
        self.assertIn(
            "Banks", opf,
            f"Author not found in OPF after write:\n{opf}",
        )

        # 6. Verify cover image was embedded
        with zipfile.ZipFile(epub, "r") as zf:
            names = zf.namelist()
            cover_names = [n for n in names if "cover" in n.lower()]
            self.assertTrue(cover_names, f"No cover entry found in EPUB. Files: {names}")

            # The embedded bytes should match what we downloaded
            cover_entry = zf.read(cover_names[0])
        self.assertEqual(
            cover_entry, cover_bytes,
            "Embedded cover bytes do not match the downloaded image",
        )
