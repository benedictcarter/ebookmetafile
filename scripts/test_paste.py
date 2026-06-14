from pathlib import Path

from ebookmetafile.models import BookRecord
from ebookmetafile.gui_main_qt import BookTableModel
from src.ebookmetafile import ui_helpers

# Setup two records
r1 = BookRecord(id=1, filepath=Path(r"S:\ebooks\scifi\file1.epub"))
r1.metadata_file = {"author": "A1", "title": "T1", "series": "S1", "series_index": "1"}
r1.chosen_metadata = {
    "author": "A1",
    "title": "T1",
    "series": "S1",
    "series_index": "1",
}
r1.recompute_new_filepath()

r2 = BookRecord(id=2, filepath=Path(r"S:\ebooks\scifi\file2.epub"))
r2.metadata_file = {"author": "A2", "title": "T2", "series": "S2", "series_index": "2"}
r2.chosen_metadata = {
    "author": "A2",
    "title": "T2",
    "series": "S2",
    "series_index": "2",
}
r2.recompute_new_filepath()

model = BookTableModel([r1, r2])

# Simulate clipboard with two columns (author, title)
clip = "NewAuthor1\tNewTitle1\nNewAuthor2\tNewTitle2"
clip_data = ui_helpers.parse_tsv(clip)
# Tile into rows 0-1 cols 5-6 (author_file, author_pattern)
updates = ui_helpers.tile_clipboard_to_rect(clip_data, 0, 5, 1, 6)

for r, row_map in updates.items():
    col_texts = {}
    for c, text in row_map.items():
        col_key = model.columns[c]
        col_texts[col_key] = text
    model.apply_row_updates(r, col_texts)

for i, rec in enumerate(model.records):
    print(
        i,
        rec.metadata_file.get("author"),
        rec.metadata_pattern.get("title"),
        rec.new_filepath,
    )
