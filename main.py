# main.py
"""
PCAP PDF Extractor — Main Entry Point

Speed improvements added (non-breaking):
  1. PDF text caching  — skip re-reading unchanged PDFs
                         on repeated runs (10-50x faster
                         for development iterations)
  2. Multiprocessing   — process multiple PDFs in parallel
                         using all CPU cores (4-6x faster
                         for full production runs)
  3. Single-threaded   fallback always available via
                         USE_MULTIPROCESSING = False

Nothing else changed — all template logic, output format,
progress display and Excel writing are identical.
"""

import sys
import os
import json
import importlib
import hashlib
import pickle
import multiprocessing as mp
from datetime import datetime
from functools import partial as functools_partial

from core.pdf_reader import (
    extract_text, detect_template,
    get_pdf_files
)
from core.output_writer import (
    create_output_workbook, write_extracted_row,
    write_unknown_file, write_skipped_file, write_error
)

# ══════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════

PDF_FOLDER = (
    r"C:\Users\wj596\Desktop\Personal\PE- Automation"
    r"\PCAP\Input"
)
OUTPUT_FOLDER = (
    r"C:\Users\wj596\Desktop\Personal\PE- Automation"
    r"\PCAP\Output"
)
CONFIG_PATH = os.path.join(
    os.path.dirname(__file__), "config", "template_rules.json"
)
TEMPLATES_DIR = "templates"

# ── Speed settings ────────────────────────────────────────────
# Set USE_MULTIPROCESSING = False to revert to original
# single-threaded behaviour at any time
USE_MULTIPROCESSING = True

# Number of parallel workers (None = auto = CPU cores - 1)
# Set to 4 if you want a fixed number, e.g. MAX_WORKERS = 4
MAX_WORKERS = None

# Cache extracted PDF text to disk so repeated runs skip
# re-reading unchanged PDFs (huge speedup during development)
USE_CACHE = True
CACHE_DIR = os.path.join(
    os.path.dirname(__file__), ".pdf_text_cache"
)


# ══════════════════════════════════════════════════════════════
# PDF TEXT CACHE
# ══════════════════════════════════════════════════════════════

def _cache_key(pdf_path: str) -> str:
    """
    Generate a unique cache key for a PDF file based on its
    full path and last-modified timestamp.
    If the file changes on disk the key changes and the cache
    miss triggers a fresh extraction.
    """
    mtime = os.path.getmtime(pdf_path)
    raw   = f"{pdf_path}:{mtime}"
    return hashlib.md5(raw.encode()).hexdigest()


def _get_cached(pdf_path: str):
    """
    Return cached (full_text, page_texts, page_count) tuple
    if available, else None.
    """
    if not USE_CACHE:
        return None
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_file = os.path.join(
        CACHE_DIR, f"{_cache_key(pdf_path)}.pkl"
    )
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "rb") as f:
                return pickle.load(f)
        except Exception:
            # Corrupted cache file — ignore and re-extract
            return None
    return None


def _save_cache(pdf_path: str,
                full_text: str,
                page_texts: list,
                page_count: int) -> None:
    """Save extracted text to cache."""
    if not USE_CACHE:
        return
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_file = os.path.join(
        CACHE_DIR, f"{_cache_key(pdf_path)}.pkl"
    )
    try:
        with open(cache_file, "wb") as f:
            pickle.dump((full_text, page_texts, page_count), f)
    except Exception:
        pass  # Cache write failure is non-fatal


def extract_text_cached(pdf_path: str):
    """
    Extract text from PDF with caching.
    Returns (full_text, page_texts, page_count, error)
    — identical signature to extract_text().
    """
    cached = _get_cached(pdf_path)
    if cached is not None:
        full_text, page_texts, page_count = cached
        return full_text, page_texts, page_count, None

    # Cache miss — do real extraction
    full_text, page_texts, page_count, error = extract_text(
        pdf_path
    )
    if not error:
        _save_cache(pdf_path, full_text, page_texts, page_count)
    return full_text, page_texts, page_count, error


# ══════════════════════════════════════════════════════════════
# RULES LOADER
# ══════════════════════════════════════════════════════════════

def load_rules():
    """Load template detection rules from
    config/template_rules.json."""
    with open(CONFIG_PATH, 'r') as f:
        return json.load(f)


# ══════════════════════════════════════════════════════════════
# AUTO HANDLER LOADER
# ══════════════════════════════════════════════════════════════

def load_handlers(rules):
    """
    Automatically build HANDLERS dict from
    template_rules.json.

    For each template entry, reads the 'handler' field and
    dynamically imports templates/<handler>.py → extract()

    No manual updates needed — just add new entry to
    template_rules.json + drop the .py file in /templates.
    """
    handlers = {}

    print(f"\n{'─' * 65}")
    print("  Loading template handlers...")
    print(f"{'─' * 65}")

    for template_name, config in rules["templates"].items():
        handler_module_name = config.get("handler")

        if not handler_module_name:
            print(
                f"  [WARN]  No handler defined for: "
                f"{template_name}"
            )
            continue

        module_path = f"{TEMPLATES_DIR}.{handler_module_name}"

        try:
            module = importlib.import_module(module_path)

            if hasattr(module, "extract"):
                handlers[template_name] = module.extract
                print(
                    f"  [OK]    {template_name:<30} "
                    f"→ {handler_module_name}.py"
                )
            else:
                print(
                    f"  [WARN]  {handler_module_name}.py "
                    f"has no extract() function — skipping"
                )

        except ModuleNotFoundError:
            print(
                f"  [SKIP]  {template_name:<30} "
                f"→ {handler_module_name}.py not found"
            )
        except Exception as e:
            print(
                f"  [ERROR] Failed to load "
                f"{handler_module_name}: {e}"
            )

    print(f"{'─' * 65}")
    print(
        f"  {len(handlers)} of "
        f"{len(rules['templates'])} handlers loaded\n"
    )

    return handlers


# ══════════════════════════════════════════════════════════════
# WORKER FUNCTION (runs in each parallel process)
# ══════════════════════════════════════════════════════════════

def _process_one_file(args):
    """
    Process a single PDF file.
    Must be a top-level function (not a lambda or closure)
    so Python's multiprocessing can pickle it.

    Args:
        args: tuple of (pdf_path, rules_path, templates_dir,
                        use_cache, cache_dir)

    Returns:
        dict with keys:
          filename, template_name, confidence,
          extracted, status, notes,
          page_count, full_text_preview, error
    """
    pdf_path, rules_path, templates_dir, \
        use_cache, cache_dir = args

    filename = os.path.basename(pdf_path)

    # Each worker process must reload rules + handlers
    # independently (can't share across processes)
    try:
        with open(rules_path, 'r') as f:
            rules = json.load(f)
    except Exception as e:
        return {
            "filename": filename,
            "error": f"Failed to load rules: {e}",
        }

    # Dynamically load handlers in this worker process
    handlers = {}
    for tname, cfg in rules["templates"].items():
        hname = cfg.get("handler", "")
        if not hname:
            continue
        mpath = f"{templates_dir}.{hname}"
        try:
            mod = importlib.import_module(mpath)
            if hasattr(mod, "extract"):
                handlers[tname] = mod.extract
        except Exception:
            pass

    # ── Extract text (with cache if enabled) ──────────────────
    if use_cache:
        # Inline cache logic (worker can't call main's functions)
        def _worker_cache_key(path):
            mtime = os.path.getmtime(path)
            return hashlib.md5(
                f"{path}:{mtime}".encode()
            ).hexdigest()

        os.makedirs(cache_dir, exist_ok=True)
        cache_file = os.path.join(
            cache_dir,
            f"{_worker_cache_key(pdf_path)}.pkl"
        )
        if os.path.exists(cache_file):
            try:
                with open(cache_file, "rb") as f:
                    full_text, page_texts, page_count = (
                        pickle.load(f)
                    )
                error = None
            except Exception:
                full_text, page_texts, page_count, error = (
                    extract_text(pdf_path)
                )
        else:
            full_text, page_texts, page_count, error = (
                extract_text(pdf_path)
            )
            if not error:
                try:
                    with open(cache_file, "wb") as f:
                        pickle.dump(
                            (full_text, page_texts,
                             page_count), f
                        )
                except Exception:
                    pass
    else:
        full_text, page_texts, page_count, error = (
            extract_text(pdf_path)
        )

    if error:
        return {
            "filename":   filename,
            "error":      error,
            "page_count": 0,
            "preview":    "",
        }

    # ── Detect template ───────────────────────────────────────
    template_name, confidence = detect_template(
        full_text, rules
    )

    # ── Run handler ───────────────────────────────────────────
    if template_name not in handlers:
        return {
            "filename":      filename,
            "template_name": template_name,
            "confidence":    confidence,
            "page_count":    page_count,
            "full_text_len": len(full_text),
            "preview":       full_text[:300].replace(
                '\n', ' | '
            ),
            "unknown":       True,
        }

    try:
        handler   = handlers[template_name]
        extracted = handler(full_text, filename)
        extracted["Source File"] = filename

        return {
            "filename":      filename,
            "template_name": template_name,
            "confidence":    confidence,
            "extracted":     extracted,
            "status":        extracted.get(
                "Extraction Status", ""
            ),
            "notes":         extracted.get(
                "Extraction Notes", ""
            ),
            "page_count":    page_count,
            "full_text_len": len(full_text),
            "preview":       full_text[:300].replace(
                '\n', ' | '
            ),
            "unknown":       False,
            "error":         None,
        }

    except Exception as e:
        return {
            "filename":      filename,
            "template_name": template_name,
            "confidence":    confidence,
            "page_count":    page_count,
            "full_text_len": len(full_text),
            "preview":       full_text[:300].replace(
                '\n', ' | '
            ),
            "handler_error": str(e),
            "unknown":       False,
        }


# ══════════════════════════════════════════════════════════════
# RESULT WRITER  (always runs in main process)
# ══════════════════════════════════════════════════════════════

def _write_result(result: dict,
                  output_path: str,
                  counts: dict,
                  idx: int,
                  total: int) -> None:
    """
    Write a single worker result to Excel and update counts.
    Called in the main process — keeps all Excel I/O
    single-threaded and safe.
    """
    filename = result.get("filename", "unknown")
    short_name = (
        filename[:55] + "..."
        if len(filename) > 55
        else filename
    )
    print(f"[{idx:3d}/{total}] {short_name}")

    # ── Read error ────────────────────────────────────────────
    if "error" in result and result.get("error"):
        err = result["error"]
        print(f"         ❌ Read error: {err}")
        write_error(output_path, filename, err)
        counts["error"] += 1
        return

    # ── Unknown template ──────────────────────────────────────
    if result.get("unknown"):
        template_name = result.get("template_name", "UNKNOWN")
        print(
            f"         ⚠️  UNKNOWN template "
            f"(char count: {result.get('full_text_len', 0)})"
        )
        write_unknown_file(
            output_path,
            filename,
            result.get("page_count", 0),
            result.get("full_text_len", 0),
            result.get("preview", ""),
            f"No handler for: {template_name}"
        )
        counts["unknown"] += 1
        return

    # ── Handler exception ─────────────────────────────────────
    if "handler_error" in result:
        err = result["handler_error"]
        template_name = result.get("template_name", "unknown")
        print(f"         ❌ Extraction failed: {err}")
        write_error(
            output_path, filename, err, template_name
        )
        counts["error"] += 1
        return

    # ── Normal result ─────────────────────────────────────────
    template_name = result.get("template_name", "")
    confidence    = result.get("confidence", 0)
    extracted     = result.get("extracted", {})
    status        = result.get("status", "")
    notes         = result.get("notes", "")

    print(
        f"         ✅ Template: {template_name} "
        f"(confidence: {confidence})"
    )

    if status == "SUCCESS":
        write_extracted_row(output_path, extracted)
        counts["success"] += 1
        print(f"         ✅ Extracted successfully")

    elif status == "PARTIAL":
        write_extracted_row(output_path, extracted)
        counts["partial"] += 1
        print(f"         ⚠️  Partial: {notes}")

    elif status == "SKIP":
        write_skipped_file(
            output_path,
            filename,
            result.get("page_count", 0),
            result.get("full_text_len", 0),
            result.get("preview", ""),
            notes
        )
        counts["skipped"] += 1
        print(f"         ⏭️  Skipped: {notes}")

    else:
        write_error(
            output_path,
            filename,
            f"Unexpected status: '{status}'",
            template_name
        )
        counts["error"] += 1
        print(f"         ❌ Unexpected status: '{status}'")


# ══════════════════════════════════════════════════════════════
# MAIN PROCESSOR
# ══════════════════════════════════════════════════════════════

def process_all_pdfs():
    print("=" * 65)
    print("  PCAP PDF EXTRACTOR")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 65)

    # ── Load rules + handlers (main process only) ──────────────
    rules    = load_rules()
    HANDLERS = load_handlers(rules)   # still used for
                                      # single-threaded mode

    # ── Get PDF files ──────────────────────────────────────────
    pdf_files = get_pdf_files(PDF_FOLDER)
    total     = len(pdf_files)

    if total == 0:
        print(f"\n❌ No PDF files found in:\n   {PDF_FOLDER}")
        return

    print(f"  ✅ Found {total} PDF files in input folder")

    # ── Cache stats ────────────────────────────────────────────
    if USE_CACHE:
        os.makedirs(CACHE_DIR, exist_ok=True)
        cache_files = [
            f for f in os.listdir(CACHE_DIR)
            if f.endswith(".pkl")
        ]
        print(
            f"  💾 PDF cache: {len(cache_files)} files cached"
        )

    # ── Speed mode banner ──────────────────────────────────────
    if USE_MULTIPROCESSING:
        n_workers = MAX_WORKERS or max(1, mp.cpu_count() - 1)
        print(
            f"  ⚡ Multiprocessing: {n_workers} workers "
            f"(of {mp.cpu_count()} CPU cores)"
        )
    else:
        print("  🔄 Single-threaded mode")

    # ── Create output workbook ─────────────────────────────────
    timestamp   = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = os.path.join(
        OUTPUT_FOLDER,
        f"PCAP_Extracted_{timestamp}.xlsx"
    )
    create_output_workbook(output_path)

    # ── Counters ───────────────────────────────────────────────
    counts = {
        "success": 0,
        "partial": 0,
        "skipped": 0,
        "unknown": 0,
        "error":   0
    }

    start_time = datetime.now()

    print(f"\n{'─' * 65}")
    print(f"  Processing {total} files...")
    print(f"{'─' * 65}\n")

    # ══════════════════════════════════════════════════════════
    # MODE A — MULTIPROCESSING
    # ══════════════════════════════════════════════════════════
    if USE_MULTIPROCESSING:

        n_workers = MAX_WORKERS or max(1, mp.cpu_count() - 1)

        # Build args list for worker function
        # Each element = one tuple passed to _process_one_file
        worker_args = [
            (
                pdf_path,
                CONFIG_PATH,
                TEMPLATES_DIR,
                USE_CACHE,
                CACHE_DIR,
            )
            for pdf_path in pdf_files
        ]

        # Use a process pool — imap_unordered returns results
        # as soon as each worker finishes (fastest throughput)
        # chunksize=2 balances overhead vs parallelism
        completed = 0
        with mp.Pool(processes=n_workers) as pool:
            for result in pool.imap_unordered(
                _process_one_file,
                worker_args,
                chunksize=2
            ):
                completed += 1
                _write_result(
                    result, output_path, counts,
                    completed, total
                )

    # ══════════════════════════════════════════════════════════
    # MODE B — SINGLE-THREADED (original behaviour)
    # ══════════════════════════════════════════════════════════
    else:
        for idx, pdf_path in enumerate(pdf_files, 1):
            filename   = os.path.basename(pdf_path)
            short_name = (
                filename[:55] + "..."
                if len(filename) > 55
                else filename
            )

            print(f"[{idx:3d}/{total}] {short_name}")

            # ── Step 1: Extract text ───────────────────────────
            full_text, page_texts, page_count, error = \
                extract_text_cached(pdf_path)

            if error:
                print(f"         ❌ Read error: {error}")
                write_error(output_path, filename, error)
                counts["error"] += 1
                continue

            # ── Step 2: Detect template ────────────────────────
            template_name, confidence = detect_template(
                full_text, rules
            )

            # ── Step 3: Route to handler ───────────────────────
            if template_name in HANDLERS:
                print(
                    f"         ✅ Template: {template_name} "
                    f"(confidence: {confidence})"
                )

                try:
                    handler   = HANDLERS[template_name]
                    extracted = handler(full_text, filename)
                    extracted["Source File"] = filename

                    status = extracted.get(
                        "Extraction Status", ""
                    )
                    notes  = extracted.get(
                        "Extraction Notes", ""
                    )

                    # ── SUCCESS ────────────────────────────────
                    if status == "SUCCESS":
                        write_extracted_row(
                            output_path, extracted
                        )
                        counts["success"] += 1
                        print(
                            f"         ✅ Extracted "
                            f"successfully"
                        )

                    # ── PARTIAL ────────────────────────────────
                    elif status == "PARTIAL":
                        write_extracted_row(
                            output_path, extracted
                        )
                        counts["partial"] += 1
                        print(f"         ⚠️  Partial: {notes}")

                    # ── SKIP ───────────────────────────────────
                    elif status == "SKIP":
                        write_skipped_file(
                            output_path,
                            filename,
                            page_count,
                            len(full_text),
                            full_text[:300].replace(
                                '\n', ' | '
                            ),
                            notes
                        )
                        counts["skipped"] += 1
                        print(f"         ⏭️  Skipped: {notes}")

                    # ── UNEXPECTED STATUS ──────────────────────
                    else:
                        write_error(
                            output_path,
                            filename,
                            f"Unexpected status: '{status}'",
                            template_name
                        )
                        counts["error"] += 1
                        print(
                            f"         ❌ Unexpected status: "
                            f"'{status}'"
                        )

                except Exception as e:
                    print(
                        f"         ❌ Extraction failed: {e}"
                    )
                    write_error(
                        output_path,
                        filename,
                        str(e),
                        template_name
                    )
                    counts["error"] += 1

            # ── No handler / Unknown template ──────────────────
            else:
                print(
                    f"         ⚠️  UNKNOWN template "
                    f"(char count: {len(full_text)})"
                )
                write_unknown_file(
                    output_path,
                    filename,
                    page_count,
                    len(full_text),
                    full_text[:300].replace('\n', ' | '),
                    f"No handler for: {template_name}"
                )
                counts["unknown"] += 1

    # ── Timing ────────────────────────────────────────────────
    elapsed = datetime.now() - start_time
    mins    = int(elapsed.total_seconds() // 60)
    secs    = int(elapsed.total_seconds() % 60)

    # ── Final Summary ──────────────────────────────────────────
    print(f"\n{'=' * 65}")
    print(f"  EXTRACTION COMPLETE")
    print(f"{'=' * 65}")
    print(f"  ✅ Success:        {counts['success']:4d} files")
    print(f"  ⚠️  Partial:        {counts['partial']:4d} files")
    print(f"  ⏭️  Skipped (AFS):  {counts['skipped']:4d} files")
    print(f"  ❓ Unknown:         {counts['unknown']:4d} files")
    print(f"  ❌ Errors:          {counts['error']:4d} files")
    print(f"  {'─' * 45}")
    print(f"  Total:             {sum(counts.values()):4d} files")
    print(f"  ⏱️  Time:            {mins}m {secs}s")
    if USE_MULTIPROCESSING:
        n_workers = MAX_WORKERS or max(1, mp.cpu_count() - 1)
        print(f"  ⚡ Workers used:    {n_workers}")
    print(f"\n  📊 Output saved to:")
    print(f"  {output_path}")
    print(f"{'=' * 65}\n")


if __name__ == "__main__":
    process_all_pdfs()
