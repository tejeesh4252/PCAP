"""
crescent_pdf_diagnostic.py

Diagnostic extractor for Crescent Capital PDF statements.

For every PDF in a folder (recursively), this dumps to a .txt file:
  1. Raw text per page
  2. Word-level extraction with x0/x1 (column) and top (row) positions,
     clustered into reconstructed rows so you can see exactly where each
     number sits relative to its label and relative to QTD/YTD/ITD columns
  3. A histogram of recurring x0 positions (helps identify true column
     boundaries across the whole document)
  4. pdfplumber's native extract_tables(), tried with two strategies
  5. camelot tables (stream + lattice), if camelot-py is installed
     -- optional; the script still runs fully without it

Nothing here guesses values by string distance -- it's all positional,
so the output tells us the *actual* layout instead of assumptions.

USAGE
    python crescent_pdf_diagnostic.py "/path/to/folder/with/pdfs"

    (or just run it with no argument -- it will prompt you to paste
    the folder path)

OUTPUT
    Writes one <filename>__diagnostic.txt per PDF into:
        <input_folder>/_diagnostics/

REQUIREMENTS
    pip install pdfplumber
    pip install camelot-py[cv]     # optional -- needs Ghostscript for
                                    # the 'lattice' flavor; skipped
                                    # automatically if not installed
"""

import sys
import os
import glob
from pathlib import Path
from collections import Counter

import pdfplumber

try:
    import camelot
    HAVE_CAMELOT = True
except ImportError:
    HAVE_CAMELOT = False


ROW_TOLERANCE = 3   # points -- words within this many pts of each other's
                     # 'top' coordinate are treated as being on the same row


def cluster_words_into_rows(words, tolerance=ROW_TOLERANCE):
    """
    Group word dicts (from page.extract_words()) into visual rows based on
    their 'top' coordinate, then sort each row left-to-right by x0.
    Returns a list of rows; each row is a list of word dicts.
    """
    if not words:
        return []
    words_sorted = sorted(words, key=lambda w: (w["top"], w["x0"]))
    rows = []
    current_row = [words_sorted[0]]
    current_top = words_sorted[0]["top"]
    for w in words_sorted[1:]:
        if abs(w["top"] - current_top) <= tolerance:
            current_row.append(w)
        else:
            rows.append(sorted(current_row, key=lambda x: x["x0"]))
            current_row = [w]
            current_top = w["top"]
    rows.append(sorted(current_row, key=lambda x: x["x0"]))
    return rows


def dump_page_words(page, page_num, f):
    words = page.extract_words(x_tolerance=1.5, y_tolerance=1.5)
    rows = cluster_words_into_rows(words)
    f.write(f"\n--- PAGE {page_num} : WORD-LEVEL ROWS (x-position clustered) ---\n")
    f.write(f"(page size: {page.width:.1f} x {page.height:.1f})\n\n")
    for row in rows:
        row_top = row[0]["top"]
        pieces = [f"[{w['text']}](x0={w['x0']:.1f},x1={w['x1']:.1f})" for w in row]
        f.write(f"top={row_top:7.1f} | " + "  ".join(pieces) + "\n")
    return words


def dump_column_histogram(all_words, f):
    """
    Histogram of rounded x0 starting positions across the whole document.
    Recurring x0 clusters usually mark real table columns (e.g. the QTD /
    YTD / ITD number columns), independent of whatever text sits above them.
    """
    buckets = Counter(round(w["x0"] / 5) * 5 for w in all_words)
    f.write("\n--- COLUMN X0 HISTOGRAM (rounded to nearest 5pt, top 30) ---\n")
    for x0, count in sorted(buckets.items(), key=lambda kv: -kv[1])[:30]:
        f.write(f"x0≈{x0:>4}  : {count} words\n")


def dump_pdfplumber_tables(page, page_num, f):
    f.write(f"\n--- PAGE {page_num} : pdfplumber.extract_tables() ---\n")
    for strategy_name, settings in [
        ("lines", {"vertical_strategy": "lines", "horizontal_strategy": "lines"}),
        ("text", {"vertical_strategy": "text", "horizontal_strategy": "text"}),
    ]:
        try:
            tables = page.extract_tables(table_settings=settings)
        except Exception as exc:
            f.write(f"  [{strategy_name}] extraction error: {exc}\n")
            continue
        f.write(f"  [{strategy_name} strategy] found {len(tables)} table(s)\n")
        for t_idx, table in enumerate(tables):
            f.write(f"    table {t_idx}:\n")
            for row in table:
                f.write(f"      {row}\n")


def dump_camelot_tables(pdf_path, f):
    f.write("\n--- CAMELOT ---\n")
    if not HAVE_CAMELOT:
        f.write("  camelot not installed -- skipped (pip install camelot-py[cv])\n")
        return
    for flavor in ("stream", "lattice"):
        try:
            tables = camelot.read_pdf(pdf_path, pages="all", flavor=flavor)
        except Exception as exc:
            f.write(f"  [{flavor}] error: {exc}\n")
            continue
        f.write(f"  [{flavor}] found {tables.n} table(s)\n")
        for i, table in enumerate(tables):
            f.write(f"    table {i} (page {table.page}), shape={table.df.shape}\n")
            f.write(table.df.to_string() + "\n")


def process_pdf(pdf_path, out_dir):
    name = Path(pdf_path).stem
    out_path = os.path.join(out_dir, f"{name}__diagnostic.txt")
    print(f"Processing: {pdf_path}")
    try:
        all_words = []
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(f"FILE: {pdf_path}\n")
            f.write("=" * 80 + "\n")
            with pdfplumber.open(pdf_path) as pdf:
                f.write(f"Pages: {len(pdf.pages)}\n")
                for i, page in enumerate(pdf.pages, start=1):
                    raw_text = page.extract_text() or ""
                    f.write(f"\n=== PAGE {i} : RAW TEXT ===\n")
                    f.write(raw_text + "\n")

                    words = dump_page_words(page, i, f)
                    all_words.extend(words)
                    dump_pdfplumber_tables(page, i, f)

            dump_column_histogram(all_words, f)
            dump_camelot_tables(pdf_path, f)

        print(f"  -> wrote {out_path}")
    except Exception as exc:
        print(f"  !! FAILED on {pdf_path}: {exc}")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(f"FILE: {pdf_path}\nEXTRACTION FAILED: {exc}\n")


def main():
    if len(sys.argv) > 1:
        folder = sys.argv[1]
    else:
        folder = input("Paste the path to the folder containing the Crescent PDFs: ").strip()

    folder = os.path.expanduser(folder.strip('"').strip("'"))
    if not os.path.isdir(folder):
        print(f"Not a folder: {folder}")
        sys.exit(1)

    pdf_files = sorted(glob.glob(os.path.join(folder, "**", "*.pdf"), recursive=True))
    if not pdf_files:
        print(f"No PDFs found in {folder}")
        sys.exit(1)

    out_dir = os.path.join(folder, "_diagnostics")
    os.makedirs(out_dir, exist_ok=True)

    print(f"Found {len(pdf_files)} PDF(s). Writing diagnostics to: {out_dir}\n")
    for pdf_path in pdf_files:
        process_pdf(pdf_path, out_dir)

    print(f"\nDone. Zip up the folder below and send it back:\n{out_dir}")


if __name__ == "__main__":
    main()
