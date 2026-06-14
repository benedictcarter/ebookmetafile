_HELP_HTML = """
<style>
  body  { font-family: Segoe UI, Arial, sans-serif; font-size: 10pt; margin: 12px; }
  h1    { font-size: 14pt; margin-bottom: 4px; }
  h2    { font-size: 11pt; margin-top: 18px; margin-bottom: 4px; border-bottom: 1px solid #ccc; }
  h3    { font-size: 10pt; margin-top: 12px; margin-bottom: 3px; }
  code  { font-family: Consolas, monospace; background: #f0f0f0; padding: 1px 4px; border-radius: 2px; }
  table { border-collapse: collapse; margin: 6px 0; }
  td, th { border: 1px solid #ccc; padding: 4px 10px; }
  th    { background: #e8e8e8; }
  li    { margin-bottom: 3px; }
  .note { background: #fffbe6; border-left: 3px solid #e0c000; padding: 4px 8px; margin: 6px 0; }
</style>

<h1>Ebook Metadata &amp; Filepath Manager — Documentation</h1>

<h2>Overview</h2>
<p>This app scans a folder of ebook files, reads metadata from file contents and
filenames, lets you enrich it from multiple web sources, then renames/moves files
and writes the chosen metadata back. It can also convert MOBI and EPUB 2 files to
clean <b>EPUB 3.3</b> format.</p>

<h2>Scanning a Library</h2>
<ul>
  <li>Click <b>Browse…</b> to pick a root folder, then <b>Scan</b> (or <b>Ctrl+R</b>).</li>
  <li>Recursively finds <code>.epub</code>, <code>.mobi</code>, <code>.azw3</code>, <code>.azw</code>, <code>.prc</code>, and <code>.pdf</code> files.</li>
  <li>File metadata is read directly from the file (pure Python — no external tools required).</li>
  <li>Folder, patterns, window geometry, column widths, and hidden columns are <b>saved between sessions</b>.</li>
</ul>

<h2>Table Structure</h2>
<p>Each book occupies <b>8 rows</b>, separated by a thick line:</p>
<table>
  <tr><th>Label</th><th>Source</th><th>Description</th></tr>
  <tr><td><b>C</b> (Chosen)</td><td>—</td><td>Values that will be written. <b>Bold.</b> Directly editable.</td></tr>
  <tr><td>F (Filename)</td><td>Input pattern</td><td>Metadata parsed from the existing filename.</td></tr>
  <tr><td>M (File meta)</td><td>File contents</td><td>Metadata read from inside the file (ExifTool).</td></tr>
  <tr><td>G (Google)</td><td>Google Books</td><td>Best match from Google Books API.</td></tr>
  <tr><td>L (Open Library)</td><td>Open Library</td><td>Best match from Open Library.</td></tr>
  <tr><td>S (ISFDB)</td><td>ISFDB</td><td>Internet Speculative Fiction Database — strong for genre fiction.</td></tr>
  <tr><td>A (Amazon)</td><td>Amazon</td><td>Amazon product page — fetched by ISBN only; may be blocked.</td></tr>
</table>
<p>Use the <b>Source</b> column filter to collapse the table to a single source row per book
(e.g. type <code>Chosen</code> to see only the row that will be written, or <code>Google</code>
to review what Google returned). This is a <em>row-level</em> filter, not a book-level filter.</p>

<h2>Format Column</h2>
<p>The <b>Format</b> column (shown on the Chosen row) indicates each file's current format:</p>
<table>
  <tr><th>Value</th><th>Colour</th><th>Meaning</th></tr>
  <tr><td><code>EPUB 3.3 ✓</code></td><td style="background:#c8f0c8">Green</td><td>Already ideal — metadata/rename only.</td></tr>
  <tr><td><code>EPUB 3</code></td><td style="background:#d0e8ff">Blue</td><td>EPUB 3 but not yet ideal — will be rebuilt if rebuild is checked.</td></tr>
  <tr><td><code>EPUB 2</code></td><td style="background:#fff0a0">Yellow</td><td>Older format — upgraded to EPUB 3.3 if rebuild is checked.</td></tr>
  <tr><td><code>MOBI</code> / <code>AZW3</code></td><td style="background:#ffd8a8">Orange</td><td>Can be converted to EPUB 3.3 when written.</td></tr>
  <tr><td><code>PDF</code></td><td style="background:#e8e8e8">Grey</td><td>Metadata updates only; no format conversion.</td></tr>
  <tr><td><i>any</i> <code>⚠</code></td><td style="background:#ffb0b0">Red</td><td>Filename contains a character Windows cannot open. Auto-fixed on write.</td></tr>
</table>

<h2>Colour Scheme (metadata cells)</h2>
<table>
  <tr><th>Colour</th><th>Where</th><th>Meaning</th></tr>
  <tr><td style="border: 2px solid black; padding: 3px 9px;">Black border</td><td>Any editable cell</td><td>Cell can be <b>directly edited</b> — double-click to type.</td></tr>
  <tr><td style="background:#99bbff">Blue</td><td>Source row cell</td><td>Value <b>matches</b> the Chosen value.</td></tr>
  <tr><td style="background:#ff9999">Red</td><td>Source row cell</td><td>Value <b>differs</b> from Chosen.</td></tr>
  <tr><td style="background:#ff9999">Red</td><td>Pattern cell (F row)</td><td>Input pattern <b>failed to match</b> — hover for error.</td></tr>
  <tr><td style="background:#ff9999">Red</td><td>Output Pattern (C row)</td><td>Output pattern has an <b>unknown placeholder</b> — hover for error.</td></tr>
  <tr><td style="background:#99ee99">Green</td><td>Pattern cell (F row)</td><td>Input pattern <b>matched successfully</b>.</td></tr>
  <tr><td style="background:#99ee99">Green</td><td>Output Pattern (C row)</td><td>Pattern is <b>valid</b> and produced a new filepath.</td></tr>
</table>

<h2>Fetching Web Metadata</h2>
<ol>
  <li>Optionally select books to limit the fetch (otherwise all books are fetched).</li>
  <li>Click <b>Fetch web metadata…</b> (or <b>Ctrl+Shift+F</b>).</li>
  <li>Choose which sources to query. Sources checked by default: Google Books, Open Library, ISFDB, Amazon (may be blocked by bot detection).</li>
  <li>All sources run in parallel. Progress shows per-source counts and percentages.</li>
</ol>

<h3>Two-phase fetch</h3>
<p>Amazon is fetched in a <b>second phase</b> after all other sources complete. This means
even if your ebook files have no embedded ISBN, an ISBN discovered by Google Books or
Open Library in Phase 1 is automatically used to look up Amazon in Phase 2.</p>

<h3>Auto-population of blank fields</h3>
<p>After fetching, any <b>Chosen field that is currently blank</b> is automatically filled
from the first source that has a value, in priority order:
<b>Google → Open Library → ISFDB → Amazon</b>.<br>
Fields that already have a value are <b>never overwritten</b> — manual edits are always preserved.
The output filepath is recomputed immediately if any field changes.</p>
<p>You can still click any source-row cell to manually override the Chosen value, or
double-click a Chosen cell to type a value directly.</p>

<h2>Choosing Metadata Manually</h2>
<p><b>Click any cell</b> in a source row (F, M, G, L, S, A) to copy that field into the
Chosen row. The clicked cell turns blue; disagreeing source rows turn red.</p>
<p><b>Double-click</b> any editable Chosen cell to type a value directly.</p>

<h2>Right-Click Menu</h2>
<p>Right-click any cell in the table for quick actions:</p>
<ul>
  <li><b>Open book</b> — open the file in Windows' default application.</li>
  <li><b>Reveal in Explorer</b> — open the containing folder with the file selected.</li>
  <li><b>Filter to selected books</b> — restrict the table to the selected books (book-level filter).</li>
  <li><b>Remove selected from view</b> — hide the selected books from the current view.</li>
  <li><b>Clear book filter</b> — restore all books hidden by the book filter.</li>
  <li><b>Copy file path</b> — copy the book's full file path to the clipboard.</li>
  <li><b>Copy "…"</b> — copy the clicked cell's text to the clipboard.</li>
  <li><b>Set global filter to "…"</b> — populate the global search bar with the cell value.</li>
  <li><b>Clear global filter</b> — clear the global search bar.</li>
  <li><b>Set column filter to "…"</b> — populate this column's filter with the cell value.</li>
  <li><b>Clear column filter</b> — clear this column's filter.</li>
  <li><b>Fetch / Write</b> — fetch or write the selected books (or all if nothing selected).</li>
  <li><b>Reset chosen metadata</b> — clear chosen values for the selected books (or all books) and re-apply auto-population defaults. Useful after a fetch if you want to re-choose from scratch.</li>
</ul>

<h2>Filtering</h2>
<h3>Global search bar</h3>
<p>The full-width bar at the top searches <b>all columns across all source rows</b> for any
match (union). Books where any cell in any row contains the text are shown.
The <b>✕ Filters</b> button to the right of the search bar clears both the search text
and all column filters in one click.</p>

<h3>Column filter bar</h3>
<p>The row of small inputs above the column headers. Behaviour depends on the column:</p>
<ul>
  <li><b>Source column</b> — <em>row-level</em> filter: collapses each book to only the
      matching sub-rows. Type <code>Chosen</code> to show only the row that will be written;
      type <code>Amazon</code> to review only Amazon results, etc.</li>
  <li><b>All other columns</b> — <em>book-level</em> filter: hides entire books where no
      source row has a matching value in that column. Multiple column filters combine with AND.</li>
</ul>
<p>Both filters can be active simultaneously. All filters reset when a new scan runs.</p>

<h2>Input Pattern</h2>
<p>Tells the app how filenames are structured so it can extract fields.
Use <code>{placeholder}</code> tokens separated by literal text.</p>
<table>
  <tr><th>Placeholder</th><th>Field extracted</th></tr>
  <tr><td><code>{author}</code></td><td>Author name</td></tr>
  <tr><td><code>{title}</code></td><td>Book title</td></tr>
  <tr><td><code>{series}</code></td><td>Series name</td></tr>
  <tr><td><code>{series_index}</code></td><td>Position in series (number)</td></tr>
  <tr><td><code>{subject}</code></td><td>Subject / genre</td></tr>
  <tr><td><code>{tags}</code></td><td>Tags / keywords</td></tr>
  <tr><td><code>{dir_in}</code></td><td>Parent directory (prefix only)</td></tr>
  <tr><td><code>{filename}</code></td><td>Full filename including extension</td></tr>
</table>
<p><b>Examples:</b><br>
<code>{dir_in}\\{author} - {title}</code><br>
<code>{dir_in}\\{author} - {series} {series_index} - {title}</code><br>
<code>{author} - {title}</code> &nbsp;(filename only)</p>
<p>The Pattern cell turns <span style="background:#99ee99">green</span> on match, or
<span style="background:#ff9999">red</span> on failure (hover for reason).
Use <b>Apply to all</b> to push the pattern to every book.</p>

<h2>Output Pattern &amp; New Filepath</h2>
<p>Determines where each book will be written. The result appears in the <b>New Filepath</b> column.</p>

<h3>Placeholders</h3>
<p>All input placeholders work, plus:</p>
<table>
  <tr><th>Placeholder</th><th>Value</th></tr>
  <tr><td><code>{dir_out}</code></td><td>The Output Dir for this book (editable, falls back to current folder).</td></tr>
  <tr><td><code>{dir_in}</code></td><td>Book's current parent directory.</td></tr>
  <tr><td><code>{filepath}</code></td><td>Book's full current path.</td></tr>
</table>
<p>The original file extension is <b>appended automatically</b> — omit it from the pattern.
MOBI files converted to EPUB get the <code>.epub</code> extension.</p>

<h3>Empty-field behaviour</h3>
<table>
  <tr><th>Empty field</th><th>Effect</th></tr>
  <tr><td>Output Pattern</td><td>Default: <code>{dir_out}\\{author} - {series} {series_index} - {title}</code></td></tr>
  <tr><td>Output Dir</td><td>Uses the book's current directory (in-place rename).</td></tr>
  <tr><td>Series / Index</td><td>Surrounding <code> - </code> separators collapse: <code>Author - Title</code> not <code>Author -  - Title</code>.</td></tr>
</table>

<h3>Filename sanitisation</h3>
<p>Metadata values are cleaned before substitution:
<code>:</code>&nbsp;→&nbsp;<code>-</code> &nbsp;|&nbsp;
<code>"</code>&nbsp;→&nbsp;<code>'</code> &nbsp;|&nbsp;
<code>* ? &lt; &gt; | / \\</code>&nbsp;→&nbsp;removed.
Directory separators in the pattern itself are preserved.</p>

<h2>Writing Files</h2>
<p>The <b>Write</b> button (or <b>Ctrl+Shift+W</b>) processes all books with a computed New Filepath, or only
the selected books if a selection is active. Press <b>Escape</b> to deselect.</p>

<h3>Write dialog</h3>
<ul>
  <li><b>Convert MOBI/AZW3 → EPUB 3.3</b> — creates a <code>.epub</code> file alongside the original (MOBI kept).</li>
  <li><b>Rebuild to ideal EPUB 3.3</b> — upgrades EPUB 2 and non-ideal EPUB 3 files.</li>
  <li><b>File operation</b> — Copy (keep originals) or Move.</li>
  <li><b>Clash handling</b> — append a numbered suffix or overwrite.</li>
</ul>

<h3>Ideal EPUB 3.3 format</h3>
<ul>
  <li>One <code>.xhtml</code> file per chapter — enables the TOC navigation panel in all major readers.</li>
  <li><code>nav.xhtml</code> (EPUB 3) + <code>toc.ncx</code> (EPUB 2 fallback) for maximum compatibility.</li>
  <li>Clean HTML5 — Mobipocket-specific markup stripped.</li>
</ul>

<h3>Metadata writing</h3>
<ul>
  <li><b>EPUB</b> — OPF XML rewrite inside the ZIP (or full rebuild for ideal-format output).</li>
  <li><b>MOBI / AZW3 / AZW / PRC</b> — PalmDB EXTH record manipulation.</li>
  <li><b>PDF</b> — pypdf metadata writer.</li>
</ul>

<h2>Cover Images</h2>
<p>The <b>Cover</b> column shows the source and pixel dimensions once loaded
(e.g. <code>Google Books · 128×192</code>). Click a Cover cell to adopt that source
as Chosen. The cover is downloaded and embedded when the file is written.
Click the Chosen Cover cell to preview full-size.</p>

<h2>Editing Cells</h2>
<p>Editable cells have a <b>black border</b>. Double-click to open an editor.</p>
<ul>
  <li><b>Chosen row</b>: author, title, series, series index, subject, tags, output pattern, and Output Dir.</li>
  <li><b>Filename row</b>: the Pattern cell. Changing it re-parses the filename immediately.</li>
</ul>

<h2>Keyboard Shortcuts</h2>
<table>
  <tr><th>Key</th><th>Action</th></tr>
  <tr><td><code>Ctrl+R</code></td><td>Scan the selected folder</td></tr>
  <tr><td><code>Ctrl+Shift+F</code></td><td>Fetch web metadata</td></tr>
  <tr><td><code>Ctrl+Shift+W</code></td><td>Write all books</td></tr>
  <tr><td><code>Ctrl+C</code></td><td>Copy selected cells as TSV</td></tr>
  <tr><td><code>Ctrl+V</code></td><td>Paste TSV into selected cells</td></tr>
  <tr><td><code>Ctrl+A</code></td><td>Select all</td></tr>
  <tr><td><code>Escape</code></td><td>Clear selection</td></tr>
</table>

<h2>Multi-Cell Editing &amp; Clipboard</h2>
<ul>
  <li>Select multiple cells, double-click one and type — value fills all selected editable cells (Excel-style).</li>
  <li><b>Ctrl+C</b> — copy selected cells as TSV.</li>
  <li><b>Ctrl+V</b> — paste TSV; tiled to fill the selection if smaller.</li>
  <li><b>Ctrl+A</b> — select all. <b>Escape</b> — clear selection.</li>
</ul>

<h2>Sorting</h2>
<p>Click any column header to sort by that column's Chosen value. Click again to reverse.
Series index sorts numerically; everything else alphabetically.</p>

<h2>Column Visibility</h2>
<p>Right-click any column header to show/hide columns. Saved between sessions.</p>

<h2>Settings Persistence</h2>
<p>Automatically saved on close and restored on next launch:</p>
<ul>
  <li>Window size and position</li>
  <li>Library folder path</li>
  <li>Input and output patterns</li>
  <li>Column widths and visibility</li>
</ul>
"""
