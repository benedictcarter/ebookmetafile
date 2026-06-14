"""Extract and clean HTML content from existing EPUB or MOBI-derived files.

Public API:
    clean_chapter_html(body_html)        strip Mobipocket / layout-only markup
    parse_ncx_chapters(ncx_text)         NCX navMap → chapter list
    split_body_at_chapters(body, anchors) split body at anchor positions
    read_epub_book(epub_path)            harvest EpubBook from an existing EPUB
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .epub_build import EpubBook, EpubChapter, EpubImage


# ---------------------------------------------------------------------------
# HTML cleaning
# ---------------------------------------------------------------------------

def _convert_font_size_tags(html: str, size_map: dict) -> str:
    """Replace <font size="N"> with <span style="font-size: Xem">, track open/close pairs.

    Uses a stack so that only <font> tags that were converted to <span> emit a
    matching </span>.  <font> tags with no size or size=3 are stripped entirely
    along with their corresponding </font>.
    """
    parts: List[str] = []
    pos = 0
    stack: List[bool] = []  # True = was converted to <span>

    for m in re.finditer(r"<(/?)font\b[^>]*>", html, re.IGNORECASE):
        parts.append(html[pos: m.start()])
        pos = m.end()
        if m.group(1):  # closing tag
            was_span = stack.pop() if stack else False
            parts.append("</span>" if was_span else "")
        else:  # opening tag
            size_m = re.search(r'\bsize\s*=\s*["\']?(\d+)["\']?', m.group(0), re.IGNORECASE)
            em = size_map.get(size_m.group(1)) if size_m else None
            if em:
                parts.append(f'<span style="font-size: {em}">')
                stack.append(True)
            else:
                parts.append("")
                stack.append(False)

    parts.append(html[pos:])
    return "".join(parts)


_BLOCK_TAGS = frozenset({
    "p", "div", "table", "td", "th", "tr", "blockquote",
    "li", "ul", "ol", "section", "article", "pre", "body",
    "h1", "h2", "h3", "h4", "h5", "h6",
})


def clean_chapter_html(body_html: str) -> str:
    """Strip Mobipocket-specific and layout-only markup from a body fragment."""

    # Remove <mbp:*> void/self-closing tags (e.g. <mbp:pagebreak/>)
    body_html = re.sub(r"<mbp:[^>]*/\s*>", "", body_html, flags=re.IGNORECASE)
    # Remove <mbp:*>…</mbp:*> pairs, keeping inner content
    body_html = re.sub(
        r"<mbp:[^>]*>(.*?)</mbp:[^>]*>", r"\1",
        body_html, flags=re.IGNORECASE | re.DOTALL,
    )

    # Remove filepos anchors — internal MOBI navigation artefacts
    # Handles: <a id="filepos123"/>, <a name="filepos123">, <a id="filepos123"></a>
    body_html = re.sub(
        r'<a\b[^>]*\b(?:id|name)\s*=\s*["\']filepos\d+["\'][^>]*/?>(?:</a>)?',
        "", body_html, flags=re.IGNORECASE,
    )

    # Convert <font size="N"> to <span style="font-size: Xem"> so heading/body
    # size distinctions survive the conversion.  size=3 is the normal body size
    # and is stripped.  Other font attributes (face, color) are discarded.
    # Uses a stack to match open/close tags so we never emit orphaned </span>.
    _FONT_SIZE_EM = {
        "1": "0.63em", "2": "0.82em",
        "4": "1.13em", "5": "1.5em", "6": "2.0em", "7": "3.0em",
    }
    body_html = _convert_font_size_tags(body_html, _FONT_SIZE_EM)

    # Remove hidefromncx attributes (Mobipocket reader directive)
    body_html = re.sub(
        r'\s+hidefromncx\s*=\s*(?:"[^"]*"|\'[^\']*\'|\S+)',
        "", body_html, flags=re.IGNORECASE,
    )

    # Strip height= and width= layout attributes from block-level elements only
    def _strip_block_layout(m: re.Match) -> str:
        if m.group(1).lower() in _BLOCK_TAGS:
            return re.sub(
                r'\s+(?:height|width)\s*=\s*(?:"[^"]*"|\'[^\']*\'|\S+)',
                "", m.group(0), flags=re.IGNORECASE,
            )
        return m.group(0)

    body_html = re.sub(
        r"<([a-zA-Z][a-zA-Z0-9]*)\b[^>]*>",
        _strip_block_layout, body_html,
    )

    # Strip layout attributes from <img> tags: height=/width= cause EPUB readers
    # to stretch images to fill the viewport (especially bad with percentage values),
    # and align= is a legacy HTML4 attribute not valid in EPUB XHTML.
    def _strip_img_layout(m: re.Match) -> str:
        tag = m.group(0)
        tag = re.sub(r'\s+(?:height|width|align)\s*=\s*(?:"[^"]*"|\'[^\']*\'|\S+)', "", tag, flags=re.IGNORECASE)
        return tag

    body_html = re.sub(r"<img\b[^>]*>", _strip_img_layout, body_html, flags=re.IGNORECASE)

    return body_html


# ---------------------------------------------------------------------------
# NCX parsing
# ---------------------------------------------------------------------------

def parse_ncx_chapters(
    ncx_text: str,
) -> List[Tuple[str, str, Optional[str]]]:
    """Parse a toc.ncx navMap into a flat chapter list.

    Returns [(label, src_file, anchor_or_None), …] where src_file is the
    bare filename (no directory) and anchor is the fragment id (or None).
    Nested navPoints are flattened to one level.
    """
    import html as _html_mod_ncx
    results: List[Tuple[str, str, Optional[str]]] = []
    for m in re.finditer(
        r"<navPoint\b.*?<navLabel\s*>\s*<text\s*>(.*?)</text\s*>.*?"
        r"<content\s+src=[\"']([^\"']+)[\"']",
        ncx_text, re.DOTALL | re.IGNORECASE,
    ):
        label = _html_mod_ncx.unescape(re.sub(r"<[^>]+>", "", m.group(1)).strip())
        src = m.group(2)
        if "#" in src:
            file_part, anchor = src.rsplit("#", 1)
        else:
            file_part, anchor = src, None
        src_file = file_part.rsplit("/", 1)[-1].lstrip("./")
        results.append((label, src_file, anchor or None))
    return results


def parse_html_toc(html_text: str) -> List[Tuple[str, str]]:
    """Extract chapter structure from an inline HTML TOC (MOBI7 fallback).

    When the NCX is empty, MOBI7 books typically embed a TOC as a list of
    ``<a href="#fileposNNN">`` hyperlinks inside the HTML body.  This function
    finds those links, extracts their labels, and returns them as
    ``[(label, anchor_id), …]`` suitable for passing to
    ``split_body_at_chapters``.

    Only returns a non-empty list when ≥ 3 distinct anchors are found.
    """
    entries: List[Tuple[str, str]] = []
    seen: set = set()
    for m in re.finditer(
        r'<a\b[^>]+href\s*=\s*["\']#(filepos\d+)["\'][^>]*>(.*?)</a>',
        html_text, re.IGNORECASE | re.DOTALL,
    ):
        anchor_id = m.group(1)
        if anchor_id in seen:
            continue
        seen.add(anchor_id)
        label = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        # Normalise curly quotes and other common encoding artefacts
        label = label.replace("‘", "'").replace("’", "'")
        label = label.replace("“", '"').replace("”", '"')
        label = re.sub(r"\s+", " ", label)
        if label:
            entries.append((anchor_id, label))   # (anchor, label) — matches split_body_at_chapters

    return entries if len(entries) >= 3 else []


# ---------------------------------------------------------------------------
# Body splitting
# ---------------------------------------------------------------------------

def split_body_at_headings(
    body: str,
    min_chapters: int = 3,
) -> List[Tuple[str, str]]:
    """Split body at heading tags when the NCX/nav doesn't supply enough anchors.

    Scans for ``<h1>``/``<h2>``/``<h3>`` elements in document order.  If at
    least *min_chapters* headings are found the body is split at each heading
    position and the heading text is used as the chapter label.

    Returns ``[(label, fragment), …]`` or ``[]`` if fewer than *min_chapters*
    headings exist (caller should fall back to treating the body as one chapter).
    """
    positions: List[Tuple[int, str]] = []
    for m in re.finditer(
        r"<(h[123])\b[^>]*>(.*?)</\1\s*>",
        body, re.DOTALL | re.IGNORECASE,
    ):
        label = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        if label:
            positions.append((m.start(), label))

    if len(positions) < min_chapters:
        return []

    preamble = body[: positions[0][0]]
    fragments: List[Tuple[str, str]] = []
    for i, (pos, label) in enumerate(positions):
        end = positions[i + 1][0] if i + 1 < len(positions) else len(body)
        chunk = (preamble + body[pos:end]) if i == 0 else body[pos:end]
        fragments.append((label, chunk))
    return fragments


def split_body_at_chapters(
    body: str,
    chapter_anchors: List[Tuple[str, str]],   # [(anchor_id, label), …]
) -> List[Tuple[str, str]]:
    """Split an HTML body string at chapter anchor positions.

    chapter_anchors is an ordered list of (anchor_id, label).  Anchors are
    located by searching for ``<a id="…">`` or ``<a name="…">`` elements.
    Content before the first found anchor is prepended to the first chapter.

    Returns [(label, fragment), …].  Returns [] when no anchors are found
    in the body (caller should try heading-label matching or whole-file).
    """
    positions: List[Tuple[int, str, str]] = []
    for anchor_id, label in chapter_anchors:
        m = re.search(
            r'<a\b[^>]+\b(?:id|name)\s*=\s*["\']' + re.escape(anchor_id) + r'["\'][^>]*/?>',
            body, re.IGNORECASE,
        )
        if m:
            positions.append((m.start(), anchor_id, label))
    positions.sort()

    if len(positions) < 1:
        return []

    preamble = body[: positions[0][0]]
    fragments: List[Tuple[str, str]] = []

    # If the preamble has substantial content (front matter before the first
    # chapter anchor), yield it as its own chapter rather than attaching it
    # to chapter 1 where it doesn't belong.
    preamble_text = re.sub(r"<[^>]+>", "", preamble).strip()
    preamble_has_image = bool(re.search(r"<img\b", preamble, re.IGNORECASE))
    if len(preamble_text) > 100 or preamble_has_image:
        h_m = re.search(r"<h[1-3]\b[^>]*>(.*?)</h[1-3]\s*>", preamble, re.IGNORECASE | re.DOTALL)
        fm_label = re.sub(r"<[^>]+>", "", h_m.group(1)).strip() if h_m else "Front Matter"
        fragments.append((fm_label or "Front Matter", preamble))
        preamble = ""

    for i, (pos, _anchor_id, label) in enumerate(positions):
        end = positions[i + 1][0] if i + 1 < len(positions) else len(body)
        chunk = (preamble + body[pos:end]) if i == 0 else body[pos:end]
        fragments.append((label, chunk))
    return fragments


def _match_ncx_labels_to_headings(
    body: str,
    anchor_pairs: List[Tuple[str, str]],   # [(anchor_id, label), …]
) -> List[Tuple[int, str, str]]:           # [(position, anchor_id, label)]
    """Locate NCX chapter labels as headings in *body*.

    Used when NCX anchor IDs are not present in the HTML (malformed EPUB).
    For each anchor label, scans all ``<h1>``/``<h2>``/``<h3>`` tags and
    picks the first whose stripped text is a case-insensitive substring of
    the label or vice-versa.

    Returns ``[(position, anchor_id, label)]`` sorted by position, or ``[]``
    when no matches are found.
    """
    import html as _html_mod

    def _norm(s: str) -> str:
        # Decode HTML entities, strip punctuation (apostrophes, quotes, colons,
        # etc. vary between NCX and HTML), then collapse whitespace for comparison.
        text = _html_mod.unescape(s).lower()
        text = re.sub(r"[^a-z0-9 ]", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    heading_candidates: List[Tuple[int, str]] = []
    for m in re.finditer(r"<h[123]\b[^>]*>(.*?)</h[123]>", body, re.DOTALL | re.IGNORECASE):
        text = _norm(re.sub(r"<[^>]+>", "", m.group(1)))
        if text:
            heading_candidates.append((m.start(), text))

    if not heading_candidates:
        return []

    results: List[Tuple[int, str, str]] = []
    used: set = set()
    for anchor_id, label in anchor_pairs:
        label_norm = _norm(label)
        for pos, htext in heading_candidates:
            if pos in used:
                continue
            if label_norm in htext or htext in label_norm:
                results.append((pos, anchor_id, label))
                used.add(pos)
                break

    results.sort()
    return results


# ---------------------------------------------------------------------------
# EPUB reading — harvest EpubBook from an existing EPUB file
# ---------------------------------------------------------------------------

def read_epub_book(epub_path: Path) -> Tuple[Optional[EpubBook], str]:
    """Extract all content from *epub_path* into an :class:`EpubBook`.

    Returns ``(book, "")`` on success or ``(None, error_message)`` on failure.
    Does NOT check whether the file was produced by us — callers should call
    :func:`epub_build.is_our_epub` first when skipping ours is desired.
    """
    try:
        with zipfile.ZipFile(epub_path, "r") as zf:
            return _read_epub_zip(zf, epub_path)
    except zipfile.BadZipFile:
        return None, f"Not a valid ZIP/EPUB: {epub_path.name}"
    except Exception as exc:
        return None, f"Failed to read EPUB: {exc}"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_attrs(tag: str) -> Dict[str, str]:
    """Extract all attribute values from a single XML/HTML tag string."""
    return {
        m.group(1).lower(): m.group(2)
        for m in re.finditer(r'([\w:-]+)\s*=\s*["\']([^"\']*)["\']', tag)
    }


def _dc(opf_text: str, tag: str) -> str:
    """Return the text of the first dc:tag element in *opf_text*."""
    m = re.search(
        r"<dc:" + tag + r"\b[^>]*>(.*?)</dc:" + tag + r">",
        opf_text, re.DOTALL | re.IGNORECASE,
    )
    return re.sub(r"<[^>]+>", "", m.group(1)).strip() if m else ""


def _meta_content(opf_text: str, name: str) -> str:
    """Return content= from <meta name='name' content='…'> in OPF."""
    # Try both attribute orderings
    for pattern in (
        r'<meta\b[^>]*\bname\s*=\s*["\']' + re.escape(name) + r'["\'][^>]*\bcontent\s*=\s*["\']([^"\']*)["\']',
        r'<meta\b[^>]*\bcontent\s*=\s*["\']([^"\']*)["\'][^>]*\bname\s*=\s*["\']' + re.escape(name) + r'["\']',
    ):
        m = re.search(pattern, opf_text, re.IGNORECASE)
        if m:
            return m.group(1)
    return ""


def _resolve_zip_path(href: str, base_href: str, opf_dir: str) -> str:
    """Resolve a relative *href* from *base_href*'s location to a full ZIP path.

    base_href is relative to opf_dir (e.g. ``"text/chapter1.html"``).
    Returns the full ZIP path (e.g. ``"OEBPS/images/fig.jpg"``).
    """
    base_dir = (base_href.rsplit("/", 1)[0] + "/") if "/" in base_href else ""
    raw = base_dir + href
    parts: List[str] = []
    for part in raw.split("/"):
        if part == "..":
            if parts:
                parts.pop()
        elif part and part != ".":
            parts.append(part)
    rel = "/".join(parts)
    return (opf_dir + "/" + rel).lstrip("/") if opf_dir else rel


def _extract_body(html_text: str) -> str:
    """Return the content of the <body> element (or full text if absent)."""
    m = re.search(r"<body\b[^>]*>(.*)</body>", html_text, re.DOTALL | re.IGNORECASE)
    return m.group(1) if m else html_text


def _is_cover_page(body: str) -> bool:
    """Return True if the body is nothing but a cover image (no readable text)."""
    if re.sub(r"<[^>]+>", "", body).strip():
        return False  # Has actual text
    tags = [t.lower() for t in re.findall(r"<([a-zA-Z]+)\b", body)]
    non_wrapper = [t for t in tags if t not in ("img", "div", "a", "span", "figure", "p")]
    return len(non_wrapper) == 0


def _guess_chapter_label(html_text: str, index: int) -> str:
    """Extract a title from h1/h2/h3, first paragraph, or 'Chapter N'."""
    for tag in ("h1", "h2", "h3"):
        m = re.search(
            r"<" + tag + r"\b[^>]*>(.*?)</" + tag + r">",
            html_text, re.DOTALL | re.IGNORECASE,
        )
        if m:
            text = re.sub(r"<[^>]+>", "", m.group(1)).strip()
            if text:
                return text[:80]
    # For spine items with no heading (front/back-matter pages), use the first
    # non-trivial paragraph so the TOC label is descriptive rather than "Chapter N".
    m = re.search(r"<p\b[^>]*>(.*?)</p>", html_text, re.DOTALL | re.IGNORECASE)
    if m:
        text = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        if len(text) >= 30:
            return (text[:57] + "…") if len(text) > 60 else text
    return f"Chapter {index}"


def _read_epub_zip(
    zf: zipfile.ZipFile,
    epub_path: Path,
) -> Tuple[Optional[EpubBook], str]:
    names = set(zf.namelist())

    # ── container.xml ────────────────────────────────────────────────────
    if "META-INF/container.xml" not in names:
        return None, "Missing META-INF/container.xml"
    container = zf.read("META-INF/container.xml").decode("utf-8", errors="replace")
    m = re.search(r'full-path\s*=\s*["\']([^"\']+)["\']', container)
    if not m:
        return None, "Cannot locate OPF path in container.xml"
    opf_zip = m.group(1)
    if opf_zip not in names:
        return None, f"OPF not in ZIP: {opf_zip}"
    opf_dir = opf_zip.rsplit("/", 1)[0] if "/" in opf_zip else ""
    opf_text = zf.read(opf_zip).decode("utf-8", errors="replace")

    def zip_path(href: str) -> str:
        return (opf_dir + "/" + href).lstrip("/") if opf_dir else href

    # ── Metadata ─────────────────────────────────────────────────────────
    title       = _dc(opf_text, "title")
    author      = _dc(opf_text, "creator")
    language    = _dc(opf_text, "language") or "en"
    publisher   = _dc(opf_text, "publisher")
    pub_date    = _dc(opf_text, "date")
    description = _dc(opf_text, "description")
    rights      = _dc(opf_text, "rights")
    subject     = _dc(opf_text, "subject")
    contributor = _dc(opf_text, "contributor")
    identifier  = _dc(opf_text, "identifier")

    isbn_m = re.search(
        r'<dc:identifier\b[^>]*(?:opf:scheme|scheme)\s*=\s*["\']ISBN["\'][^>]*>(.*?)</dc:identifier>',
        opf_text, re.IGNORECASE | re.DOTALL,
    )
    isbn = re.sub(r"<[^>]+>", "", isbn_m.group(1)).strip() if isbn_m else ""

    series       = _meta_content(opf_text, "calibre:series")
    series_index = _meta_content(opf_text, "calibre:series_index")
    if not series:
        coll_m = re.search(
            r'<meta\b[^>]*property\s*=\s*["\']belongs-to-collection["\'][^>]*>(.*?)</meta>',
            opf_text, re.IGNORECASE | re.DOTALL,
        )
        if coll_m:
            series = re.sub(r"<[^>]+>", "", coll_m.group(1)).strip()

    # ── Manifest ─────────────────────────────────────────────────────────
    # id → (href_rel_to_opf_dir, media_type, properties)
    manifest: Dict[str, Tuple[str, str, str]] = {}
    for mm in re.finditer(r"<item\b[^>]*/?>", opf_text, re.IGNORECASE):
        attrs = _parse_attrs(mm.group(0))
        if "id" in attrs and "href" in attrs:
            manifest[attrs["id"]] = (
                attrs["href"],
                attrs.get("media-type", ""),
                attrs.get("properties", ""),
            )

    # ── Spine ────────────────────────────────────────────────────────────
    spine_ids = re.findall(
        r'<itemref\b[^>]*\bidref\s*=\s*["\']([^"\']+)["\']',
        opf_text, re.IGNORECASE,
    )

    # ── Cover image ──────────────────────────────────────────────────────
    cover_data: Optional[bytes] = None
    cover_mime = "image/jpeg"

    # Method 1: manifest item with properties="cover-image"
    for _iid, (href, mtype, props) in manifest.items():
        if "cover-image" in props:
            zp = zip_path(href)
            if zp in names:
                cover_data = zf.read(zp)
                cover_mime = mtype
                break

    # Method 2: OPF meta name="cover" → manifest id
    if cover_data is None:
        cov_id = _meta_content(opf_text, "cover")
        if cov_id and cov_id in manifest:
            zp = zip_path(manifest[cov_id][0])
            if zp in names:
                cover_data = zf.read(zp)
                cover_mime = manifest[cov_id][1]

    # ── NCX ──────────────────────────────────────────────────────────────
    ncx_zip: Optional[str] = None
    toc_id_m = re.search(r'<spine\b[^>]*\btoc\s*=\s*["\']([^"\']+)["\']', opf_text, re.IGNORECASE)
    if toc_id_m and toc_id_m.group(1) in manifest:
        ncx_zip = zip_path(manifest[toc_id_m.group(1)][0])
    if not ncx_zip or ncx_zip not in names:
        for _iid, (href, mtype, _props) in manifest.items():
            if "dtbncx" in mtype or href.endswith(".ncx"):
                ncx_zip = zip_path(href)
                break

    ncx_chapters: List[Tuple[str, str, Optional[str]]] = []
    if ncx_zip and ncx_zip in names:
        ncx_chapters = parse_ncx_chapters(
            zf.read(ncx_zip).decode("utf-8", errors="replace")
        )

    # ── nav.xhtml (EPUB 3) ───────────────────────────────────────────────
    nav_zip: Optional[str] = None
    for _iid, (href, mtype, props) in manifest.items():
        if "nav" in props and "xhtml" in mtype:
            nav_zip = zip_path(href)
            break

    # ── Chapter map: src_file (lowercase) → [(anchor_or_None, label)] ────
    file_chapters: Dict[str, List[Tuple[Optional[str], str]]] = {}
    for label, src_file, anchor in ncx_chapters:
        file_chapters.setdefault(src_file.lower(), []).append((anchor, label))

    # ── Images ───────────────────────────────────────────────────────────
    # Build zip_path → new dest (relative to OEBPS/) mapping
    image_dest_map: Dict[str, str] = {}
    image_list: List[EpubImage] = []
    _taken_dests: set = set()

    for _iid, (href, mtype, props) in manifest.items():
        if not mtype.startswith("image/"):
            continue
        if "cover-image" in props:
            continue  # cover handled separately
        zp = zip_path(href)
        basename = href.rsplit("/", 1)[-1]
        dest = f"images/{basename}"
        n = 1
        while dest in _taken_dests:
            stem, _, ext = basename.rpartition(".")
            dest = f"images/{stem}_{n}.{ext}" if ext else f"images/{basename}_{n}"
            n += 1
        _taken_dests.add(dest)
        image_dest_map[zp] = dest
        if zp in names:
            image_list.append(EpubImage(dest=dest, data=zf.read(zp), mime=mtype))

    def _rewrite_img_srcs(body: str, chapter_href: str) -> str:
        """Rewrite img src= paths from original EPUB layout to our flat layout."""
        def _fix(mm: re.Match) -> str:
            attr, val = mm.group(1), mm.group(2)
            if val.startswith(("data:", "http:", "https:")):
                return mm.group(0)
            zp = _resolve_zip_path(val, chapter_href, opf_dir)
            new_dest = image_dest_map.get(zp)
            return f'{attr}="{new_dest}"' if new_dest else mm.group(0)

        return re.sub(r'\b(src)\s*=\s*"([^"]*)"', _fix, body, flags=re.IGNORECASE)

    # ── Build chapter list from spine ─────────────────────────────────────
    chapters: List[EpubChapter] = []
    _cover_page_skipped = False  # only skip the first image-only page (the cover)

    for item_id in spine_ids:
        if item_id not in manifest:
            continue
        href, mtype, props = manifest[item_id]
        if "dtbncx" in mtype or "css" in mtype:
            continue

        zp = zip_path(href)
        if zp not in names:
            continue

        # Skip the nav document (we generate our own)
        if nav_zip and zp == nav_zip:
            continue

        html_text = zf.read(zp).decode("utf-8", errors="replace")
        body = _extract_body(html_text)

        # Skip the cover page (we generate our own from cover_data).  Only
        # apply once — later image-only pages (e.g. title pages rendered as
        # images) are intentional content and must not be silently dropped.
        if cover_data is not None and not _cover_page_skipped and _is_cover_page(body):
            _cover_page_skipped = True
            continue

        src_basename = href.rsplit("/", 1)[-1].lower()
        anchor_entries = file_chapters.get(src_basename, [])
        anchor_pairs = [(a, lbl) for a, lbl in anchor_entries if a is not None]

        # ── Path 1: anchor-based split (threshold lowered from 2 → 1) ────
        if len(anchor_pairs) >= 1:
            frags = split_body_at_chapters(body, anchor_pairs)
            if frags:
                # Relabel preamble chapter with the parent NCX label when
                # the split produced more than one fragment.
                parent_labels = [lbl for a, lbl in anchor_entries if a is None]
                if parent_labels and len(frags) > 1:
                    frags[0] = (parent_labels[0], frags[0][1])
                for label, fragment in frags:
                    cleaned = clean_chapter_html(_rewrite_img_srcs(fragment, href))
                    chapters.append(EpubChapter(title=label, body_html=cleaned))
                continue

            # Anchor IDs are in the NCX but absent from the HTML (malformed
            # EPUB) — fall back to matching NCX labels against heading text.
            positions = _match_ncx_labels_to_headings(body, anchor_pairs)
            if positions:
                parent_label = next(
                    (lbl for a, lbl in anchor_entries if a is None),
                    _guess_chapter_label(html_text, len(chapters) + 1),
                )
                preamble = body[: positions[0][0]]
                preamble_text = re.sub(r"<[^>]+>", "", preamble).strip()
                preamble_has_img = bool(re.search(r"<img\b", preamble, re.IGNORECASE))
                if len(preamble_text) > 100 or preamble_has_img:
                    cleaned = clean_chapter_html(_rewrite_img_srcs(preamble, href))
                    chapters.append(EpubChapter(title=parent_label, body_html=cleaned))
                    preamble = ""
                for i, (pos, _aid, lbl) in enumerate(positions):
                    end = positions[i + 1][0] if i + 1 < len(positions) else len(body)
                    chunk = (preamble + body[pos:end]) if i == 0 else body[pos:end]
                    cleaned = clean_chapter_html(_rewrite_img_srcs(chunk, href))
                    chapters.append(EpubChapter(title=lbl, body_html=cleaned))
                continue

        # ── Path 2: heading-split — only when NCX has NO info for this file.
        # If the NCX has any entry (even just a parent label with no anchor),
        # trust the NCX and fall through to the whole-file path below rather
        # than splitting at every heading tag.
        if not anchor_entries:
            frags = split_body_at_headings(body)
            if frags:
                for label, fragment in frags:
                    cleaned = clean_chapter_html(_rewrite_img_srcs(fragment, href))
                    chapters.append(EpubChapter(title=label, body_html=cleaned))
                continue

        # ── Path 3: whole file as one chapter ────────────────────────────
        label = (
            anchor_entries[0][1]
            if anchor_entries
            else _guess_chapter_label(html_text, len(chapters) + 1)
        )
        cleaned = clean_chapter_html(_rewrite_img_srcs(body, href))
        chapters.append(EpubChapter(title=label, body_html=cleaned))

    if not chapters:
        return None, "No readable spine items found in EPUB"

    book = EpubBook(
        title=title or epub_path.stem,
        author=author,
        language=language,
        identifier=identifier,
        series=series,
        series_index=series_index,
        publisher=publisher,
        pub_date=pub_date,
        description=description,
        isbn=isbn,
        rights=rights,
        subject=subject,
        contributor=contributor,
        chapters=chapters,
        images=image_list,
        cover_data=cover_data,
        cover_mime=cover_mime,
    )
    return book, ""


# ---------------------------------------------------------------------------
# Post-conversion integrity check
# ---------------------------------------------------------------------------

def check_epub_integrity(original_path: Path, converted_book: "EpubBook") -> List[str]:
    """Return a list of warning strings about potential conversion quality issues.

    Compares the original EPUB's NCX chapter count against the converted
    EpubBook, and flags suspiciously short chapters.  Returns ``[]`` when
    everything looks reasonable.
    """
    warnings: List[str] = []

    # ── Count original NCX entries ────────────────────────────────────────
    original_ncx_count = 0
    try:
        with zipfile.ZipFile(original_path, "r") as zf:
            names_orig = set(zf.namelist())
            if "META-INF/container.xml" not in names_orig:
                raise ValueError("no container.xml")
            container = zf.read("META-INF/container.xml").decode("utf-8", errors="replace")
            m_opf = re.search(r'full-path\s*=\s*["\']([^"\']+)["\']', container)
            if not m_opf:
                raise ValueError("no OPF path")
            opf_zip = m_opf.group(1)
            if opf_zip not in names_orig:
                raise ValueError("OPF not in zip")
            opf_dir_orig = opf_zip.rsplit("/", 1)[0] if "/" in opf_zip else ""
            opf_text_orig = zf.read(opf_zip).decode("utf-8", errors="replace")

            ncx_zip_orig: Optional[str] = None
            toc_m = re.search(
                r'<spine\b[^>]*\btoc\s*=\s*["\']([^"\']+)["\']',
                opf_text_orig, re.IGNORECASE,
            )
            if toc_m:
                for mm in re.finditer(r"<item\b[^>]*/?>", opf_text_orig, re.IGNORECASE):
                    attrs = _parse_attrs(mm.group(0))
                    if attrs.get("id") == toc_m.group(1) and "href" in attrs:
                        ncx_zip_orig = (opf_dir_orig + "/" + attrs["href"]).lstrip("/")
                        break
            if not ncx_zip_orig or ncx_zip_orig not in names_orig:
                for mm in re.finditer(r"<item\b[^>]*/?>", opf_text_orig, re.IGNORECASE):
                    attrs = _parse_attrs(mm.group(0))
                    if "dtbncx" in attrs.get("media-type", "") or attrs.get("href", "").endswith(".ncx"):
                        ncx_zip_orig = (opf_dir_orig + "/" + attrs["href"]).lstrip("/")
                        break
            if ncx_zip_orig and ncx_zip_orig in names_orig:
                ncx_text_orig = zf.read(ncx_zip_orig).decode("utf-8", errors="replace")
                original_ncx_count = len(parse_ncx_chapters(ncx_text_orig))
    except Exception:
        pass

    # ── Chapter count comparison ──────────────────────────────────────────
    converted_count = len(converted_book.chapters)
    if original_ncx_count > 0 and converted_count > 0:
        ratio = converted_count / original_ncx_count
        if ratio > 1.5:
            warnings.append(
                f"Chapter count: original NCX had {original_ncx_count} entries but "
                f"converted has {converted_count} chapters ({ratio:.1f}× expected) — "
                "possible incorrect splitting"
            )
        elif ratio < 0.6:
            warnings.append(
                f"Chapter count: original NCX had {original_ncx_count} entries but "
                f"converted has only {converted_count} chapters ({ratio:.1f}× expected) — "
                "possible missing content"
            )

    # ── Short chapter detection ───────────────────────────────────────────
    short = [
        ch.title for ch in converted_book.chapters
        if len(re.sub(r"<[^>]+>", "", ch.body_html).split()) < 50
    ]
    if short:
        sample = ", ".join(f'"{t}"' for t in short[:5])
        suffix = "…" if len(short) > 5 else ""
        warnings.append(
            f"{len(short)} chapter(s) under 200 words (may indicate incorrect "
            f"splitting): {sample}{suffix}"
        )

    return warnings
