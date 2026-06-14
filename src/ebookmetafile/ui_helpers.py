"""UI helper utilities shared across GUI backends."""

import difflib
import re
import unicodedata
from typing import Optional


def get_source_row_highlight(source_val: str, chosen_val: str) -> Optional[str]:
    """Return 'blue' if source_val matches chosen_val, None otherwise.

    Used for source sub-rows (Filename, File meta) to show which value was chosen.
    """
    if not source_val:
        return None
    if source_val.strip() == (chosen_val or "").strip():
        return "blue"
    return "red"


def color_key_to_hex(key: Optional[str]) -> Optional[str]:
    mapping = {
        "green":  "#99ee99",
        "orange": "#ffd080",
        "red":    "#ff9999",
        "blue":   "#99bbff",
    }
    return mapping.get(key)


def _norm(s: str) -> str:
    """Lowercase, strip accents, remove punctuation, collapse whitespace."""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^\w\s]", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def _ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _author_ratio(a: str, b: str) -> float:
    """Best ratio across direct and Last, First ↔ First Last rearrangements."""
    na, nb = _norm(a), _norm(b)
    best = _ratio(na, nb)
    # Try swapping "last, first" to "first last" on both sides
    for s in (na, nb):
        if "," in s:
            parts = [p.strip() for p in s.split(",", 1)]
            swapped = " ".join(reversed(parts))
            other = nb if s == na else na
            best = max(best, _ratio(swapped, other))
    return best


def _extract_year(s: str) -> Optional[int]:
    m = re.search(r"\b(1[5-9]\d\d|20\d\d)\b", s)
    return int(m.group(1)) if m else None


def isbn_lookup_quality(
    chosen_title: str, chosen_author: str,
    source_title: str, source_author: str,
    chosen_year: str = "", source_year: str = "",
) -> Optional[str]:
    """Return 'green', 'orange', or 'red' based on fuzzy match quality.

    Returns None if there is not enough data to compare.
    """
    ct, st = _norm(chosen_title), _norm(source_title)
    if not ct or not st:
        return None

    t_ratio = _ratio(ct, st)
    a_ratio = _author_ratio(chosen_author, source_author) if chosen_author and source_author else None

    cy = _extract_year(chosen_year)
    sy = _extract_year(source_year)
    year_gap = abs(cy - sy) if cy and sy else None

    # Green: strong title + author match, and year within 5 years (or unknown)
    if t_ratio >= 0.80 and (a_ratio is None or a_ratio >= 0.70):
        if year_gap is None or year_gap <= 5:
            return "green"
        return "orange"  # good text match but noticeably different year

    # Orange: title recognisably similar, or title+author both partial
    if t_ratio >= 0.55 or (t_ratio >= 0.40 and a_ratio is not None and a_ratio >= 0.55):
        return "orange"

    return "red"


def parse_tsv(tsv: str) -> list[list[str]]:
    """Parse TSV text into a rectangular 2D list of strings.

    Pads rows so all rows have the same number of columns.
    """
    if not tsv:
        return []
    lines = tsv.splitlines()
    rows: list[list[str]] = [line.split("\t") for line in lines]
    max_cols = max((len(r) for r in rows), default=0)
    for r in rows:
        if len(r) < max_cols:
            r += [""] * (max_cols - len(r))
    return rows


def tile_clipboard_to_rect(
    clip_data: list[list[str]],
    start_row: int,
    start_col: int,
    end_row: int,
    end_col: int,
) -> dict:
    """Tile the clipboard rectangular block across the destination rectangle.

    Returns a mapping: dest_row -> { dest_col: text, ... }
    """
    result: dict[int, dict[int, str]] = {}
    if not clip_data:
        return result
    clip_rows = len(clip_data)
    clip_cols = len(clip_data[0]) if clip_rows else 0
    if clip_rows == 0 or clip_cols == 0:
        return result

    for r in range(start_row, end_row + 1):
        row_map: dict[int, str] = {}
        for c in range(start_col, end_col + 1):
            r_off = (r - start_row) % clip_rows
            c_off = (c - start_col) % clip_cols
            row_map[c] = clip_data[r_off][c_off]
        result[r] = row_map

    return result
