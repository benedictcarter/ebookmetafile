## What this is
A PyQt5 desktop application for managing ebook metadata and file paths. It scans a directory for EPUB/MOBI/PDF/AZW3 files, fetches metadata from Google Books, Open Library, ISFDB, and Amazon, auto-populates blank fields, lets the user review/edit metadata per field, and writes it back while optionally renaming/moving files. It can also convert MOBI and EPUB 2 files to clean EPUB 3.3 format.

## Architecture
```
src/ebookmetafile/
  models.py           BookRecord dataclass, BookTableModel, recompute/sync logic
  columns.py          Column definitions and METADATA_FIELDS list
  pattern_engine.py   Filename pattern parser (e.g. {author}/{title})
  file_scan.py        Directory scanner (path discovery only)
  metadata_read.py    Pure-Python metadata readers: EPUB (OPF/zipfile), MOBI (EXTH headers), PDF (pypdf)
  metadata_fetch.py   Google Books, Open Library, ISFDB, Amazon fetch functions
  file_apply.py       write_metadata(), apply_record() (copy/move + write), split_epub_by_toc()
  file_convert.py     convert_mobi_to_epub() — MOBI7 and KF8/AZW3 paths
  epub_build.py       EpubBook/EpubChapter dataclasses, build_epub(), is_our_epub()
  epub_clean.py       clean_chapter_html(), read_epub_book(), parse_ncx_chapters(), split_body_at_chapters()
  gui_constants.py    Shared constants, cover image cache, helper functions
  gui_workers.py      QThread workers: ScanWorker, MetadataFetchWorker, WriteWorker, CoverPreloadWorker
  gui_dialogs.py      CoverPreviewWindow, ApplyDialog, FetchSourceDialog
  gui_table.py        BookTableModel, LiveEditDelegate
  gui_main_qt.py      MainWindow, entry point
  gui_help.py         Help HTML string
  ui_helpers.py       parse_tsv, tile_clipboard_to_rect, highlight colours
tests/                pytest suite (401 tests), no GUI unit tests
```

## Current task
Security review complete (2026-06-14): fixed two input-handling vulnerabilities reachable from untrusted metadata (web sources or crafted ebook fields) — (1) regex-replacement backreference injection in OPF/NCX insert branches, (2) cover-download URL scheme allowlist. Reset-chosen-metadata crash fixed; garbage file-meta values (no word chars) now lose to filename-parsed values in `ensure_chosen_defaults`. Help updated for GUI additions. 401 tests pass.

## Key decisions made
- **Ideal EPUB pipeline**: Instead of patching kindleunpack output, any source (MOBI7, KF8, existing EPUB) is treated as a harvest source. Content is extracted, cleaned, and assembled into a fresh EPUB 3.3 via `epub_build.py`. This was chosen after patching produced EPUBs with blank pages, missing TOC panels, and Mobipocket namespace pollution.
- **EPUB 3.3 with EPUB 2 fallback**: `epub_build.py` writes both `nav.xhtml` (EPUB 3 TOC) and `toc.ncx` (EPUB 2 fallback) for maximum reader compatibility (Calibre, Sumatra, Kobo, Kindle all read one or the other).
- **Generator marker**: OPF includes `<meta name="generator" content="ebookmetafile"/>`. `is_our_epub()` checks for this so `split_epub_by_toc()` is a no-op on EPUBs we already produced (idempotent).
- **Cover cache key**: `embedded_cover_cache_key(filepath, url)` uses filepath-qualified keys to avoid collision when different EPUBs share the same internal path (e.g. `OEBPS/cover.jpg`).
- **Write safety**: `apply_record()` updates `rec.filepath` immediately after `shutil.move` succeeds, before writing metadata, so the record always points to the actual file location even if metadata write fails.
- **Atomic writes**: All EPUB/MOBI writers use temp-file-then-replace pattern to avoid partial writes.
- **GUI workers not unit-tested**: Too much Qt setup cost for the value; only the logic layer (models, file_apply, epub_build, etc.) is unit-tested.
- **Two-phase fetch**: Amazon runs after all other sources complete so ISBNs discovered by Google/OpenLibrary in Phase 1 are available for Amazon's ISBN-only lookup in Phase 2.
- **Per-source thread pools**: `MetadataFetchWorker` uses a separate `ThreadPoolExecutor` per source (not a shared pool with semaphores) — shared pools cause thread starvation when semaphores block workers.
- **Auto-population**: After fetch, blank `chosen_metadata` fields are filled from sources in priority order (Google → OpenLibrary → ISFDB → Amazon). Existing values are never overwritten.
- **OpenLibrary ISBN fallback**: When `search.json` doesn't return `isbn` (common for older books), we fetch `edition_key[0..2]` individually — edition JSON has `isbn_13`/`isbn_10` far more reliably than the Solr index.
- **Source column filter is row-level**: Filtering on the Source column collapses each book to only matching sub-rows (using `_visible_sub_rows`). All other column filters are book-level (hide entire books). Both use `BookTableModel.set_filters()` to apply in one reset.
- **MOBI7 front-matter isolation**: `split_body_at_chapters` separates substantial pre-chapter-1 content (bibliography pages, title page images) into a "Front Matter" chapter rather than dumping it into chapter 1. Threshold: >100 stripped text chars or any `<img>` tag.
- **img layout attribute stripping**: `clean_chapter_html` strips `height=`, `width=`, and `align=` from `<img>` tags. Percentage values on these cause EPUB readers to stretch thumbnail images to fill the viewport.
- **MOBI title from binary**: `_read_mobi_title()` reads the MOBI Full Name field directly from the PalmDB/MOBI header. kindleunpack often writes a garbled dc:title to the extracted OPF; the binary field is authoritative.
- **Pure-Python metadata reading**: `metadata_read.py` replaced ExifTool (external dep) with direct parsers — EPUB: regex over OPF from zipfile; MOBI/AZW3: EXTH record iteration; PDF: pypdf. ISBN was never surfaced by ExifTool (returned as numeric `Identifier` field, wrong key in TAG_MAP) — fixed as part of this rewrite.
- **NCX anchor fallback**: When an NCX anchor ID is absent from the HTML body (publisher EPUBs often omit the `<a id="...">` element), `_match_ncx_labels_to_headings()` locates the matching heading by text (alphanumeric-normalised, entity-decoded comparison) and splits there. This fixed Hyperion-style EPUBs where each chapter had `Chapter N` + tale subtitle structure.
- **Heading-split guard**: `split_body_at_headings` is now only called for spine items with NO NCX entries. Files with NCX metadata (even just a parent label) are never split at arbitrary headings — this prevents multi-section chapters being fragmented.
- **Cover-page-once**: `_is_cover_page` now only fires on the first image-only spine item (`_cover_page_skipped` flag). Later image-only pages (title pages rendered as images) are kept.
- **Single-anchor split**: `split_body_at_chapters` threshold lowered from `< 2` to `< 1`, allowing EPUBs where each chapter has exactly 1 NCX sub-anchor to split correctly.
- **NCX entity decoding**: `parse_ncx_chapters` now calls `html.unescape()` on labels, so `&#x2019;` → `'` in chapter titles rather than appearing as literal entities in the output EPUB.
- **`check_epub_integrity`**: New public function in `epub_clean.py` — compares original NCX count vs converted chapter count (warns if ratio > 1.5× or < 0.6×) and flags chapters under 50 words. Called automatically by `split_epub_by_toc` and `_rebuild_epub`, warnings emitted via logger.warning.
- **Duplicate placeholder guard**: `pattern_engine._build_segment_regex` raises `ValueError` on duplicate named placeholders (e.g. `{author} … {author}`); `_match_segment` catches this and returns an error string rather than propagating a `re.PatternError`.
- **pytest scope**: `pyproject.toml` sets `testpaths = ["tests"]` so scratch scripts in `scripts/` are never collected.
- **OPF/NCX insert via lambda replacement**: All `re.sub` calls in `file_apply.py` that splice a metadata-derived value into the document use a function (lambda) replacement, never an f-string replacement. A string replacement reinterprets `\1`/`\g<n>` sequences in untrusted metadata as backreferences → crash (`re.error`) or `</metadata>` injection. `_escape_xml` does not escape backslashes, so this guard lives at the substitution call, not the escaper.
- **Cover download scheme allowlist**: `_download_image` only honours `http`/`https` and caps the response at 50 MB. Cover URLs come from web metadata or hand-edits; `urllib` would otherwise open `file://` (local file disclosure) or `ftp://` (SSRF). Validation is at the single download chokepoint so all callers (preview, preload, embed) are covered.
- **Garbage file-meta rejection**: `ensure_chosen_defaults` skips a file-meta value containing no word chars (`[]`, `""`, `'`) so the filename-parsed value wins instead.

## Conventions
- Functions return `Optional[str]` — `None` on success, error message string on failure.
- No external tools for EPUB/MOBI/PDF reading or writing — pure Python only (PyQt5 + pypdf).
- EPUB XML escaping via `_xe()` in epub_build.py or `_escape_xml()` in file_apply.py.
- OPF parsing uses regex throughout (no XML parser) — consistent with rest of codebase.
- `epub_build.py` imports nothing from project; `epub_clean.py` imports only from `epub_build`. Keeps the dependency graph clean.
- Chapter body HTML is a fragment only (no `<html>/<head>/<body>` wrapper) — `_chapter_xhtml()` in epub_build adds the wrapper.
- Test files mirror module names: `test_file_apply.py`, `test_file_apply_extended.py`, etc.

## Do not
- Do not add XML parsers (lxml, ElementTree) to the EPUB pipeline — the regex approach is intentional and avoids namespace handling complexity.
- Do not call `split_epub_by_toc()` expecting it to be surgical/in-place anymore — it fully rebuilds the EPUB now.
- Do not patch `epub_build.py` output with post-processing — if the output is wrong, fix the generator.
- Do not write MOBI cover images directly — MOBI stores covers as PalmDB binary records; the conversion path (MOBI → EPUB) handles covers instead.
- Do not add GUI unit tests — the cost/value tradeoff is intentionally not worth it for this project.
- Do not use `re.DOTALL` carelessly on large EPUB bodies — use targeted patterns.
- Do not use an f-string/plain-string replacement in `re.sub` when the inserted value derives from metadata — use a lambda replacement (untrusted `\g<n>`/`\1` sequences would otherwise be reinterpreted). See `_update_opf_text`.
- Do not pass an unvalidated URL to `_download_image` / `urllib` — non-HTTP(S) schemes enable local file read and SSRF.

## Meta instruction
After each completed task:
1. Update "Current task" to reflect what's next
2. Log architectural decisions under "Key decisions made"
3. Update "Conventions" and "Do not" as patterns emerge
Do this automatically without being asked. Keep entries concise.

When starting a debugging or investigation task, read the relevant source files and any actual data files (EPUBs, test fixtures, etc.) before forming a hypothesis. Inspect first, theorise second — avoid explaining what might be wrong until you've seen what is actually there.
