"""Tests for file_scan.scan_ebooks."""

from pathlib import Path

import pytest

from ebookmetafile.file_scan import scan_ebooks, EBOOK_EXTENSIONS


class TestScanEbooks:
    def test_finds_epub_files(self, tmp_path):
        (tmp_path / "book.epub").touch()
        result = scan_ebooks(tmp_path)
        assert any(p.name == "book.epub" for p in result)

    def test_finds_all_supported_extensions(self, tmp_path):
        for ext in [".epub", ".mobi", ".azw3", ".azw", ".prc", ".pdf"]:
            (tmp_path / f"book{ext}").touch()
        result = scan_ebooks(tmp_path)
        found_exts = {p.suffix.lower() for p in result}
        assert found_exts == EBOOK_EXTENSIONS

    def test_excludes_non_ebook_files(self, tmp_path):
        (tmp_path / "book.epub").touch()
        (tmp_path / "readme.txt").touch()
        (tmp_path / "cover.jpg").touch()
        (tmp_path / "doc.docx").touch()
        result = scan_ebooks(tmp_path)
        names = [p.name for p in result]
        assert "readme.txt" not in names
        assert "cover.jpg" not in names
        assert "doc.docx" not in names
        assert "book.epub" in names

    def test_scans_subdirectories_recursively(self, tmp_path):
        subdir = tmp_path / "scifi" / "author"
        subdir.mkdir(parents=True)
        (subdir / "book.epub").touch()
        result = scan_ebooks(tmp_path)
        assert any(p.name == "book.epub" for p in result)

    def test_deep_nesting(self, tmp_path):
        deep = tmp_path / "a" / "b" / "c" / "d"
        deep.mkdir(parents=True)
        (deep / "deep.mobi").touch()
        result = scan_ebooks(tmp_path)
        assert any(p.name == "deep.mobi" for p in result)

    def test_empty_directory_returns_empty_list(self, tmp_path):
        result = scan_ebooks(tmp_path)
        assert result == []

    def test_directory_with_only_non_ebooks(self, tmp_path):
        (tmp_path / "notes.txt").touch()
        (tmp_path / "image.png").touch()
        result = scan_ebooks(tmp_path)
        assert result == []

    def test_returns_path_objects(self, tmp_path):
        (tmp_path / "book.epub").touch()
        result = scan_ebooks(tmp_path)
        assert all(isinstance(p, Path) for p in result)

    def test_case_insensitive_extension(self, tmp_path):
        (tmp_path / "book.EPUB").touch()
        (tmp_path / "other.Mobi").touch()
        result = scan_ebooks(tmp_path)
        assert len(result) == 2

    def test_multiple_files_across_subdirs(self, tmp_path):
        (tmp_path / "scifi").mkdir()
        (tmp_path / "fantasy").mkdir()
        (tmp_path / "scifi" / "a.epub").touch()
        (tmp_path / "scifi" / "b.mobi").touch()
        (tmp_path / "fantasy" / "c.pdf").touch()
        result = scan_ebooks(tmp_path)
        assert len(result) == 3

    def test_files_are_not_directories(self, tmp_path):
        (tmp_path / "notafile.epub").mkdir()
        result = scan_ebooks(tmp_path)
        assert result == []
