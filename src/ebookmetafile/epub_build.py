"""Assemble a clean EPUB 3.3 file from structured content.

Everything that touches the ZIP goes here.  Callers (file_convert, file_apply)
are responsible for extracting and cleaning content; this module only knows
about assembly.
"""
from __future__ import annotations

import logging
import re
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

_GENERATOR = "ebookmetafile"

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class EpubImage:
    """An image file to embed under OEBPS/."""
    dest: str       # path relative to OEBPS/, e.g. "images/cover.jpg"
    data: bytes
    mime: str       # "image/jpeg", "image/png", etc.


@dataclass
class EpubChapter:
    """One spine item."""
    title: str          # label shown in the TOC panel
    body_html: str      # clean HTML fragment — body content only, no wrappers


@dataclass
class EpubBook:
    """All content and metadata needed to build an EPUB."""
    title: str
    author: str = ""
    language: str = "en"
    identifier: str = ""    # urn:uuid:… or ISBN; auto-generated if empty

    series: str = ""
    series_index: str = ""
    publisher: str = ""
    pub_date: str = ""
    description: str = ""
    isbn: str = ""
    rights: str = ""
    subject: str = ""
    contributor: str = ""

    chapters: List[EpubChapter] = field(default_factory=list)
    images: List[EpubImage] = field(default_factory=list)

    cover_data: Optional[bytes] = None
    cover_mime: str = "image/jpeg"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def is_our_epub(epub_path: Path) -> bool:
    """Return True if *epub_path* was produced by this tool (has our generator tag)."""
    try:
        with zipfile.ZipFile(epub_path, "r") as zf:
            names = set(zf.namelist())
            container = zf.read("META-INF/container.xml").decode("utf-8", errors="replace")
            m = re.search(r'full-path\s*=\s*["\']([^"\']+)["\']', container)
            if not m or m.group(1) not in names:
                return False
            opf_text = zf.read(m.group(1)).decode("utf-8", errors="replace")
            return f'content="{_GENERATOR}"' in opf_text
    except Exception:
        return False


def detect_format_state(filepath: Path) -> str:
    """Return a short human-readable format state string for *filepath*.

    Values: "MOBI", "AZW3", "AZW", "PDF", "EPUB 2", "EPUB 3", "EPUB 3.3 ✓", "EPUB"
    """
    ext = filepath.suffix.lower()
    if ext == ".mobi":
        return "MOBI"
    if ext == ".azw3":
        return "AZW3"
    if ext == ".azw":
        return "AZW"
    if ext == ".prc":
        return "MOBI"
    if ext == ".pdf":
        return "PDF"
    if ext != ".epub":
        return ext.lstrip(".").upper() or "Unknown"

    try:
        with zipfile.ZipFile(filepath, "r") as zf:
            names = set(zf.namelist())
            if "META-INF/container.xml" not in names:
                return "EPUB"
            container = zf.read("META-INF/container.xml").decode("utf-8", errors="replace")
            m = re.search(r'full-path\s*=\s*["\']([^"\']+)["\']', container)
            if not m or m.group(1) not in names:
                return "EPUB"
            opf_text = zf.read(m.group(1)).decode("utf-8", errors="replace")
            ver_m = re.search(
                r'<package\b[^>]*\bversion\s*=\s*["\']([^"\']+)["\']',
                opf_text, re.IGNORECASE,
            )
            major = (ver_m.group(1).split(".")[0] if ver_m else "")
            if f'content="{_GENERATOR}"' in opf_text:
                return "EPUB 3.3 ✓"
            if major == "3":
                return "EPUB 3"
            if major == "2":
                return "EPUB 2"
            return "EPUB"
    except Exception:
        return "EPUB"



def build_epub(book: EpubBook, dst: Path) -> Optional[str]:
    """Write *book* to *dst* as a clean EPUB 3.3 file.

    Returns an error string on failure, or ``None`` on success.
    """
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_suffix(dst.suffix + "._tmp")
        try:
            _write_zip(book, tmp)
            tmp.replace(dst)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        return None
    except Exception as exc:
        return f"EPUB build failed: {exc}"


# ---------------------------------------------------------------------------
# ZIP assembly
# ---------------------------------------------------------------------------

def _write_zip(book: EpubBook, dst: Path) -> None:
    uid = book.identifier or f"urn:uuid:{uuid.uuid4()}"
    cover_ext = "png" if "png" in book.cover_mime.lower() else "jpg"
    cover_dest = f"images/cover.{cover_ext}"
    has_cover = book.cover_data is not None

    chapter_filenames = [
        f"chapter{i + 1:03d}.xhtml" for i in range(len(book.chapters))
    ]

    with zipfile.ZipFile(dst, "w") as zf:
        # mimetype — must be first and stored uncompressed
        mi = zipfile.ZipInfo("mimetype")
        mi.compress_type = zipfile.ZIP_STORED
        zf.writestr(mi, b"application/epub+zip")

        _write(zf, "META-INF/container.xml", _container_xml())
        _write(zf, "OEBPS/content.opf",
               _opf(book, uid, cover_dest, cover_ext, chapter_filenames, has_cover))
        _write(zf, "OEBPS/nav.xhtml",
               _nav(book, chapter_filenames))
        _write(zf, "OEBPS/toc.ncx",
               _ncx(book, uid, chapter_filenames))
        _write(zf, "OEBPS/style.css", _CSS)

        if has_cover:
            _write(zf, f"OEBPS/{cover_dest}",
                   book.cover_data, compress=False)
            _write(zf, "OEBPS/cover.xhtml",
                   _cover_page(cover_dest))

        for i, ch in enumerate(book.chapters):
            _write(zf, f"OEBPS/{chapter_filenames[i]}",
                   _chapter_xhtml(ch))

        for img in book.images:
            _write(zf, f"OEBPS/{img.dest}", img.data, compress=False)


def _write(zf: zipfile.ZipFile, name: str, content, compress: bool = True) -> None:
    if isinstance(content, str):
        content = content.encode("utf-8")
    zf.writestr(
        name, content,
        compress_type=zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED,
    )


# ---------------------------------------------------------------------------
# File generators
# ---------------------------------------------------------------------------

def _container_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<container version="1.0"'
        ' xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
        '  <rootfiles>\n'
        '    <rootfile full-path="OEBPS/content.opf"'
        ' media-type="application/oebps-package+xml"/>\n'
        '  </rootfiles>\n'
        '</container>'
    )


def _opf(book: EpubBook, uid: str, cover_dest: str, cover_ext: str,
         chapter_filenames: List[str], has_cover: bool) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    title   = _xe(book.title   or "Untitled")
    author  = _xe(book.author  or "Unknown")
    lang    = _xe(book.language or "en")
    uid_val = _xe(uid)
    cover_mime = "image/png" if cover_ext == "png" else "image/jpeg"

    # Build optional metadata lines
    meta_extra = []
    if book.publisher:
        meta_extra.append(f'    <dc:publisher>{_xe(book.publisher)}</dc:publisher>')
    if book.pub_date:
        meta_extra.append(f'    <dc:date>{_xe(book.pub_date)}</dc:date>')
    if book.description:
        meta_extra.append(f'    <dc:description>{_xe(book.description)}</dc:description>')
    if book.rights:
        meta_extra.append(f'    <dc:rights>{_xe(book.rights)}</dc:rights>')
    if book.subject:
        meta_extra.append(f'    <dc:subject>{_xe(book.subject)}</dc:subject>')
    if book.contributor:
        meta_extra.append(f'    <dc:contributor>{_xe(book.contributor)}</dc:contributor>')
    if book.isbn:
        meta_extra.append(
            f'    <dc:identifier opf:scheme="ISBN">{_xe(book.isbn)}</dc:identifier>'
        )
    if book.series:
        # EPUB 3 collection + Calibre extension (most readers understand one or the other)
        meta_extra.append(
            f'    <meta property="belongs-to-collection" id="series-id">'
            f'{_xe(book.series)}</meta>'
        )
        meta_extra.append(
            '    <meta refines="#series-id" property="collection-type">series</meta>'
        )
        if book.series_index:
            meta_extra.append(
                f'    <meta refines="#series-id" property="group-position">'
                f'{_xe(book.series_index)}</meta>'
            )
        meta_extra.append(
            f'    <meta name="calibre:series" content="{_xe(book.series)}"/>'
        )
        if book.series_index:
            meta_extra.append(
                f'    <meta name="calibre:series_index"'
                f' content="{_xe(book.series_index)}"/>'
            )
    extra_meta_block = ("\n" + "\n".join(meta_extra)) if meta_extra else ""

    # Manifest items
    manifest_items = [
        '    <item id="nav" href="nav.xhtml"'
        ' media-type="application/xhtml+xml" properties="nav"/>',
        '    <item id="ncx" href="toc.ncx"'
        ' media-type="application/x-dtbncx+xml"/>',
        '    <item id="css" href="style.css" media-type="text/css"/>',
    ]
    if has_cover:
        manifest_items += [
            f'    <item id="cover-image" href="{cover_dest}"'
            f' media-type="{cover_mime}" properties="cover-image"/>',
            '    <item id="cover-page" href="cover.xhtml"'
            ' media-type="application/xhtml+xml"/>',
        ]
    for i, fn in enumerate(chapter_filenames):
        manifest_items.append(
            f'    <item id="ch{i + 1:03d}" href="{fn}"'
            ' media-type="application/xhtml+xml"/>'
        )
    for img in []:  # book.images are embedded inside chapter bodies via src refs
        pass
    manifest_block = "\n".join(manifest_items)

    # Spine items
    spine_items = []
    if has_cover:
        spine_items.append('    <itemref idref="cover-page"/>')
    spine_items.append('    <itemref idref="nav" linear="no"/>')
    for i in range(len(chapter_filenames)):
        spine_items.append(f'    <itemref idref="ch{i + 1:03d}"/>')
    spine_block = "\n".join(spine_items)

    # Guide
    guide_items = []
    if has_cover:
        guide_items.append(
            '    <reference type="cover" title="Cover" href="cover.xhtml"/>'
        )
    guide_items.append(
        '    <reference type="toc" title="Table of Contents" href="nav.xhtml"/>'
    )
    if chapter_filenames:
        guide_items.append(
            f'    <reference type="text" title="Start of Content"'
            f' href="{chapter_filenames[0]}"/>'
        )
    guide_block = "\n".join(guide_items)

    return f"""\
<?xml version="1.0" encoding="utf-8"?>
<package version="3.0"
    xmlns="http://www.idpf.org/2007/opf"
    xmlns:dc="http://purl.org/dc/elements/1.1/"
    xmlns:opf="http://www.idpf.org/2007/opf"
    unique-identifier="book-id"
    xml:lang="{lang}">
  <metadata>
    <dc:identifier id="book-id">{uid_val}</dc:identifier>
    <dc:title>{title}</dc:title>
    <dc:creator id="creator-01">{author}</dc:creator>
    <dc:language>{lang}</dc:language>
    <meta property="dcterms:modified">{now}</meta>
    <meta name="generator" content="{_GENERATOR}"/>{extra_meta_block}
  </metadata>
  <manifest>
{manifest_block}
  </manifest>
  <spine toc="ncx">
{spine_block}
  </spine>
  <guide>
{guide_block}
  </guide>
</package>"""


def _nav(book: EpubBook, chapter_filenames: List[str]) -> str:
    title = _xe(book.title or "Table of Contents")
    items = "\n".join(
        f'      <li><a href="{fn}">{_xe(ch.title)}</a></li>'
        for fn, ch in zip(chapter_filenames, book.chapters)
    )
    first = chapter_filenames[0] if chapter_filenames else "chapter001.xhtml"
    landmarks = (
        '    <nav epub:type="landmarks" hidden="">\n'
        '      <h2>Landmarks</h2>\n'
        '      <ol>\n'
    )
    if book.cover_data:
        landmarks += '        <li><a epub:type="cover" href="cover.xhtml">Cover</a></li>\n'
    landmarks += (
        '        <li><a epub:type="toc" href="nav.xhtml">Table of Contents</a></li>\n'
        f'        <li><a epub:type="bodymatter" href="{first}">Start of Content</a></li>\n'
        '      </ol>\n'
        '    </nav>'
    )
    return f"""\
<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml"
      xmlns:epub="http://www.idpf.org/2007/ops"
      xml:lang="{_xe(book.language or 'en')}">
<head>
  <meta charset="utf-8"/>
  <title>{title}</title>
  <link rel="stylesheet" href="style.css"/>
</head>
<body epub:type="frontmatter">
  <nav epub:type="toc" id="toc">
    <h1>{title}</h1>
    <ol>
{items}
    </ol>
  </nav>
{landmarks}
</body>
</html>"""


def _ncx(book: EpubBook, uid: str, chapter_filenames: List[str]) -> str:
    title  = _xe(book.title  or "Untitled")
    author = _xe(book.author or "")
    uid_val = _xe(uid)
    depth = "1"
    navpoints = []
    for i, (fn, ch) in enumerate(zip(chapter_filenames, book.chapters), 1):
        navpoints.append(
            f'  <navPoint id="np{i}" playOrder="{i}">\n'
            f'    <navLabel><text>{_xe(ch.title)}</text></navLabel>\n'
            f'    <content src="{fn}"/>\n'
            f'  </navPoint>'
        )
    navmap = "\n".join(navpoints)
    return f"""\
<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE ncx PUBLIC "-//NISO//DTD ncx 2005-1//EN"
    "http://www.daisy.org/z3986/2005/ncx-2005-1.dtd">
<ncx version="2005-1" xmlns="http://www.daisy.org/z3986/2005/ncx/">
<head>
  <meta name="dtb:uid" content="{uid_val}"/>
  <meta name="dtb:depth" content="{depth}"/>
  <meta name="dtb:totalPageCount" content="0"/>
  <meta name="dtb:maxPageNumber" content="0"/>
</head>
<docTitle><text>{title}</text></docTitle>
<docAuthor><text>{author}</text></docAuthor>
<navMap>
{navmap}
</navMap>
</ncx>"""


def _cover_page(cover_dest: str) -> str:
    return f"""\
<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
  <meta charset="utf-8"/>
  <title>Cover</title>
  <style>body{{margin:0;padding:0}} img{{max-width:100%;max-height:100%;display:block;margin:0 auto}}</style>
</head>
<body>
  <img src="{cover_dest}" alt="Cover"/>
</body>
</html>"""


def _chapter_xhtml(ch: EpubChapter) -> str:
    title = _xe(ch.title)
    return f"""\
<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
  <meta charset="utf-8"/>
  <title>{title}</title>
  <link rel="stylesheet" href="style.css"/>
</head>
<body>
{ch.body_html}
</body>
</html>"""


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

_CSS = """\
@charset "UTF-8";

body {
    margin: 0 5%;
    padding: 0;
    font-size: 1em;
    line-height: 1.5;
}

h1, h2, h3, h4, h5, h6 {
    font-weight: bold;
    margin: 1.5em 0 0.5em;
    page-break-after: avoid;
}

p {
    margin: 0.2em 0;
    text-indent: 1.5em;
}

p:first-of-type, p.noindent {
    text-indent: 0;
}

blockquote {
    margin: 0.5em 2em;
}

img {
    max-width: 100%;
    height: auto;
    display: block;
    margin: 0 auto;
}

hr {
    border: none;
    border-top: 1px solid #888;
    margin: 1.5em auto;
    width: 33%;
}

/* Preserve align= attributes from converted content */
p[align="center"], div[align="center"],
p[align="right"],  div[align="right"] {
    text-indent: 0;
}
p[align="center"], div[align="center"] { text-align: center; }
p[align="right"],  div[align="right"]  { text-align: right;  }

table {
    border-collapse: collapse;
    margin: 1em auto;
}

td, th {
    padding: 0.3em 0.5em;
}
"""


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _xe(s: str) -> str:
    """XML-escape a string for use in element content or attribute values."""
    return (s
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))
