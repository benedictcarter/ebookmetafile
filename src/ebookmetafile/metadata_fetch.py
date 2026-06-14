"""Fetch book metadata from Google Books, Open Library, ISFDB, and Amazon."""

import html as _html
import json
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class WebCandidate:
    source: str
    author: Optional[str]
    title: Optional[str]
    series: Optional[str]
    series_index: Optional[str]
    subject: Optional[str]
    tags: Optional[str]
    publisher: Optional[str] = None
    pub_date: Optional[str] = None
    description: Optional[str] = None
    isbn: Optional[str] = None
    language: Optional[str] = None
    cover: Optional[str] = None
    raw: Dict = field(default_factory=dict)


@dataclass
class FetchResult:
    book_id: int
    google_top: Optional[WebCandidate]
    openlibrary_top: Optional[WebCandidate]
    isfdb_top: Optional[WebCandidate] = None
    amazon_top: Optional[WebCandidate] = None
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Google Books
# ---------------------------------------------------------------------------

def _google_query(q: str, timeout: int, api_key: str = "") -> Optional[dict]:
    p: dict = {"q": q, "maxResults": "5"}
    if api_key:
        p["key"] = api_key
    url = "https://www.googleapis.com/books/v1/volumes?" + urllib.parse.urlencode(p)
    for attempt in range(2):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 403) and attempt == 0:
                time.sleep(2)   # rate-limited — back off once then retry
                continue
            return None
        except Exception:
            return None
    return None


def _fetch_google(title: str, author: str, isbn: str = "", timeout: int = 8, api_key: str = "") -> Optional[WebCandidate]:
    # ISBN search is far more precise — try it first
    data = None
    if isbn:
        clean = re.sub(r"\D", "", isbn)
        data = _google_query(f"ISBN:{clean}", timeout, api_key)
        if not (data or {}).get("items"):
            data = None

    if data is None:
        data = _google_query(f"intitle:{title}+inauthor:{author}", timeout, api_key)

    items = (data or {}).get("items") or []
    if not items:
        return None

    info = items[0].get("volumeInfo", {})
    authors = info.get("authors") or []
    categories = info.get("categories") or []

    cover_url: Optional[str] = None
    image_links = info.get("imageLinks") or {}
    raw_thumb = image_links.get("thumbnail") or image_links.get("smallThumbnail")
    if raw_thumb:
        cover_url = raw_thumb.replace("zoom=1", "zoom=3").replace("http://", "https://")

    isbn_val: Optional[str] = None
    for ident in info.get("industryIdentifiers") or []:
        if ident.get("type") == "ISBN_13":
            isbn_val = ident.get("identifier")
            break
    if not isbn_val:
        for ident in info.get("industryIdentifiers") or []:
            if ident.get("type") == "ISBN_10":
                isbn_val = ident.get("identifier")
                break

    return WebCandidate(
        source="google",
        author=", ".join(authors) if authors else None,
        title=info.get("title"),
        series=None,
        series_index=None,
        subject=categories[0] if categories else None,
        tags=", ".join(categories) if categories else None,
        publisher=info.get("publisher"),
        pub_date=info.get("publishedDate"),
        description=info.get("description"),
        isbn=isbn_val,
        language=info.get("language"),
        cover=cover_url,
        raw=info,
    )


# ---------------------------------------------------------------------------
# Open Library
# ---------------------------------------------------------------------------

_OL_FIELDS = "title,author_name,publisher,first_publish_year,isbn,subject,cover_i,edition_key"


def _ol_search(params: dict, timeout: int) -> Optional[dict]:
    """Single Open Library search.json call; returns first doc or None."""
    params.setdefault("limit", "1")
    params["fields"] = _OL_FIELDS
    try:
        url = "https://openlibrary.org/search.json?" + urllib.parse.urlencode(params)
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            docs = json.loads(resp.read()).get("docs") or []
            return docs[0] if docs else None
    except Exception:
        return None


def _fetch_openlibrary(title: str, author: str, isbn: str = "", timeout: int = 8) -> Optional[WebCandidate]:
    clean_isbn = re.sub(r"\D", "", isbn)

    # ISBN search is unambiguous and returns richer data — only try it when we have one
    doc = _ol_search({"isbn": clean_isbn}, timeout) if clean_isbn else None

    # Title+author: prefer English editions, fall back to any language
    if doc is None:
        doc = _ol_search({"title": title, "author": author, "lang": "eng"}, timeout)
    if doc is None:
        doc = _ol_search({"title": title, "author": author}, timeout)

    if doc is None:
        # If we only have an ISBN and metadata failed, at least return a cover
        if clean_isbn:
            cover_url = f"https://covers.openlibrary.org/b/isbn/{clean_isbn}-L.jpg"
            return WebCandidate(
                source="openlibrary",
                author=None, title=None, series=None, series_index=None,
                subject=None, tags=None, isbn=clean_isbn, cover=cover_url, raw={},
            )
        return None

    author_names = doc.get("author_name") or []
    subjects = doc.get("subject") or []
    cover_id = doc.get("cover_i")
    # Prefer direct ISBN cover URL (higher res, faster) over cover_id
    cover_url = (
        f"https://covers.openlibrary.org/b/isbn/{clean_isbn}-L.jpg"
        if clean_isbn
        else (f"https://covers.openlibrary.org/b/id/{cover_id}-L.jpg" if cover_id else None)
    )

    publishers = doc.get("publisher") or []
    isbns = doc.get("isbn") or []
    isbn13 = next((i for i in isbns if len(i) == 13), None)
    isbn_val = isbn13 or (isbns[0] if isbns else clean_isbn or None)

    # If the Solr index didn't return ISBNs, fetch editions directly —
    # edition JSON almost always has isbn_13/isbn_10. Prefer English editions.
    if not isbn_val:
        edition_keys = doc.get("edition_key") or []
        eng_isbn: Optional[str] = None
        any_isbn: Optional[str] = None
        for ek in edition_keys[:8]:
            olid = ek.split("/")[-1]
            try:
                with urllib.request.urlopen(
                    f"https://openlibrary.org/books/{olid}.json", timeout=timeout
                ) as resp:
                    edition = json.loads(resp.read())
                isbn13s = edition.get("isbn_13") or []
                isbn10s = edition.get("isbn_10") or []
                candidate_isbn = (isbn13s[0] if isbn13s else None) or (isbn10s[0] if isbn10s else None)
                if not candidate_isbn:
                    continue
                langs = [l.get("key", "") for l in (edition.get("languages") or [])]
                is_eng = any("eng" in l for l in langs) or not langs
                if is_eng and not eng_isbn:
                    eng_isbn = candidate_isbn
                    break
                if any_isbn is None:
                    any_isbn = candidate_isbn
            except Exception:
                continue
        isbn_val = eng_isbn or any_isbn

    pub_year = doc.get("first_publish_year")

    return WebCandidate(
        source="openlibrary",
        author=", ".join(author_names) if author_names else None,
        title=doc.get("title"),
        series=None,
        series_index=None,
        subject=subjects[0] if subjects else None,
        tags=", ".join(subjects[:5]) if subjects else None,
        publisher=publishers[0] if publishers else None,
        pub_date=str(pub_year) if pub_year else None,
        isbn=isbn_val,
        cover=cover_url,
        raw=doc,
    )


# ---------------------------------------------------------------------------
# ISFDB  (Internet Speculative Fiction Database)
# ---------------------------------------------------------------------------

def _fetch_isfdb(title: str, author: str, isbn: str = "", timeout: int = 10) -> Optional[WebCandidate]:
    """Fetch from ISFDB.  ISBN via REST API; title search via HTML scraping of se.cgi."""
    clean_isbn = re.sub(r"\D", "", isbn)

    if clean_isbn:
        url = f"https://www.isfdb.org/cgi-bin/rest/getpub.cgi?{clean_isbn}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "EbookMetafile/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                xml = resp.read().decode("utf-8", errors="replace")
            if "<Publication>" in xml:
                pub_m = re.search(r"<Publication>(.*?)</Publication>", xml, re.DOTALL | re.IGNORECASE)
                if pub_m:
                    pub = pub_m.group(1)

                    def _xv(tag: str) -> Optional[str]:
                        m = re.search(rf"<{tag}>([^<]*)</{tag}>", pub, re.IGNORECASE)
                        return _html.unescape(m.group(1).strip()) if m else None

                    author_names: List[str] = re.findall(r"<Name>([^<]+)</Name>", pub, re.IGNORECASE)
                    year = _xv("Year")
                    if year:
                        year = year[:4]
                    return WebCandidate(
                        source="isfdb",
                        author=", ".join(_html.unescape(a.strip()) for a in author_names) if author_names else None,
                        title=_xv("Title"),
                        series=_xv("Series"),
                        series_index=_xv("SeriesNum"),
                        subject=None,
                        tags=None,
                        publisher=_xv("Publisher"),
                        pub_date=year,
                        isbn=_xv("Isbn") or clean_isbn,
                        cover=_xv("Image") or _xv("Thumb"),
                        raw={},
                    )
        except Exception:
            pass

    # Title search — scrape se.cgi (the getpub_by_title REST endpoint no longer exists)
    search_url = (
        "https://www.isfdb.org/cgi-bin/se.cgi?"
        + urllib.parse.urlencode({"arg": title, "type": "Fiction Titles"})
    )
    try:
        req = urllib.request.Request(search_url, headers={"User-Agent": "EbookMetafile/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html = resp.read().decode("iso-8859-1", errors="replace")
    except Exception:
        return None

    # Parse result table: Date | Type | Language | Title | Authors | … | Tags
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.DOTALL):
        if "NOVEL" not in row:
            continue
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
        clean = [re.sub(r"<[^>]+>", "", c).strip() for c in cells]
        if len(clean) < 5 or clean[1] != "NOVEL":
            continue
        date_str, row_title, row_author = clean[0], clean[3], clean[4]
        year = date_str[:4] if date_str and date_str[:4].isdigit() else None
        tags_raw = clean[7] if len(clean) > 7 else ""
        tags = ", ".join(re.findall(r"([^,(]+?)\s*\(\d+\)", tags_raw)) if tags_raw else None
        return WebCandidate(
            source="isfdb",
            author=_html.unescape(row_author) if row_author else None,
            title=_html.unescape(row_title) if row_title else None,
            series=None,
            series_index=None,
            subject=None,
            tags=tags or None,
            publisher=None,
            pub_date=year,
            isbn=clean_isbn if len(clean_isbn) in (10, 13) else None,
            cover=None,
            raw={},
        )
    return None


# ---------------------------------------------------------------------------
# Amazon
# ---------------------------------------------------------------------------

_AMAZON_UAS: List[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
]


def _amazon_headers() -> dict:
    return {
        "User-Agent": random.choice(_AMAZON_UAS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }


def _isbn13_to_10(isbn13: str) -> Optional[str]:
    """Convert ISBN-13 with 978 prefix to ISBN-10."""
    digits = re.sub(r"\D", "", isbn13)
    if len(digits) != 13 or not digits.startswith("978"):
        return None
    d = digits[3:12]
    check = (11 - sum((10 - i) * int(d[i]) for i in range(9)) % 11) % 11
    return d + ("X" if check == 10 else str(check))


def _amazon_blocked(html: str) -> bool:
    return (
        "/errors/validateCaptcha" in html
        or "To discuss automated access to Amazon data" in html
        or "robot check" in html.lower()
    )


def _amazon_get(url: str, timeout: int) -> Optional[str]:
    try:
        req = urllib.request.Request(url, headers=_amazon_headers())
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        return None if _amazon_blocked(html) else html
    except Exception:
        return None


def _find_asin_via_ddg(title: str, author: str, isbn: str = "", timeout: int = 10) -> Optional[str]:
    """Search DuckDuckGo for an Amazon product URL; extract ASIN without hitting Amazon search."""
    query = f'site:amazon.com/dp "{isbn}"' if isbn else f'site:amazon.com/dp "{title}" "{author}"'
    params = urllib.parse.urlencode({"q": query, "kl": "us-en"})
    try:
        req = urllib.request.Request(
            f"https://duckduckgo.com/html/?{params}",
            headers={"User-Agent": "Mozilla/5.0 (compatible; EbookMetafile/1.0)"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None
    m = re.search(r"amazon\.com/(?:dp|gp/product)/([A-Z0-9]{10})", html)
    return m.group(1) if m else None


def _parse_amazon_product(html: str, isbn_fallback: str = "") -> Optional[WebCandidate]:
    """Extract metadata from an Amazon product page. Tries JSON-LD first."""

    # --- JSON-LD (most reliable) ---
    for ld_m in re.finditer(
        r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
        html, re.DOTALL | re.IGNORECASE,
    ):
        try:
            data = json.loads(ld_m.group(1))
        except Exception:
            continue
        if isinstance(data, list):
            data = next((d for d in data if d.get("@type") in ("Book", "Product")), data[0] if data else {})
        if data.get("@type") not in ("Book", "Product"):
            continue

        authors_raw = data.get("author") or []
        if isinstance(authors_raw, dict):
            authors_raw = [authors_raw]
        author_names = [a.get("name", "") for a in authors_raw if isinstance(a, dict)]

        isbn_val = data.get("isbn") or isbn_fallback or None

        cover_url = None
        img = data.get("image")
        if isinstance(img, list) and img:
            cover_url = img[0]
        elif isinstance(img, str):
            cover_url = img

        pub = data.get("publisher")
        if isinstance(pub, dict):
            pub = pub.get("name")

        return WebCandidate(
            source="amazon",
            author=", ".join(a for a in author_names if a) or None,
            title=data.get("name"),
            series=None,
            series_index=None,
            subject=None,
            tags=None,
            publisher=pub,
            pub_date=data.get("datePublished") or data.get("dateCreated"),
            description=data.get("description"),
            isbn=isbn_val,
            cover=cover_url,
            raw={"source": "ld+json"},
        )

    # --- HTML fallback ---
    def _rx(pattern: str) -> Optional[str]:
        m = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
        return _html.unescape(m.group(1)).strip() if m else None

    title_val = _rx(r'id="productTitle"[^>]*>\s*(.+?)\s*</')
    if not title_val:
        return None

    author_hits = re.findall(
        r'class="[^"]*contributorNameID[^"]*"[^>]*>\s*([^<]+?)\s*</',
        html, re.IGNORECASE,
    )
    author_val = ", ".join(dict.fromkeys(author_hits)) if author_hits else None

    isbn_val = (
        _rx(r'ISBN-13[^:]*:.*?<span[^>]*>\s*([\d\-]{10,17})\s*</')
        or _rx(r'ISBN-10[^:]*:.*?<span[^>]*>\s*([\d\-X]{9,13})\s*</')
        or isbn_fallback or None
    )

    cover_url = None
    dyn_m = re.search(r'data-a-dynamic-image="([^"]+)"', html)
    if dyn_m:
        try:
            img_map = json.loads(_html.unescape(dyn_m.group(1)))
            cover_url = max(img_map, key=lambda u: img_map[u][0])
        except Exception:
            pass

    return WebCandidate(
        source="amazon",
        author=author_val,
        title=title_val,
        series=None, series_index=None,
        subject=None, tags=None,
        publisher=_rx(r'Publisher[^:]*:.*?<span[^>]*>\s*([^<(]+?)(?:\s*\(|\s*</)'),
        pub_date=_rx(r'Publication date[^:]*:.*?<span[^>]*>\s*([^<]+?)\s*</'),
        isbn=isbn_val,
        cover=cover_url,
        raw={"source": "html"},
    )


def _fetch_amazon(title: str, author: str, isbn: str = "", timeout: int = 10) -> Optional[WebCandidate]:
    # Strict ISBN-only: one request, fast success or fast failure.
    # Without a persistent cookie session, multi-step fallbacks just burn
    # more requests and accelerate bot-blocking without improving hit rate.
    clean_isbn = re.sub(r"\D", "", isbn)
    if not clean_isbn:
        return None

    isbn10 = _isbn13_to_10(clean_isbn) or (clean_isbn if len(clean_isbn) == 10 else None)
    if not isbn10:
        return None

    html = _amazon_get(f"https://www.amazon.com/dp/{isbn10}", timeout)
    if html and ("productTitle" in html or "application/ld+json" in html):
        return _parse_amazon_product(html, clean_isbn)
    return None


# ---------------------------------------------------------------------------
# ISBN verification lookup
# ---------------------------------------------------------------------------

def _lookup_isbn(isbn: str, timeout: int = 8) -> Optional[dict]:
    """Given a known ISBN, return {'title', 'author', 'pub_date'} or None.

    Tries Open Library first (no key required), then Google Books as fallback.
    Only sends ISBN-exact queries — never fuzzy title/author searches.
    """
    clean = re.sub(r"\D", "", isbn)
    if not clean:
        return None

    # Open Library ISBN search
    doc = _ol_search({"isbn": clean}, timeout)
    if doc:
        authors = doc.get("author_name") or []
        year = doc.get("first_publish_year")
        return {
            "title":    doc.get("title"),
            "author":   ", ".join(authors) if authors else None,
            "pub_date": str(year) if year else None,
        }

    # Google Books ISBN fallback
    data = _google_query(f"ISBN:{clean}", timeout)
    items = (data or {}).get("items") or []
    if items:
        info = items[0].get("volumeInfo") or {}
        authors = info.get("authors") or []
        pub = info.get("publishedDate") or ""
        return {
            "title":    info.get("title"),
            "author":   ", ".join(authors) if authors else None,
            "pub_date": pub[:4] if pub else None,
        }

    return None


# ---------------------------------------------------------------------------
# Combined
# ---------------------------------------------------------------------------

def fetch_for_record(
    book_id: int,
    title: str,
    author: str,
    fetch_google: bool = True,
    fetch_openlibrary: bool = True,
    fetch_isfdb: bool = False,
    fetch_amazon: bool = False,
    isbn: str = "",
) -> FetchResult:
    google_result = ol_result = isfdb_result = amz_result = None
    error = None
    try:
        if fetch_google:
            google_result = _fetch_google(title, author)
        if fetch_openlibrary:
            ol_result = _fetch_openlibrary(title, author)
        if fetch_isfdb:
            isfdb_result = _fetch_isfdb(title, author, isbn)
        if fetch_amazon:
            amz_result = _fetch_amazon(title, author, isbn)
    except Exception as e:
        error = str(e)
    return FetchResult(
        book_id=book_id,
        google_top=google_result,
        openlibrary_top=ol_result,
        isfdb_top=isfdb_result,
        amazon_top=amz_result,
        error=error,
    )
