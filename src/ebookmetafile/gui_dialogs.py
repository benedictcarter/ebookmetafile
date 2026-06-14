import webbrowser

from PyQt5 import QtCore, QtGui, QtWidgets

# ---------------------------------------------------------------------------
# API key storage — Windows Credential Manager via keyring (DPAPI-encrypted).
# Falls back to QSettings plain-text when keyring is unavailable.
# ---------------------------------------------------------------------------

_KR_SERVICE  = "ebookmetafile"
_KR_USERNAME = "google_books_api_key"
_QS_KEY      = "google_books_api_key"


def load_google_api_key() -> str:
    """Return the saved Google Books API key, or '' if none stored."""
    try:
        import keyring
        val = keyring.get_password(_KR_SERVICE, _KR_USERNAME)
        if val is not None:
            return val
        # One-time migration: promote any plain-text QSettings value to keyring
        qs = QtCore.QSettings("ebookmetafile", "EbookMetafile")
        legacy = qs.value(_QS_KEY, "") or ""
        if legacy:
            keyring.set_password(_KR_SERVICE, _KR_USERNAME, legacy)
            qs.remove(_QS_KEY)
            return legacy
        return ""
    except Exception:
        return QtCore.QSettings("ebookmetafile", "EbookMetafile").value(_QS_KEY, "") or ""


def save_google_api_key(key: str) -> None:
    """Persist the Google Books API key in Windows Credential Manager."""
    try:
        import keyring
        import keyring.errors
        if key:
            keyring.set_password(_KR_SERVICE, _KR_USERNAME, key)
        else:
            try:
                keyring.delete_password(_KR_SERVICE, _KR_USERNAME)
            except keyring.errors.PasswordDeleteError:
                pass
        # Remove any legacy QSettings copy
        QtCore.QSettings("ebookmetafile", "EbookMetafile").remove(_QS_KEY)
    except Exception:
        QtCore.QSettings("ebookmetafile", "EbookMetafile").setValue(_QS_KEY, key)


class CoverPreviewWindow(QtWidgets.QDialog):
    """Persistent, non-modal window that shows a cover image.

    A single instance is created by MainWindow and reused for every cover click.
    Clicking a cover cell calls update_cover(); the window stays in its current
    screen position and simply swaps the displayed image.
    """

    def __init__(self, parent=None):
        super().__init__(parent, QtCore.Qt.Window)
        self.setWindowTitle("Cover Preview")
        self.resize(400, 560)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        self._image_label = QtWidgets.QLabel(alignment=QtCore.Qt.AlignCenter)
        self._image_label.setMinimumSize(200, 200)
        self._image_label.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding
        )
        layout.addWidget(self._image_label, stretch=1)

        self._info_label = QtWidgets.QLabel()
        self._info_label.setAlignment(QtCore.Qt.AlignCenter)
        self._info_label.setWordWrap(True)
        layout.addWidget(self._info_label)

        self._pixmap: QtGui.QPixmap | None = None

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._refresh_pixmap()

    def update_cover(self, image_bytes: bytes, label: str) -> None:
        """Replace displayed image. label is shown below (e.g. 'Google Books')."""
        px = QtGui.QPixmap()
        px.loadFromData(image_bytes)
        if px.isNull():
            self._image_label.setText("(could not decode image)")
            self._info_label.setText(label)
            self._pixmap = None
        else:
            self._pixmap = px
            self._refresh_pixmap()
            w, h = px.width(), px.height()
            self._info_label.setText(f"{label}  ·  {w}×{h}")
        self.show()
        self.raise_()
        self.activateWindow()

    def show_loading(self, label: str) -> None:
        self._image_label.setText("Loading…")
        self._info_label.setText(label)
        self.show()
        self.raise_()

    def _refresh_pixmap(self) -> None:
        if self._pixmap is None:
            return
        available = self._image_label.size()
        scaled = self._pixmap.scaled(
            available, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation
        )
        self._image_label.setPixmap(scaled)


class ApplyDialog(QtWidgets.QDialog):
    """Options dialog shown before applying metadata writes and file operations."""

    def __init__(self, records, parent=None):
        super().__init__(parent)
        self._records = records
        self.setWindowTitle("Write Files")
        self.setMinimumWidth(460)

        layout = QtWidgets.QVBoxLayout(self)

        op_group = QtWidgets.QGroupBox("File operation")
        op_layout = QtWidgets.QVBoxLayout(op_group)
        self._copy_radio = QtWidgets.QRadioButton(
            "Copy to new location (keep originals)"
        )
        self._move_radio = QtWidgets.QRadioButton("Move to new location")
        self._copy_radio.setChecked(True)
        op_layout.addWidget(self._copy_radio)
        op_layout.addWidget(self._move_radio)
        layout.addWidget(op_group)

        clash_group = QtWidgets.QGroupBox("If the destination file already exists")
        clash_layout = QtWidgets.QVBoxLayout(clash_group)
        self._append_radio = QtWidgets.QRadioButton(
            "Append suffix — file (1).epub, file (2).epub, …"
        )
        self._replace_radio = QtWidgets.QRadioButton("Replace (overwrite)")
        self._append_radio.setChecked(True)
        clash_layout.addWidget(self._append_radio)
        clash_layout.addWidget(self._replace_radio)
        layout.addWidget(clash_group)

        epub_group = QtWidgets.QGroupBox("EPUB / conversion options")
        epub_layout = QtWidgets.QVBoxLayout(epub_group)
        self._split_cb = QtWidgets.QCheckBox(
            "Rebuild to ideal EPUB 3.3 (split chapters, nav.xhtml, clean markup)"
        )
        self._split_cb.setToolTip(
            "Converts EPUB 2 and non-ideal EPUB 3 files to clean EPUB 3.3 format:\n"
            "one file per chapter, nav.xhtml + NCX TOC, stripped Mobipocket markup.\n"
            "Already-ideal EPUBs (marked ✓) are unchanged."
        )
        epub_layout.addWidget(self._split_cb)

        # Show MOBI conversion option only when MOBI files are in the set
        _mobi_states = {"MOBI", "AZW3", "AZW"}
        has_mobi = any(
            getattr(r, "format_state", "") in _mobi_states for r in records
        )
        self._mobi_cb: QtWidgets.QCheckBox | None = None
        if has_mobi:
            self._mobi_cb = QtWidgets.QCheckBox(
                "Convert MOBI/AZW3 → EPUB 3.3 (creates .epub alongside original)"
            )
            self._mobi_cb.setChecked(True)
            self._mobi_cb.setToolTip(
                "For each MOBI/AZW3 file, create a companion .epub in the same\n"
                "output location. The original MOBI still gets its text metadata\n"
                "updated. Cover images are embedded in the new EPUB only."
            )
            epub_layout.addWidget(self._mobi_cb)

        layout.addWidget(epub_group)

        # Operations summary — updates live when checkboxes change
        summary_group = QtWidgets.QGroupBox("Operations summary")
        summary_layout = QtWidgets.QVBoxLayout(summary_group)
        self._summary_label = QtWidgets.QLabel()
        self._summary_label.setTextFormat(QtCore.Qt.RichText)
        self._summary_label.setWordWrap(True)
        summary_layout.addWidget(self._summary_label)
        layout.addWidget(summary_group)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch()
        apply_btn = QtWidgets.QPushButton("Apply")
        apply_btn.setDefault(True)
        cancel_btn = QtWidgets.QPushButton("Cancel")
        apply_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(apply_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

        # Wire live updates
        self._split_cb.stateChanged.connect(self._refresh_summary)
        if self._mobi_cb:
            self._mobi_cb.stateChanged.connect(self._refresh_summary)
        self._refresh_summary()

    def _refresh_summary(self) -> None:
        split = self._split_cb.isChecked()
        convert = self._mobi_cb.isChecked() if self._mobi_cb else False
        _mobi_states = {"MOBI", "AZW3", "AZW"}

        counts: dict = {
            "convert":    0,   # MOBI → EPUB 3.3
            "mobi_meta":  0,   # MOBI, no conversion
            "upgrade":    0,   # EPUB 2 → EPUB 3.3
            "improve":    0,   # EPUB 3 (non-ideal) → EPUB 3.3
            "meta_only":  0,   # already ideal or PDF — metadata/rename only
            "renamed":    0,   # path changes
        }

        for rec in self._records:
            fs = getattr(rec, "format_state", "")
            if fs in _mobi_states:
                if convert:
                    counts["convert"] += 1
                else:
                    counts["mobi_meta"] += 1
            elif fs == "EPUB 2" and split:
                counts["upgrade"] += 1
            elif fs in ("EPUB 3", "EPUB") and split:
                counts["improve"] += 1
            else:
                counts["meta_only"] += 1

            try:
                new_fp = getattr(rec, "new_filepath", None)
                if new_fp and new_fp.resolve() != rec.filepath.resolve():
                    counts["renamed"] += 1
            except Exception:
                pass

        rows = []
        if counts["convert"]:
            rows.append(f"Convert MOBI/AZW3 → EPUB 3.3 &nbsp; <b>{counts['convert']}</b> files")
        if counts["mobi_meta"]:
            rows.append(f"MOBI/AZW3 — metadata only &nbsp; <b>{counts['mobi_meta']}</b> files")
        if counts["upgrade"]:
            rows.append(f"Upgrade EPUB 2 → EPUB 3.3 &nbsp; <b>{counts['upgrade']}</b> files")
        if counts["improve"]:
            rows.append(f"Improve EPUB 3 → ideal EPUB 3.3 &nbsp; <b>{counts['improve']}</b> files")
        if counts["meta_only"]:
            rows.append(f"Metadata / rename only &nbsp; <b>{counts['meta_only']}</b> files")
        if counts["renamed"]:
            rows.append(f"Files to rename / move &nbsp; <b>{counts['renamed']}</b> files")

        total = len(self._records)
        header = f"<b>{total}</b> file{'s' if total != 1 else ''} selected<br>"
        self._summary_label.setText(
            header + "<br>".join(rows) if rows else header + "Nothing to do."
        )

    @property
    def operation(self) -> str:
        return "copy" if self._copy_radio.isChecked() else "move"

    @property
    def on_clash(self) -> str:
        return "append" if self._append_radio.isChecked() else "replace"

    @property
    def split_epub(self) -> bool:
        return self._split_cb.isChecked()

    @property
    def convert_mobi(self) -> bool:
        return self._mobi_cb.isChecked() if self._mobi_cb else False


# Direct link to create a Books API key in Google Cloud Console
_GOOGLE_KEY_URL = (
    "https://console.cloud.google.com/flows/enableapi"
    "?apiid=books.googleapis.com"
    "&redirect=https://console.cloud.google.com/apis/credentials/key"
)


class FetchProgressDialog(QtWidgets.QDialog):
    """Modal fetch-progress dialog with a scrollable console log for errors/misses."""

    canceled = QtCore.pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Fetching metadata")
        self.setModal(True)
        self.setMinimumWidth(500)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(6)

        self._label = QtWidgets.QLabel("Fetching…")
        layout.addWidget(self._label)

        self._bar = QtWidgets.QProgressBar()
        self._bar.setRange(0, 0)
        layout.addWidget(self._bar)

        self._console = QtWidgets.QPlainTextEdit()
        self._console.setReadOnly(True)
        self._console.setMaximumBlockCount(1000)
        font = QtGui.QFont("Courier New", 8)
        font.setStyleHint(QtGui.QFont.Monospace)
        self._console.setFont(font)
        self._console.setMinimumHeight(160)
        layout.addWidget(self._console)

        self._cancel_btn = QtWidgets.QPushButton("Cancel")
        self._cancel_btn.clicked.connect(self._on_cancel)
        layout.addWidget(self._cancel_btn, alignment=QtCore.Qt.AlignRight)

    def _on_cancel(self):
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.setText("Cancelling…")
        self.canceled.emit()

    def set_progress(self, label: str, value: int, maximum: int) -> None:
        self._label.setText(label)
        self._bar.setMaximum(maximum)
        self._bar.setValue(value)

    def append_log(self, text: str) -> None:
        self._console.appendPlainText(text)
        sb = self._console.verticalScrollBar()
        sb.setValue(sb.maximum())


class FetchSourceDialog(QtWidgets.QDialog):
    """Ask which web sources to query before fetching metadata."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Fetch Web Metadata")
        self.setMinimumWidth(420)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel("Select sources to query:"))

        self._google_cb = QtWidgets.QCheckBox("Google Books")
        self._google_cb.setChecked(True)
        self._ol_cb = QtWidgets.QCheckBox("Open Library")
        self._ol_cb.setChecked(True)
        self._isfdb_cb = QtWidgets.QCheckBox("ISFDB  (Internet Speculative Fiction Database)")
        self._isfdb_cb.setChecked(True)
        self._amz_cb = QtWidgets.QCheckBox("Amazon  (uses ISBN→direct URL; may be blocked)")
        self._amz_cb.setChecked(True)
        layout.addWidget(self._google_cb)
        layout.addWidget(self._ol_cb)
        layout.addWidget(self._isfdb_cb)
        layout.addWidget(self._amz_cb)

        # --- Google Books API key ---
        layout.addSpacing(8)
        key_group = QtWidgets.QGroupBox("Google Books API Key  (recommended for large libraries)")
        key_layout = QtWidgets.QVBoxLayout(key_group)

        saved_key = load_google_api_key()
        has_key = bool(saved_key)

        status_label = QtWidgets.QLabel(
            "✓  Key saved — rate limiting avoided." if has_key
            else "⚠  No key set — Google may rate-limit large fetches."
        )
        status_label.setStyleSheet("color: green;" if has_key else "color: #b06000;")
        key_layout.addWidget(status_label)

        key_row = QtWidgets.QHBoxLayout()
        self._key_edit = QtWidgets.QLineEdit(saved_key)
        self._key_edit.setPlaceholderText("Paste API key here…")
        self._key_edit.setEchoMode(QtWidgets.QLineEdit.Password)

        get_key_btn = QtWidgets.QPushButton("Get key →")
        get_key_btn.setToolTip("Opens Google Cloud Console in your browser to create a free API key")
        get_key_btn.clicked.connect(lambda: webbrowser.open(_GOOGLE_KEY_URL))

        key_row.addWidget(self._key_edit)
        key_row.addWidget(get_key_btn)
        key_layout.addLayout(key_row)

        note = QtWidgets.QLabel(
            "Free key · 1,000 queries/day · "
            "<a href='https://developers.google.com/books/docs/v1/using#APIKey'>instructions</a>"
        )
        note.setOpenExternalLinks(True)
        note.setStyleSheet("font-size: 8pt; color: #555;")
        key_layout.addWidget(note)

        layout.addWidget(key_group)
        layout.addSpacing(4)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QtWidgets.QPushButton("Fetch")
        ok_btn.setDefault(True)
        cancel_btn = QtWidgets.QPushButton("Cancel")
        ok_btn.clicked.connect(self._on_accept)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

    def _on_accept(self) -> None:
        save_google_api_key(self._key_edit.text().strip())
        self.accept()

    @property
    def google_api_key(self) -> str:
        return self._key_edit.text().strip()

    @property
    def fetch_google(self) -> bool:
        return self._google_cb.isChecked()

    @property
    def fetch_openlibrary(self) -> bool:
        return self._ol_cb.isChecked()

    @property
    def fetch_isfdb(self) -> bool:
        return self._isfdb_cb.isChecked()

    @property
    def fetch_amazon(self) -> bool:
        return self._amz_cb.isChecked()
