"""MOBI/AZW3 → clean EPUB 3.3 conversion.

Uses the `mobi` package (a kindleunpack wrapper) to extract raw content, then
builds a clean EPUB 3.3 via epub_build.  No patching of kindleunpack output —
the extracted content is treated as a data source only.

Two input cases:
- KF8 / AZW3 (modern): kindleunpack extracts an embedded EPUB.
  We read that EPUB with epub_clean.read_epub_book and rebuild it cleanly.
- Old MOBI7: kindleunpack produces HTML + images + OPF + NCX in a directory.
  We parse the OPF/NCX directly and assemble a fresh EPUB.
"""
from __future__ import annotations

import logging
import re
import shutil
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

MOBI_EXTENSIONS = frozenset({".mobi", ".azw3", ".azw", ".prc"})


_WIN_FORBIDDEN_IN_NAME = frozenset('<>:"/\\|?*')
# U+FFFD is the Unicode replacement character — appears when a character
# couldn't be decoded (e.g. a Linux apostrophe via a Samba codepage mismatch).
_NEEDS_RENAME_CHARS = _WIN_FORBIDDEN_IN_NAME | {'�'}
_WIN_REPLACEMENTS = {
    '?': "'", '*': '_', '<': '_', '>': '_',
    '|': '_', '"': "'", ':': '-', '�': "'",
}


def has_invalid_filename_chars(filepath: Path) -> bool:
    """Return True if *filepath*'s name contains characters that prevent Windows from opening it."""
    return bool(_NEEDS_RENAME_CHARS.intersection(filepath.name))


def _resolve_actual_path(filepath: Path) -> Optional[Path]:
    """Return the true filesystem path for *filepath*, finding it via os.scandir if needed.

    When a stored path has encoding issues (e.g. '?' stored as U+003F but the
    actual file has U+FFFD), the stored path fails os.stat but os.scandir can
    still find it.  This function compares names with non-ASCII chars normalised
    to '?' so we can match despite the encoding mismatch.
    """
    if filepath.exists():
        return filepath
    import os
    approx = filepath.name.encode('ascii', errors='replace').decode('ascii')
    try:
        for entry in os.scandir(filepath.parent):
            if entry.name.encode('ascii', errors='replace').decode('ascii') == approx:
                return Path(entry.path)
    except OSError:
        pass
    return None


def fix_invalid_filename(filepath: Path) -> Optional[Path]:
    """Rename *filepath* so its name contains no Windows-forbidden or corrupt characters.

    Uses ``os.scandir`` to locate the file (bypassing Win32 open-path validation),
    then ``shutil.move`` to rename it.  Returns the new :class:`Path` on success,
    or ``None`` if nothing needed doing or the rename failed.
    """
    if not has_invalid_filename_chars(filepath):
        return None

    real = _resolve_actual_path(filepath)
    if real is None:
        return None

    # Build clean name from the REAL entry name (which may differ in encoding)
    clean_name = real.name
    for bad, good in _WIN_REPLACEMENTS.items():
        clean_name = clean_name.replace(bad, good)
    if clean_name == real.name:
        return None

    new_path = filepath.parent / clean_name
    if new_path.exists():
        return None

    try:
        shutil.move(str(real), str(new_path))
        return new_path
    except OSError:
        return None


def _read_any_path(path: Path) -> bytes:
    """Read *path* as bytes, using os.scandir to resolve encoding-mangled paths.

    When the stored path has an encoding mismatch (e.g. '?' stored as U+003F
    but the real file has U+FFFD), os.stat/open fail.  _resolve_actual_path
    uses os.scandir (which uses FindFirstFileW, tolerant of encoding differences)
    to find the true entry, then we read that.
    """
    try:
        return path.read_bytes()
    except OSError:
        pass

    real = _resolve_actual_path(path)
    if real is not None and real != path:
        return real.read_bytes()

    raise OSError(
        f"Cannot read '{path.name}': file has a filename encoding issue and "
        f"could not be located. Try renaming the file on the server."
    )


def is_mobi(filepath: Path) -> bool:
    return filepath.suffix.lower() in MOBI_EXTENSIONS


def _read_mobi_title(data: bytes) -> Optional[str]:
    """Read the full book title from MOBI binary data.

    kindleunpack sometimes writes a garbled dc:title to the extracted OPF
    (e.g. 'pear. On' instead of 'Xenocide').  The MOBI header's Full Name
    field is more reliable and is what the device displays.
    """
    import struct
    try:
        if len(data) < 82:
            return None
        rec0_offset = struct.unpack('>I', data[78:82])[0]
        rec0 = data[rec0_offset:]
        if len(rec0) < 20 or rec0[16:20] != b'MOBI':
            return None
        mobi_hdr = rec0[16:]
        if len(mobi_hdr) < 0x5c:
            return None
        fn_offset = struct.unpack('>I', mobi_hdr[0x54:0x58])[0]
        fn_length = struct.unpack('>I', mobi_hdr[0x58:0x5c])[0]
        if fn_length <= 0 or fn_offset + fn_length > len(rec0):
            return None
        title = rec0[fn_offset: fn_offset + fn_length].decode('utf-8', errors='replace').strip()
        return title or None
    except Exception:
        return None


def convert_mobi_to_epub(src: Path, dst: Path) -> Optional[str]:
    """Convert *src* (MOBI/AZW3/…) to a clean EPUB 3.3 at *dst*.

    The original file is never modified.  Returns an error string on failure,
    or ``None`` on success.
    """
    try:
        import mobi as _mobi
    except ImportError:
        return "mobi package not installed — run: pip install mobi"

    # The mobi library fails on non-ASCII or Windows-reserved characters in the
    # source path (e.g. '?' from Linux filesystems, curly apostrophes, etc.).
    # Stage a copy with a plain ASCII name in a temp directory first.
    import tempfile
    tmp_input_dir: Optional[str] = None
    tmp_dir: Optional[str] = None
    try:
        tmp_input_dir = tempfile.mkdtemp()
        safe_src = Path(tmp_input_dir) / ("ebook" + src.suffix.lower())
        safe_src.write_bytes(_read_any_path(src))
        # Read title from MOBI binary before cleanup — kindleunpack often writes
        # a garbled dc:title to the extracted OPF; the MOBI Full Name is reliable.
        mobi_title = _read_mobi_title(safe_src.read_bytes())
    except Exception as exc:
        if tmp_input_dir:
            shutil.rmtree(tmp_input_dir, ignore_errors=True)
        return f"MOBI extraction failed (could not stage file): {exc}"

    try:
        tmp_dir, extracted = _mobi.extract(str(safe_src))
    except Exception as exc:
        return f"MOBI extraction failed: {exc}"
    finally:
        shutil.rmtree(tmp_input_dir, ignore_errors=True)

    try:
        extracted_path = Path(extracted)

        if extracted_path.suffix.lower() == ".epub":
            # KF8/AZW3: kindleunpack produced an embedded EPUB.
            # Rebuild it cleanly rather than using kindleunpack's output as-is.
            return _rebuild_epub(extracted_path, dst, title_override=mobi_title)

        # Old MOBI7: kindleunpack produced a directory of HTML + assets.
        content_dir = extracted_path.parent
        return _convert_mobi7(content_dir, dst, title_override=mobi_title)

    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# MOBI7 path — build from kindleunpack HTML/OPF/NCX directory
# ---------------------------------------------------------------------------

def _convert_mobi7(content_dir: Path, dst: Path, title_override: Optional[str] = None) -> Optional[str]:
    """Build a clean EPUB 3.3 from a kindleunpack MOBI7 extraction directory."""
    from .epub_build import EpubBook, EpubChapter, EpubImage, build_epub
    from .epub_clean import (
        clean_chapter_html, parse_ncx_chapters, parse_html_toc,
        split_body_at_chapters, split_body_at_headings,
        _dc, _parse_attrs, _guess_chapter_label,
    )

    # ── Locate OPF ────────────────────────────────────────────────────────
    opf_files = list(content_dir.glob("*.opf"))
    if not opf_files:
        return "No OPF file found in extracted MOBI content"
    opf_text = opf_files[0].read_bytes().replace(b"\x00", b"").decode("utf-8", errors="replace")

    # ── Metadata from OPF ────────────────────────────────────────────────
    title       = title_override or _dc(opf_text, "title")
    author      = _dc(opf_text, "creator")
    language    = _dc(opf_text, "language") or "en"
    publisher   = _dc(opf_text, "publisher")
    pub_date    = _dc(opf_text, "date")
    description = _dc(opf_text, "description")
    rights      = _dc(opf_text, "rights")
    subject     = _dc(opf_text, "subject")

    # ── Manifest ─────────────────────────────────────────────────────────
    # id → (filename_relative_to_content_dir, media_type)
    manifest: Dict[str, Tuple[str, str]] = {}
    for mm in re.finditer(r"<item\b[^>]*/?>", opf_text, re.IGNORECASE):
        attrs = _parse_attrs(mm.group(0))
        if "id" in attrs and "href" in attrs:
            manifest[attrs["id"]] = (attrs["href"], attrs.get("media-type", ""))

    spine_ids = re.findall(
        r'<itemref\b[^>]*\bidref\s*=\s*["\']([^"\']+)["\']',
        opf_text, re.IGNORECASE,
    )

    # ── NCX ──────────────────────────────────────────────────────────────
    ncx_chapters: List[Tuple[str, str, Optional[str]]] = []
    toc_m = re.search(r'<spine\b[^>]*\btoc\s*=\s*["\']([^"\']+)["\']', opf_text, re.IGNORECASE)
    ncx_id = toc_m.group(1) if toc_m else None
    ncx_href = manifest.get(ncx_id, (None,))[0] if ncx_id else None
    if not ncx_href:
        # Fallback: find by media-type
        for _iid, (href, mtype) in manifest.items():
            if "dtbncx" in mtype or href.endswith(".ncx"):
                ncx_href = href
                break
    if ncx_href:
        ncx_path = content_dir / ncx_href
        if ncx_path.exists():
            ncx_text = ncx_path.read_bytes().replace(b"\x00", b"").decode("utf-8", errors="replace")
            ncx_chapters = parse_ncx_chapters(ncx_text)

    # Group NCX chapters by source file
    file_chapters: Dict[str, List[Tuple[Optional[str], str]]] = {}
    for label, src_file, anchor in ncx_chapters:
        file_chapters.setdefault(src_file.lower(), []).append((anchor, label))

    # ── Cover image ──────────────────────────────────────────────────────
    cover_data: Optional[bytes] = None
    cover_mime = "image/jpeg"

    # OPF meta name="cover" → manifest id
    cov_m = re.search(
        r'<meta\b[^>]*\bname\s*=\s*["\']cover["\'][^>]*\bcontent\s*=\s*["\']([^"\']+)["\']'
        r'|<meta\b[^>]*\bcontent\s*=\s*["\']([^"\']+)["\'][^>]*\bname\s*=\s*["\']cover["\']',
        opf_text, re.IGNORECASE,
    )
    if cov_m:
        cov_id = cov_m.group(1) or cov_m.group(2)
        if cov_id in manifest:
            cov_path = content_dir / manifest[cov_id][0]
            if cov_path.exists():
                cover_data = cov_path.read_bytes()
                cover_mime = manifest[cov_id][1] or "image/jpeg"

    # Fallback: first image item in manifest
    if cover_data is None:
        for _iid, (href, mtype) in manifest.items():
            if mtype.startswith("image/"):
                cov_path = content_dir / href
                if cov_path.exists():
                    cover_data = cov_path.read_bytes()
                    cover_mime = mtype
                    break

    # ── Images ───────────────────────────────────────────────────────────
    image_list: List[EpubImage] = []
    image_dest_map: Dict[str, str] = {}  # original filename → new dest under OEBPS/
    _taken: set = set()

    for _iid, (href, mtype) in manifest.items():
        if not mtype.startswith("image/"):
            continue
        img_path = content_dir / href
        if not img_path.exists():
            continue
        basename = href.rsplit("/", 1)[-1]
        dest = f"images/{basename}"
        n = 1
        while dest in _taken:
            stem, _, ext = basename.rpartition(".")
            dest = f"images/{stem}_{n}.{ext}" if ext else f"images/{basename}_{n}"
            n += 1
        _taken.add(dest)
        image_dest_map[href.lower()] = dest
        image_list.append(EpubImage(dest=dest, data=img_path.read_bytes(), mime=mtype))

    def _rewrite_img_srcs(body: str, html_href: str) -> str:
        """Rewrite img src= from original layout to our flat images/ layout."""
        html_dir = (html_href.rsplit("/", 1)[0] + "/") if "/" in html_href else ""

        def _fix(mm: re.Match) -> str:
            val = mm.group(1)
            if val.startswith(("data:", "http:", "https:")):
                return mm.group(0)
            # Resolve relative to html_dir
            resolved = (html_dir + val).lstrip("./")
            new_dest = image_dest_map.get(resolved.lower())
            return f'src="{new_dest}"' if new_dest else mm.group(0)

        return re.sub(r'\bsrc\s*=\s*"([^"]*)"', lambda m: _fix(m), body, flags=re.IGNORECASE)

    # ── Build chapters ────────────────────────────────────────────────────
    chapters: List[EpubChapter] = []

    for item_id in spine_ids:
        if item_id not in manifest:
            continue
        href, mtype = manifest[item_id]
        if not ("html" in mtype or href.lower().endswith((".html", ".htm", ".xhtml"))):
            continue

        html_path = content_dir / href
        if not html_path.exists():
            continue

        html_bytes = html_path.read_bytes().replace(b"\x00", b"")
        html_text = html_bytes.decode("utf-8", errors="replace")

        body_m = re.search(r"<body\b[^>]*>(.*)</body>", html_text, re.DOTALL | re.IGNORECASE)
        body = body_m.group(1) if body_m else html_text

        src_basename = href.rsplit("/", 1)[-1].lower()
        anchor_entries = file_chapters.get(src_basename, [])
        anchor_pairs = [(a, lbl) for a, lbl in anchor_entries if a is not None]

        # Path 1: anchor-based split (threshold lowered from 2 → 1)
        if len(anchor_pairs) >= 1:
            frags = split_body_at_chapters(body, anchor_pairs)
            if frags:
                parent_labels = [lbl for a, lbl in anchor_entries if a is None]
                if parent_labels and len(frags) > 1:
                    frags[0] = (parent_labels[0], frags[0][1])
                for label, fragment in frags:
                    cleaned = clean_chapter_html(_rewrite_img_srcs(fragment, href))
                    chapters.append(EpubChapter(title=label, body_html=cleaned))
                continue

        # Path 2: heading-split — only when NCX has NO info for this file
        if not anchor_entries:
            frags = split_body_at_headings(body)
            if frags:
                for label, fragment in frags:
                    cleaned = clean_chapter_html(_rewrite_img_srcs(fragment, href))
                    chapters.append(EpubChapter(title=label, body_html=cleaned))
                continue

        # No headings — try inline HTML TOC (MOBI7 books with empty NCX often
        # embed their TOC as <a href="#fileposNNN"> links inside the HTML body)
        html_toc = parse_html_toc(html_text)
        if html_toc:
            frags = split_body_at_chapters(body, html_toc)
            if frags:
                for label, fragment in frags:
                    cleaned = clean_chapter_html(_rewrite_img_srcs(fragment, href))
                    chapters.append(EpubChapter(title=label, body_html=cleaned))
                continue

        # Use whole file as one chapter
        label = (
            anchor_entries[0][1]
            if anchor_entries
            else _guess_chapter_label(html_text, len(chapters) + 1)
        )
        cleaned = clean_chapter_html(_rewrite_img_srcs(body, href))
        chapters.append(EpubChapter(title=label, body_html=cleaned))

    if not chapters:
        return "No HTML content found in extracted MOBI"

    book = EpubBook(
        title=title,
        author=author,
        language=language,
        publisher=publisher,
        pub_date=pub_date,
        description=description,
        rights=rights,
        subject=subject,
        chapters=chapters,
        images=image_list,
        cover_data=cover_data,
        cover_mime=cover_mime,
    )
    return build_epub(book, dst)


# ---------------------------------------------------------------------------
# KF8/AZW3 path — rebuild extracted EPUB cleanly
# ---------------------------------------------------------------------------

def _rebuild_epub(src_epub: Path, dst: Path, title_override: Optional[str] = None) -> Optional[str]:
    """Read *src_epub* (kindleunpack-extracted EPUB) and rebuild as clean EPUB 3.3."""
    from .epub_clean import read_epub_book, check_epub_integrity
    from .epub_build import build_epub

    book, err = read_epub_book(src_epub)
    if book is None:
        return f"Could not read extracted KF8 EPUB: {err}"
    if title_override:
        book.title = title_override
    build_err = build_epub(book, dst)
    if build_err:
        return build_err

    warnings = check_epub_integrity(src_epub, book)
    if warnings:
        logger.warning("[integrity] %s: %s", dst.name, "; ".join(warnings))

    return None


