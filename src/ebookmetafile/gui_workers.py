import logging
import re
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

from PyQt5 import QtCore

from .models import BookRecord
from .file_scan import scan_ebooks
from .metadata_read import read_metadata_for_files
from . import file_apply
from .gui_constants import _try_patterns, _cover_image_cache, embedded_cover_cache_key


class CoverPreloadWorker(QtCore.QThread):
    """Eagerly loads all cover images for a set of records so dimensions are
    always available in the table without the user needing to click first.

    Works through two types of task:
      - embedded: reads bytes directly from the EPUB ZIP (fast, local)
      - web URL: downloads via HTTP (runs after embedded tasks)

    Emits *loaded* for each image as it completes so the table can refresh
    incrementally rather than waiting for the whole batch.
    """
    loaded = QtCore.pyqtSignal(str, bytes, str)  # cache_key, data, mime

    def __init__(self, records: List[BookRecord], parent=None):
        super().__init__(parent)
        self.records = records

    def run(self) -> None:
        from .file_apply import _download_image

        embedded_tasks: List[Tuple[str, Path, str]] = []  # (cache_key, epub_path, inner_path)
        web_urls: List[str] = []  # cache_key == url for web covers

        for rec in self.records:
            # Embedded cover from file metadata
            url = (rec.metadata_file.get("cover") or "").strip()
            if url.startswith("embedded:"):
                key = embedded_cover_cache_key(rec.filepath, url)
                if key not in _cover_image_cache:
                    embedded_tasks.append((key, rec.filepath, url[len("embedded:"):]))

            # Web covers from all web sources
            for src_dict in (rec.metadata_google, rec.metadata_openlibrary, rec.metadata_isfdb, rec.metadata_amazon):
                url = (src_dict.get("cover") or "").strip()
                if url.startswith("http") and url not in _cover_image_cache:
                    web_urls.append(url)

        # Deduplicate web URLs while preserving order
        seen: set = set()
        web_urls = [u for u in web_urls if not (u in seen or seen.add(u))]  # type: ignore[func-returns-value]

        # Process embedded covers first (fast)
        for cache_key, epub_path, inner_path in embedded_tasks:
            if self.isInterruptionRequested():
                return
            try:
                with zipfile.ZipFile(epub_path, "r") as zf:
                    data = zf.read(inner_path)
                self.loaded.emit(cache_key, data, "image/jpeg")
            except Exception:
                pass

        # Then download web covers
        for url in web_urls:
            if self.isInterruptionRequested():
                return
            if url in _cover_image_cache:
                continue  # may have been loaded by a concurrent click
            try:
                data, mime = _download_image(url)
                self.loaded.emit(url, data, mime)
            except Exception:
                pass


class CoverLoadWorker(QtCore.QThread):
    """Downloads a cover image URL in the background."""
    finished = QtCore.pyqtSignal(str, bytes, str)  # url, image_bytes, mime_type
    failed = QtCore.pyqtSignal(str)                # url

    def __init__(self, url: str, parent=None):
        super().__init__(parent)
        self.url = url

    def run(self) -> None:
        try:
            from .file_apply import _download_image
            data, mime = _download_image(self.url)
            self.finished.emit(self.url, data, mime)
        except Exception:
            self.failed.emit(self.url)


class MetadataFetchWorker(QtCore.QThread):
    # (done, total, {source: (done, total)})
    progress = QtCore.pyqtSignal(int, int, dict)
    finished = QtCore.pyqtSignal(list)
    log      = QtCore.pyqtSignal(str)

    def __init__(
        self,
        records: List[BookRecord],
        fetch_google: bool,
        fetch_openlibrary: bool,
        fetch_isfdb: bool = False,
        fetch_amazon: bool = False,
        google_api_key: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self.records = records
        self.fetch_google = fetch_google
        self.fetch_openlibrary = fetch_openlibrary
        self.fetch_isfdb = fetch_isfdb
        self.fetch_amazon = fetch_amazon
        self.google_api_key = google_api_key

    def run(self) -> None:
        from . import metadata_fetch
        import concurrent.futures

        # Each source gets its own pool — no shared contention, no blocking between sources.
        # Amazon gets fewer workers to avoid triggering bot-detection.
        # ISFDB gets fewer to be polite to smaller services.
        _WORKERS = {
            "google":      5 if self.google_api_key else 1,
            "openlibrary": 16,
            "isfdb":       5,
            "amazon":      10,
        }

        _google_key = self.google_api_key
        _fetchers = {
            "google":      lambda t, a, i: metadata_fetch._fetch_google(t, a, i, api_key=_google_key),
            "openlibrary": metadata_fetch._fetch_openlibrary,
            "isfdb":       metadata_fetch._fetch_isfdb,
            "amazon":      metadata_fetch._fetch_amazon,
        }

        # Collect per-record info needed for both phases
        rec_info: Dict[int, tuple] = {}   # book_id -> (title, author, original_isbn)
        phase1_tasks: Dict[str, list] = {src: [] for src in _fetchers if src != "amazon"}
        for rec in self.records:
            title  = (rec.chosen_metadata.get("title")  or "").strip()
            author = (rec.chosen_metadata.get("author") or "").strip()
            isbn   = (rec.chosen_metadata.get("isbn")   or "").strip()
            rec_info[rec.id] = (title, author, isbn)
            for src, flag in [
                ("google",      self.fetch_google),
                ("openlibrary", self.fetch_openlibrary),
                ("isfdb",       self.fetch_isfdb),
            ]:
                if flag:
                    phase1_tasks[src].append((rec.id, title, author, isbn))

        source_totals: Dict[str, int] = {src: len(t) for src, t in phase1_tasks.items() if t}
        total = sum(source_totals.values())
        if not total and not self.fetch_amazon:
            self.finished.emit([])
            return

        done_count = 0
        source_done: Dict[str, int] = {}
        per_book: Dict[int, Dict[str, Any]] = {}

        _SRC_LABEL = {
            "google":      "Google Books",
            "openlibrary": "Open Library",
            "isfdb":       "ISFDB",
            "amazon":      "Amazon",
        }

        def _do_fetch(source: str, book_id: int, title: str, author: str, isbn: str):
            try:
                return book_id, source, _fetchers[source](title, author, isbn), None
            except Exception as e:
                return book_id, source, None, str(e)

        def _emit():
            self.progress.emit(done_count, total,
                               {s: (source_done.get(s, 0), source_totals[s])
                                for s in source_totals})

        # --- Phase 1: everything except Amazon ---
        pools: Dict[str, concurrent.futures.ThreadPoolExecutor] = {}
        try:
            pools = {
                src: concurrent.futures.ThreadPoolExecutor(max_workers=min(len(t), _WORKERS[src]))
                for src, t in phase1_tasks.items() if t
            }
            p1_futures: Dict[concurrent.futures.Future, None] = {}
            for src, pool in pools.items():
                for book_id, title, author, isbn in phase1_tasks[src]:
                    p1_futures[pool.submit(_do_fetch, src, book_id, title, author, isbn)] = None

            for fut in concurrent.futures.as_completed(p1_futures):
                if self.isInterruptionRequested():
                    for f in p1_futures:
                        f.cancel()
                    self.finished.emit([])
                    return
                try:
                    book_id, source, candidate, err = fut.result()
                except Exception as e:
                    self.log.emit(f"[Phase 1] Unexpected error — {e}")
                    continue
                lbl = _SRC_LABEL.get(source, source)
                if err:
                    title_str = rec_info.get(book_id, ("?", "", ""))[0]
                    self.log.emit(f'[{lbl}] Error — "{title_str}": {err}')
                elif candidate is None:
                    title_str, author_str, _ = rec_info.get(book_id, ("?", "?", ""))
                    self.log.emit(f'[{lbl}] No result — "{title_str}" by {author_str}')
                per_book.setdefault(book_id, {})[source] = candidate
                source_done[source] = source_done.get(source, 0) + 1
                done_count += 1
                _emit()
        finally:
            for pool in pools.values():
                pool.shutdown(wait=False)
            pools.clear()

        # --- Phase 2: Amazon, using ISBNs discovered in Phase 1 ---
        if self.fetch_amazon and not self.isInterruptionRequested():
            import re as _re
            amazon_tasks = []
            for book_id, (title, author, orig_isbn) in rec_info.items():
                isbn = orig_isbn
                if not isbn:
                    # Use the first ISBN returned by any Phase 1 source
                    for candidate in per_book.get(book_id, {}).values():
                        if candidate and getattr(candidate, "isbn", None):
                            isbn = _re.sub(r"\D", "", candidate.isbn)
                            if isbn:
                                break
                if isbn:
                    amazon_tasks.append((book_id, title, author, isbn))

            if amazon_tasks:
                source_totals["amazon"] = len(amazon_tasks)
                total += len(amazon_tasks)
                amz_pool = None
                try:
                    amz_pool = concurrent.futures.ThreadPoolExecutor(
                        max_workers=min(len(amazon_tasks), _WORKERS["amazon"])
                    )
                    amz_futures = {
                        amz_pool.submit(_do_fetch, "amazon", bid, t, a, i): None
                        for bid, t, a, i in amazon_tasks
                    }
                    for fut in concurrent.futures.as_completed(amz_futures):
                        if self.isInterruptionRequested():
                            for f in amz_futures:
                                f.cancel()
                            break
                        try:
                            book_id, source, candidate, err = fut.result()
                        except Exception as e:
                            self.log.emit(f"[Amazon] Unexpected error — {e}")
                            continue
                        if err:
                            title_str = rec_info.get(book_id, ("?", "", ""))[0]
                            self.log.emit(f'[Amazon] Error — "{title_str}": {err}')
                        elif candidate is None:
                            title_str, author_str, _ = rec_info.get(book_id, ("?", "?", ""))
                            self.log.emit(f'[Amazon] No result — "{title_str}" by {author_str}')
                        per_book.setdefault(book_id, {})[source] = candidate
                        source_done["amazon"] = source_done.get("amazon", 0) + 1
                        done_count += 1
                        _emit()
                finally:
                    if amz_pool is not None:
                        amz_pool.shutdown(wait=False)

        pb = per_book
        results = [
            metadata_fetch.FetchResult(
                book_id=rec.id,
                google_top=pb.get(rec.id, {}).get("google"),
                openlibrary_top=pb.get(rec.id, {}).get("openlibrary"),
                isfdb_top=pb.get(rec.id, {}).get("isfdb"),
                amazon_top=pb.get(rec.id, {}).get("amazon"),
            )
            for rec in self.records
        ]
        self.finished.emit(results)


class ISBNLookupWorker(QtCore.QThread):
    """For every source row that carries an ISBN, verify it via OL / Google Books.

    Emits finished with list of (book_id, source_key, result_dict).
    Deduplicates network calls: identical ISBNs within a book are looked up once.
    """

    progress = QtCore.pyqtSignal(int, int)   # done, total unique ISBNs
    finished = QtCore.pyqtSignal(list)       # list of (book_id, source_key, dict)
    log      = QtCore.pyqtSignal(str)

    def __init__(self, records: List[BookRecord], parent=None):
        super().__init__(parent)
        self.records = records

    def run(self) -> None:
        from . import metadata_fetch
        from .models import _ISBN_LOOKUP_MAP
        import concurrent.futures

        # Collect (book_id, source_key, isbn) for every row that has an ISBN.
        # Then deduplicate network calls by isbn (within a book, same ISBN → one request).
        isbn_to_sources: Dict[Tuple[int, str], List[Tuple[int, str]]] = {}  # (book_id, isbn) → [(book_id, source_key)]
        for rec in self.records:
            for source_key, meta_attr in _ISBN_LOOKUP_MAP.values():
                meta = getattr(rec, meta_attr, {}) or {}
                isbn = re.sub(r"\D", "", meta.get("isbn") or "")
                if not isbn:
                    continue
                key: Tuple[int, str] = (rec.id, isbn)
                isbn_to_sources.setdefault(key, []).append((rec.id, source_key))

        # Build unique lookup tasks: one per (book_id, isbn)
        unique_tasks = list(isbn_to_sources.keys())   # [(book_id, isbn), ...]
        total = len(unique_tasks)
        if not total:
            self.finished.emit([])
            return

        # isbn_cache[(book_id, isbn)] → result dict or None
        isbn_cache: Dict[Tuple[int, str], Optional[dict]] = {}
        done = 0

        def _do(book_id: int, isbn: str):
            try:
                return book_id, isbn, metadata_fetch._lookup_isbn(isbn), None
            except Exception as e:
                return book_id, isbn, None, str(e)

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(_do, bid, isbn): None for bid, isbn in unique_tasks}
            for fut in concurrent.futures.as_completed(futures):
                if self.isInterruptionRequested():
                    break
                try:
                    book_id, isbn, result, err = fut.result()
                except Exception as e:
                    self.log.emit(f"[ISBN Lookup] Unexpected error — {e}")
                    continue
                if err:
                    self.log.emit(f"[ISBN Lookup] Error for ISBN {isbn}: {err}")
                elif result is None:
                    self.log.emit(f"[ISBN Lookup] No result for ISBN {isbn}")
                isbn_cache[(book_id, isbn)] = result or {}
                done += 1
                self.progress.emit(done, total)

        # Expand cache back to per-(book_id, source_key) results
        results = []
        for (book_id, isbn), sources in isbn_to_sources.items():
            cached = isbn_cache.get((book_id, isbn)) or {}
            for _, source_key in sources:
                results.append((book_id, source_key, cached))

        self.finished.emit(results)


class WriteWorker(QtCore.QThread):
    progress = QtCore.pyqtSignal(int, int, str)  # done, total, current_filename
    finished = QtCore.pyqtSignal(list)            # list of (name, ok, msg)

    def __init__(self, records, operation: str, on_clash: str,
                 convert_mobi: bool = False, split_epub: bool = False, parent=None):
        super().__init__(parent)
        self.records = records
        self.operation = operation
        self.on_clash = on_clash
        self.convert_mobi = convert_mobi
        self.split_epub = split_epub

    def run(self) -> None:
        from .file_apply import _download_image, write_metadata, split_epub_by_toc
        from . import file_convert
        results = []
        total = len(self.records)
        for i, rec in enumerate(self.records):
            if self.isInterruptionRequested():
                break
            self.progress.emit(i, total, rec.filepath.name)

            # Filenames with non-ASCII or Windows-forbidden characters (e.g. a
            # curly apostrophe stored as '?' in exiftool's JSON, or a literal
            # '?' from a Linux filesystem) need the stored path corrected before
            # any file operation.  Use os.scandir to resolve the true path, then
            # rename if the real name still has genuinely forbidden characters.
            if file_convert.has_invalid_filename_chars(rec.filepath):
                real = file_convert._resolve_actual_path(rec.filepath)
                if real is not None and real != rec.filepath:
                    logger.info(
                        "Corrected path encoding: '%s' → '%s'",
                        rec.filepath.name, real.name,
                    )
                    rec.filepath = real
                    rec.format_state = rec.format_state.removesuffix(" ⚠")
                # After resolving, check if the real name still has forbidden chars
                if file_convert.has_invalid_filename_chars(rec.filepath):
                    new_path = file_convert.fix_invalid_filename(rec.filepath)
                    if new_path:
                        logger.info(
                            "Auto-renamed '%s' → '%s'",
                            rec.filepath.name, new_path.name,
                        )
                        rec.filepath = new_path
                        rec.format_state = rec.format_state.removesuffix(" ⚠")
                    else:
                        logger.warning(
                            "Cannot fix filename '%s': manual rename required.",
                            rec.filepath.name,
                        )

            # Ensure the chosen cover is cached before writing.  The preload
            # worker runs concurrently and may not have finished yet; if the
            # URL is already cached this is a no-op dict lookup.
            cover_url = (rec.chosen_metadata.get("cover") or "").strip()
            if cover_url.startswith("http") and cover_url not in _cover_image_cache:
                try:
                    data, mime = _download_image(cover_url)
                    _cover_image_cache[cover_url] = (data, mime)
                except Exception as exc:
                    logger.warning("WriteWorker: cover download failed for %s: %s", rec.filepath.name, exc)

            # MOBI → EPUB conversion: create an EPUB alongside the original,
            # then write the full metadata (including cover) into it.
            if self.convert_mobi and file_convert.is_mobi(rec.filepath):
                epub_dst = rec.filepath.with_suffix(".epub")
                conv_err = file_convert.convert_mobi_to_epub(rec.filepath, epub_dst)
                if conv_err:
                    logger.warning("Conversion failed for %s: %s", rec.filepath.name, conv_err)
                else:
                    meta_err = write_metadata(epub_dst, rec.chosen_metadata, _cover_image_cache)
                    if meta_err:
                        logger.warning("Metadata write to converted EPUB failed for %s: %s",
                                       rec.filepath.name, meta_err)
                    else:
                        logger.info("Converted and wrote metadata: %s → %s",
                                    rec.filepath.name, epub_dst.name)

            ok, msg = file_apply.apply_record(rec, self.operation, self.on_clash, _cover_image_cache)
            if ok and self.split_epub and rec.filepath.suffix.lower() == ".epub":
                split_err = split_epub_by_toc(rec.filepath)
                if split_err:
                    logger.warning("EPUB split failed for %s: %s", rec.filepath.name, split_err)
                    msg = f"{msg} (split failed: {split_err})"
                else:
                    logger.info("Split chapters: %s", rec.filepath.name)
            results.append((rec.filepath.name, ok, msg))
        self.progress.emit(total, total, "")
        self.finished.emit(results)


class ScanWorker(QtCore.QThread):
    # (label, done, total) — total=0 means indeterminate
    progress = QtCore.pyqtSignal(str, int, int)
    finished = QtCore.pyqtSignal(list)
    error = QtCore.pyqtSignal(str)

    def __init__(
        self,
        root: Path,
        global_patterns: List[str],
        global_output_pattern: str,
        parent=None,
    ):
        super().__init__(parent)
        self.root = root
        self.global_patterns = global_patterns
        self.global_output_pattern = global_output_pattern

    def run(self) -> None:
        try:
            self.progress.emit("Scanning for ebooks…", 0, 0)
            files = scan_ebooks(self.root)
            n = len(files)

            def _read_progress(done: int, total: int) -> None:
                pct = 100 * done // total if total else 0
                self.progress.emit(f"Reading metadata… {done}/{total} ({pct}%)", done, total)

            records = read_metadata_for_files(files, progress_callback=_read_progress)

            for idx, rec in enumerate(records, 1):
                if self.isInterruptionRequested():
                    return
                pct = 100 * idx // n if n else 0
                self.progress.emit(f"Initialising… {idx}/{n} ({pct}%)", idx, n)
                rec.output_pattern = self.global_output_pattern
                _try_patterns(self.global_patterns, rec)

            self.finished.emit(records)
        except Exception as exc:
            self.error.emit(str(exc))
