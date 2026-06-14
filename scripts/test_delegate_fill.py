from pathlib import Path

from PyQt5 import QtWidgets, QtCore
from ebookmetafile.models import BookRecord
from ebookmetafile.gui_main_qt import BookTableModel, LiveEditDelegate

app = QtWidgets.QApplication([])

# Create two records
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

view = QtWidgets.QTableView()
view.setModel(model)
view.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectItems)
view.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)

# Create and attach delegate with parent view
delegate = LiveEditDelegate(view)
view.setItemDelegate(delegate)

# Select a rectangle: rows 0-1, cols 5-6 (author_file, author_pattern)
sel_model = view.selectionModel()
indexes = []
for r in (0, 1):
    for c in (5, 6):
        idx = model.index(r, c)
        indexes.append(idx)
        sel_model.select(idx, QtCore.QItemSelectionModel.Select)

# Prepare editor
editor = QtWidgets.QLineEdit()
editor.setText("Zzz")

# Call delegate.setModelData with top-left index
delegate.setModelData(editor, model, model.index(0, 5))

# Inspect results
for i, rec in enumerate(model.records):
    print(i, rec.metadata_file.get("author"), rec.metadata_pattern.get("author"))

app.quit()
