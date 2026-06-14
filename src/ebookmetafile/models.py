"""Domain model for a single ebook record."""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


METADATA_FIELDS = {
    "author", "title", "series", "series_index", "subject", "tags",
    "publisher", "pub_date", "description", "isbn", "language", "rights", "contributor",
    "cover",
}

_WIN_FORBIDDEN = re.compile(r'[*?<>|/\\]')


def _sanitize(s: str) -> str:
    """Remove or replace characters that are illegal in Windows filenames."""
    s = s.replace(":", "-").replace('"', "'")
    s = _WIN_FORBIDDEN.sub("", s)
    while "  " in s:
        s = s.replace("  ", " ")
    return s.strip()

_SOURCE_LABELS = {0: "Chosen", 1: "Filename", 2: "File meta", 3: "Google", 4: "Open Library", 5: "ISFDB", 6: "Amazon"}
_SOURCE_DICTS = ("chosen_metadata", "metadata_pattern", "metadata_file", "metadata_google", "metadata_openlibrary", "metadata_isfdb", "metadata_amazon")

# Maps sub_row index → (source_key used in isbn_check_by_source, BookRecord attr name for that row's metadata).
# sub_row=1 (filename/pattern) is excluded — it never carries an ISBN.
_ISBN_LOOKUP_MAP: Dict[int, tuple] = {
    0: ("chosen",      "chosen_metadata"),
    2: ("file",        "metadata_file"),
    3: ("google",      "metadata_google"),
    4: ("openlibrary", "metadata_openlibrary"),
    5: ("isfdb",       "metadata_isfdb"),
    6: ("amazon",      "metadata_amazon"),
}


@dataclass
class BookRecord:
    """One ebook and all its associated metadata."""

    id: int
    filepath: Path

    metadata_file: Dict[str, Optional[str]] = field(default_factory=dict)

    pattern: str = ""
    metadata_pattern: Dict[str, Optional[str]] = field(default_factory=dict)
    pattern_status: str = ""

    chosen_metadata: Dict[str, Optional[str]] = field(default_factory=dict)

    metadata_google: Dict[str, Optional[str]] = field(default_factory=dict)
    metadata_openlibrary: Dict[str, Optional[str]] = field(default_factory=dict)
    metadata_isfdb: Dict[str, Optional[str]] = field(default_factory=dict)
    metadata_amazon: Dict[str, Optional[str]] = field(default_factory=dict)
    # isbn_check_by_source: populated by ISBNLookupWorker.
    # Keys are source names ("chosen","file","google","openlibrary","isfdb","amazon").
    # Values are {'title', 'author', 'pub_date'} returned by the ISBN verification service.
    isbn_check_by_source: Dict[str, Dict] = field(default_factory=dict)

    output_pattern: str = ""
    dir_out: str = ""
    new_filepath: Optional[Path] = None

    format_state: str = ""   # e.g. "MOBI", "EPUB 2", "EPUB 3", "EPUB 3.3 ✓", "PDF"
    error_message: str = ""

    def get_display_value(self, source: str, field_name: str) -> str:
        """Return the stripped value for *source* ('file'/'pattern'/'chosen'/'google'/'openlibrary')."""
        mapping = {
            "file": self.metadata_file,
            "pattern": self.metadata_pattern,
            "chosen": self.chosen_metadata,
            "google": self.metadata_google,
            "openlibrary": self.metadata_openlibrary,
            "isfdb": self.metadata_isfdb,
            "amazon": self.metadata_amazon,
        }
        return (mapping.get(source, {}).get(field_name) or "").strip()

    def set_chosen_from_source(self, field_name: str, source: str) -> None:
        """Copy a field value from *source* into chosen_metadata."""
        mapping = {
            "file": self.metadata_file,
            "pattern": self.metadata_pattern,
            "google": self.metadata_google,
            "openlibrary": self.metadata_openlibrary,
            "isfdb": self.metadata_isfdb,
            "amazon": self.metadata_amazon,
        }
        if source in mapping:
            self.chosen_metadata[field_name] = mapping[source].get(field_name)

    def sync_dir_out(self) -> None:
        """Seed dir_out from the parsed dir_in (only when not yet user-set)."""
        if not self.dir_out:
            dir_in = self.metadata_pattern.get("dir_in") or ""
            self.dir_out = dir_in or str(self.filepath.parent)

    def ensure_chosen_defaults(self, fields: List[str]) -> None:
        """Set chosen_metadata defaults for any field not yet chosen.

        Preference: file meta > pattern, but file meta is skipped when it
        contains no word characters (e.g. '[]', '""', "'") — those are
        placeholder/garbage values that should lose to a parsed filename.
        Falls back to empty string.
        """
        for f in fields:
            if (self.chosen_metadata.get(f) or "").strip():
                continue
            file_val = (self.metadata_file.get(f) or "").strip()
            pat_val = (self.metadata_pattern.get(f) or "").strip()
            if file_val and not re.search(r"\w", file_val):
                file_val = ""
            self.chosen_metadata[f] = file_val or pat_val or ""

    def recompute_new_filepath(
        self,
        default_pattern: str = r"{dir_out}\{author} - {series} {series_index} - {title}",
    ) -> None:
        """Build new_filepath from chosen_metadata and the output pattern.

        The original file extension is appended automatically; no {ext} needed.

        Supported placeholders: {dir_in}, {filename}, {author}, {title},
        {series}, {series_index}, {subject}, {tags}, {dir_out}, {filepath}.
        """
        meta = self.chosen_metadata
        ext = self.filepath.suffix
        dir_path = self.filepath.parent
        pattern = self.output_pattern.strip() or default_pattern

        fmt = {
            "author":       _sanitize((meta.get("author") or "").strip()),
            "title":        _sanitize((meta.get("title") or "").strip()),
            "series":       _sanitize((meta.get("series") or "").strip()),
            "series_index": _sanitize((meta.get("series_index") or "").strip()),
            "subject":      _sanitize((meta.get("subject") or "").strip()),
            "tags":         _sanitize((meta.get("tags") or "").strip()),
            "publisher":    _sanitize((meta.get("publisher") or "").strip()),
            "pub_date":     _sanitize((meta.get("pub_date") or "").strip()),
            "description":  _sanitize((meta.get("description") or "").strip()),
            "isbn":         _sanitize((meta.get("isbn") or "").strip()),
            "language":     _sanitize((meta.get("language") or "").strip()),
            "rights":       _sanitize((meta.get("rights") or "").strip()),
            "contributor":  _sanitize((meta.get("contributor") or "").strip()),
            "filename":     self.filepath.name,
            "dir_in":       str(dir_path),
            "dir_out":      self.dir_out or str(dir_path),
            "filepath":     str(self.filepath),
        }

        try:
            target = pattern.format(**fmt)
        except KeyError as e:
            self.error_message = f"Unknown placeholder in output_pattern: {e}"
            return

        target = target.replace("/", "\\").strip()
        while "  " in target:
            target = target.replace("  ", " ")
        # Collapse " - - " left when series/series_index are empty
        while " - - " in target:
            target = target.replace(" - - ", " - ")

        # Append extension before Path() so dots in author/title are never
        # mistaken for a file extension.
        target += ext

        candidate = Path(target)
        if not candidate.is_absolute():
            candidate = dir_path / candidate
        self.new_filepath = candidate

    def get_source_row_values(self, sub_row: int, columns: List[str]) -> List[str]:
        """Return display strings for one source sub-row.

        sub_row: 0=Chosen, 1=Filename/pattern, 2=File meta, 3=Google, 4=Open Library.
        """
        meta = getattr(self, _SOURCE_DICTS[sub_row])

        row: List[str] = []
        for col_key in columns:
            if col_key == "source_label":
                row.append(_SOURCE_LABELS.get(sub_row, ""))
            elif col_key == "filepath":
                row.append(str(self.filepath) if sub_row == 0 else "")
            elif col_key == "dir_in":
                if sub_row == 1:
                    row.append(self.metadata_pattern.get("dir_in") or str(self.filepath.parent))
                else:
                    row.append("")
            elif col_key == "pattern":
                row.append(self.pattern if sub_row == 1 else "")
            elif col_key == "output_pattern":
                row.append(self.output_pattern if sub_row == 0 else "")
            elif col_key == "dir_out":
                row.append(self.dir_out if sub_row == 0 else "")
            elif col_key == "new_filepath":
                row.append(str(self.new_filepath) if (sub_row == 0 and self.new_filepath) else "")
            elif col_key == "format_state":
                row.append(self.format_state if sub_row == 0 else "")
            elif col_key == "isbn_lookup":
                entry = _ISBN_LOOKUP_MAP.get(sub_row)
                if entry is None:
                    row.append("")
                    continue
                source_key, _ = entry
                check = self.isbn_check_by_source.get(source_key) or {}
                parts = [
                    (check.get("title") or "").strip(),
                    (check.get("author") or "").strip(),
                    (check.get("pub_date") or "").strip()[:4],
                ]
                row.append("  ·  ".join(p for p in parts if p))
            elif col_key in METADATA_FIELDS:
                row.append((meta.get(col_key) or "").strip())
            else:
                row.append("")
        return row
