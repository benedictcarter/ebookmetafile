# Ebook Metadata & Filepath Manager

A desktop app for bulk-renaming ebook files and writing metadata, driven by a visual table that lets you compare and choose between multiple metadata sources before committing any changes.

![Python 3.14](https://img.shields.io/badge/python-3.14-blue) ![PyQt5](https://img.shields.io/badge/GUI-PyQt5-green)

---

## Features

- **Scan** a library folder recursively (`.epub`, `.mobi`, `.azw3`, `.azw`, `.pdf`)
- **Three metadata sources per book** — parsed from filename, read from file, and fetched from the web — shown side-by-side so you can pick the best value for each field
- **Web fetch** from Google Books and Open Library in parallel, with live progress
- **Colour-coded cells** — blue = matches chosen, red = disagrees, green = pattern parsed OK
- **Click any source cell** to adopt that value as the chosen value for that field
- **Flexible patterns** — `{author}`, `{title}`, `{series}`, `{series_index}`, `{subject}`, `{tags}`, `{dir_in}`, `{dir_out}` placeholders for both input parsing and output renaming
- **Write selected or all books** — copy or move files to computed new paths, with metadata written into the destination
- **Excel-style copy/paste** — Ctrl+C/V with tile-fill across selections
- **Column visibility** — right-click any header to show/hide columns
- **Settings persistence** — window size, folder, patterns, and column layout saved between sessions
- Built-in **Help documentation** (Help menu or F1)

---

## Download

Grab the latest `EbookMetafile.exe` from [Releases](../../releases) — no installation required, just download and run.

> **Note:** [ExifTool](https://exiftool.org/) must be on your PATH for reading file metadata and writing MOBI/AZW3/PDF files. EPUB metadata is handled natively with no external tools.

---

## Running from source

**Requirements:** Python 3.12+, [ExifTool](https://exiftool.org/) on PATH

```
git clone <this repo>
cd ebookmetafile
python -m venv .venv
.venv\Scripts\pip install -e .
.venv\Scripts\pip install PyQt5
.venv\Scripts\python -m ebookmetafile.gui_main_qt
```

**Run tests:**
```
.venv\Scripts\pip install pytest
.venv\Scripts\python -m pytest tests/ -v
```

**Build the exe:**
```
.venv\Scripts\pip install pyinstaller
.venv\Scripts\pyinstaller ebookmetafile.spec --clean
# Output: dist\EbookMetafile.exe
```

---

## Quick start

1. Click **Browse…** and select your ebook library folder, then click **Scan**
2. Each book shows 5 rows: **Chosen** (bold), **Filename**, **File meta**, **Google**, **Open Library**
3. Set the **Input pattern** to match how your files are named, e.g. `{dir_in}\{author} - {title}`
4. Set the **Output pattern** for how you want them renamed, e.g. `{dir_out}\{author} - {series} {series_index} - {title}`
5. Click any source cell to adopt that value as the chosen value for that field
6. Optionally select books and click **Fetch web metadata for selected books…** to pull from Google Books / Open Library
7. Click **Write all books…** (or select specific books first) to copy/move and write metadata

Press **F1** or open **Help → Documentation** for the full reference.

---

## Licence

MIT
