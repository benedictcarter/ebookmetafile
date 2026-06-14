from pathlib import Path

from PyQt5 import QtCore
from ebookmetafile.models import BookRecord
from ebookmetafile.gui_main_qt import BookTableModel

rec = BookRecord(id=1, filepath=Path(r"S:\ebooks\scifi\file1.epub"))
rec.metadata_file = {"author": "A", "title": "T", "series": "S", "series_index": "2"}
rec.metadata_pattern = {
    "author": "A",
    "title": "T alt",
    "series": "S",
    "series_index": "1",
}
rec.chosen_metadata = {"author": "A", "title": "T", "series": "S", "series_index": "2"}
rec.output_pattern = "{author} - {title}{ext}"
rec.recompute_new_filepath()

model = BookTableModel([rec])
print("cols", model.columnCount())
for col in range(model.columnCount()):
    idx = model.index(0, col)
    hdr = model.headerData(col, QtCore.Qt.Horizontal)
    val = model.data(idx, QtCore.Qt.DisplayRole)
    print(col, hdr, val)

# test sort
model.sort(0, QtCore.Qt.AscendingOrder)
print("sorted ok")
