from typing import List

from .models import BookRecord
from . import pattern_engine

METADATA_FIELDS = {
    "author", "title", "series", "series_index", "subject", "tags",
    "publisher", "pub_date", "description", "isbn", "language", "rights", "contributor",
    "cover",
}
_SETTINGS_ORG = "ebookmetafile"
_SETTINGS_APP = "EbookMetafile"
DEFAULT_FILENAME_PATTERN = r"{dir_in}\{author} - {series} {series_index} - {title}"
DEFAULT_OUTPUT_PATTERN = r"{dir_out}\{author} - {series} {series_index} - {title}"

DEFAULT_FILENAME_PATTERNS: List[str] = [
    DEFAULT_FILENAME_PATTERN,
    "",
    "",
]

ROWS_PER_BOOK = 7
ROW_CHOSEN, ROW_FILENAME, ROW_FILEMETA, ROW_GOOGLE, ROW_OPENLIBRARY, ROW_ISFDB, ROW_AMAZON = 0, 1, 2, 3, 4, 5, 6
SOURCE_MAP = {
    ROW_FILENAME: "pattern",
    ROW_FILEMETA: "file",
    ROW_GOOGLE: "google",
    ROW_OPENLIBRARY: "openlibrary",
    ROW_ISFDB: "isfdb",
    ROW_AMAZON: "amazon",
}

_cover_image_cache: dict = {}  # key → (bytes, mime_type), populated on demand
_pixmap_cache: dict = {}       # key → QPixmap, populated lazily by ThumbnailDelegate


def embedded_cover_cache_key(filepath, url: str) -> str:
    """Unique cache key for an embedded cover image.

    Different EPUBs commonly use the same internal path (e.g. OEBPS/cover.jpg),
    so the key must be qualified by the book's filepath to prevent collisions.
    """
    return f"{filepath}|{url}"


def _image_dimensions(data: bytes):
    """Return (width, height) from raw JPEG or PNG bytes, or None if unreadable."""
    if not data or len(data) < 8:
        return None
    # PNG: magic at 0-7, width at 16-19, height at 20-23 (IHDR chunk)
    if data[:8] == b'\x89PNG\r\n\x1a\n' and len(data) >= 24:
        w = int.from_bytes(data[16:20], 'big')
        h = int.from_bytes(data[20:24], 'big')
        return w, h
    # JPEG: scan for SOF marker (0xFF 0xCn) which carries precision/height/width
    if data[:2] == b'\xff\xd8':
        i = 2
        while i < len(data) - 9:
            if data[i] != 0xFF:
                break
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB):
                h = int.from_bytes(data[i + 5: i + 7], 'big')
                w = int.from_bytes(data[i + 7: i + 9], 'big')
                return w, h
            if i + 3 >= len(data):
                break
            seg_len = int.from_bytes(data[i + 2: i + 4], 'big')
            if seg_len < 2:
                break
            i += 2 + seg_len
    return None


def _cover_source_name(url: str) -> str:
    """Short source label for the cover preview window (no dimensions).

    The preview window appends its own pixel dimensions from the QPixmap,
    so the label it receives should be the source name only.
    """
    if not url:
        return ""
    if url.startswith("embedded:"):
        path = url[len("embedded:"):]
        return f"Embedded · {path.split('/')[-1]}"
    if "books.google.com" in url or "googleapis.com" in url:
        return "Google Books"
    if "covers.openlibrary.org" in url:
        return "Open Library"
    if "isfdb.org" in url:
        return "ISFDB"
    if "amazon.com" in url or "images-amazon.com" in url or "media-amazon.com" in url:
        return "Amazon"
    return url[:60] + ("…" if len(url) > 60 else "")


def _format_cover_display(url: str, image_bytes: bytes | None = None) -> str:
    """Human-readable summary of a cover URL for table cell display.

    Shows pixel dimensions when *image_bytes* are available (already cached),
    otherwise shows just the source name until the image is loaded.
    """
    if not url:
        return ""
    dims = _image_dimensions(image_bytes) if image_bytes else None
    dims_str = f"{dims[0]}×{dims[1]}" if dims else None

    if url.startswith("embedded:"):
        # For embedded covers show dimensions when loaded, filename when not
        path = url[len("embedded:"):]
        suffix = dims_str or path.split("/")[-1]
        return f"Embedded · {suffix}"

    source = _cover_source_name(url)
    return f"{source} · {dims_str}" if dims_str else source


def _try_patterns(patterns: List[str], rec: "BookRecord") -> None:
    """Apply input patterns to *rec* in order, stopping at the first that parses OK."""
    valid = [p for p in patterns if p.strip()]
    if not valid:
        return
    last_parsed: dict = {}
    last_status: str = "No pattern specified"
    last_pattern: str = valid[-1]
    for pat in valid:
        parsed, status = pattern_engine.parse_filename(pat, rec.filepath)
        last_parsed, last_status, last_pattern = parsed, status, pat
        if status == "OK":
            break
    rec.pattern = last_pattern
    rec.metadata_pattern = last_parsed
    rec.pattern_status = last_status
    rec.sync_dir_out()
    rec.ensure_chosen_defaults(list(METADATA_FIELDS))
    rec.recompute_new_filepath()
