# templates/crescent_capital_aus.py
"""
Crescent Capital (Australian) fund handler.
Covers: Crescent Capital Partners IVB, VB (Project Wellness),
        Crescent Capital Partners V (PAPEF VIII),
        Crescent Capital Partners VI AUD (mirrored/reversed PDF).

GP: Crescent Management Pty Limited | ABN 18 108 571 820
Currency: AUD

Layout: Partner table with one row per LP.
Columns (left to right):
    LP Name | Commitment | Beginning | Calls |
    Distributions | Gain/Loss | Mgmt Fee | Ending

Special case: Crescent Capital VI AUD — PDF text is mirrored/reversed.
Detected by 2+ reversed keywords and corrected before parsing.

v3 fixes:
  - Added "CAB" and broader investor aliases to LP row search
  - Strategy B: header-based row scan when keyword search fails
  - Safe number extractor preserving negatives and decimals
  - Stray digit filter (years, page numbers, small ints)
  - Richer date patterns (31 Dec 2024, December 31 2024, etc.)
  - Requires 2+ reversed markers before flipping text
  - SUCCESS gate: fund_name + ending_balance both populated
  - Full traceback on exceptions for GUI tester visibility
"""

import re
import traceback
from templates.simple_rollforward import _parse_number


# ──────────────────────────────────────────────────────────────────
# Reversed-PDF helpers  (Crescent Capital VI AUD)
# ──────────────────────────────────────────────────────────────────

def _is_reversed(text: str) -> bool:
    """
    Detect mirrored/reversed PDF text.
    Requires 2 or more reversed markers to avoid false positives.
    """
    reversed_markers = [
        "NOITACIFITON",   # NOTIFICATION reversed
        "SRENTRAP",       # PARTNERS reversed
        "LATIPAC",        # CAPITAL reversed
        "DNUF",           # FUND reversed
        "TNEMTIMMOC",     # COMMITMENT reversed
    ]
    hits = sum(1 for m in reversed_markers if m in text.upper())
    return hits >= 2


def _reverse_text(text: str) -> str:
    """Reverse the full extracted text to recover readable content."""
    return text[::-1]


# ──────────────────────────────────────────────────────────────────
# Number extraction helper
# ──────────────────────────────────────────────────────────────────

def _extract_numbers_from_row(row_text: str):
    """
    Safely extract all financial numbers from a table row string.

    Handles:
      Positive  :  1,234,567.89  or  1234567
      Negative  :  -1,234,567    or  (1,234,567)  -> stored as negative float
    Skips:
      Year-like 4-digit numbers  (1990-2099)
      Pure integers <= 999       (likely page refs or percentages)
      Values between -1 and +1   (percentage fragments)
    """
    # Convert parenthesised negatives:  (123,456) -> -123456
    row_text = re.sub(
        r'\(([\d,]+\.?\d*)\)',
        lambda m: '-' + m.group(1),
        row_text
    )

    raw_nums = re.findall(r'-?[\d,]+\.?\d*', row_text)

    results = []
    for n in raw_nums:
        cleaned = n.replace(',', '')
        try:
            val = float(cleaned)
        except ValueError:
            continue
        abs_val = abs(val)
        if abs_val == 0:
            continue
        if 1990 <= abs_val <= 2099:          # looks like a year
            continue
        if abs_val < 1:                       # percentage fragment
            continue
        if abs_val < 100 and '.' not in n:   # small integer (page no / %)
            continue
        results.append(val)

    return results


# ──────────────────────────────────────────────────────────────────
# Main extract function
# ──────────────────────────────────────────────────────────────────

def extract(full_text: str, filename: str) -> dict:

    result = {
        "Extraction Status":            "PARTIAL",
        "Extraction Notes":             "",
        "Source File":                  filename,
        "template_used":                "Crescent_Capital_AUS",
        "currency":                     "AUD",
        "fund_name":                    "",
        "investor_name":                "",
        "report_date":                  "December 31, 2025",
        "capital_commitment":           None,
        "unfunded_commitment":          None,
        "beginning_balance_qtd":        None,
        "beginning_balance_ytd":        None,
        "beginning_balance_itd":        None,
        "contributions_qtd":            None,
        "contributions_ytd":            None,
        "contributions_itd":            None,
        "distributions_qtd":            None,
        "distributions_ytd":            None,
        "distributions_itd":            None,
        "net_investment_income_qtd":    None,
        "net_investment_income_ytd":    None,
        "net_investment_income_itd":    None,
        "management_fees_qtd":          None,
        "management_fees_ytd":          None,
        "management_fees_itd":          None,
        "net_realized_gain_loss_qtd":   None,
        "net_realized_gain_loss_ytd":   None,
        "net_realized_gain_loss_itd":   None,
        "net_unrealized_gain_loss_qtd": None,
        "net_unrealized_gain_loss_ytd": None,
        "net_unrealized_gain_loss_itd": None,
        "carried_interest_qtd":         None,
        "carried_interest_ytd":         None,
        "carried_interest_itd":         None,
        "ending_balance_qtd":           None,
        "ending_balance_ytd":           None,
        "ending_balance_itd":           None,
        "irr":                          None,
        "moic":                         None,
    }

    try:
        # ── Step 1: Detect and fix reversed text (VI AUD) ─────────
        work_text = full_text
        is_rev = _is_reversed(full_text)
        print(f"[DEBUG CCP-AUS] file         = {filename}")
        print(f"[DEBUG CCP-AUS] is_reversed  = {is_rev}")
        print(f"[DEBUG CCP-AUS] first 400 chars:\n{work_text[:400]}\n")

        if is_rev:
            work_text = _reverse_text(full_text)
            result["Extraction Notes"] = "Reversed PDF — text corrected"

        # ── Step 2: Fund name ──────────────────────────────────────
        fn_m = re.search(
            r"(CRESCENT\s+CAPITAL\s+PARTNERS\s+[^\n]{2,60})",
            work_text, re.IGNORECASE
        )
        if fn_m:
            result["fund_name"] = (
                re.sub(r'\s+', ' ', fn_m.group(1).strip())[:100]
            )
        else:
            result["fund_name"] = (
                filename.replace('.pdf', '').replace('_', ' ').strip()[:80]
            )
        print(f"[DEBUG CCP-AUS] fund_name    = {result['fund_name']}")

        # ── Step 3: Investor name ──────────────────────────────────
        # Covers: PAAF, PAPEF, PAREF, Portfolio Advisors, PA PALACE,
        #         CAB, and any "PA " prefix entity
        inv_m = re.search(
            r"((?:PAAF|PAPEF|PAREF|Portfolio\s+Advisors|PA\s+PALACE"
            r"|PA\s+[A-Z]{2,10}|\bCAB\b)[^\n,]{0,80})",
            work_text, re.IGNORECASE
        )
        if inv_m:
            result["investor_name"] = (
                re.sub(r'\s+', ' ', inv_m.group(1).strip())[:150]
            )
        print(f"[DEBUG CCP-AUS] investor     = {result['investor_name']}")

        # ── Step 4: Report date ────────────────────────────────────
        date_patterns = [
            # 31-Dec-24  or  31 Dec 2024
            r"(\d{1,2}[-\s](?:Jan|Feb|Mar|Apr|May|Jun|"
            r"Jul|Aug|Sep|Oct|Nov|Dec)[-\s]\d{2,4})",
            # December 31, 2024
            r"((?:January|February|March|April|May|June|July|August|"
            r"September|October|November|December)\s+\d{1,2},?\s+\d{4})",
            # 31 December 2024
            r"(\d{1,2}\s+(?:January|February|March|April|May|June|July|"
            r"August|September|October|November|December)\s+\d{4})",
        ]
        for dp in date_patterns:
            date_m = re.search(dp, work_text, re.IGNORECASE)
            if date_m:
                result["report_date"] = (
                    re.sub(r'\s+', ' ', date_m.group(1).strip())
                )
                break
        print(f"[DEBUG CCP-AUS] report_date  = {result['report_date']}")

        # ── Step 5: Capital commitment (standalone label) ──────────
        commit_m = re.search(
            r"(?:Capital\s+Commitment|Total\s+Commitment"
            r"|Commitment|Capital\s+Subscribed)"
            r"[\s:]+(?:A?\$\s*)?([\d,]+\.?\d*)",
            work_text, re.IGNORECASE
        )
        if commit_m:
            val = _parse_number(commit_m.group(1))
            if val and val > 1_000:
                result["capital_commitment"] = val
        print(f"[DEBUG CCP-AUS] commitment   = {result['capital_commitment']}")

        # ── Step 6: Find LP row ────────────────────────────────────

        def _find_lp_row_by_keyword():
            """
            Strategy A: scan lines for any known investor keyword
            including CAB, PAAF, PAPEF, PAREF, Portfolio Advisors.
            Grabs that line + 3 following lines (values may wrap).
            """
            lines = work_text.split('\n')
            for i, line in enumerate(lines):
                if re.search(
                    r'(?:PAAF|PAPEF|PAREF|Portfolio\s+Advisors'
                    r'|PA\s+PALACE|PA\s+[A-Z]{2,10}|\bCAB\b)',
                    line, re.IGNORECASE
                ):
                    combined = ' '.join(lines[i:i + 4])
                    if re.search(r'[\d,]{4,}', combined):
                        print(
                            f"[DEBUG CCP-AUS] StrategyA line {i}: "
                            f"{repr(line[:80])}"
                        )
                        return combined
            return None

        def _find_lp_row_by_header():
            """
            Strategy B: locate the table column-header row, then
            scan forward for the first row that contains >= 4
            financial numbers.  Works even when the investor name
            is unknown / not in our keyword list.
            """
            lines = work_text.split('\n')
            header_idx = None

            for i, line in enumerate(lines):
                if re.search(
                    r'(?:Beginning|Opening)\s*(?:Capital|Balance)?'
                    r'.{0,60}(?:Call|Contribution)',
                    line, re.IGNORECASE
                ):
                    header_idx = i
                    print(
                        f"[DEBUG CCP-AUS] Table header line {i}: "
                        f"{repr(line[:80])}"
                    )
                    break

            if header_idx is None:
                return None

            for j in range(header_idx + 1,
                           min(header_idx + 25, len(lines))):
                candidate = ' '.join(lines[j:j + 3])
                nums = _extract_numbers_from_row(candidate)
                if len(nums) >= 4:
                    print(
                        f"[DEBUG CCP-AUS] StrategyB data line {j}: "
                        f"{repr(lines[j][:80])}"
                    )
                    return candidate

            return None

        def _find_lp_row_by_total():
            """
            Strategy C: last resort — find a 'Total' row which
            often contains the same 7 numbers as the single LP row
            in funds that have only one investor.
            """
            lines = work_text.split('\n')
            for i, line in enumerate(lines):
                if re.search(r'\bTotal\b', line, re.IGNORECASE):
                    combined = ' '.join(lines[i:i + 3])
                    nums = _extract_numbers_from_row(combined)
                    if len(nums) >= 4:
                        print(
                            f"[DEBUG CCP-AUS] StrategyC Total line {i}: "
                            f"{repr(line[:80])}"
                        )
                        return combined
            return None

        # Run strategies in order until one succeeds
        lp_row = (
            _find_lp_row_by_keyword()
            or _find_lp_row_by_header()
            or _find_lp_row_by_total()
        )
        print(f"[DEBUG CCP-AUS] lp_row = {repr(lp_row)}")

        # ── Step 7: Parse numbers from LP row ─────────────────────
        if lp_row:
            parsed = _extract_numbers_from_row(lp_row)
            print(f"[DEBUG CCP-AUS] parsed = {parsed}")

            # Expected column order:
            #  idx 0  Commitment
            #  idx 1  Beginning Balance
            #  idx 2  Calls / Contributions
            #  idx 3  Distributions
            #  idx 4  Gain / Loss
            #  idx 5  Management Fee
            #  idx 6  Ending Balance
            #
            # Some PDFs omit the Commitment column (6 values).

            if len(parsed) >= 7:
                result["capital_commitment"]          = parsed[0]
                result["beginning_balance_qtd"]       = parsed[1]
                result["beginning_balance_ytd"]       = parsed[1]
                result["contributions_qtd"]           = parsed[2]
                result["contributions_ytd"]           = parsed[2]
                result["distributions_qtd"]           = parsed[3]
                result["distributions_ytd"]           = parsed[3]
                result["net_realized_gain_loss_qtd"]  = parsed[4]
                result["net_realized_gain_loss_ytd"]  = parsed[4]
                result["management_fees_qtd"]         = parsed[5]
                result["management_fees_ytd"]         = parsed[5]
                result["ending_balance_qtd"]          = parsed[6]
                result["ending_balance_ytd"]          = parsed[6]

            elif len(parsed) == 6:
                # No commitment column in this row
                result["beginning_balance_qtd"]       = parsed[0]
                result["beginning_balance_ytd"]       = parsed[0]
                result["contributions_qtd"]           = parsed[1]
                result["contributions_ytd"]           = parsed[1]
                result["distributions_qtd"]           = parsed[2]
                result["distributions_ytd"]           = parsed[2]
                result["net_realized_gain_loss_qtd"]  = parsed[3]
                result["net_realized_gain_loss_ytd"]  = parsed[3]
                result["management_fees_qtd"]         = parsed[4]
                result["management_fees_ytd"]         = parsed[4]
                result["ending_balance_qtd"]          = parsed[5]
                result["ending_balance_ytd"]          = parsed[5]

            elif len(parsed) >= 4:
                result["beginning_balance_qtd"] = parsed[0]
                result["beginning_balance_ytd"] = parsed[0]
                result["ending_balance_qtd"]    = parsed[-1]
                result["ending_balance_ytd"]    = parsed[-1]

            elif len(parsed) >= 2:
                result["beginning_balance_qtd"] = parsed[0]
                result["beginning_balance_ytd"] = parsed[0]
                result["ending_balance_qtd"]    = parsed[-1]
                result["ending_balance_ytd"]    = parsed[-1]

        # ── Step 8: Label-based fallback ───────────────────────────
        # Only fires for fields still None after row parsing.

        def _label_search(patterns, field_qtd, field_ytd):
            if result[field_ytd] is not None:
                return
            for pat in patterns:
                m = re.search(
                    pat + r"[^\n]{0,60}?([\d,]+\.?\d*)",
                    work_text, re.IGNORECASE
                )
                if m:
                    val = _parse_number(m.group(1))
                    if val and val > 100:
                        result[field_qtd] = val
                        result[field_ytd] = val
                        return

        _label_search(
            [r"(?:Ending|Closing|End)\s+(?:Capital|Balance|NAV)",
             r"Balance[,\s]+end\s+of\s+(?:period|year|quarter)",
             r"Net\s+Asset\s+Value"],
            "ending_balance_qtd", "ending_balance_ytd"
        )
        _label_search(
            [r"(?:Beginning|Opening|Start)\s+(?:Capital|Balance)",
             r"Balance[,\s]+beginning\s+of\s+(?:period|year|quarter)"],
            "beginning_balance_qtd", "beginning_balance_ytd"
        )
        _label_search(
            [r"(?:Capital\s+Call|Contribution|Drawdown)s?"],
            "contributions_qtd", "contributions_ytd"
        )
        _label_search(
            [r"Distribution"],
            "distributions_qtd", "distributions_ytd"
        )
        _label_search(
            [r"Management\s+Fee"],
            "management_fees_qtd", "management_fees_ytd"
        )

        # ── Step 9: Commitment last-resort ─────────────────────────
        if result["capital_commitment"] is None:
            cm2 = re.search(
                r"Commitment[^\n]{0,30}?([\d,]{6,}\.?\d*)",
                work_text, re.IGNORECASE
            )
            if cm2:
                val = _parse_number(cm2.group(1))
                if val and val > 1_000:
                    result["capital_commitment"] = val

        # ── Step 10: Final status ──────────────────────────────────
        populated = [
            k for k in [
                "ending_balance_ytd",
                "beginning_balance_ytd",
                "contributions_ytd",
                "distributions_ytd",
                "management_fees_ytd",
            ]
            if result[k] is not None
        ]
        print(f"[DEBUG CCP-AUS] populated = {populated}")

        if result["ending_balance_ytd"] is not None and result["fund_name"]:
            result["Extraction Status"] = "SUCCESS"
            result["Extraction Notes"]  = (
                (result["Extraction Notes"] + " | "
                 if result["Extraction Notes"] else "")
                + f"OK — {len(populated)} financial fields"
            )
        elif len(populated) >= 2:
            result["Extraction Status"] = "PARTIAL"
            result["Extraction Notes"]  = (
                (result["Extraction Notes"] + " | "
                 if result["Extraction Notes"] else "")
                + f"Missing ending balance — got {populated}"
            )
        else:
            result["Extraction Status"] = "PARTIAL"
            result["Extraction Notes"]  = (
                (result["Extraction Notes"] + " | "
                 if result["Extraction Notes"] else "")
                + "LP row not found or unparseable"
            )

    except Exception as e:
        result["Extraction Status"] = "PARTIAL"
        result["Extraction Notes"]  = f"Exception: {e}"
        print(f"[DEBUG CCP-AUS] EXCEPTION: {e}")
        traceback.print_exc()

    return result
