"""Apply chosen metadata writes and file copy/move operations.

Metadata writing is pure-Python for all supported formats — no external tools needed:
- EPUB              → ZIP/XML (OPF) manipulation
- MOBI / AZW3 / AZW / PRC → PalmDB EXTH record manipulation
- PDF               → pypdf metadata writer
"""

from __future__ import annotations

import logging
import re
import shutil
import struct
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

from .models import BookRecord

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_unique_path(path: Path) -> Path:
    """Return a non-existing path by appending Windows-style (1), (2), … suffixes."""
    if not path.exists():
        return path
    parent, stem, ext = path.parent, path.stem, path.suffix
    n = 1
    while True:
        candidate = parent / f"{stem} ({n}){ext}"
        if not candidate.exists():
            return candidate
        n += 1


def _find_opf_path(container_xml: str) -> Optional[str]:
    """Return the OPF rootfile path from META-INF/container.xml content."""
    m = re.search(r'full-path=["\']([^"\']+)["\']', container_xml)
    return m.group(1) if m else None


def _escape_xml(value: str) -> str:
    """Escape characters that are special in XML text/attribute values."""
    return (
        value
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _upsert_dc_element(opf_text: str, dc_name: str, value: str) -> str:
    """Update the text of the first matching dc:X element, or insert one before </metadata>."""
    escaped = _escape_xml(value)
    tag_re = rf'(?:[^:>\s]+:)?{re.escape(dc_name)}'
    if re.search(rf'<{tag_re}(?:\s[^>]*)?>', opf_text, re.IGNORECASE):
        opf_text = re.sub(
            rf'(<{tag_re}(?:\s[^>]*)?>)[^<]*(</\s*{tag_re}\s*>)',
            lambda m, v=escaped: m.group(1) + v + m.group(2),
            opf_text, count=1, flags=re.IGNORECASE,
        )
    else:
        new_el = f'    <dc:{dc_name}>{escaped}</dc:{dc_name}>\n'
        opf_text = re.sub(
            r'(</\s*(?:[^:>\s]+:)?metadata\s*>)',
            lambda m, el=new_el: el + m.group(1),
            opf_text, count=1, flags=re.IGNORECASE,
        )
    return opf_text


def _upsert_isbn(opf_text: str, isbn: str) -> str:
    """Update or insert a dc:identifier with opf:scheme="isbn"."""
    escaped = _escape_xml(isbn)
    if re.search(r'opf:scheme=["\']isbn["\']', opf_text, re.IGNORECASE):
        opf_text = re.sub(
            r'(<dc:identifier\b[^>]*opf:scheme=["\']isbn["\'][^>]*>)[^<]*(</dc:identifier>)',
            lambda m, v=escaped: m.group(1) + v + m.group(2),
            opf_text, count=1, flags=re.IGNORECASE,
        )
    else:
        new_el = f'    <dc:identifier opf:scheme="isbn">{escaped}</dc:identifier>\n'
        opf_text = re.sub(
            r'(</\s*(?:[^:>\s]+:)?metadata\s*>)',
            lambda m, el=new_el: el + m.group(1),
            opf_text, count=1, flags=re.IGNORECASE,
        )
    return opf_text


def _upsert_meta_element(opf_text: str, name: str, value: str) -> str:
    """Update or insert a <meta name='X' content='...'> element in OPF metadata."""
    escaped = _escape_xml(value)
    # Build the name-matching pattern with proper string concatenation so the
    # name value is NOT accidentally placed inside a regex character class.
    name_pat = r'["\']' + re.escape(name) + r'["\']'
    if re.search(r'name=' + name_pat, opf_text, re.IGNORECASE):
        opf_text = re.sub(
            r'(<meta\s[^>]*name=' + name_pat + r'[^>]*\s)content=["\'][^"\']*["\']',
            lambda m, v=escaped: m.group(1) + f'content="{v}"',
            opf_text, count=1, flags=re.IGNORECASE,
        )
    else:
        new_el = f'    <meta name="{name}" content="{escaped}"/>\n'
        opf_text = re.sub(
            r'(</\s*(?:[^:>\s]+:)?metadata\s*>)',
            lambda m, el=new_el: el + m.group(1),
            opf_text, count=1, flags=re.IGNORECASE,
        )
    return opf_text


def _update_opf_text(opf_text: str, chosen: Dict[str, Optional[str]]) -> str:
    """Apply chosen metadata to OPF XML via targeted regex substitution.

    Operates on the raw text so that the original namespace declarations,
    attribute order, and indentation are preserved exactly.  Only the text
    content of specific elements (and the ``content`` attribute of calibre
    meta elements) is replaced.
    """
    title = (chosen.get("title") or "").strip()
    author = (chosen.get("author") or "").strip()
    series = (chosen.get("series") or "").strip()
    series_index = (chosen.get("series_index") or "").strip()
    subject = (chosen.get("subject") or "").strip()

    # Upsert dc:title and dc:creator — insert if absent, update if present.
    # (These use _upsert_dc_element like all other dc: fields so that EPUBs
    # missing a title or creator element get one written rather than silently
    # dropped.)
    if title:
        opf_text = _upsert_dc_element(opf_text, "title", title)
    if author:
        opf_text = _upsert_dc_element(opf_text, "creator", author)

    # Replace ALL existing <dc:subject> elements with a single new one.
    # Delete both self-closing (<dc:subject/>) and paired elements; then
    # insert the fresh value before </metadata>.
    if subject:
        escaped_subj = _escape_xml(subject)
        _subj = r'(?:[^:>\s]+:)?subject'
        # Delete self-closing subject elements (these survived the paired-tag
        # regex and caused empty entries to accumulate on repeated writes)
        opf_text = re.sub(
            rf'\s*<{_subj}(?:\s[^>]*)?/>',
            '',
            opf_text,
            flags=re.IGNORECASE,
        )
        # Delete paired subject elements
        opf_text = re.sub(
            rf'\s*<{_subj}(?:\s[^>]*)?>.*?</{_subj}>',
            '',
            opf_text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        new_subj = f'    <dc:subject>{escaped_subj}</dc:subject>\n'
        opf_text = re.sub(
            r'(</\s*(?:[^:>\s]+:)?metadata\s*>)',
            lambda m, el=new_subj: el + m.group(1),
            opf_text,
            count=1,
            flags=re.IGNORECASE,
        )

    # Update or insert calibre:series
    if series:
        escaped = _escape_xml(series)
        if re.search(r'name=["\']calibre:series["\']', opf_text):
            opf_text = re.sub(
                r'(<meta\s[^>]*name=["\']calibre:series["\'][^>]*\s)content=["\'][^"\']*["\']',
                lambda m: m.group(1) + f'content="{escaped}"',
                opf_text,
                count=1,
                flags=re.IGNORECASE,
            )
            # Also handle content= appearing before name=
            opf_text = re.sub(
                r'(<meta\s[^>]*?)content=["\'][^"\']*["\']([^>]*name=["\']calibre:series["\'])',
                lambda m: m.group(1) + f'content="{escaped}"' + m.group(2),
                opf_text,
                count=1,
                flags=re.IGNORECASE,
            )
        else:
            new_el = f'    <meta name="calibre:series" content="{escaped}"/>\n'
            opf_text = re.sub(
                r'(</\s*(?:[^:>\s]+:)?metadata\s*>)',
                lambda m, el=new_el: el + m.group(1),
                opf_text,
                count=1,
                flags=re.IGNORECASE,
            )

    # Update or insert calibre:series_index
    if series_index:
        escaped_idx = _escape_xml(series_index)
        if re.search(r'name=["\']calibre:series_index["\']', opf_text):
            opf_text = re.sub(
                r'(<meta\s[^>]*name=["\']calibre:series_index["\'][^>]*\s)content=["\'][^"\']*["\']',
                lambda m: m.group(1) + f'content="{escaped_idx}"',
                opf_text,
                count=1,
                flags=re.IGNORECASE,
            )
            opf_text = re.sub(
                r'(<meta\s[^>]*?)content=["\'][^"\']*["\']([^>]*name=["\']calibre:series_index["\'])',
                lambda m: m.group(1) + f'content="{escaped_idx}"' + m.group(2),
                opf_text,
                count=1,
                flags=re.IGNORECASE,
            )
        else:
            new_el = f'    <meta name="calibre:series_index" content="{escaped_idx}"/>\n'
            opf_text = re.sub(
                r'(</\s*(?:[^:>\s]+:)?metadata\s*>)',
                lambda m, el=new_el: el + m.group(1),
                opf_text,
                count=1,
                flags=re.IGNORECASE,
            )

    # Simple dc: elements (update or insert)
    for dc_name, field in [
        ("publisher", "publisher"),
        ("date",      "pub_date"),
        ("description", "description"),
        ("language",  "language"),
        ("rights",    "rights"),
        ("contributor", "contributor"),
    ]:
        value = (chosen.get(field) or "").strip()
        if value:
            opf_text = _upsert_dc_element(opf_text, dc_name, value)

    isbn = (chosen.get("isbn") or "").strip()
    if isbn:
        opf_text = _upsert_isbn(opf_text, isbn)

    tags = (chosen.get("tags") or "").strip()
    if tags:
        opf_text = _upsert_meta_element(opf_text, "keywords", tags)

    return opf_text


# ---------------------------------------------------------------------------
# EPUB writer (pure Python)
# ---------------------------------------------------------------------------


def _update_opf_bytes(
    opf_bytes: bytes, chosen: Dict[str, Optional[str]]
) -> Union[bytes, str]:
    """Apply chosen metadata to OPF bytes, returning updated bytes or an error string."""
    try:
        opf_text = opf_bytes.decode("utf-8", errors="replace")
    except Exception as exc:
        return f"OPF decode error: {exc}"

    updated = _update_opf_text(opf_text, chosen)
    return updated.encode("utf-8")


_ALLOWED_IMAGE_SCHEMES = ("http", "https")
_MAX_IMAGE_BYTES = 50 * 1024 * 1024  # 50 MB — far above any real cover; bounds memory use


def _download_image(url: str, timeout: int = 10) -> tuple:
    """Download an image URL over HTTP(S). Returns (bytes, mime_type) or raises on failure.

    Only http/https are honoured. Cover URLs can originate from web metadata
    sources or be hand-edited, and urllib.request would otherwise happily open
    ``file://`` (local file disclosure) or ``ftp://`` (SSRF) URLs — so the
    scheme is validated here, at the single download chokepoint.
    """
    import urllib.parse as _parse
    import urllib.request as _req

    scheme = _parse.urlparse(url).scheme.lower()
    if scheme not in _ALLOWED_IMAGE_SCHEMES:
        raise ValueError(f"Refusing to fetch cover from non-HTTP(S) URL: {scheme!r}")

    req = _req.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with _req.urlopen(req, timeout=timeout) as resp:
        data = resp.read(_MAX_IMAGE_BYTES + 1)
        mime = resp.headers.get_content_type() or "image/jpeg"
    if len(data) > _MAX_IMAGE_BYTES:
        raise ValueError("Cover image exceeds maximum allowed size")
    return data, mime



def _embed_cover_in_epub(
    all_files: Dict[str, bytes],
    opf_path: str,
    cover_bytes: bytes,
    mime_type: str,
) -> None:
    """Add cover image into the EPUB file dict and patch the OPF manifest and spine."""
    ext = "png" if "png" in mime_type.lower() else "jpg"
    opf_dir = opf_path.rsplit("/", 1)[0] if "/" in opf_path else ""
    cover_filename = f"cover.{ext}"
    cover_full_path = f"{opf_dir}/{cover_filename}".lstrip("/") if opf_dir else cover_filename
    cover_page_filename = "cover-page.xhtml"
    cover_page_full_path = (
        f"{opf_dir}/{cover_page_filename}".lstrip("/") if opf_dir else cover_page_filename
    )

    all_files[cover_full_path] = cover_bytes

    # Minimal XHTML wrapper so readers render the cover as the first page of the book.
    all_files[cover_page_full_path] = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml">\n'
        '<head><title>Cover</title>'
        '<style type="text/css">body{margin:0;padding:0}'
        ' img{max-width:100%;max-height:100%}</style></head>\n'
        '<body><div style="text-align:center">'
        f'<img src="{cover_filename}" alt="Cover"/>'
        '</div></body>\n</html>'
    ).encode("utf-8")

    opf_text = all_files[opf_path].decode("utf-8", errors="replace")

    # Remove any existing cover-image manifest item to avoid duplicates
    opf_text = re.sub(
        r'\s*<item\b[^>]*properties=["\'][^"\']*cover-image[^"\']*["\'][^>]*/?>',
        "", opf_text, flags=re.IGNORECASE,
    )
    opf_text = re.sub(
        r'\s*<item\b[^>]*id=["\']cover-image["\'][^>]*/?>',
        "", opf_text, flags=re.IGNORECASE,
    )
    # Remove existing cover-page manifest item and spine reference (re-added below)
    opf_text = re.sub(
        r'\s*<item\b[^>]*id=["\']cover-page["\'][^>]*/?>',
        "", opf_text, flags=re.IGNORECASE,
    )
    opf_text = re.sub(
        r'\s*<itemref\b[^>]*idref=["\']cover-page["\'][^>]*/?>',
        "", opf_text, flags=re.IGNORECASE,
    )

    # Insert cover-image and cover-page manifest items
    new_items = (
        f'\n    <item id="cover-image" href="{cover_filename}"'
        f' media-type="{mime_type}" properties="cover-image"/>'
        f'\n    <item id="cover-page" href="{cover_page_filename}"'
        ' media-type="application/xhtml+xml"/>'
    )
    opf_text = re.sub(
        r'(<manifest\b[^>]*>)',
        lambda m: m.group(1) + new_items,
        opf_text, count=1, flags=re.IGNORECASE,
    )

    # Prepend cover-page as the first spine item
    opf_text = re.sub(
        r'(<spine\b[^>]*>)',
        lambda m: m.group(1) + '\n    <itemref idref="cover-page"/>',
        opf_text, count=1, flags=re.IGNORECASE,
    )

    # Upsert <meta name="cover" content="cover-image"/>
    opf_text = _upsert_meta_element(opf_text, "cover", "cover-image")

    all_files[opf_path] = opf_text.encode("utf-8")


def _write_epub_metadata(
    filepath: Path,
    chosen: Dict[str, Optional[str]],
    cover_cache: Optional[Dict[str, tuple]] = None,
) -> Optional[str]:
    """Rewrite the OPF metadata inside an EPUB ZIP (no external tools required)."""
    try:
        # Read the whole ZIP into memory
        with zipfile.ZipFile(filepath, "r") as zf:
            names = zf.namelist()
            try:
                container_text = zf.read("META-INF/container.xml").decode(
                    "utf-8", errors="replace"
                )
            except KeyError:
                return "Invalid EPUB: missing META-INF/container.xml"

            opf_path = _find_opf_path(container_text)
            if not opf_path:
                return "Cannot locate OPF rootfile in META-INF/container.xml"
            if opf_path not in names:
                return f"OPF file listed in container.xml not found in ZIP: {opf_path}"

            all_files: Dict[str, bytes] = {name: zf.read(name) for name in names}

        # Update the OPF text metadata
        result = _update_opf_bytes(all_files[opf_path], chosen)
        if isinstance(result, str):
            return result  # error message from _update_opf_bytes
        all_files[opf_path] = result

        # Fix NCX <docTitle> — kindleunpack often writes garbled bytes there.
        title = (chosen.get("title") or "").strip()
        if title:
            for ncx_path in [k for k in all_files if k.endswith(".ncx")]:
                raw = all_files[ncx_path]
                text = raw.decode("utf-8", errors="replace")
                escaped = _escape_xml(title)
                new_doctitle = f"<docTitle><text>{escaped}</text></docTitle>"
                updated = re.sub(
                    r"<docTitle>\s*<text>.*?</text>\s*</docTitle>",
                    lambda m, el=new_doctitle: el,
                    text, count=1, flags=re.DOTALL,
                )
                if updated != text:
                    all_files[ncx_path] = updated.encode("utf-8")

        # Embed cover image only if the URL is already cached — never re-download at write time
        cover_url = (chosen.get("cover") or "").strip()
        if cover_url.startswith("http") and cover_cache and cover_url in cover_cache:
            try:
                cover_bytes, mime_type = cover_cache[cover_url]
                _embed_cover_in_epub(all_files, opf_path, cover_bytes, mime_type)
            except Exception as exc:
                logger.warning("Cover embed failed (%s): %s", cover_url, exc)

        # Write the updated EPUB to a temp file then atomically replace
        tmp = filepath.with_suffix(filepath.suffix + "._tmp")
        try:
            with zipfile.ZipFile(tmp, "w") as out_zf:
                # The EPUB spec requires 'mimetype' to be the first entry,
                # stored uncompressed.
                if "mimetype" in all_files:
                    info = zipfile.ZipInfo("mimetype")
                    info.compress_type = zipfile.ZIP_STORED
                    out_zf.writestr(info, all_files.pop("mimetype"))
                for name, data in all_files.items():
                    out_zf.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED)
            tmp.replace(filepath)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise

        return None

    except Exception as exc:
        return f"EPUB write error: {exc}"


# ---------------------------------------------------------------------------
# MOBI / AZW3 / AZW writer (pure Python — PalmDB EXTH manipulation)
# ---------------------------------------------------------------------------

# EXTH record type codes
_EXTH_AUTHOR = 100
_EXTH_PUBLISHER = 101
_EXTH_DESCRIPTION = 103
_EXTH_ISBN = 104
_EXTH_SUBJECT = 105
_EXTH_PUB_DATE = 106
_EXTH_CONTRIBUTOR = 108
_EXTH_RIGHTS = 109
_EXTH_UPDATED_TITLE = 503
_EXTH_SERIES = 517       # Calibre extension, recognised by most readers
_EXTH_SERIES_INDEX = 518  # Calibre extension
_EXTH_LANGUAGE = 524


def _write_mobi_metadata(
    filepath: Path, chosen: Dict[str, Optional[str]]
) -> Optional[str]:
    """Patch EXTH metadata records in a MOBI / AZW3 / AZW file in-place."""
    try:
        raw = filepath.read_bytes()
    except Exception as exc:
        return f"Read error: {exc}"

    try:
        patched = _patch_mobi(raw, chosen)
    except Exception as exc:
        return f"MOBI parse error: {exc}"

    if patched is None:
        return None  # nothing to write

    if isinstance(patched, str):
        return patched  # error message

    tmp = filepath.with_suffix(filepath.suffix + "._tmp")
    try:
        tmp.write_bytes(patched)
        tmp.replace(filepath)
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        return f"Write error: {exc}"
    return None


def _patch_mobi(raw: bytes, chosen: Dict[str, Optional[str]]) -> Union[bytes, str, None]:
    """Return patched bytes, an error string, or None if nothing to write."""

    if len(raw) < 78:
        return "File too small to be a valid MOBI"

    num_records = struct.unpack_from(">H", raw, 76)[0]
    if num_records < 1:
        return "No PalmDB records found"

    offsets = [struct.unpack_from(">I", raw, 78 + i * 8)[0] for i in range(num_records)]
    rec0_start = offsets[0]
    rec0_end = offsets[1] if num_records > 1 else len(raw)

    # Locate MOBI header: typically at rec0_start+32 (after PalmDOC header) but
    # some variants place it at a different offset, so scan the first 128 bytes.
    mobi_id: Optional[int] = None
    for scan in range(rec0_start, min(rec0_start + 128, rec0_end - 4), 4):
        if raw[scan : scan + 4] == b"MOBI":
            mobi_id = scan
            break

    if mobi_id is None:
        return (
            "Cannot locate MOBI header — file may be DRM-protected "
            "or in an unsupported format"
        )
    if len(raw) < mobi_id + 136:
        return "MOBI header too short"

    mobi_hdr_len = struct.unpack_from(">I", raw, mobi_id + 4)[0]
    full_title_off = struct.unpack_from(">I", raw, mobi_id + 84)[0]   # from rec0_start
    full_title_len = struct.unpack_from(">I", raw, mobi_id + 88)[0]
    exth_flags = struct.unpack_from(">I", raw, mobi_id + 128)[0]

    exth_abs = mobi_id + mobi_hdr_len  # absolute start of EXTH area

    # Parse existing EXTH records
    existing: List[Tuple[int, bytes]] = []
    if (exth_flags & 0x40) and raw[exth_abs : exth_abs + 4] == b"EXTH":
        n_exth = struct.unpack_from(">I", raw, exth_abs + 8)[0]
        pos = exth_abs + 12
        for _ in range(n_exth):
            if pos + 8 > len(raw):
                break
            rec_type, rec_len = struct.unpack_from(">II", raw, pos)
            if rec_len < 8 or pos + rec_len > len(raw):
                break
            existing.append((rec_type, raw[pos + 8 : pos + rec_len]))
            pos += rec_len

    # Build the map of EXTH types → new bytes we want to write
    updates: Dict[int, bytes] = {}

    def _add(exth_type: int, field: str) -> None:
        v = (chosen.get(field) or "").strip()
        if v:
            updates[exth_type] = v.encode("utf-8")

    _add(_EXTH_UPDATED_TITLE, "title")
    _add(_EXTH_AUTHOR, "author")
    _add(_EXTH_PUBLISHER, "publisher")
    _add(_EXTH_DESCRIPTION, "description")
    _add(_EXTH_ISBN, "isbn")
    _add(_EXTH_SUBJECT, "subject")
    _add(_EXTH_PUB_DATE, "pub_date")
    _add(_EXTH_CONTRIBUTOR, "contributor")
    _add(_EXTH_RIGHTS, "rights")
    _add(_EXTH_LANGUAGE, "language")
    _add(_EXTH_SERIES, "series")
    _add(_EXTH_SERIES_INDEX, "series_index")
    # tags: no standard MOBI EXTH type; skip

    if not updates:
        return None

    # Merge: keep existing records not being replaced, then append new ones
    update_types = set(updates)
    merged: List[Tuple[int, bytes]] = [
        (t, d) for (t, d) in existing if t not in update_types
    ]
    merged.extend(updates.items())

    # Build new EXTH block (header + records, padded to 4-byte boundary)
    records_payload = b"".join(
        struct.pack(">II", t, 8 + len(d)) + d for (t, d) in merged
    )
    exth_content_len = 12 + len(records_payload)
    exth_pad = (-exth_content_len) % 4
    new_exth = (
        b"EXTH"
        + struct.pack(">II", exth_content_len, len(merged))
        + records_payload
        + bytes(exth_pad)
    )

    # Determine new full title bytes
    title = (chosen.get("title") or "").strip()
    new_full_title = (
        title.encode("utf-8")
        if title
        else raw[rec0_start + full_title_off : rec0_start + full_title_off + full_title_len]
    )

    # Tail: anything in record 0 after the old full title (e.g. alignment padding)
    tail = raw[rec0_start + full_title_off + full_title_len : rec0_end]

    # Build the patched MOBI header (update full_title_off, full_title_len, EXTH flag)
    mobi_hdr = bytearray(raw[mobi_id : mobi_id + mobi_hdr_len])
    pre_mobi_len = mobi_id - rec0_start  # PalmDOC header (usually 32 bytes)
    new_full_title_off = pre_mobi_len + mobi_hdr_len + len(new_exth)
    struct.pack_into(">I", mobi_hdr, 84, new_full_title_off)
    struct.pack_into(">I", mobi_hdr, 88, len(new_full_title))
    struct.pack_into(">I", mobi_hdr, 128, exth_flags | 0x40)

    # Assemble new record 0
    new_rec0 = (
        raw[rec0_start : mobi_id]  # PalmDOC header (unchanged)
        + bytes(mobi_hdr)
        + new_exth
        + new_full_title
        + tail
    )
    delta = len(new_rec0) - (rec0_end - rec0_start)

    # Rebuild PalmDB prefix with updated record offsets (record 0 stays put; 1..n shift)
    prefix = bytearray(raw[:rec0_start])
    for i in range(1, num_records):
        old_off = struct.unpack_from(">I", prefix, 78 + i * 8)[0]
        struct.pack_into(">I", prefix, 78 + i * 8, old_off + delta)

    # Update PalmDB book-name field (bytes 0-31) with the new title (cosmetic, latin-1)
    if title:
        name_bytes = title.encode("latin-1", errors="replace")[:31].ljust(32, b"\x00")
        prefix[0:32] = name_bytes

    return bytes(prefix) + new_rec0 + raw[rec0_end:]


# ---------------------------------------------------------------------------
# PDF writer (pypdf)
# ---------------------------------------------------------------------------


def _write_pdf_metadata(
    filepath: Path, chosen: Dict[str, Optional[str]]
) -> Optional[str]:
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError:
        return "pypdf not installed — run: pip install pypdf"

    try:
        reader = PdfReader(str(filepath))
        writer = PdfWriter()
        writer.append(reader)

        meta: Dict[str, str] = {}
        def _add(key: str, field: str) -> None:
            v = (chosen.get(field) or "").strip()
            if v:
                meta[key] = v

        _add("/Title", "title")
        _add("/Author", "author")
        _add("/Subject", "subject")
        _add("/Keywords", "tags")
        _add("/Publisher", "publisher")
        _add("/Description", "description")
        _add("/PublicationDate", "pub_date")
        _add("/ISBN", "isbn")
        _add("/Language", "language")
        _add("/Rights", "rights")
        _add("/Contributor", "contributor")
        # series/series_index: no standard PDF metadata field; skip

        if not meta:
            return None

        writer.add_metadata(meta)

        tmp = filepath.with_suffix(filepath.suffix + "._tmp")
        try:
            with open(str(tmp), "wb") as f:
                writer.write(f)
            tmp.replace(filepath)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise

        return None

    except Exception as exc:
        return f"PDF write error: {exc}"


# ---------------------------------------------------------------------------
# EPUB chapter splitting / rebuild
# ---------------------------------------------------------------------------


def split_epub_by_toc(filepath: Path) -> Optional[str]:
    """Rebuild *filepath* as a clean EPUB 3.3 with one spine item per chapter.

    Extracts content and metadata from the existing EPUB, passes it through the
    clean assembler, and atomically replaces the file.  EPUBs already produced
    by this tool are left untouched (they are already in the ideal state).

    Returns an error string on failure, or None on success (including the
    no-op case where the file is already ours).
    """
    from .epub_build import is_our_epub, build_epub
    from .epub_clean import read_epub_book, check_epub_integrity

    if is_our_epub(filepath):
        return None  # Already in ideal state - do nothing.

    book, err = read_epub_book(filepath)
    if book is None:
        return f"EPUB rebuild error: {err}"

    build_err = build_epub(book, filepath)
    if build_err:
        return build_err

    warnings = check_epub_integrity(filepath, book)
    if warnings:
        logger.warning("[integrity] %s: %s", filepath.name, "; ".join(warnings))

    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def write_metadata(
    filepath: Path,
    chosen: Dict[str, Optional[str]],
    cover_cache: Optional[Dict[str, tuple]] = None,
) -> Optional[str]:
    """Write *chosen* metadata to *filepath*, routing by file extension.

    cover_cache maps cover URL → (bytes, mime_type); used to avoid re-downloading
    images that were already fetched during the session.

    Returns an error string on failure, or ``None`` on success.
    All writers are pure-Python — no external tools required.
    """
    ext = filepath.suffix.lower()
    if ext == ".epub":
        return _write_epub_metadata(filepath, chosen, cover_cache)
    if ext in (".mobi", ".azw3", ".azw", ".prc"):
        return _write_mobi_metadata(filepath, chosen)
    if ext == ".pdf":
        return _write_pdf_metadata(filepath, chosen)
    return None  # unsupported format — skip silently


def apply_record(
    rec: BookRecord,
    operation: str,  # "copy" or "move"
    on_clash: str,  # "append" or "replace"
    cover_cache: Optional[Dict[str, tuple]] = None,
) -> Tuple[bool, str]:
    """Copy/move *rec* to its computed new_filepath, then write metadata there.

    The source file is never modified.  Metadata is written only to the
    destination so a "copy" leaves the original completely untouched.

    Returns ``(success, message)``.
    On a successful move ``rec.filepath`` is updated to the destination.
    """
    if rec.new_filepath is None:
        return False, "No new filepath computed — scan first and check the output pattern"

    src = rec.filepath
    dst = rec.new_filepath

    # 1. Skip file op when source and destination are the same path
    try:
        same = src.resolve() == dst.resolve()
    except Exception:
        same = str(src) == str(dst)

    if not same:
        # 2. Ensure destination directory exists
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            return False, f"Cannot create directory {dst.parent}: {exc}"

        # 3. Handle destination clash
        if dst.exists():
            if on_clash == "append":
                dst = make_unique_path(dst)
            # on_clash == "replace": shutil will overwrite

        # 4. Copy or move
        try:
            if operation == "copy":
                shutil.copy2(str(src), str(dst))
            else:
                shutil.move(str(src), str(dst))
                # Update record immediately after move so rec.filepath reflects
                # reality even if the subsequent metadata write fails — the file
                # is at dst now and src no longer exists.
                rec.filepath = dst
                rec.new_filepath = None
        except Exception as exc:
            return False, f"File operation failed: {exc}"

    # 5. When copy + same path: preserve original, create numbered copy with new metadata
    if same and operation == "copy":
        dst = make_unique_path(dst)
        try:
            shutil.copy2(str(src), str(dst))
        except Exception as exc:
            return False, f"File operation failed: {exc}"
        err = write_metadata(dst, rec.chosen_metadata, cover_cache)
        if err:
            return False, f"Metadata write failed: {err}"
        return True, f"Copied → {dst}"

    # 5. Write metadata to the destination (never touches the source)
    err = write_metadata(dst, rec.chosen_metadata, cover_cache)
    if err:
        return False, f"Metadata write failed: {err}"

    meta_note = ""

    if same:
        return True, f"Metadata written (file already at target location){meta_note}"

    if operation == "copy":
        return True, f"Copied → {dst}{meta_note}"
    else:
        rec.filepath = dst
        rec.new_filepath = None
        return True, f"Moved → {dst}{meta_note}"
