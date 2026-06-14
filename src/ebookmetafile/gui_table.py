from typing import List, Dict, Any

from PyQt5 import QtCore, QtGui, QtWidgets

from .models import BookRecord, _SOURCE_LABELS
from .columns import COLUMNS, HEADERS
from . import ui_helpers
from . import pattern_engine
from .gui_constants import (
    METADATA_FIELDS,
    ROWS_PER_BOOK,
    ROW_CHOSEN,
    ROW_FILENAME,
    ROW_FILEMETA,
    ROW_GOOGLE,
    ROW_OPENLIBRARY,
    ROW_ISFDB,
    ROW_AMAZON,
    SOURCE_MAP,
    _format_cover_display,
    _cover_image_cache,
    _pixmap_cache,
    embedded_cover_cache_key,
)


_DIVIDER_W = 8  # pixel width of the filter-bar drag zone, centered on each column boundary


class GripHeaderView(QtWidgets.QHeaderView):
    """Horizontal header that paints a visible 6-dot grip at each resizable section's right edge."""

    def __init__(self, parent=None):
        super().__init__(QtCore.Qt.Horizontal, parent)
        self.setSectionsClickable(True)
        self.setHighlightSections(True)

    def paintSection(self, painter: QtGui.QPainter, rect: QtCore.QRect, logical_index: int) -> None:
        super().paintSection(painter, rect, logical_index)
        if self.sectionResizeMode(logical_index) == QtWidgets.QHeaderView.Fixed:
            return
        painter.save()
        painter.setClipRect(rect)
        color = self.palette().color(QtGui.QPalette.Dark)
        color.setAlpha(120)
        cx = rect.right() - 5
        cy = rect.center().y()
        for dx in (0, 3):
            for dy in (-4, 0, 4):
                painter.fillRect(cx + dx, cy + dy, 2, 2, color)
        painter.restore()


class ColumnDivider(QtWidgets.QWidget):
    """Transparent drag handle positioned at a column boundary inside the filter bar."""

    def __init__(self, table: "QtWidgets.QTableView", col_index: int, parent: QtWidgets.QWidget) -> None:
        super().__init__(parent)
        self._table = table
        self._col = col_index
        self._drag_start_x: int | None = None
        self._drag_start_width: int = 0
        self.setCursor(QtCore.Qt.SizeHorCursor)
        self.setFixedWidth(_DIVIDER_W)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton:
            self._drag_start_x = event.globalX()
            self._drag_start_width = self._table.columnWidth(self._col)
        event.accept()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._drag_start_x is not None:
            delta = event.globalX() - self._drag_start_x
            new_width = max(10, self._drag_start_width + delta)
            self._table.setColumnWidth(self._col, new_width)
        event.accept()

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        self._drag_start_x = None
        event.accept()


class ColumnFilterBar(QtWidgets.QWidget):
    """A row of QLineEdit inputs that filter the table by per-column substring."""

    def __init__(
        self,
        table: "QtWidgets.QTableView",
        model: "BookTableModel",
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._table = table
        self._model = model
        self._edits: Dict[int, QtWidgets.QLineEdit] = {}
        self._dividers: Dict[int, ColumnDivider] = {}
        self._global_text: str = ""
        self.setFixedHeight(24)

        for i, col_key in enumerate(model.columns):
            edit = QtWidgets.QLineEdit(self)
            edit.setPlaceholderText("🔍")
            edit.setToolTip(f"Filter by {model.headers.get(col_key, col_key)}")
            edit.textChanged.connect(self._apply_filters)
            self._edits[i] = edit

        # Dividers created after edits so they paint on top (higher z-order).
        for i in range(len(model.columns) - 1):
            self._dividers[i] = ColumnDivider(table, i, self)

        h = table.horizontalHeader()
        h.sectionResized.connect(self._sync_layout)
        h.sectionMoved.connect(self._sync_layout)
        h.geometriesChanged.connect(self._sync_layout)
        table.horizontalScrollBar().valueChanged.connect(self._sync_layout)

    def _sync_layout(self) -> None:
        header = self._table.horizontalHeader()
        v_offset = self._table.verticalHeader().width()
        h = self.height()
        for i, edit in self._edits.items():
            if self._table.isColumnHidden(i):
                edit.hide()
                continue
            x = v_offset + header.sectionViewportPosition(i)
            w = header.sectionSize(i)
            edit.setGeometry(x, 0, w, h)
            edit.show()
        for i, div in self._dividers.items():
            left_hidden = self._table.isColumnHidden(i)
            right_hidden = self._table.isColumnHidden(i + 1)
            if left_hidden or right_hidden:
                div.hide()
                continue
            boundary_x = v_offset + header.sectionViewportPosition(i) + header.sectionSize(i)
            div.setGeometry(boundary_x - _DIVIDER_W // 2, 0, _DIVIDER_W, h)
            div.show()
            div.raise_()

    def set_global_filter(self, text: str) -> None:
        self._global_text = text
        self._apply_filters()

    def _apply_filters(self) -> None:
        active_cols = {
            i: edit.text().lower()
            for i, edit in self._edits.items()
            if edit.text()
        }
        global_text = self._global_text.lower().strip()

        # Source column → sub-row visibility; everything else → book filter
        source_col_idx = next(
            (i for i, _ in active_cols.items()
             if i < len(self._model.columns) and self._model.columns[i] == "source_label"),
            None,
        )
        data_active = {i: t for i, t in active_cols.items() if i != source_col_idx}

        if source_col_idx is not None:
            source_text = active_cols[source_col_idx]
            visible = [sub for sub in range(ROWS_PER_BOOK)
                       if source_text in _SOURCE_LABELS.get(sub, "").lower()]
            new_visible = visible if visible else list(range(ROWS_PER_BOOK))
        else:
            new_visible = list(range(ROWS_PER_BOOK))

        if not data_active and not global_text:
            self._model.set_filters(None, new_visible)
            return

        matching: set = set()
        for rec in self._model.records:
            all_rows = [rec.get_source_row_values(sub, self._model.columns) for sub in range(ROWS_PER_BOOK)]
            all_vals = [v.lower() for row in all_rows for v in row if v]

            col_match = all(
                any(text in (row[i] if i < len(row) else "").lower() for row in all_rows)
                for i, text in data_active.items()
            ) if data_active else True

            global_match = any(global_text in v for v in all_vals) if global_text else True

            if col_match and global_match:
                matching.add(rec.id)
        self._model.set_filters(matching, new_visible)

    def clear_all(self) -> None:
        self._global_text = ""
        for edit in self._edits.values():
            edit.blockSignals(True)
            edit.clear()
            edit.blockSignals(False)
        self._model.set_filters(None, list(range(ROWS_PER_BOOK)))

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self._sync_layout()


class GlobalSearchBar(QtWidgets.QWidget):
    """Full-width search box that filters books where any Chosen-row column matches."""

    def __init__(
        self,
        column_filter_bar: ColumnFilterBar,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._cfb = column_filter_bar

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(6)

        label = QtWidgets.QLabel("Search:")
        label.setFixedWidth(46)
        self._edit = QtWidgets.QLineEdit()
        self._edit.setPlaceholderText("Filter all columns (any match)…")
        self._edit.setClearButtonEnabled(True)
        self._edit.textChanged.connect(self._on_changed)

        self._clear_btn = QtWidgets.QPushButton("✕ Filters")
        self._clear_btn.setFixedWidth(72)
        self._clear_btn.setToolTip("Clear all column filters and the search box")
        self._clear_btn.clicked.connect(self._clear_all_filters)

        layout.addWidget(label)
        layout.addWidget(self._edit)
        layout.addWidget(self._clear_btn)

    def _on_changed(self, text: str) -> None:
        self._cfb.set_global_filter(text)

    def _clear_all_filters(self) -> None:
        self._edit.blockSignals(True)
        self._edit.clear()
        self._edit.blockSignals(False)
        self._cfb.clear_all()

    def clear(self) -> None:
        self._edit.blockSignals(True)
        self._edit.clear()
        self._edit.blockSignals(False)
        self._cfb._global_text = ""


def _cached_cover_bytes(rec: BookRecord, url: str) -> bytes | None:
    """Return cached image bytes for *url*, or None if not yet loaded."""
    if not url:
        return None
    if url.startswith("embedded:"):
        entry = _cover_image_cache.get(embedded_cover_cache_key(rec.filepath, url))
    else:
        entry = _cover_image_cache.get(url)
    return entry[0] if entry else None


class BookTableModel(QtCore.QAbstractTableModel):
    """Qt table model: N*ROWS_PER_BOOK rows, one block per BookRecord."""

    def __init__(self, records: List[BookRecord], parent=None):
        super().__init__(parent)
        self.records = records
        self._book_filter: set | None = None
        self._visible_sub_rows: List[int] = list(range(ROWS_PER_BOOK))
        self.columns = COLUMNS.copy()
        self.headers = HEADERS.copy()

    # --- View state ----------------------------------------------------

    @property
    def _rows_per_book(self) -> int:
        return len(self._visible_sub_rows)

    @property
    def _display_records(self) -> List[BookRecord]:
        if self._book_filter is None:
            return self.records
        return [r for r in self.records if r.id in self._book_filter]

    def records_for_rows(self, rows) -> List[BookRecord]:
        """Convert an iterable of display row indices to their BookRecord objects."""
        disp = self._display_records
        rpb = self._rows_per_book
        seen: set[int] = set()
        result: List[BookRecord] = []
        for row in rows:
            book_idx = row // rpb
            if 0 <= book_idx < len(disp) and book_idx not in seen:
                seen.add(book_idx)
                result.append(disp[book_idx])
        return result

    def set_chosen_only(self, val: bool) -> None:
        self.beginResetModel()
        self._visible_sub_rows = [ROW_CHOSEN] if val else list(range(ROWS_PER_BOOK))
        self.endResetModel()

    def set_book_filter(self, ids: set | None) -> None:
        self.beginResetModel()
        self._book_filter = ids
        self.endResetModel()

    def set_filters(self, book_filter: set | None, visible_sub_rows: List[int]) -> None:
        """Set book filter and sub-row visibility in one reset."""
        self.beginResetModel()
        self._book_filter = book_filter
        self._visible_sub_rows = visible_sub_rows
        self.endResetModel()

    # --- Basic shape ---------------------------------------------------

    def rowCount(self, parent=QtCore.QModelIndex()) -> int:
        return len(self._display_records) * self._rows_per_book

    def columnCount(self, parent=QtCore.QModelIndex()) -> int:
        return len(self.columns)

    # --- Data access ---------------------------------------------------

    def data(self, index: QtCore.QModelIndex, role: int = QtCore.Qt.DisplayRole) -> Any:
        if not index.isValid():
            return None

        rpb = self._rows_per_book
        disp = self._display_records
        book_idx = index.row() // rpb
        sub_row = self._visible_sub_rows[index.row() % rpb]
        col = index.column()

        if book_idx >= len(disp):
            return None

        rec = disp[book_idx]
        col_key = self.columns[col]

        if col_key == "cover_thumb":
            if role == QtCore.Qt.UserRole:
                # Return the cache key the ThumbnailDelegate should look up.
                # Embedded covers need the filepath-qualified key; web covers use the URL directly.
                if sub_row == ROW_CHOSEN:
                    url = rec.get_display_value("chosen", "cover")
                else:
                    source = SOURCE_MAP.get(sub_row)
                    url = rec.get_display_value(source, "cover") if source else ""
                if url.startswith("embedded:"):
                    return embedded_cover_cache_key(rec.filepath, url)
                return url
            if role == QtCore.Qt.DisplayRole:
                return ""
            return None

        if role == QtCore.Qt.EditRole:
            row_vals = rec.get_source_row_values(sub_row, self.columns)
            return row_vals[col] if col < len(row_vals) else ""

        if role == QtCore.Qt.DisplayRole:
            row_vals = rec.get_source_row_values(sub_row, self.columns)
            raw = row_vals[col] if col < len(row_vals) else ""
            if col_key == "cover":
                return _format_cover_display(raw, _cached_cover_bytes(rec, raw))
            return raw

        if role == QtCore.Qt.FontRole and sub_row == ROW_CHOSEN:
            font = QtGui.QFont()
            font.setBold(True)
            return font

        if role == QtCore.Qt.BackgroundRole:
            return self._get_background_brush(rec, sub_row, col_key)

        if role == QtCore.Qt.ToolTipRole:
            if col_key == "format_state" and sub_row == 0 and rec.format_state.endswith(" ⚠"):
                return ("Filename contains a character Windows cannot open (e.g. '?').\n"
                        "It will be automatically renamed when you write.")
            if sub_row == ROW_FILENAME and col_key == "pattern" and rec.pattern_status != "OK":
                return rec.pattern_status
            if sub_row == ROW_CHOSEN and col_key == "output_pattern" and rec.error_message:
                return rec.error_message
            if col_key == "cover":
                raw = rec.get_source_row_values(sub_row, self.columns)
                url = raw[col] if col < len(raw) else ""
                return url or None
            return None

        return None

    def _get_background_brush(
        self, rec: BookRecord, sub_row: int, col_key: str
    ) -> QtGui.QBrush:
        if sub_row == ROW_CHOSEN:
            if col_key == "output_pattern" and rec.output_pattern:
                color_hex = "#ff9999" if rec.error_message else "#99ee99"
                return QtGui.QBrush(QtGui.QColor(color_hex))
            if col_key in METADATA_FIELDS:
                return self._chosen_metadata_brush(rec, col_key)
            if col_key != "isbn_lookup":
                return QtGui.QBrush()

        if sub_row == ROW_FILENAME and col_key == "pattern":
            if not rec.pattern:
                return QtGui.QBrush()
            color_hex = "#99ee99" if rec.pattern_status == "OK" else "#ff9999"
            return QtGui.QBrush(QtGui.QColor(color_hex))

        if col_key == "format_state" and sub_row == 0:
            _FORMAT_COLORS = {
                "EPUB 3.3 ✓": "#c8f0c8",   # green  — already ideal
                "EPUB 3":     "#d0e8ff",   # blue   — needs improvement
                "EPUB 2":     "#fff0a0",   # yellow — needs upgrade
                "EPUB":       "#fff0a0",
                "MOBI":       "#ffd8a8",   # orange — needs conversion
                "AZW3":       "#ffd8a8",
                "AZW":        "#ffd8a8",
                "PDF":        "#e8e8e8",   # grey   — no conversion
            }
            # Strip the ⚠ suffix for colour lookup; warn states get red tint
            base_state = rec.format_state.removesuffix(" ⚠")
            if rec.format_state.endswith(" ⚠"):
                return QtGui.QBrush(QtGui.QColor("#ffb0b0"))   # red — filename issue
            color = _FORMAT_COLORS.get(base_state)
            if color:
                return QtGui.QBrush(QtGui.QColor(color))
            return QtGui.QBrush()

        if col_key == "isbn_lookup":
            from .models import _ISBN_LOOKUP_MAP
            entry = _ISBN_LOOKUP_MAP.get(sub_row)
            if entry is None:
                return QtGui.QBrush()
            source_key, meta_attr = entry
            check = rec.isbn_check_by_source.get(source_key) or {}
            if not check:
                return QtGui.QBrush()
            row_meta = getattr(rec, meta_attr, {}) or {}
            quality = ui_helpers.isbn_lookup_quality(
                chosen_title=row_meta.get("title") or "",
                chosen_author=row_meta.get("author") or "",
                source_title=check.get("title") or "",
                source_author=check.get("author") or "",
                chosen_year=row_meta.get("pub_date") or "",
                source_year=check.get("pub_date") or "",
            )
            color_hex = ui_helpers.color_key_to_hex(quality)
            if color_hex:
                return QtGui.QBrush(QtGui.QColor(color_hex))
            return QtGui.QBrush()

        if col_key not in METADATA_FIELDS:
            return QtGui.QBrush()

        source = SOURCE_MAP.get(sub_row)
        if source is None:
            return QtGui.QBrush()

        source_val = rec.get_display_value(source, col_key)
        chosen_val = rec.get_display_value("chosen", col_key)
        key = ui_helpers.get_source_row_highlight(source_val, chosen_val)
        color_hex = ui_helpers.color_key_to_hex(key)
        if color_hex:
            return QtGui.QBrush(QtGui.QColor(color_hex))
        return QtGui.QBrush()

    def _chosen_metadata_brush(self, rec: BookRecord, col_key: str) -> QtGui.QBrush:
        """Colour the Chosen row based on source consensus for a metadata field."""
        chosen = rec.get_display_value("chosen", col_key).strip()
        source_vals = [
            v for src in SOURCE_MAP.values()
            if (v := rec.get_display_value(src, col_key).strip())
        ]

        if not source_vals:
            return QtGui.QBrush()

        unique = set(source_vals)

        if len(unique) == 1:
            agreed = next(iter(unique))
            if not chosen or chosen == agreed:
                return QtGui.QBrush(QtGui.QColor("#99bbff"))  # blue: consensus
            return QtGui.QBrush(QtGui.QColor("#ff9999"))  # red: diverges from consensus

        if chosen and chosen in unique:
            return QtGui.QBrush(QtGui.QColor("#99ee99"))  # green: picked from a source
        return QtGui.QBrush(QtGui.QColor("#ff9999"))  # red: conflict unresolved

    # --- Editing -------------------------------------------------------

    def flags(self, index: QtCore.QModelIndex) -> QtCore.Qt.ItemFlags:
        if not index.isValid():
            return QtCore.Qt.NoItemFlags

        sub_row = self._visible_sub_rows[index.row() % self._rows_per_book]
        col_key = self.columns[index.column()]

        base = QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable
        if col_key == "cover_thumb":
            return base
        editable_chosen = sub_row == ROW_CHOSEN and col_key in (
            METADATA_FIELDS | {"output_pattern", "dir_out"}
        )
        editable_filename = sub_row == ROW_FILENAME and col_key == "pattern"

        if editable_chosen or editable_filename:
            return base | QtCore.Qt.ItemIsEditable
        return base

    def setData(
        self, index: QtCore.QModelIndex, value: Any, role: int = QtCore.Qt.EditRole
    ) -> bool:
        if not index.isValid() or role != QtCore.Qt.EditRole:
            return False

        rpb = self._rows_per_book
        disp = self._display_records
        book_idx = index.row() // rpb
        sub_row = self._visible_sub_rows[index.row() % rpb]
        col_key = self.columns[index.column()]

        if book_idx >= len(disp):
            return False

        rec = disp[book_idx]
        text = str(value)

        if sub_row == ROW_CHOSEN:
            if col_key in METADATA_FIELDS:
                rec.chosen_metadata[col_key] = text
                rec.recompute_new_filepath()
            elif col_key == "output_pattern":
                rec.output_pattern = text
                rec.recompute_new_filepath()
            elif col_key == "dir_out":
                rec.dir_out = text
                rec.recompute_new_filepath()
            else:
                return False
        elif sub_row == ROW_FILENAME and col_key == "pattern":
            rec.pattern = text
            parsed, status = pattern_engine.parse_filename(rec.pattern, rec.filepath)
            rec.metadata_pattern = parsed
            rec.pattern_status = status
            rec.sync_dir_out()
            rec.ensure_chosen_defaults(list(METADATA_FIELDS))
            rec.recompute_new_filepath()
        else:
            return False

        first_row = book_idx * rpb
        top_left = self.index(first_row, 0)
        bottom_right = self.index(first_row + rpb - 1, self.columnCount() - 1)
        self.dataChanged.emit(
            top_left, bottom_right, [QtCore.Qt.DisplayRole, QtCore.Qt.BackgroundRole]
        )
        return True

    def apply_row_updates(self, row_index: int, col_texts: Dict[str, str]) -> None:
        """Apply multiple column updates for an absolute row index (paste support)."""
        rpb = self._rows_per_book
        disp = self._display_records
        book_idx = row_index // rpb
        sub_row = self._visible_sub_rows[row_index % rpb]

        if book_idx < 0 or book_idx >= len(disp):
            return

        rec = disp[book_idx]
        affects_filename = False
        pattern_changed = False

        if sub_row == ROW_CHOSEN:
            for col_key, text in col_texts.items():
                t = (text or "").strip()
                if col_key in METADATA_FIELDS:
                    rec.chosen_metadata[col_key] = t
                    affects_filename = True
                elif col_key == "output_pattern":
                    rec.output_pattern = t
                    affects_filename = True
                elif col_key == "dir_out":
                    rec.dir_out = t
                    affects_filename = True
        elif sub_row == ROW_FILENAME and "pattern" in col_texts:
            rec.pattern = (col_texts["pattern"] or "").strip()
            pattern_changed = True
            affects_filename = True

        if pattern_changed:
            parsed, status = pattern_engine.parse_filename(rec.pattern, rec.filepath)
            rec.metadata_pattern = parsed
            rec.pattern_status = status
            rec.sync_dir_out()
            rec.ensure_chosen_defaults(list(METADATA_FIELDS))

        if affects_filename:
            rec.recompute_new_filepath()

        first_row = book_idx * rpb
        top_left = self.index(first_row, 0)
        bottom_right = self.index(first_row + rpb - 1, self.columnCount() - 1)
        self.dataChanged.emit(
            top_left, bottom_right, [QtCore.Qt.DisplayRole, QtCore.Qt.BackgroundRole]
        )

    # --- Header labels --------------------------------------------------

    def headerData(
        self,
        section: int,
        orientation: QtCore.Qt.Orientation,
        role: int = QtCore.Qt.DisplayRole,
    ) -> Any:
        if role != QtCore.Qt.DisplayRole:
            return None
        if orientation == QtCore.Qt.Horizontal:
            col_key = self.columns[section]
            return self.headers.get(col_key, col_key)
        else:
            rpb = self._rows_per_book
            book_idx = section // rpb
            sub_row = self._visible_sub_rows[section % rpb]
            sub_labels = {ROW_CHOSEN: "C", ROW_FILENAME: "F", ROW_FILEMETA: "M", ROW_GOOGLE: "G", ROW_OPENLIBRARY: "L", ROW_ISFDB: "S", ROW_AMAZON: "A"}
            return f"{book_idx + 1}{sub_labels.get(sub_row, str(sub_row))}"

    # --- Sorting --------------------------------------------------------

    def sort(
        self, column: int, order: QtCore.Qt.SortOrder = QtCore.Qt.AscendingOrder
    ) -> None:
        if column < 0 or column >= len(self.columns):
            return

        col_key = self.columns[column]
        reverse = order == QtCore.Qt.DescendingOrder

        def _index_key(val: str):
            if val and val.isdigit():
                return (int(val), val)
            return (float("inf"), val or "")

        if col_key in METADATA_FIELDS:
            if col_key == "series_index":
                key_fn = lambda r: _index_key(r.chosen_metadata.get("series_index") or "")
            else:
                key_fn = lambda r, k=col_key: (r.chosen_metadata.get(k) or "").lower()
        elif col_key == "filepath":
            key_fn = lambda r: str(r.filepath).lower()
        elif col_key == "dir_in":
            key_fn = lambda r: (r.metadata_pattern.get("dir_in") or str(r.filepath.parent)).lower()
        elif col_key == "dir_out":
            key_fn = lambda r: r.dir_out.lower()
        elif col_key == "pattern":
            key_fn = lambda r: r.pattern.lower()
        elif col_key == "new_filepath":
            key_fn = lambda r: str(r.new_filepath or r.filepath).lower()
        elif col_key == "error":
            key_fn = lambda r: (r.error_message or "").lower()
        else:
            key_fn = lambda r: str(r.filepath).lower()

        self.layoutAboutToBeChanged.emit()
        self.records.sort(key=key_fn, reverse=reverse)
        self.layoutChanged.emit()


class LiveEditDelegate(QtWidgets.QStyledItemDelegate):
    """Delegate that ensures edits go through model.setData and draws block separators."""

    def initStyleOption(
        self,
        option: QtWidgets.QStyleOptionViewItem,
        index: QtCore.QModelIndex,
    ) -> None:
        super().initStyleOption(option, index)
        # Set both backgroundBrush and palette.Base/AlternateBase: the Windows
        # Vista style (Win10/11) ignores backgroundBrush and reads the palette.
        if not (option.state & QtWidgets.QStyle.State_Selected):
            bg = index.data(QtCore.Qt.BackgroundRole)
            if isinstance(bg, QtGui.QBrush) and bg.style() != QtCore.Qt.NoBrush:
                option.backgroundBrush = bg
                palette = QtGui.QPalette(option.palette)
                palette.setBrush(QtGui.QPalette.Base, bg)
                palette.setBrush(QtGui.QPalette.AlternateBase, bg)
                option.palette = palette

    def paint(self, painter: QtGui.QPainter, option: QtWidgets.QStyleOptionViewItem, index: QtCore.QModelIndex) -> None:
        super().paint(painter, option, index)

        model = index.model()
        r = option.rect

        painter.save()
        painter.setBrush(QtCore.Qt.NoBrush)

        # Black border around directly editable cells
        if model.flags(index) & QtCore.Qt.ItemIsEditable:
            painter.setPen(QtGui.QPen(QtGui.QColor("#000000"), 1))
            painter.drawRect(r.adjusted(0, 0, -1, -1))

        # Thick grey line under the last sub-row of each book (group separator)
        rpb = model._rows_per_book if hasattr(model, "_rows_per_book") else ROWS_PER_BOOK
        if index.row() % rpb == rpb - 1:
            painter.setPen(QtGui.QPen(QtGui.QColor("#888888"), 2))
            painter.drawLine(r.bottomLeft(), r.bottomRight())

        painter.restore()

    def setModelData(self, editor, model, index):
        """Push editor text into the model; with multi-select, fills all selected cells."""
        if isinstance(editor, QtWidgets.QLineEdit):
            text = editor.text()

            view = self.parent()
            try:
                sel_model = view.selectionModel()
                indexes = sel_model.selectedIndexes()
            except Exception:
                indexes = []

            if indexes and len(indexes) > 1:
                rows: Dict[int, set[int]] = {}
                for idx in indexes:
                    if not idx.isValid():
                        continue
                    rows.setdefault(idx.row(), set()).add(idx.column())

                for r, cols in rows.items():
                    col_texts: Dict[str, str] = {}
                    for c in sorted(cols):
                        if 0 <= c < model.columnCount():
                            col_key = model.columns[c]
                            col_texts[col_key] = text
                    model.apply_row_updates(r, col_texts)

                return

            model.setData(index, text, QtCore.Qt.EditRole)
        else:
            super().setModelData(editor, model, index)


class ThumbnailDelegate(QtWidgets.QStyledItemDelegate):
    """Paints a scaled cover image in the cover_thumb column."""

    def paint(self, painter: QtGui.QPainter, option: QtWidgets.QStyleOptionViewItem, index: QtCore.QModelIndex) -> None:
        super().paint(painter, option, index)  # draws background / selection highlight

        cache_key = index.data(QtCore.Qt.UserRole)
        if not cache_key:
            return

        pm = _pixmap_cache.get(cache_key)
        if pm is None:
            entry = _cover_image_cache.get(cache_key)
            if not entry:
                return
            pm = QtGui.QPixmap()
            pm.loadFromData(entry[0])
            if pm.isNull():
                return
            _pixmap_cache[cache_key] = pm

        rect = option.rect.adjusted(2, 2, -2, -2)
        scaled = pm.scaled(rect.size(), QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
        x = rect.x() + (rect.width() - scaled.width()) // 2
        y = rect.y() + (rect.height() - scaled.height()) // 2
        painter.drawPixmap(x, y, scaled)
