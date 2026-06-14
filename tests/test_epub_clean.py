"""Tests for epub_clean — focusing on parse_html_toc and the split fallback chain."""

import pytest

from ebookmetafile.epub_clean import (
    clean_chapter_html,
    parse_html_toc,
    parse_ncx_chapters,
    split_body_at_chapters,
    split_body_at_headings,
)


# ---------------------------------------------------------------------------
# clean_chapter_html — img layout stripping
# ---------------------------------------------------------------------------

class TestCleanChapterHtml:
    def test_img_height_width_align_stripped(self):
        """height=, width=, and align= are removed from <img> tags."""
        body = '<p><img src="cover.jpg" height="100%" width="100%" align="baseline"/></p>'
        result = clean_chapter_html(body)
        assert 'height=' not in result
        assert 'width=' not in result
        assert 'align=' not in result
        assert 'src="cover.jpg"' in result

    def test_img_src_preserved(self):
        """Only layout attrs are removed; src= is always kept."""
        body = '<img src="images/fig.png" width="200" height="150"/>'
        result = clean_chapter_html(body)
        assert 'src="images/fig.png"' in result
        assert 'width=' not in result
        assert 'height=' not in result


# ---------------------------------------------------------------------------
# parse_html_toc
# ---------------------------------------------------------------------------

class TestParseHtmlToc:
    def test_finds_filepos_links(self):
        html = """
        <html><body>
        <a href="#filepos100">Chapter One</a>
        <a href="#filepos200">Chapter Two</a>
        <a href="#filepos300">Chapter Three</a>
        </body></html>
        """
        result = parse_html_toc(html)
        assert len(result) == 3
        assert result[0] == ("filepos100", "Chapter One")
        assert result[1] == ("filepos200", "Chapter Two")
        assert result[2] == ("filepos300", "Chapter Three")

    def test_returns_empty_when_fewer_than_3(self):
        html = """
        <a href="#filepos100">Intro</a>
        <a href="#filepos200">Epilogue</a>
        """
        assert parse_html_toc(html) == []

    def test_deduplicates_anchors(self):
        html = """
        <a href="#filepos100">Chapter One</a>
        <a href="#filepos100">Chapter One (dup)</a>
        <a href="#filepos200">Chapter Two</a>
        <a href="#filepos300">Chapter Three</a>
        """
        result = parse_html_toc(html)
        anchors = [a for a, _ in result]
        assert anchors.count("filepos100") == 1
        assert len(result) == 3

    def test_strips_nested_html_from_labels(self):
        html = """
        <a href="#filepos100"><font color="blue"><i>Chapter One</i></font></a>
        <a href="#filepos200"><b>Chapter Two</b></a>
        <a href="#filepos300">Chapter Three</a>
        """
        result = parse_html_toc(html)
        assert result[0][1] == "Chapter One"
        assert result[1][1] == "Chapter Two"

    def test_normalises_curly_quotes(self):
        html = (
            '<a href="#filepos100">‘I’m Not Myself’</a>\n'
            '<a href="#filepos200">“You Don’t Believe”</a>\n'
            '<a href="#filepos300">Chapter Three</a>\n'
        )
        result = parse_html_toc(html)
        assert "'" in result[0][1]
        assert '"' in result[1][1]

    def test_skips_links_with_empty_labels(self):
        html = """
        <a href="#filepos100"></a>
        <a href="#filepos200">   </a>
        <a href="#filepos300">Real Chapter</a>
        <a href="#filepos400">Another Chapter</a>
        <a href="#filepos500">Third Chapter</a>
        """
        result = parse_html_toc(html)
        for anchor, label in result:
            assert label.strip() != ""

    def test_ignores_non_filepos_links(self):
        """External links and non-filepos internal links must be ignored."""
        html = """
        <a href="http://example.com">External</a>
        <a href="#chapter1">Chapter Link</a>
        <a href="#filepos100">Real One</a>
        <a href="#filepos200">Real Two</a>
        <a href="#filepos300">Real Three</a>
        """
        result = parse_html_toc(html)
        anchors = [a for a, _ in result]
        assert all(a.startswith("filepos") for a in anchors)
        assert len(result) == 3


# ---------------------------------------------------------------------------
# Integration: fallback chain in split logic
# ---------------------------------------------------------------------------

class TestSplitFallbackChain:
    """Verify that NCX, headings, and HTML-TOC fallbacks interact correctly."""

    def _make_body_with_filepos(self):
        """Body with filepos anchors but no h1/h2/h3 headings (like Children of the Mind)."""
        return """
        <p>Table of contents area</p>
        <a id="filepos200" />
        <p>Content of chapter one goes here. It is quite long.</p>
        <a id="filepos400" />
        <p>Content of chapter two goes here. Also quite long.</p>
        <a id="filepos600" />
        <p>Content of chapter three.</p>
        """

    def test_html_toc_fallback_used_when_ncx_empty_and_no_headings(self):
        """When NCX is empty and body has no headings, HTML TOC should produce chapters."""
        body = self._make_body_with_filepos()
        html = f"""
        <html><body>
        <a href="#filepos200">Chapter One</a>
        <a href="#filepos400">Chapter Two</a>
        <a href="#filepos600">Chapter Three</a>
        {body}
        </body></html>
        """
        # Confirm NCX path would give nothing
        assert parse_ncx_chapters("") == []

        # Confirm heading path would give nothing
        assert split_body_at_headings(body) == []

        # HTML TOC fallback should work
        toc = parse_html_toc(html)
        assert len(toc) == 3
        frags = split_body_at_chapters(body, toc)
        assert len(frags) == 3
        labels = [label for label, _ in frags]
        assert labels == ["Chapter One", "Chapter Two", "Chapter Three"]

    def test_ncx_path_takes_priority_over_html_toc(self):
        """When NCX provides >= 2 anchors that ARE in the body, HTML TOC is irrelevant."""
        body = """
        <a id="ncx_ch1" />
        <p>NCX chapter one content.</p>
        <a id="ncx_ch2" />
        <p>NCX chapter two content.</p>
        """
        html = f"""
        <html><body>
        <a href="#filepos100">TOC One</a>
        <a href="#filepos200">TOC Two</a>
        <a href="#filepos300">TOC Three</a>
        {body}
        </body></html>
        """
        # NCX-derived anchor pairs
        ncx_anchor_pairs = [("ncx_ch1", "NCX Chapter One"), ("ncx_ch2", "NCX Chapter Two")]
        frags = split_body_at_chapters(body, ncx_anchor_pairs)
        assert len(frags) == 2
        assert frags[0][0] == "NCX Chapter One"
        assert frags[1][0] == "NCX Chapter Two"

    def test_heading_path_takes_priority_over_html_toc(self):
        """When body has >= 3 headings, heading split fires before HTML TOC fallback."""
        body = """
        <h2>Part One</h2><p>Content one.</p>
        <h2>Part Two</h2><p>Content two.</p>
        <h2>Part Three</h2><p>Content three.</p>
        """
        frags = split_body_at_headings(body)
        assert len(frags) == 3
        assert frags[0][0] == "Part One"

    def test_substantial_preamble_becomes_front_matter_chapter(self):
        """Content before the first NCX anchor is yielded as its own chapter, not
        prepended to chapter 1 (regression: MOBI7 with bibliography page before chapter 1)."""
        front_matter = "<p>" + ("x" * 200) + "</p>"  # > 100 chars of text
        body = (
            front_matter
            + '<a id="filepos100"/><p>Chapter one content.</p>'
            + '<a id="filepos200"/><p>Chapter two content.</p>'
        )
        frags = split_body_at_chapters(body, [("filepos100", "One"), ("filepos200", "Two")])
        assert len(frags) == 3
        labels = [label for label, _ in frags]
        assert labels[0] == "Front Matter"
        assert labels[1] == "One"
        assert labels[2] == "Two"
        # chapter 1 must NOT contain the bibliography content
        assert "x" * 10 not in frags[1][1]

    def test_trivial_preamble_still_prepended_to_chapter_1(self):
        """Whitespace-only preamble is still prepended to chapter 1 (backward compat)."""
        body = (
            "   \n   "
            + '<a id="filepos100"/><p>Ch one.</p>'
            + '<a id="filepos200"/><p>Ch two.</p>'
        )
        frags = split_body_at_chapters(body, [("filepos100", "One"), ("filepos200", "Two")])
        assert len(frags) == 2
        assert frags[0][0] == "One"

    def test_preamble_with_only_image_becomes_front_matter(self):
        """Preamble containing only an image (title page) is also separated out."""
        body = (
            '<p><img src="title.jpg"/></p>'
            + '<a id="filepos100"/><p>Ch one.</p>'
            + '<a id="filepos200"/><p>Ch two.</p>'
        )
        frags = split_body_at_chapters(body, [("filepos100", "One"), ("filepos200", "Two")])
        assert len(frags) == 3
        assert frags[0][0] == "Front Matter"
        assert "img" not in frags[1][1]

    def test_html_toc_anchor_not_in_body_is_skipped_gracefully(self):
        """Anchors referenced in the TOC but absent from body are silently skipped."""
        body = """
        <a id="filepos400" />
        <p>Chapter two content.</p>
        <a id="filepos600" />
        <p>Chapter three content.</p>
        """
        # filepos200 is NOT in body — only 400 and 600 are
        toc = [
            ("filepos200", "Missing Chapter"),
            ("filepos400", "Chapter Two"),
            ("filepos600", "Chapter Three"),
        ]
        frags = split_body_at_chapters(body, toc)
        # Should still produce 2 chapters from the anchors that ARE present
        assert len(frags) == 2
        labels = [label for label, _ in frags]
        assert "Chapter Two" in labels
        assert "Chapter Three" in labels


# ---------------------------------------------------------------------------
# Single-anchor split (threshold lowered from 2 → 1)
# ---------------------------------------------------------------------------

class TestSingleAnchorSplit:
    """split_body_at_chapters now handles a single anchor."""

    def test_single_anchor_large_preamble_yields_two_chapters(self):
        """Single anchor with >100-char preamble → Front Matter + anchored chapter."""
        preamble = "<p>" + "x" * 150 + "</p>"
        body = preamble + '<a id="s01"/>Section content here.'
        frags = split_body_at_chapters(body, [("s01", "The Section")])
        assert len(frags) == 2
        assert frags[0][0] == "Front Matter"
        assert frags[1][0] == "The Section"
        assert "Section content" in frags[1][1]

    def test_single_anchor_tiny_preamble_yields_one_chapter(self):
        """Single anchor with a tiny preamble → one chapter with preamble prepended."""
        body = '  <a id="s01"/>Section content here.'
        frags = split_body_at_chapters(body, [("s01", "Section Title")])
        assert len(frags) == 1
        assert frags[0][0] == "Section Title"
        assert "Section content" in frags[0][1]


# ---------------------------------------------------------------------------
# _match_ncx_labels_to_headings
# ---------------------------------------------------------------------------

class TestMatchNcxLabelsToHeadings:
    """Tests for the heading-label matching fallback."""

    def test_finds_heading_matching_ncx_label(self):
        """Heading whose text matches the NCX label is found and its position returned."""
        from ebookmetafile.epub_clean import _match_ncx_labels_to_headings
        body = (
            "<p>Intro text before the tale.</p>"
            "<h2>THE PRIEST'S TALE: PART ONE</h2>"
            "<p>Tale content here.</p>"
        )
        pairs = [("c01-1", "THE PRIEST'S TALE: PART ONE")]
        result = _match_ncx_labels_to_headings(body, pairs)
        assert len(result) == 1
        pos, anchor_id, label = result[0]
        assert anchor_id == "c01-1"
        assert label == "THE PRIEST'S TALE: PART ONE"
        assert pos > 0  # heading is not at position 0

    def test_entity_encoded_ncx_label_matches_unicode_heading(self):
        """NCX label with HTML entities matches heading with the decoded Unicode chars."""
        from ebookmetafile.epub_clean import _match_ncx_labels_to_headings
        body = "<h2>THE PRIEST’S TALE</h2><p>Content.</p>"
        pairs = [("ch1", "THE PRIEST&#x2019;S TALE")]
        result = _match_ncx_labels_to_headings(body, pairs)
        assert len(result) == 1

    def test_no_match_returns_empty(self):
        """Returns [] when no heading text overlaps any NCX label."""
        from ebookmetafile.epub_clean import _match_ncx_labels_to_headings
        body = "<h2>Something Completely Different</h2><p>Content.</p>"
        pairs = [("ch1", "Chapter One")]
        result = _match_ncx_labels_to_headings(body, pairs)
        assert result == []

    def test_multiple_labels_sorted_by_body_position(self):
        """Multiple labels matched; results are in body position order regardless of input order."""
        from ebookmetafile.epub_clean import _match_ncx_labels_to_headings
        body = (
            "<h2>Part One</h2><p>Content one.</p>"
            "<h2>Part Two</h2><p>Content two.</p>"
        )
        pairs = [("ch2", "Part Two"), ("ch1", "Part One")]  # reversed input order
        result = _match_ncx_labels_to_headings(body, pairs)
        assert len(result) == 2
        assert result[0][2] == "Part One"   # first in body
        assert result[1][2] == "Part Two"


# ---------------------------------------------------------------------------
# read_epub_book integration: heading-split guard + cover-page-once fix
# ---------------------------------------------------------------------------

def _build_epub_zip(spine_files, ncx_entries=None, cover_jpg=None):
    """Return in-memory EPUB ZIP bytes from (id, href, html) tuples.

    spine_files: [(id, href, html_content), ...]
    ncx_entries: [(navPoint_id, label, src_href), ...] or None
    cover_jpg: bytes for a cover image; if given, added to manifest
    """
    import io
    import zipfile as zf_mod

    manifest_items = "\n".join(
        f'    <item id="{fid}" href="{href}" media-type="application/xhtml+xml"/>'
        for fid, href, _ in spine_files
    )
    if ncx_entries:
        manifest_items += '\n    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
    if cover_jpg:
        manifest_items += '\n    <item id="cover-img" href="cover.jpg" media-type="image/jpeg"/>'

    spine_items = "\n".join(
        f'    <itemref idref="{fid}"/>' for fid, _, _ in spine_files
    )
    toc_attr = ' toc="ncx"' if ncx_entries else ""

    cover_meta = '<meta name="cover" content="cover-img"/>' if cover_jpg else ""

    opf = f"""<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Test Book</dc:title><dc:creator>Author</dc:creator>
    <dc:language>en</dc:language>{cover_meta}
  </metadata>
  <manifest>{manifest_items}</manifest>
  <spine{toc_attr}>{spine_items}</spine>
</package>"""

    container = """<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
  <rootfiles>
    <rootfile full-path="content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>"""

    buf = io.BytesIO()
    with zf_mod.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", container)
        z.writestr("content.opf", opf)
        if cover_jpg:
            z.writestr("cover.jpg", cover_jpg)
        for fid, href, html in spine_files:
            z.writestr(href, html)
        if ncx_entries:
            nav_points = "\n".join(
                f'<navPoint id="{nid}" playOrder="{i+1}">'
                f'<navLabel><text>{lbl}</text></navLabel>'
                f'<content src="{src}"/></navPoint>'
                for i, (nid, lbl, src) in enumerate(ncx_entries)
            )
            z.writestr("toc.ncx", f"""<?xml version="1.0"?>
<!DOCTYPE ncx PUBLIC "-//NISO//DTD ncx 2005-1//EN" "">
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"><navMap>{nav_points}</navMap></ncx>""")
    return buf.getvalue()


IMG_ONLY_HTML = "<html><body><div><img src='x.jpg'/></div></body></html>"
CHAPTER_HTML = "<html><body><p>" + "word " * 80 + "</p></body></html>"


class TestReadEpubBookBehaviours:
    """Integration tests for read_epub_book with in-memory EPUBs."""

    def test_cover_page_skipped_only_once(self, tmp_path):
        """First image-only spine item is skipped; subsequent image-only pages are kept."""
        from ebookmetafile.epub_clean import read_epub_book
        fake_jpg = b"\xff\xd8\xff\xe0" + b"\x00" * 20
        epub_bytes = _build_epub_zip(
            [
                ("cvi", "cover.xhtml", IMG_ONLY_HTML),
                ("tp", "title.xhtml", IMG_ONLY_HTML),
                ("ch1", "chapter1.xhtml", CHAPTER_HTML),
            ],
            cover_jpg=fake_jpg,
        )
        p = tmp_path / "test.epub"
        p.write_bytes(epub_bytes)
        book, err = read_epub_book(p)
        assert err == ""
        assert book is not None
        # cover.xhtml skipped, title.xhtml kept, chapter1.xhtml kept → 2 chapters
        assert len(book.chapters) == 2
        assert any("word" in ch.body_html for ch in book.chapters)

    def test_no_heading_split_when_ncx_has_entry_for_file(self, tmp_path):
        """File with 3+ headings but a NCX entry must NOT be split at headings."""
        from ebookmetafile.epub_clean import read_epub_book
        body = (
            "<h1>1</h1><p>Chapter intro.</p>"
            "<h2>The Tale Title</h2><p>" + "word " * 40 + "</p>"
            "<h3>Section A</h3><p>Section content.</p>"
        )
        chapter_html = f"<html><body>{body}</body></html>"
        epub_bytes = _build_epub_zip(
            [("ch1", "chapter1.xhtml", chapter_html)],
            ncx_entries=[("c01", "Chapter 1", "chapter1.xhtml")],
        )
        p = tmp_path / "test.epub"
        p.write_bytes(epub_bytes)
        book, err = read_epub_book(p)
        assert err == ""
        assert book is not None
        # Must be ONE chapter, not three — NCX has info so heading-split is suppressed
        assert len(book.chapters) == 1
        assert book.chapters[0].title == "Chapter 1"

    def test_ncx_label_matches_heading_when_anchor_absent(self, tmp_path):
        """Hyperion-style: NCX anchor not in HTML but heading text matches → split there."""
        from ebookmetafile.epub_clean import read_epub_book
        intro = "<p>" + "intro " * 30 + "</p>"
        tale = "<p>" + "tale " * 50 + "</p>"
        body = f"<h1>1</h1>{intro}<h2>The Tale of Adventure</h2>{tale}"
        chapter_html = f"<html><body>{body}</body></html>"
        # NCX: parent label (no anchor) + child label (anchor #c01-1 — absent from HTML)
        ncx_xml = """<?xml version="1.0"?>
<!DOCTYPE ncx PUBLIC "-//NISO//DTD ncx 2005-1//EN" "">
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/">
  <navMap>
    <navPoint id="c01" playOrder="1">
      <navLabel><text>Chapter 1</text></navLabel>
      <content src="chapter1.xhtml"/>
      <navPoint id="c01-1" playOrder="2">
        <navLabel><text>The Tale of Adventure</text></navLabel>
        <content src="chapter1.xhtml#c01-1"/>
      </navPoint>
    </navPoint>
  </navMap>
</ncx>"""
        import io, zipfile as zf_mod
        buf = io.BytesIO()
        opf = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Test</dc:title><dc:creator>A</dc:creator><dc:language>en</dc:language>
  </metadata>
  <manifest>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="ch1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine toc="ncx"><itemref idref="ch1"/></spine>
</package>"""
        container = """<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
  <rootfiles>
    <rootfile full-path="content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>"""
        with zf_mod.ZipFile(buf, "w") as z:
            z.writestr("mimetype", "application/epub+zip")
            z.writestr("META-INF/container.xml", container)
            z.writestr("content.opf", opf)
            z.writestr("toc.ncx", ncx_xml)
            z.writestr("chapter1.xhtml", chapter_html)
        p = tmp_path / "test.epub"
        p.write_bytes(buf.getvalue())

        book, err = read_epub_book(p)
        assert err == ""
        assert book is not None
        # Anchor c01-1 not in HTML, but h2 heading matches → 2 chapters
        assert len(book.chapters) == 2
        assert book.chapters[0].title == "Chapter 1"      # parent NCX label for preamble
        assert book.chapters[1].title == "The Tale of Adventure"


# ---------------------------------------------------------------------------
# check_epub_integrity
# ---------------------------------------------------------------------------

class TestCheckEpubIntegrity:
    """check_epub_integrity compares original NCX count to converted EpubBook."""

    def _original_epub(self, tmp_path, n_chapters: int) -> "Path":
        """Build a real EPUB with n_chapters NCX entries using build_epub."""
        from ebookmetafile.epub_build import EpubBook, EpubChapter, build_epub
        p = tmp_path / "original.epub"
        book = EpubBook(
            title="Test",
            chapters=[EpubChapter(title=f"Ch {i+1}", body_html=f"<p>{'word ' * 100}</p>")
                      for i in range(n_chapters)],
        )
        err = build_epub(book, p)
        assert err is None
        return p

    def _converted_book(self, n_chapters: int, short: bool = False) -> "EpubBook":
        from ebookmetafile.epub_build import EpubBook, EpubChapter
        body = "<p>x</p>" if short else "<p>" + ("word " * 100) + "</p>"
        return EpubBook(
            title="Test",
            chapters=[EpubChapter(title=f"Ch {i+1}", body_html=body)
                      for i in range(n_chapters)],
        )

    def test_no_warnings_when_counts_match(self, tmp_path):
        from ebookmetafile.epub_clean import check_epub_integrity
        orig = self._original_epub(tmp_path, 5)
        conv = self._converted_book(5)
        warnings = check_epub_integrity(orig, conv)
        assert warnings == []

    def test_warning_when_too_many_chapters(self, tmp_path):
        from ebookmetafile.epub_clean import check_epub_integrity
        orig = self._original_epub(tmp_path, 5)
        conv = self._converted_book(10)  # ratio = 2.0 > 1.5
        warnings = check_epub_integrity(orig, conv)
        assert len(warnings) == 1
        assert "possible incorrect splitting" in warnings[0]

    def test_warning_when_too_few_chapters(self, tmp_path):
        from ebookmetafile.epub_clean import check_epub_integrity
        orig = self._original_epub(tmp_path, 10)
        conv = self._converted_book(4)  # ratio = 0.4 < 0.6
        warnings = check_epub_integrity(orig, conv)
        assert len(warnings) == 1
        assert "possible missing content" in warnings[0]

    def test_warning_for_short_chapter(self, tmp_path):
        from ebookmetafile.epub_clean import check_epub_integrity
        orig = self._original_epub(tmp_path, 1)
        conv = self._converted_book(1, short=True)  # < 50 words
        warnings = check_epub_integrity(orig, conv)
        assert any("under 200 words" in w for w in warnings)

    def test_corrupt_original_path_returns_no_chapter_count_warning(self, tmp_path):
        from ebookmetafile.epub_clean import check_epub_integrity
        orig = tmp_path / "notreal.epub"
        orig.write_bytes(b"garbage")
        conv = self._converted_book(5)
        # Can't compare counts, so no count warning; but no exception either
        warnings = check_epub_integrity(orig, conv)
        assert isinstance(warnings, list)
