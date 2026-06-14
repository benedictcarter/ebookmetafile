"""Read metadata from ebook files — pure Python, no external tools required."""

import re
import struct
import zipfile
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .models import BookRecord, METADATA_FIELDS

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _empty_meta() -> Dict[str, Optional[str]]:
    return {f: None for f in ("author", "title", "series", "series_index", "subject",
                               "tags", "publisher", "pub_date", "description",
                               "isbn", "language", "rights", "contributor", "cover")}


# ---------------------------------------------------------------------------
# EPUB reader
# ---------------------------------------------------------------------------

_DC_TAGS = {
    "dc:title":        "title",
    "dc:creator":      "author",
    "dc:publisher":    "publisher",
    "dc:date":         "pub_date",
    "dc:description":  "description",
    "dc:language":     "language",
    "dc:rights":       "rights",
    "dc:contributor":  "contributor",
    "dc:subject":      "subject",
}

_XML_ENTITY = re.compile(r"&(amp|lt|gt|quot|apos);")
_XML_ENTITY_MAP = {"amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'"}


def _xe_decode(s: str) -> str:
    return _XML_ENTITY.sub(lambda m: _XML_ENTITY_MAP[m.group(1)], s)


def _read_epub_meta(filepath: Path) -> Tuple[Dict[str, Optional[str]], str]:
    """Return (meta_dict, format_state) by reading the OPF directly."""
    from .epub_build import _GENERATOR
    _WIN_BAD = frozenset('<>:"/\\|?*�')
    warn = " ⚠" if _WIN_BAD.intersection(filepath.name) else ""
    meta = _empty_meta()
    fmt = "EPUB" + warn

    try:
        with zipfile.ZipFile(filepath, "r") as zf:
            try:
                container = zf.read("META-INF/container.xml").decode("utf-8", errors="replace")
            except KeyError:
                return meta, fmt
            m = re.search(r'full-path=["\']([^"\']+)["\']', container)
            if not m:
                return meta, fmt
            opf_path = m.group(1)
            try:
                opf_text = zf.read(opf_path).decode("utf-8", errors="replace")
            except KeyError:
                return meta, fmt

            # ── Format state ──────────────────────────────────────────────
            ver_m = re.search(
                r'<package\b[^>]*\bversion\s*=\s*["\']([^"\']+)["\']', opf_text, re.IGNORECASE
            )
            major = ver_m.group(1).split(".")[0] if ver_m else ""
            if f'content="{_GENERATOR}"' in opf_text:
                fmt = "EPUB 3.3 ✓"
            elif major == "3":
                fmt = "EPUB 3"
            elif major == "2":
                fmt = "EPUB 2"
            else:
                fmt = "EPUB"
            fmt += warn

            # ── DC metadata fields ────────────────────────────────────────
            for dc_tag, field in _DC_TAGS.items():
                m2 = re.search(
                    rf"<{dc_tag}\b[^>]*>([^<]*)</{dc_tag}>",
                    opf_text, re.IGNORECASE,
                )
                if m2:
                    val = _xe_decode(m2.group(1).strip())
                    if val:
                        meta[field] = val

            # Multiple dc:subject → join as tags
            subjects = re.findall(
                r"<dc:subject\b[^>]*>([^<]+)</dc:subject>", opf_text, re.IGNORECASE
            )
            if subjects:
                meta["subject"] = _xe_decode(subjects[0].strip())
                if len(subjects) > 1:
                    meta["tags"] = ", ".join(_xe_decode(s.strip()) for s in subjects[1:])

            # ISBN from dc:identifier with opf:scheme="ISBN"/"isbn"
            for id_m in re.finditer(
                r'<dc:identifier\b([^>]*)>([^<]*)</dc:identifier>', opf_text, re.IGNORECASE
            ):
                attrs, val = id_m.group(1), id_m.group(2).strip()
                if re.search(r'(?:opf:scheme|scheme)\s*=\s*["\']isbn["\']', attrs, re.IGNORECASE):
                    meta["isbn"] = _xe_decode(val)
                    break

            # Calibre series / series_index from <meta> elements
            for meta_m in re.finditer(
                r'<meta\b[^>]*name=["\']([^"\']+)["\'][^>]*content=["\']([^"\']+)["\']',
                opf_text, re.IGNORECASE,
            ):
                name, content = meta_m.group(1).lower(), _xe_decode(meta_m.group(2).strip())
                if name == "calibre:series" and not meta.get("series"):
                    meta["series"] = content
                elif name == "calibre:series_index" and not meta.get("series_index"):
                    meta["series_index"] = content

            # ── Cover detection ───────────────────────────────────────────
            opf_dir = opf_path.rsplit("/", 1)[0] if "/" in opf_path else ""

            def _full(href: str) -> str:
                return f"{opf_dir}/{href}".lstrip("/") if opf_dir else href

            def _item_href(extra_attr_pattern: str) -> Optional[str]:
                for pat in [
                    r'<item\b[^>]*' + extra_attr_pattern + r'[^>]*href=["\']([^"\']+)["\']',
                    r'<item\b[^>]*href=["\']([^"\']+)["\']' + r'[^>]*' + extra_attr_pattern,
                ]:
                    hit = re.search(pat, opf_text, re.IGNORECASE)
                    if hit:
                        return hit.group(1)
                return None

            href = _item_href(r'properties=["\'][^"\']*cover-image[^"\']*["\']')
            if href:
                meta["cover"] = f"embedded:{_full(href)}"
            else:
                for mp in [
                    r'<meta\b[^>]*name=["\']cover["\'][^>]*content=["\']([^"\']+)["\']',
                    r'<meta\b[^>]*content=["\']([^"\']+)["\'][^>]*name=["\']cover["\']',
                ]:
                    mm = re.search(mp, opf_text, re.IGNORECASE)
                    if mm:
                        href = _item_href(r'id=["\']' + re.escape(mm.group(1)) + r'["\']')
                        if href:
                            meta["cover"] = f"embedded:{_full(href)}"
                        break
                if not meta["cover"]:
                    href = _item_href(r'id=["\'][^"\']*cover[^"\']*["\'][^>]*media-type=["\']image/')
                    if href:
                        meta["cover"] = f"embedded:{_full(href)}"
                if not meta["cover"]:
                    hit = re.search(
                        r'<item\b[^>]*href=["\']([^"\']*cover[^"\']*\.(?:jpe?g|png|gif|webp)[^"\']*)["\']'
                        r'[^>]*media-type=["\']image/',
                        opf_text, re.IGNORECASE,
                    )
                    if hit:
                        meta["cover"] = f"embedded:{_full(hit.group(1))}"

    except Exception:
        pass

    return meta, fmt


# ---------------------------------------------------------------------------
# MOBI / AZW3 reader
# ---------------------------------------------------------------------------

# EXTH record type → field name (read side)
_EXTH_READ_MAP: Dict[int, str] = {
    100: "author",
    101: "publisher",
    103: "description",
    104: "isbn",
    105: "subject",
    106: "pub_date",
    108: "contributor",
    109: "rights",
    503: "title",        # MOBI updated title (preferred over PalmDB name)
    517: "series",
    518: "series_index",
    524: "language",
}


def _read_mobi_meta(filepath: Path) -> Tuple[Dict[str, Optional[str]], str]:
    """Read metadata from a MOBI/AZW3/PRC file by parsing PalmDB + EXTH headers."""
    meta = _empty_meta()
    try:
        raw = filepath.read_bytes()
    except Exception:
        return meta, "MOBI"

    suffix = filepath.suffix.lower()
    fmt = "AZW3" if suffix in (".azw3", ".azw") else "MOBI"

    try:
        if len(raw) < 82:
            return meta, fmt
        num_records = struct.unpack_from(">H", raw, 76)[0]
        if num_records < 1:
            return meta, fmt
        rec0_start = struct.unpack_from(">I", raw, 78)[0]
        rec0_end = struct.unpack_from(">I", raw, 86)[0] if num_records > 1 else len(raw)

        # PalmDB book name (bytes 0-31, null-terminated, latin-1)
        palm_name = raw[:32].split(b"\x00")[0]
        try:
            palm_title = palm_name.decode("latin-1").strip()
        except Exception:
            palm_title = ""

        # Locate MOBI header
        mobi_id: Optional[int] = None
        for scan in range(rec0_start, min(rec0_start + 128, rec0_end - 4), 4):
            if raw[scan: scan + 4] == b"MOBI":
                mobi_id = scan
                break
        if mobi_id is None:
            if palm_title:
                meta["title"] = palm_title
            return meta, fmt

        mobi_hdr_len = struct.unpack_from(">I", raw, mobi_id + 4)[0]
        exth_flags = struct.unpack_from(">I", raw, mobi_id + 128)[0]

        # Full Name field (most reliable title source)
        fn_off = struct.unpack_from(">I", raw, mobi_id + 84)[0]
        fn_len = struct.unpack_from(">I", raw, mobi_id + 88)[0]
        if fn_len > 0 and rec0_start + fn_off + fn_len <= len(raw):
            try:
                full_name = raw[rec0_start + fn_off: rec0_start + fn_off + fn_len].decode(
                    "utf-8", errors="replace"
                ).strip()
                if full_name:
                    meta["title"] = full_name
            except Exception:
                pass
        if not meta["title"] and palm_title:
            meta["title"] = palm_title

        # Parse EXTH records
        exth_abs = mobi_id + mobi_hdr_len
        if (exth_flags & 0x40) and raw[exth_abs: exth_abs + 4] == b"EXTH":
            n_exth = struct.unpack_from(">I", raw, exth_abs + 8)[0]
            pos = exth_abs + 12
            for _ in range(n_exth):
                if pos + 8 > len(raw):
                    break
                rec_type, rec_len = struct.unpack_from(">II", raw, pos)
                if rec_len < 8 or pos + rec_len > len(raw):
                    break
                data = raw[pos + 8: pos + rec_len]
                field = _EXTH_READ_MAP.get(rec_type)
                if field and not meta.get(field):
                    try:
                        meta[field] = data.decode("utf-8", errors="replace").strip() or None
                    except Exception:
                        pass
                pos += rec_len

    except Exception:
        pass

    return meta, fmt


# ---------------------------------------------------------------------------
# PDF reader
# ---------------------------------------------------------------------------

def _read_pdf_meta(filepath: Path) -> Tuple[Dict[str, Optional[str]], str]:
    """Read metadata from a PDF using pypdf."""
    meta = _empty_meta()
    try:
        import pypdf
        reader = pypdf.PdfReader(str(filepath), strict=False)
        info = reader.metadata or {}

        def _g(key: str) -> Optional[str]:
            v = info.get(key)
            return str(v).strip() if v else None

        meta["title"]    = _g("/Title")
        meta["author"]   = _g("/Author")
        meta["subject"]  = _g("/Subject")
        meta["publisher"] = _g("/Producer") or _g("/Creator")
        # PDF has no isbn/series/language fields in standard DocInfo
    except Exception:
        pass

    from .file_convert import has_invalid_filename_chars
    warn = " ⚠" if has_invalid_filename_chars(filepath) else ""
    return meta, "PDF" + warn


# ---------------------------------------------------------------------------
# Compatibility shims (imported by tests and legacy callers)
# ---------------------------------------------------------------------------

def _inspect_epub_file(filepath: Path) -> Tuple[Optional[str], str]:
    """Return (cover_url_or_None, format_state) — compatibility wrapper."""
    meta, fmt = _read_epub_meta(filepath)
    return meta.get("cover"), fmt


def _extract_fields_from_exiftool_record(rec: dict) -> dict:
    """Legacy stub — no longer used; returns empty meta dict."""
    return _empty_meta()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def read_metadata_for_files(
    files: List[Path],
    exiftool_path: str = "",          # kept for API compatibility, ignored
    max_batch: int = 100,             # kept for API compatibility, ignored
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> List[BookRecord]:
    """Read metadata from a list of ebook files using pure Python readers."""
    records: List[BookRecord] = []
    total = len(files)

    for i, filepath in enumerate(files):
        suffix = filepath.suffix.lower()
        if suffix == ".epub":
            meta, fmt_state = _read_epub_meta(filepath)
        elif suffix in (".mobi", ".azw3", ".azw", ".prc"):
            meta, fmt_state = _read_mobi_meta(filepath)
        elif suffix == ".pdf":
            meta, fmt_state = _read_pdf_meta(filepath)
        else:
            meta, fmt_state = _empty_meta(), suffix.lstrip(".").upper()

        book = BookRecord(
            id=i + 1,
            filepath=filepath,
            metadata_file=meta,
            format_state=fmt_state,
        )
        book.pattern_status = ""
        book.metadata_pattern = {}
        book.ensure_chosen_defaults(list(METADATA_FIELDS))
        book.recompute_new_filepath()
        records.append(book)

        if progress_callback:
            progress_callback(i + 1, total)

    return records
