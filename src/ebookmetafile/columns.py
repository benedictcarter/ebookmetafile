"""Centralized column/headers definitions for the app.

Keep a single source-of-truth for column keys and display headers so both
GUI backends can import and stay in sync.
"""

from typing import List, Dict

COLUMNS: List[str] = [
    "source_label",
    "filepath",
    "format_state",
    "dir_in",
    "pattern",
    "author",
    "title",
    "series",
    "series_index",
    "subject",
    "tags",
    "publisher",
    "pub_date",
    "description",
    "isbn",
    "isbn_lookup",
    "language",
    "rights",
    "contributor",
    "cover",
    "cover_thumb",
    "output_pattern",
    "dir_out",
    "new_filepath",
]

HEADERS: Dict[str, str] = {
    "source_label": "Source",
    "filepath": "Filepath",
    "format_state": "Format",
    "dir_in": "Input Dir",
    "pattern": "Pattern",
    "author": "Author",
    "title": "Title",
    "series": "Series",
    "series_index": "Index",
    "subject": "Subject",
    "tags": "Tags",
    "publisher": "Publisher",
    "pub_date": "Pub Date",
    "description": "Description",
    "isbn": "ISBN",
    "isbn_lookup": "ISBN Lookup",
    "language": "Language",
    "rights": "Rights",
    "contributor": "Contributor",
    "cover": "Cover",
    "cover_thumb": "Thumb",
    "output_pattern": "Output Pattern",
    "dir_out": "Output Dir",
    "new_filepath": "New Filepath",
}
