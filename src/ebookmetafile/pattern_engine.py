"""Pattern engine: parse file paths against a user-supplied template.

Pattern syntax
--------------
Segments are separated by ``/`` or ``\\``.  Within each segment,
``{placeholder}`` tokens capture variable text; literal text between them acts
as an anchor.

Special placeholder
-------------------
``{dir_in}``  The placeholder must occupy its own full segment.  It absorbs
           however many leading path components are left over once the other
           segments are assigned, and the absorbed value is stored in the
           result as ``"dir_in"``.
           Example::

               {dir_in}\\{subject}\\{author} - {series} {series_index} - {title}

           against ``S:\\ebooks\\scifi\\Le Guin - Hainish 01 - Rocannon.epub``
           yields dir_in=``S:\\ebooks``, subject=``scifi``, …

           When neither placeholder is present the rightmost *N* path components
           are matched (N = number of pattern segments).

Supported field placeholders
-----------------------------
  author, title, series, series_index, subject, tags
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Tuple

_METADATA_FIELDS: FrozenSet[str] = frozenset(
    {"author", "title", "series", "series_index", "subject", "tags"}
)

# series_index: digit-leading tokens  e.g. 06, 6.5, 1a, 1-2, 0.5
_INDEX_STRICT = r"\d[\d.]*[A-Za-z]?(?:-\d[\d.]*[A-Za-z]?)?"
# Loose fallback: any run of non-whitespace characters
_INDEX_LOOSE = r"\S+"


def _build_segment_regex(pattern_seg: str, strict_index: bool) -> Optional[re.Pattern]:
    """Compile *pattern_seg* into a regex with named capture groups.

    Returns ``None`` when the segment has no placeholders (pure literal).
    """
    tokens = re.split(r"(\{[^}]+\})", pattern_seg)
    # tokens alternates: literal, {field}, literal, {field}, …

    placeholders = [t[1:-1] for t in tokens if t.startswith("{") and t.endswith("}")]
    if not placeholders:
        return None
    if len(placeholders) != len(set(placeholders)):
        dupes = [p for p in set(placeholders) if placeholders.count(p) > 1]
        raise ValueError(f"Duplicate placeholder(s) in pattern: {{{', '.join(dupes)}}}")

    n = len(placeholders)
    ph_count = 0
    parts: List[str] = []

    for tok in tokens:
        if tok.startswith("{") and tok.endswith("}"):
            field = tok[1:-1]
            ph_count += 1
            is_last = ph_count == n

            if field == "series_index":
                idx_pat = _INDEX_STRICT if strict_index else _INDEX_LOOSE
                parts.append(f"(?P<{field}>{idx_pat})")
            elif is_last:
                parts.append(f"(?P<{field}>.+)")
            else:
                parts.append(f"(?P<{field}>.+?)")
        else:
            parts.append(re.escape(tok))

    return re.compile("".join(parts), re.IGNORECASE)


def _match_segment(pattern_seg: str, text: str) -> Tuple[Dict[str, str], str]:
    """Match *text* against a single *pattern_seg*.

    Tries the strict ``series_index`` regex first (digit-leading), then falls
    back to the loose variant so that non-numeric indexes are still captured.

    Returns ``(fields, status)`` where *status* is ``"OK"`` or an error string.
    """
    try:
        rx_strict = _build_segment_regex(pattern_seg, strict_index=True)
    except ValueError as exc:
        return {}, str(exc)

    if rx_strict is None:
        # Pure literal segment — require case-insensitive exact match
        if pattern_seg.casefold() == text.casefold():
            return {}, "OK"
        return {}, f"'{text}' did not match literal '{pattern_seg}'"

    m = rx_strict.fullmatch(text)
    if m is None:
        rx_loose = _build_segment_regex(pattern_seg, strict_index=False)
        m = rx_loose.fullmatch(text) if rx_loose else None

    if m is None:
        return {}, f"'{text}' did not match pattern '{pattern_seg}'"

    fields = {
        k: v.strip()
        for k, v in m.groupdict().items()
        if v is not None and k in _METADATA_FIELDS
    }
    return fields, "OK"


def parse_filename(pattern: str, path: Path) -> Tuple[Dict[str, str], str]:
    """Parse metadata from *path* using *pattern*.

    Returns ``(metadata_dict, status)`` where *status* is ``"OK"`` or a
    descriptive error/warning string.
    """
    if not pattern.strip():
        return {}, "No pattern set"

    # Normalise directory separators and split into segments
    pattern_segs: List[str] = pattern.replace("\\", "/").split("/")

    # Build path components with the filename replaced by its stem
    raw_parts: List[str] = list(path.parts)
    if not raw_parts:
        return {}, "Empty path"
    raw_parts[-1] = path.stem

    # Locate {dir_in} used as the absorbing prefix — must be a full segment.
    lib_indices = [
        i
        for i, s in enumerate(pattern_segs)
        if re.fullmatch(r"\s*\{dir_in\}\s*", s)
    ]
    has_lib = bool(lib_indices)
    non_lib_segs = (
        [s for i, s in enumerate(pattern_segs) if i not in lib_indices]
        if has_lib
        else pattern_segs
    )

    n_needed = len(non_lib_segs)
    n_available = len(raw_parts)

    if n_needed > n_available:
        suffix = " (excluding {dir_in})" if has_lib else ""
        return {}, (
            f"Path has {n_available} segment(s) but pattern needs "
            f"{n_needed}{suffix}"
        )

    # Absorb the leading path components that are not claimed by other segments
    n_lib = n_available - n_needed
    lib_value = str(Path(*raw_parts[:n_lib])) if n_lib > 0 else ""
    path_segs = raw_parts[n_lib:]

    # Match each non-dir_in pattern segment against its corresponding path segment
    result: Dict[str, str] = {"dir_in": lib_value} if has_lib else {}
    errors: List[str] = []

    for pat_seg, path_seg in zip(non_lib_segs, path_segs):
        fields, status = _match_segment(pat_seg, path_seg)
        result.update(fields)
        if status != "OK":
            errors.append(status)

    if errors:
        return result, "; ".join(errors)
    return result, "OK"
