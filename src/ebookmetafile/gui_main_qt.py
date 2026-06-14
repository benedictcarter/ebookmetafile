import os
import platform
import subprocess
from datetime import datetime
from pathlib import Path
from typing import List, Dict

_PLATFORM = platform.system()   # "Windows", "Linux", "Darwin"


def _open_file(path: Path) -> None:
    """Open a file with the OS default application."""
    if _PLATFORM == "Windows":
        os.startfile(path)
    elif _PLATFORM == "Darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def _reveal_in_files(path: Path) -> None:
    """Open the containing folder with the file selected where the platform supports it."""
    if _PLATFORM == "Windows":
        subprocess.Popen(["explorer", "/select,", str(path)])
    elif _PLATFORM == "Darwin":
        subprocess.Popen(["open", "-R", str(path)])
    else:
        # Try nautilus --select (GNOME); fall back to opening the parent folder
        try:
            subprocess.Popen(["nautilus", "--select", str(path)])
        except FileNotFoundError:
            subprocess.Popen(["xdg-open", str(path.parent)])

import logging

from PyQt5 import QtCore, QtGui, QtWidgets

from .models import BookRecord
from . import ui_helpers
from .gui_constants import (
    METADATA_FIELDS,
    _SETTINGS_ORG,
    _SETTINGS_APP,
    DEFAULT_FILENAME_PATTERNS,
    DEFAULT_OUTPUT_PATTERN,
    _cover_image_cache,
    _cover_source_name,
    _try_patterns,
    SOURCE_MAP,
    ROW_CHOSEN,
    embedded_cover_cache_key,
    _image_dimensions,
)
from .gui_workers import CoverLoadWorker, CoverPreloadWorker, MetadataFetchWorker, WriteWorker, ScanWorker, ISBNLookupWorker
from .gui_dialogs import CoverPreviewWindow, ApplyDialog, FetchSourceDialog, FetchProgressDialog
from .gui_table import BookTableModel, LiveEditDelegate, ThumbnailDelegate, ColumnFilterBar, GlobalSearchBar, GripHeaderView
from .gui_help import _HELP_HTML

logger = logging.getLogger(__name__)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Ebook Metadata And Filepath Manager")

        self.records: List[BookRecord] = []
        self.current_root: Path | None = None
        self._saved_column_widths: List[int] = []

        self.model = BookTableModel(self.records, self)
        self.global_patterns: List[str] = list(DEFAULT_FILENAME_PATTERNS)
        self.global_output_pattern = DEFAULT_OUTPUT_PATTERN
        self._cover_preview = CoverPreviewWindow(self)
        self._cover_load_worker: "CoverLoadWorker | None" = None
        self._cover_preload_worker: "CoverPreloadWorker | None" = None
        self._setup_ui()
        self._restore_settings()

    # --- Persistence ---------------------------------------------------

    def _restore_settings(self) -> None:
        s = QtCore.QSettings(_SETTINGS_ORG, _SETTINGS_APP)
        geom = s.value("geometry")
        if geom:
            self.restoreGeometry(geom)
        else:
            self.resize(3000, 1000)
        folder = s.value("library_folder", "")
        if folder:
            self.current_root = Path(folder)
            self.root_edit.setText(folder)
        for i, edit in enumerate(self._pattern_edits):
            key = f"input_pattern_{i + 1}"
            val = s.value(key, "")
            if not val and i == 0:
                val = s.value("input_pattern", "")  # backward compat
            if val:
                self.global_patterns[i] = val
                edit.setText(val)
        output_pattern = s.value("output_pattern", "")
        if output_pattern:
            self.global_output_pattern = output_pattern
            self.output_pattern_edit.setText(output_pattern)
        for col in (s.value("hidden_columns") or []):
            # Saved as key strings since column insertion can shift indices.
            # Legacy int values (pre-migration) are silently ignored.
            if isinstance(col, str) and col in self.model.columns:
                self.table.setColumnHidden(self.model.columns.index(col), True)
        self._saved_column_widths = [int(w) for w in (s.value("column_widths") or [])]

    def closeEvent(self, event) -> None:
        s = QtCore.QSettings(_SETTINGS_ORG, _SETTINGS_APP)
        s.setValue("geometry", self.saveGeometry())
        s.setValue("library_folder", str(self.current_root) if self.current_root else "")
        for i, pat in enumerate(self.global_patterns):
            s.setValue(f"input_pattern_{i + 1}", pat)
        s.setValue("output_pattern", self.global_output_pattern)
        s.setValue("hidden_columns", [self.model.columns[i] for i in range(self.model.columnCount()) if self.table.isColumnHidden(i)])
        s.setValue("column_widths", [self.table.columnWidth(i) for i in range(self.model.columnCount())])
        super().closeEvent(event)

    # --- UI setup ------------------------------------------------------

    def _setup_ui(self) -> None:
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)

        help_menu = self.menuBar().addMenu("Help")
        help_action = help_menu.addAction("Documentation")
        help_action.setShortcut(QtGui.QKeySequence.HelpContents)
        help_action.triggered.connect(self._show_help)

        layout = QtWidgets.QVBoxLayout(central)

        grid = QtWidgets.QGridLayout()
        grid.setColumnStretch(2, 1)

        grid.addWidget(QtWidgets.QLabel("Library folder:"), 0, 0)
        browse_button = QtWidgets.QPushButton("Browse…")
        browse_button.clicked.connect(self.on_browse_clicked)
        scan_button = QtWidgets.QPushButton("Scan")
        scan_button.setToolTip("Scan selected folder for ebooks (Ctrl+R)")
        scan_button.clicked.connect(self.on_scan_clicked)
        folder_btns = QtWidgets.QHBoxLayout()
        folder_btns.setContentsMargins(0, 0, 0, 0)
        folder_btns.addWidget(browse_button)
        folder_btns.addWidget(scan_button)
        folder_btn_widget = QtWidgets.QWidget()
        folder_btn_widget.setLayout(folder_btns)
        grid.addWidget(folder_btn_widget, 0, 1)
        self.root_edit = QtWidgets.QLineEdit()
        if self.current_root is not None:
            self.root_edit.setText(str(self.current_root))
        self.root_edit.setPlaceholderText("Choose a folder to scan…")
        grid.addWidget(self.root_edit, 0, 2)

        pattern_labels = ["Input pattern 1:", "Input pattern 2:", "Input pattern 3:"]
        self._pattern_edits: List[QtWidgets.QLineEdit] = []
        for i, label in enumerate(pattern_labels):
            grid.addWidget(QtWidgets.QLabel(label), i + 1, 0)
            edit = QtWidgets.QLineEdit()
            edit.setText(self.global_patterns[i])
            if i == 0:
                edit.setPlaceholderText("Required — used first")
            else:
                edit.setPlaceholderText(f"Fallback {i} — tried if pattern {i} fails")
            self._pattern_edits.append(edit)
            grid.addWidget(edit, i + 1, 2)

        apply_pattern_btn = QtWidgets.QPushButton("Apply\nto all")
        apply_pattern_btn.setToolTip(
            "Apply all three patterns to every book.\n"
            "The first pattern that parses successfully is used; "
            "subsequent patterns are tried only if earlier ones fail."
        )
        apply_pattern_btn.clicked.connect(self.on_apply_pattern_to_all)
        grid.addWidget(apply_pattern_btn, 1, 1, 3, 1)

        grid.addWidget(QtWidgets.QLabel("Output pattern:"), 4, 0)
        apply_output_btn = QtWidgets.QPushButton("Apply to all")
        apply_output_btn.clicked.connect(self.on_apply_output_pattern_to_all)
        grid.addWidget(apply_output_btn, 4, 1)
        self.output_pattern_edit = QtWidgets.QLineEdit()
        self.output_pattern_edit.setText(self.global_output_pattern)
        grid.addWidget(self.output_pattern_edit, 4, 2)

        self.fetch_button = QtWidgets.QPushButton("Fetch web metadata for all books…")
        self.fetch_button.setToolTip("Fetch metadata from web sources (Ctrl+Shift+F)")
        self.fetch_button.clicked.connect(self.on_fetch_clicked)
        grid.addWidget(self.fetch_button, 5, 0, 1, 2)

        self.isbn_lookup_button = QtWidgets.QPushButton("ISBN Lookup for all books…")
        self.isbn_lookup_button.setToolTip(
            "For each book that already has an ISBN, verify it against Open Library / Google Books\n"
            "and populate the ISBN Lookup column."
        )
        self.isbn_lookup_button.clicked.connect(self.on_isbn_lookup_clicked)
        self.isbn_lookup_button.setEnabled(False)
        grid.addWidget(self.isbn_lookup_button, 6, 0, 1, 2)

        self.write_button = QtWidgets.QPushButton("Write all books…")
        self.write_button.setToolTip("Write metadata and rename/move files (Ctrl+Shift+W)")
        self.write_button.clicked.connect(self.on_apply_clicked)
        grid.addWidget(self.write_button, 7, 0, 1, 2)

        layout.addLayout(grid)

        filter_row = QtWidgets.QHBoxLayout()
        self._show_all_radio = QtWidgets.QRadioButton("Show all rows")
        self._show_chosen_radio = QtWidgets.QRadioButton("Show \"Chosen\" rows only")
        self._show_all_radio.setChecked(True)
        self._show_all_radio.toggled.connect(self._on_row_visibility_changed)
        filter_row.addWidget(self._show_all_radio)
        filter_row.addWidget(self._show_chosen_radio)
        filter_row.addSpacing(24)

        filter_row.addWidget(QtWidgets.QLabel("Row height:"))
        self._rh_group = QtWidgets.QButtonGroup(self)
        self._rh_22 = QtWidgets.QRadioButton("22")
        self._rh_48 = QtWidgets.QRadioButton("48")
        self._rh_80 = QtWidgets.QRadioButton("80")
        self._rh_22.setChecked(True)
        for btn in (self._rh_22, self._rh_48, self._rh_80):
            self._rh_group.addButton(btn)
            filter_row.addWidget(btn)
        self._rh_group.buttonClicked.connect(self._on_row_height_changed)
        filter_row.addSpacing(24)

        self._filter_btn = QtWidgets.QPushButton("Filter to selected books")
        self._filter_btn.clicked.connect(self._on_filter_clicked)
        filter_row.addWidget(self._filter_btn)
        filter_row.addStretch()
        layout.addLayout(filter_row)

        self.table = QtWidgets.QTableView()
        self.table.setHorizontalHeader(GripHeaderView(self.table))
        self.table.setModel(self.model)
        self.table.setItemDelegate(LiveEditDelegate(self.table))
        self.table.clicked.connect(self.on_table_cell_clicked)
        self.table.selectionModel().selectionChanged.connect(
            lambda *_: self._update_write_button()
        )
        self.table.selectionModel().selectionChanged.connect(
            lambda *_: self._update_fetch_button()
        )

        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectItems)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)

        QtWidgets.QShortcut(
            QtGui.QKeySequence.Copy,
            self.table,
            activated=self.copy_selection_to_clipboard,
        )
        QtWidgets.QShortcut(
            QtGui.QKeySequence.Paste, self.table, activated=self.paste_from_clipboard
        )
        QtWidgets.QShortcut(
            QtGui.QKeySequence(QtCore.Qt.Key_Escape),
            self.table,
            activated=self.table.clearSelection,
        )
        QtWidgets.QShortcut(
            QtGui.QKeySequence("Ctrl+R"), self, activated=self.on_scan_clicked
        )
        QtWidgets.QShortcut(
            QtGui.QKeySequence("Ctrl+Shift+F"), self, activated=self.on_fetch_clicked
        )
        QtWidgets.QShortcut(
            QtGui.QKeySequence("Ctrl+Shift+W"), self, activated=self.on_apply_clicked
        )

        h_header = self.table.horizontalHeader()
        v_header = self.table.verticalHeader()
        h_header.setSectionResizeMode(QtWidgets.QHeaderView.Interactive)
        h_header.setStretchLastSection(True)
        h_header.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        h_header.customContextMenuRequested.connect(self._on_header_context_menu)
        self.table.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_table_context_menu)
        v_header.setSectionResizeMode(QtWidgets.QHeaderView.Fixed)
        v_header.setDefaultSectionSize(22)

        thumb_col = self.model.columns.index("cover_thumb")
        self.table.setItemDelegateForColumn(thumb_col, ThumbnailDelegate(self.table))
        self.table.setColumnWidth(thumb_col, 22)

        self._filter_bar = ColumnFilterBar(self.table, self.model)
        self._global_search = GlobalSearchBar(self._filter_bar)

        table_container = QtWidgets.QWidget()
        tc_layout = QtWidgets.QVBoxLayout(table_container)
        tc_layout.setContentsMargins(0, 0, 0, 0)
        tc_layout.setSpacing(0)
        tc_layout.addWidget(self._global_search)
        tc_layout.addWidget(self._filter_bar)
        tc_layout.addWidget(self.table)
        layout.addWidget(table_container)

    # --- Help ----------------------------------------------------------

    def _show_help(self) -> None:
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Documentation")
        dlg.resize(760, 620)
        layout = QtWidgets.QVBoxLayout(dlg)

        browser = QtWidgets.QTextBrowser()
        browser.setOpenExternalLinks(False)
        browser.setHtml(_HELP_HTML)
        layout.addWidget(browser)

        close_btn = QtWidgets.QPushButton("Close")
        close_btn.clicked.connect(dlg.accept)
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        dlg.exec_()

    # --- Table interaction ---------------------------------------------

    def _update_fetch_button(self) -> None:
        n = len(self.model.records_for_rows(
            idx.row() for idx in self.table.selectionModel().selectedIndexes()
        ))
        if n:
            self.fetch_button.setText(f"Fetch web metadata for {n} selected book{'s' if n != 1 else ''}…")
            self.isbn_lookup_button.setText(f"ISBN Lookup for {n} selected book{'s' if n != 1 else ''}…")
        else:
            self.fetch_button.setText("Fetch web metadata for all books…")
            self.isbn_lookup_button.setText("ISBN Lookup for all books…")

    def _update_write_button(self) -> None:
        n = len(self.model.records_for_rows(
            idx.row() for idx in self.table.selectionModel().selectedIndexes()
        ))
        if n:
            self.write_button.setText(f"Write {n} selected book{'s' if n != 1 else ''}…")
        else:
            self.write_button.setText("Write all books…")

    def _on_row_visibility_changed(self) -> None:
        self.model.set_chosen_only(self._show_chosen_radio.isChecked())

    def _on_row_height_changed(self) -> None:
        if self._rh_48.isChecked():
            h = 48
        elif self._rh_80.isChecked():
            h = 80
        else:
            h = 22
        self.table.verticalHeader().setDefaultSectionSize(h)
        thumb_col = self.model.columns.index("cover_thumb")
        # Keep thumbnail column width proportional to row height (portrait ~2:3 ratio)
        self.table.setColumnWidth(thumb_col, max(16, int(h * 0.67)))

    def _on_filter_clicked(self) -> None:
        if self.model._book_filter is not None:
            self.model.set_book_filter(None)
            self._filter_btn.setText("Filter to selected books")
        else:
            selected = self.model.records_for_rows(
                idx.row() for idx in self.table.selectionModel().selectedIndexes()
            )
            if not selected:
                return
            ids = {r.id for r in selected}
            self.model.set_book_filter(ids)
            n = len(ids)
            self._filter_btn.setText(f"Clear book filter ({n} book{'s' if n != 1 else ''})")
            self.table.clearSelection()

    def _on_header_context_menu(self, pos: QtCore.QPoint) -> None:
        """Right-click on a column header to show/hide columns."""
        menu = QtWidgets.QMenu(self)
        h = self.table.horizontalHeader()
        for i in range(self.model.columnCount()):
            key = self.model.columns[i]
            label = self.model.headers.get(key, key)
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(not self.table.isColumnHidden(i))
            action.setData(i)
        chosen = menu.exec_(h.mapToGlobal(pos))
        if chosen:
            col = chosen.data()
            was_hidden = self.table.isColumnHidden(col)
            self.table.setColumnHidden(col, not was_hidden)
            if was_hidden:
                self.table.resizeColumnToContents(col)

    def _on_table_context_menu(self, pos: QtCore.QPoint) -> None:
        index = self.table.indexAt(pos)
        cell_raw = index.data(QtCore.Qt.DisplayRole) if index.isValid() else None
        cell_str = str(cell_raw).strip() if cell_raw is not None else ""
        col = index.column() if index.isValid() else -1

        sel_records = self.model.records_for_rows(
            idx.row() for idx in self.table.selectionModel().selectedIndexes()
        )
        n_sel = len(sel_records)
        # Use the right-clicked row's record for single-book actions
        clicked_rec = sel_records[0] if n_sel == 1 else (
            self.model.records_for_rows([index.row()]) or [None]
        )[0] if index.isValid() else None
        filepath = clicked_rec.filepath if clicked_rec else None

        menu = QtWidgets.QMenu(self)

        act_open = menu.addAction("Open book")
        act_open.setEnabled(filepath is not None and filepath.exists())

        _reveal_label = {"Windows": "Reveal in Explorer", "Darwin": "Reveal in Finder"}.get(_PLATFORM, "Show in Files")
        act_reveal = menu.addAction(_reveal_label)
        act_reveal.setEnabled(filepath is not None and filepath.exists())

        menu.addSeparator()

        act_filter_sel = menu.addAction("Filter to selected books")
        act_filter_sel.setEnabled(n_sel > 0)

        act_remove_sel = menu.addAction("Remove selected from view")
        act_remove_sel.setEnabled(n_sel > 0)

        act_clear_book_filter = menu.addAction("Clear book filter")
        act_clear_book_filter.setEnabled(self.model._book_filter is not None)

        menu.addSeparator()

        act_copy_path = menu.addAction("Copy file path")
        act_copy_path.setEnabled(filepath is not None)

        trunc = cell_str[:40] + "…" if len(cell_str) > 40 else cell_str
        act_copy_cell = menu.addAction(f'Copy "{trunc}"')
        act_copy_cell.setEnabled(bool(cell_str))

        act_global = menu.addAction(f'Set global filter to "{trunc}"')
        act_global.setEnabled(bool(cell_str))

        global_active = bool(self._global_search._edit.text())
        act_clear_global = menu.addAction("Clear global filter")
        act_clear_global.setEnabled(global_active)

        act_col = menu.addAction(f'Set column filter to "{trunc}"')
        act_col.setEnabled(bool(cell_str) and col >= 0)

        col_edit = self._filter_bar._edits.get(col) if col >= 0 else None
        act_clear_col = menu.addAction("Clear column filter")
        act_clear_col.setEnabled(col_edit is not None and bool(col_edit.text()))

        menu.addSeparator()

        fetch_label = (
            f"Fetch metadata for {n_sel} selected book{'s' if n_sel != 1 else ''}…"
            if n_sel else "Fetch metadata for all books…"
        )
        write_label = (
            f"Write {n_sel} selected book{'s' if n_sel != 1 else ''}…"
            if n_sel else "Write all books…"
        )
        act_fetch = menu.addAction(fetch_label)

        isbn_label = (
            f"ISBN Lookup for {n_sel} selected book{'s' if n_sel != 1 else ''}…"
            if n_sel else "ISBN Lookup for all books…"
        )
        act_isbn = menu.addAction(isbn_label)
        act_isbn.setEnabled(self.isbn_lookup_button.isEnabled())

        act_write = menu.addAction(write_label)

        reset_label = (
            f"Reset chosen metadata for {n_sel} selected book{'s' if n_sel != 1 else ''}"
            if n_sel else "Reset chosen metadata for all books"
        )
        act_reset = menu.addAction(reset_label)
        act_reset.setEnabled(len(self.model.records) > 0)

        chosen = menu.exec_(self.table.viewport().mapToGlobal(pos))

        if chosen is act_open and filepath:
            _open_file(filepath)
        elif chosen is act_reveal and filepath:
            _reveal_in_files(filepath)
        elif chosen is act_filter_sel:
            self._on_filter_clicked()
        elif chosen is act_clear_book_filter:
            self.model.set_book_filter(None)
            self._filter_btn.setText("Filter to selected books")
        elif chosen is act_remove_sel:
            sel_ids = {r.id for r in sel_records}
            current = self.model._book_filter
            if current is None:
                remaining = {r.id for r in self.model.records} - sel_ids
            else:
                remaining = current - sel_ids
            self.model.set_book_filter(remaining)
            n = len(remaining)
            self._filter_btn.setText(f"Clear book filter ({n} book{'s' if n != 1 else ''})")
            self.table.clearSelection()
        elif chosen is act_copy_path and filepath:
            QtWidgets.QApplication.clipboard().setText(str(filepath))
        elif chosen is act_copy_cell:
            QtWidgets.QApplication.clipboard().setText(cell_str)
        elif chosen is act_global:
            self._global_search._edit.setText(cell_str)
        elif chosen is act_clear_global:
            self._global_search._edit.clear()
        elif chosen is act_col:
            edit = self._filter_bar._edits.get(col)
            if edit:
                edit.setText(cell_str)
        elif chosen is act_clear_col and col_edit:
            col_edit.clear()
        elif chosen is act_fetch:
            self.on_fetch_clicked()
        elif chosen is act_isbn:
            self.on_isbn_lookup_clicked()
        elif chosen is act_write:
            self.on_apply_clicked()
        elif chosen is act_reset:
            targets = sel_records if sel_records else self.model.records
            for rec in targets:
                rec.chosen_metadata.clear()
                rec.ensure_chosen_defaults(list(METADATA_FIELDS))
                rec.recompute_new_filepath()
            self.model.layoutChanged.emit()

    def on_table_cell_clicked(self, index: QtCore.QModelIndex) -> None:
        """Clicking a source row metadata cell sets it as the chosen value."""
        if not index.isValid():
            return

        rpb = self.model._rows_per_book
        disp = self.model._display_records
        book_idx = index.row() // rpb
        sub_row = index.row() % rpb
        col_key = self.model.columns[index.column()]

        if book_idx >= len(disp):
            return

        if sub_row not in SOURCE_MAP or col_key not in METADATA_FIELDS:
            if sub_row == ROW_CHOSEN and col_key == "cover":
                rec = disp[book_idx]
                url = rec.chosen_metadata.get("cover") or ""
                if url:
                    self._show_cover_preview(url, rec)
            return

        rec = disp[book_idx]
        rec.set_chosen_from_source(col_key, SOURCE_MAP[sub_row])
        rec.recompute_new_filepath()

        if col_key == "cover":
            url = rec.get_display_value(SOURCE_MAP[sub_row], "cover")
            if url:
                self._show_cover_preview(url, rec)

        first_row = book_idx * rpb
        top_left = self.model.index(first_row, 0)
        bottom_right = self.model.index(
            first_row + rpb - 1, self.model.columnCount() - 1
        )
        self.model.dataChanged.emit(
            top_left, bottom_right, [QtCore.Qt.DisplayRole, QtCore.Qt.BackgroundRole]
        )

    # --- Clipboard -----------------------------------------------------

    def copy_selection_to_clipboard(self) -> None:
        """Copy selected cells to the clipboard as TSV."""
        selection: QtCore.QItemSelectionModel = self.table.selectionModel()
        indexes = selection.selectedIndexes()
        if not indexes:
            return

        rows = sorted(set(idx.row() for idx in indexes))
        cols = sorted(set(idx.column() for idx in indexes))

        data_lines = []
        for r in rows:
            row_values = []
            for c in cols:
                idx = self.model.index(r, c)
                text = self.model.data(idx, QtCore.Qt.DisplayRole) or ""
                row_values.append(str(text))
            data_lines.append("\t".join(row_values))

        QtWidgets.QApplication.clipboard().setText("\n".join(data_lines))

    def paste_from_clipboard(self) -> None:
        """Paste TSV from clipboard, tiling it across the selected area (Excel-style fill)."""
        selection: QtCore.QItemSelectionModel = self.table.selectionModel()
        indexes = selection.selectedIndexes()
        if not indexes:
            return

        rows_sel = sorted(set(idx.row() for idx in indexes))
        cols_sel = sorted(set(idx.column() for idx in indexes))
        start_row, start_col = rows_sel[0], cols_sel[0]
        end_row, end_col = rows_sel[-1], cols_sel[-1]

        tsv = QtWidgets.QApplication.clipboard().text()
        if not tsv:
            return

        clip_data = ui_helpers.parse_tsv(tsv)
        updates_by_row = ui_helpers.tile_clipboard_to_rect(
            clip_data, start_row, start_col, end_row, end_col
        )

        for r, row_map in updates_by_row.items():
            if r < 0 or r >= self.model.rowCount():
                continue
            col_texts: Dict[str, str] = {}
            for c, text in row_map.items():
                if c < 0 or c >= self.model.columnCount():
                    continue
                col_texts[self.model.columns[c]] = text
            self.model.apply_row_updates(r, col_texts)

    # --- Scanning ------------------------------------------------------

    def on_browse_clicked(self) -> None:
        dir_in = str(self.current_root) if self.current_root else ""
        path = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "Select library folder",
            dir_in,
            QtWidgets.QFileDialog.ShowDirsOnly
            | QtWidgets.QFileDialog.DontResolveSymlinks,
        )
        if path:
            self.current_root = Path(path)
            self.root_edit.setText(path)

    def on_scan_clicked(self) -> None:
        text = self.root_edit.text().strip()
        if not text:
            QtWidgets.QMessageBox.warning(
                self, "No folder", "Please choose a library folder first."
            )
            return

        root = Path(text)
        if not root.exists() or not root.is_dir():
            QtWidgets.QMessageBox.warning(
                self, "Invalid folder", f"{root} is not a valid folder."
            )
            return

        self.current_root = root
        self._load_data()

    def _load_data(self) -> None:
        if self.current_root is None:
            return

        progress_dlg = QtWidgets.QProgressDialog("Scanning…", "Cancel", 0, 0, self)
        progress_dlg.setWindowModality(QtCore.Qt.WindowModal)
        progress_dlg.setMinimumDuration(0)

        def _on_progress(label: str, done: int, total: int) -> None:
            progress_dlg.setLabelText(label)
            progress_dlg.setMaximum(total)
            progress_dlg.setValue(done)

        def _on_error(msg: str) -> None:
            progress_dlg.accept()
            QtWidgets.QMessageBox.warning(self, "Scan error", msg)

        self._scan_worker = ScanWorker(
            self.current_root, self.global_patterns, self.global_output_pattern
        )
        self._scan_worker.progress.connect(_on_progress)
        self._scan_worker.finished.connect(self._on_scan_finished)
        self._scan_worker.finished.connect(lambda _: progress_dlg.accept())
        self._scan_worker.error.connect(_on_error)
        progress_dlg.canceled.connect(self._scan_worker.requestInterruption)

        self._scan_worker.start()
        progress_dlg.exec_()

    def _on_scan_finished(self, records: List[BookRecord]) -> None:
        self.records = records
        self.model.records = self.records
        self._global_search.clear()
        self._filter_bar.clear_all()
        self._filter_btn.setText("Filter to selected books")
        self.model.layoutChanged.emit()
        self.table.resizeColumnsToContents()
        for i, w in enumerate(self._saved_column_widths):
            if i < self.model.columnCount():
                self.table.setColumnWidth(i, w)
        self._update_write_button()
        self._update_fetch_button()
        self.isbn_lookup_button.setEnabled(True)
        self._start_cover_preload()

    # --- Pattern actions -----------------------------------------------

    def on_apply_pattern_to_all(self) -> None:
        patterns = [edit.text().strip() for edit in self._pattern_edits]
        if not any(patterns):
            return
        self.global_patterns = patterns
        for rec in self.records:
            _try_patterns(patterns, rec)
        self.model.dataChanged.emit(
            self.model.index(0, 0),
            self.model.index(self.model.rowCount() - 1, self.model.columnCount() - 1),
            [QtCore.Qt.DisplayRole, QtCore.Qt.BackgroundRole],
        )

    def on_apply_output_pattern_to_all(self) -> None:
        text = self.output_pattern_edit.text().strip()
        if not text:
            return
        self.global_output_pattern = text
        for rec in self.records:
            rec.output_pattern = text
            rec.recompute_new_filepath()
        self.model.dataChanged.emit(
            self.model.index(0, 0),
            self.model.index(self.model.rowCount() - 1, self.model.columnCount() - 1),
            [QtCore.Qt.DisplayRole, QtCore.Qt.BackgroundRole],
        )

    # --- Fetch web metadata --------------------------------------------

    def on_fetch_clicked(self) -> None:
        if not self.records:
            QtWidgets.QMessageBox.information(self, "No data", "Scan a folder first.")
            return

        records_to_fetch = self.model.records_for_rows(
            idx.row() for idx in self.table.selectionModel().selectedIndexes()
        ) or list(self.model._display_records)

        source_dlg = FetchSourceDialog(self)
        if source_dlg.exec_() != QtWidgets.QDialog.Accepted:
            return
        if not any([source_dlg.fetch_google, source_dlg.fetch_openlibrary,
                    source_dlg.fetch_isfdb, source_dlg.fetch_amazon]):
            return

        _SOURCE_LABELS = {
            "google":      "Google Books",
            "openlibrary": "Open Library",
            "isfdb":       "ISFDB",
            "amazon":      "Amazon",
        }

        progress_dlg = FetchProgressDialog(self)

        def _on_progress(done: int, n: int, source_counts: dict) -> None:
            pct = 100 * done // n if n else 0
            lines = [f"Fetching… {done}/{n} ({pct}%)", ""]
            pad = max((len(_SOURCE_LABELS.get(s, s)) for s in source_counts), default=0)
            for src, (sdone, stotal) in source_counts.items():
                label = _SOURCE_LABELS.get(src, src).ljust(pad)
                spct = 100 * sdone // stotal if stotal else 0
                lines.append(f"  {label}  {sdone}/{stotal} ({spct}%)")
            progress_dlg.set_progress("\n".join(lines), done, n)

        self._fetch_worker = MetadataFetchWorker(
            records_to_fetch,
            source_dlg.fetch_google,
            source_dlg.fetch_openlibrary,
            source_dlg.fetch_isfdb,
            source_dlg.fetch_amazon,
            google_api_key=source_dlg.google_api_key,
        )
        self._fetch_worker.progress.connect(_on_progress)
        self._fetch_worker.log.connect(progress_dlg.append_log)
        self._fetch_worker.finished.connect(self._on_fetch_finished)
        self._fetch_worker.finished.connect(lambda _: progress_dlg.accept())
        progress_dlg.canceled.connect(self._fetch_worker.requestInterruption)

        self.table.clearSelection()
        self._fetch_worker.start()
        progress_dlg.exec_()

    def _on_fetch_finished(self, results: list) -> None:
        rec_by_id = {rec.id: rec for rec in self.records}
        _fields = (
            "author", "title", "series", "series_index", "subject", "tags",
            "publisher", "pub_date", "description", "isbn", "language", "rights",
            "contributor", "cover",
        )

        _SOURCE_PRIORITY = ["google", "openlibrary", "isfdb", "amazon"]

        for result in results:
            rec = rec_by_id.get(result.book_id)
            if rec is None:
                continue
            if result.google_top:
                rec.metadata_google = {f: getattr(result.google_top, f, None) or "" for f in _fields}
            if result.openlibrary_top:
                rec.metadata_openlibrary = {f: getattr(result.openlibrary_top, f, None) or "" for f in _fields}
            if result.isfdb_top:
                rec.metadata_isfdb = {f: getattr(result.isfdb_top, f, None) or "" for f in _fields}
            if result.amazon_top:
                rec.metadata_amazon = {f: getattr(result.amazon_top, f, None) or "" for f in _fields}

            # Auto-populate blank chosen fields from sources in priority order
            source_dicts = {
                "google":      rec.metadata_google,
                "openlibrary": rec.metadata_openlibrary,
                "isfdb":       rec.metadata_isfdb,
                "amazon":      rec.metadata_amazon,
            }
            changed = False
            for field in _fields:
                if rec.chosen_metadata.get(field):
                    continue  # already has a value — don't overwrite
                for src in _SOURCE_PRIORITY:
                    val = source_dicts[src].get(field, "")
                    if val:
                        rec.chosen_metadata[field] = val
                        changed = True
                        break
            if changed:
                rec.recompute_new_filepath()

        self.model.layoutChanged.emit()
        self._start_cover_preload()

    # --- ISBN Lookup ---------------------------------------------------

    def on_isbn_lookup_clicked(self) -> None:
        sel = self.model.records_for_rows(
            idx.row() for idx in self.table.selectionModel().selectedIndexes()
        )
        from .models import _ISBN_LOOKUP_MAP
        records_to_check = sel or list(self.model._display_records)

        def _has_any_isbn(rec) -> bool:
            return any(
                ((getattr(rec, meta_attr, {}) or {}).get("isbn") or "").strip()
                for _, meta_attr in _ISBN_LOOKUP_MAP.values()
            )

        records_with_isbn = [r for r in records_to_check if _has_any_isbn(r)]
        if not records_with_isbn:
            QtWidgets.QMessageBox.information(self, "ISBN Lookup", "No books with ISBNs found in selection.")
            return

        progress_dlg = FetchProgressDialog(self)
        progress_dlg.setWindowTitle("ISBN Lookup")

        def _on_progress(done: int, total: int) -> None:
            pct = 100 * done // total if total else 0
            progress_dlg.set_progress(f"Verifying ISBNs… {done}/{total} ({pct}%)", done, total)

        self._isbn_lookup_worker = ISBNLookupWorker(records_with_isbn)
        self._isbn_lookup_worker.progress.connect(_on_progress)
        self._isbn_lookup_worker.log.connect(progress_dlg.append_log)
        self._isbn_lookup_worker.finished.connect(self._on_isbn_lookup_finished)
        self._isbn_lookup_worker.finished.connect(lambda _: progress_dlg.accept())
        progress_dlg.canceled.connect(self._isbn_lookup_worker.requestInterruption)

        self._isbn_lookup_worker.start()
        progress_dlg.exec_()

    def _on_isbn_lookup_finished(self, results: list) -> None:
        rec_by_id = {rec.id: rec for rec in self.records}
        for book_id, source_key, meta in results:
            rec = rec_by_id.get(book_id)
            if rec is not None:
                rec.isbn_check_by_source[source_key] = meta or {}
        self.model.layoutChanged.emit()

    # --- Write files ---------------------------------------------------

    def on_apply_clicked(self) -> None:
        selected_records = self.model.records_for_rows(
            idx.row() for idx in self.table.selectionModel().selectedIndexes()
        )
        candidate_pool = selected_records if selected_records else self.model._display_records
        records_to_apply = [r for r in candidate_pool if r.new_filepath is not None]
        if not records_to_apply:
            QtWidgets.QMessageBox.information(
                self,
                "Nothing to write",
                "No records have a computed new filepath.\n"
                "Scan a folder and ensure an output pattern is set.",
            )
            return

        dialog = ApplyDialog(records_to_apply, self)
        if dialog.exec_() != QtWidgets.QDialog.Accepted:
            return

        operation = dialog.operation
        on_clash = dialog.on_clash
        convert_mobi = dialog.convert_mobi

        total = len(records_to_apply)

        progress_dlg = QtWidgets.QProgressDialog(
            f"Writing… 0/{total} (0%)", "Cancel", 0, total, self
        )
        progress_dlg.setWindowTitle("Writing Files")
        progress_dlg.setWindowModality(QtCore.Qt.WindowModal)
        progress_dlg.setMinimumDuration(0)
        progress_dlg.setMinimumWidth(480)

        def _on_write_progress(done: int, n: int, name: str) -> None:
            pct = 100 * done // n if n else 0
            progress_dlg.setLabelText(f"Writing… {done}/{n} ({pct}%)\n{name}")
            progress_dlg.setValue(done)

        self._write_worker = WriteWorker(records_to_apply, operation, on_clash,
                                         convert_mobi=convert_mobi,
                                         split_epub=dialog.split_epub)
        self._write_worker.progress.connect(_on_write_progress)
        self._write_worker.finished.connect(
            lambda res: self._on_write_finished(res, operation, on_clash)
        )
        self._write_worker.finished.connect(lambda _: progress_dlg.accept())
        progress_dlg.canceled.connect(self._write_worker.requestInterruption)

        self._write_worker.start()
        progress_dlg.exec_()

    def _on_write_finished(
        self, results: List[tuple], operation: str, on_clash: str
    ) -> None:
        for name, ok, msg in results:
            if ok:
                logger.info("applied %s: %s", name, msg)
            else:
                logger.error("failed  %s: %s", name, msg)

        n_ok = sum(1 for _, ok, _ in results if ok)
        n_fail = len(results) - n_ok

        log_path = self._write_op_log(results, operation, on_clash)

        detail_lines = [
            f"{'OK  ' if ok else 'FAIL'}  {name}: {msg}"
            for name, ok, msg in results
        ]

        msg_box = QtWidgets.QMessageBox(self)
        msg_box.setWindowTitle("Write Complete")
        msg_box.setText(f"Completed: {n_ok} succeeded, {n_fail} failed.")
        msg_box.setDetailedText("\n".join(detail_lines))
        if log_path:
            msg_box.setInformativeText(f"Log: {log_path}")
            view_btn = msg_box.addButton("Open Log", QtWidgets.QMessageBox.ActionRole)
        else:
            view_btn = None
        msg_box.addButton(QtWidgets.QMessageBox.Ok)
        msg_box.exec_()

        if view_btn and msg_box.clickedButton() is view_btn:
            QtGui.QDesktopServices.openUrl(
                QtCore.QUrl.fromLocalFile(str(log_path))
            )

        self.model.layoutChanged.emit()

    def _write_op_log(
        self,
        results: List[tuple],
        operation: str,
        on_clash: str,
    ) -> "Path | None":
        """Write a timestamped log file for the write operation. Returns path or None on error."""
        try:
            log_dir = (
                Path(QtCore.QStandardPaths.writableLocation(
                    QtCore.QStandardPaths.AppLocalDataLocation
                ))
                / "logs"
            )
            log_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now()
            log_path = log_dir / f"write_{ts.strftime('%Y-%m-%d_%H-%M-%S')}.log"

            n_ok = sum(1 for _, ok, _ in results if ok)
            n_fail = len(results) - n_ok

            lines = [
                f"Ebook Metadata Write Log",
                f"Generated : {ts.strftime('%Y-%m-%d %H:%M:%S')}",
                f"Operation : {operation}  |  On clash: {on_clash}",
                f"Succeeded : {n_ok}  |  Failed: {n_fail}  |  Total: {len(results)}",
                "=" * 72,
                "",
            ]

            for name, ok, msg in results:
                tag = "OK  " if ok else "FAIL"
                lines.append(f"{tag}  {name}")
                lines.append(f"      {msg}")
                lines.append("")

            log_path.write_text("\n".join(lines), encoding="utf-8")
            return log_path
        except Exception as exc:
            logger.warning("Could not write log file: %s", exc)
            return None

    # --- Cover preview -------------------------------------------------

    def _start_cover_preload(self) -> None:
        """Start a background worker that loads all cover images so dimensions
        are always visible in the table without requiring a user click."""
        if self._cover_preload_worker and self._cover_preload_worker.isRunning():
            self._cover_preload_worker.requestInterruption()

        worker = CoverPreloadWorker(list(self.records), self)
        self._cover_preload_worker = worker

        def _cover_area(url: str, rec) -> int:
            """Pixel area of a cached cover, or 0 if not cached / not parseable."""
            if not url:
                return 0
            entry = (
                _cover_image_cache.get(embedded_cover_cache_key(rec.filepath, url))
                if url.startswith("embedded:")
                else _cover_image_cache.get(url)
            )
            if not entry:
                return 0
            dims = _image_dimensions(entry[0])
            return dims[0] * dims[1] if dims else 0

        def _on_loaded(cache_key: str, data: bytes, mime: str) -> None:
            _cover_image_cache[cache_key] = (data, mime)
            # Auto-upgrade chosen cover to highest-res web image as covers arrive.
            if cache_key.startswith("http"):
                dims = _image_dimensions(data)
                new_area = dims[0] * dims[1] if dims else 0
                if new_area > 0:
                    for rec in self.records:
                        if (rec.metadata_google.get("cover") == cache_key or
                                rec.metadata_openlibrary.get("cover") == cache_key):
                            if new_area > _cover_area(rec.chosen_metadata.get("cover", ""), rec):
                                rec.chosen_metadata["cover"] = cache_key
            self._refresh_cover_column()

        worker.loaded.connect(_on_loaded)
        worker.start()

    def _refresh_cover_column(self) -> None:
        """Emit dataChanged for the cover and thumbnail columns and repaint."""
        last_row = max(0, self.model.rowCount() - 1)
        for col_key in ("cover", "cover_thumb"):
            if col_key not in self.model.columns:
                continue
            col = self.model.columns.index(col_key)
            self.model.dataChanged.emit(
                self.model.index(0, col),
                self.model.index(last_row, col),
                [QtCore.Qt.DisplayRole, QtCore.Qt.UserRole],
            )
        vp = self.table.viewport()
        if vp is not None:
            vp.update()

    def _show_cover_preview(self, url: str, rec=None) -> None:
        """Load a cover image (from cache or download) and show in the preview window."""
        label = _cover_source_name(url)

        if url.startswith("embedded:"):
            if rec is None:
                self._cover_preview.show_loading("(embedded cover not readable)")
                return
            cache_key = embedded_cover_cache_key(rec.filepath, url)
            inner_path = url[len("embedded:"):]
            if cache_key in _cover_image_cache:
                data, _ = _cover_image_cache[cache_key]
                self._cover_preview.update_cover(data, label)
                self._refresh_cover_column()
                return
            try:
                import zipfile as _zf
                with _zf.ZipFile(rec.filepath, "r") as z:
                    data = z.read(inner_path)
                _cover_image_cache[cache_key] = (data, "image/jpeg")
                self._cover_preview.update_cover(data, label)
                self._refresh_cover_column()
            except Exception:
                self._cover_preview.show_loading("(embedded cover not readable)")
            return

        if url in _cover_image_cache:
            data, _ = _cover_image_cache[url]
            self._cover_preview.update_cover(data, label)
            self._refresh_cover_column()
            return

        self._cover_preview.show_loading(f"Loading {label}…")
        if self._cover_load_worker and self._cover_load_worker.isRunning():
            self._cover_load_worker.requestInterruption()

        worker = CoverLoadWorker(url)
        self._cover_load_worker = worker

        def _on_loaded(loaded_url: str, data: bytes, mime: str) -> None:
            _cover_image_cache[loaded_url] = (data, mime)
            self._cover_preview.update_cover(data, _cover_source_name(loaded_url))
            self._refresh_cover_column()

        def _on_failed(failed_url: str) -> None:
            self._cover_preview.show_loading(f"Failed to load: {_cover_source_name(failed_url)}")

        worker.finished.connect(_on_loaded)
        worker.failed.connect(_on_failed)
        worker.start()


def main() -> None:
    import sys

    logging.basicConfig(level=logging.DEBUG)

    app = QtWidgets.QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
