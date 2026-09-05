# crescent_capital_aus.py
# ─────────────────────────────────────────────────────────────────────────────
# Crescent Capital AUS — PDF Data Extractor
#
# Supports TWO distinct PDF layouts found in the wild:
#
#   MODE A — Monthly NAV Notification
#             (CCP IV UOB, CCP V UOB, CCP IVB PAAF VI, CCP VB PAAF VI)
#             Contains: investor table with columns:
#               Total Committed Capital | Net Contributed Capital | Adj. NAV
#             Does NOT contain: beginning balance, period contributions/distributions
#             Unfunded = Committed - Net Contributed (calculated)
#
#   MODE B — Capital Account Statement
#             (CCP VII PAAF VII Primary)
#             Contains: labelled row-based roll-forward with
#               QTD | YTD | ITD columns for each line item
#             Contains: explicit Ending Unfunded Commitment
#
# Output fields (only what is reliably extractable):
#   beginning_balance_qtd / ytd / itd
#   contributions_qtd     / ytd / itd
#   distributions_qtd     / ytd / itd
#   ending_balance_qtd    / ytd / itd
#   capital_commitment
#   unfunded_commitment
# ─────────────────────────────────────────────────────────────────────────────

import re


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _is_reversed(text: str) -> bool:
    """
    Detect mirrored/reversed PDF text.
    Requires 2+ reversed markers to avoid false positives.
    """
    reversed_markers = [
        "NOITACIFITON",  # NOTIFICATION
        "SRENTRAP",      # PARTNERS
        "LATIPAC",       # CAPITAL
        "DNUF",          # FUND
        "TNEMTIMMOC",    # COMMITMENT
    ]
    hits = sum(1 for m in reversed_markers if m in text.upper())
    return hits >= 2


def _reverse_text(text: str) -> str:
    return text[::-1]


def _parse_number(raw: str) -> float | None:
    """
    Parse a single number string to float.
    Handles: 1,234,567.89 | (1,234,567) negative | -1,234,567
    Returns None if unparseable.
    """
    if raw is None:
        return None
    raw = raw.strip()
    # parenthesised negative
    if raw.startswith('(') and raw.endswith(')'):
        raw = '-' + raw[1:-1]
    raw = raw.replace(',', '').replace('$', '').strip()
    try:
        return float(raw)
    except ValueError:
        return None


def _extract_financial_numbers(text: str) -> list[float]:
    """
    Extract all financial numbers from a block of text.
    Skips: year-like integers (1990-2099), small integers < 100,
           values between -1 and 1 (percentage fragments), zeros.
    Converts parenthesised values to negatives.
    """
    # Convert (123,456.78) → -123456.78
    text = re.sub(
        r'\(([\d,]+\.?\d*)\)',
        lambda m: '-' + m.group(1),
        text
    )
    raw_nums = re.findall(r'-?[\d,]+\.?\d*', text)
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
        if 1990 <= abs_val <= 2099:       # year
            continue
        if abs_val < 1:                   # percentage fragment
            continue
        if abs_val < 100 and '.' not in n:  # page number / small int
            continue
        results.append(val)
    return results


def _detect_mode(text: str) -> str:
    """
    Determine which PDF layout we are dealing with.
    Returns 'B' for Capital Account Statement, 'A' for NAV Notification.
    """
    if re.search(
        r'CAPITAL\s+ACCOUNT\s+STATEMENT'
        r'|Beginning\s+balance.{0,60}Contributions.{0,60}(?:Distributions|Paid)',
        text, re.IGNORECASE | re.DOTALL
    ):
        return 'B'
    return 'A'


# ─────────────────────────────────────────────────────────────────────────────
# MODE A parser — Monthly NAV Notification
# ─────────────────────────────────────────────────────────────────────────────

def _parse_mode_a(work_text: str, result: dict) -> None:
    """
    NAV Notification layout.
    Columns: Total Committed Capital | Net Contributed Capital | Adj. NAV

    What we CAN reliably extract:
      - capital_commitment     = Total Fund Committed Capital (investor row)
      - contributions_itd      = Net Contributed Capital      (investor row)
      - ending_balance_qtd/ytd = Adj. Net Asset Value         (investor row)
      - unfunded_commitment    = Committed - Contributed       (calculated)

    What we CANNOT extract (data not in this PDF type):
      - beginning_balance  → None  (not present)
      - contributions_qtd/ytd → None  (only ITD/cumulative available)
      - distributions      → None  (not present)

    The investor row is identified by searching for the known investor
    keyword (PAAF, PAPEF, Portfolio Advisors, CAB, etc.)
    """

    lines = work_text.split('\n')

    # ── Find the investor row ─────────────────────────────────────
    investor_pattern = re.compile(
        r'(?:PAAF|PAPEF|PAREF|Portfolio\s+Advisors'
        r'|PA\s+PALACE|UOB\s+Portfolio|CAB)',
        re.IGNORECASE
    )

    investor_row_text = None
    for i, line in enumerate(lines):
        if investor_pattern.search(line):
            # Grab this line + next 3 (values sometimes wrap)
            combined = ' '.join(lines[i:i + 4])
            nums = _extract_financial_numbers(combined)
            if len(nums) >= 2:
                investor_row_text = combined
                print(f"[DEBUG CCP-AUS][ModeA] investor row found at line {i}: "
                      f"{repr(line[:80])}")
                break

    if investor_row_text is None:
        result["Extraction Notes"] += " | ModeA: investor row not found"
        print("[DEBUG CCP-AUS][ModeA] investor row NOT found")
        return

    nums = _extract_financial_numbers(investor_row_text)
    print(f"[DEBUG CCP-AUS][ModeA] parsed nums = {nums}")

    # ── Column mapping for NAV Notification ──────────────────────
    # Expected: [Total Committed, Net Contributed, Adj.NAV]
    # Some rows also contain a 4th value (the NAV repeated for aggregate).
    # We only trust the first 3 meaningful values.
    #
    # Validation: Net Contributed <= Total Committed
    #             Adj.NAV is typically << Net Contributed (it's income only)
    #             Unfunded = Committed - Contributed  (must be >= 0)

    if len(nums) >= 3:
        committed   = nums[0]
        contributed = nums[1]
        nav         = nums[2]

        # Sanity: contributed should be <= committed
        # NAV should be much smaller than contributed (it's just income/gains)
        if contributed <= committed and nav < contributed:
            result["capital_commitment"]  = committed
            result["contributions_itd"]   = contributed
            result["ending_balance_qtd"]  = nav
            result["ending_balance_ytd"]  = nav
            result["ending_balance_itd"]  = nav

            # Calculate unfunded
            unfunded = committed - contributed
            result["unfunded_commitment"] = unfunded if unfunded >= 0 else 0

            print(f"[DEBUG CCP-AUS][ModeA] committed   = {committed:,.2f}")
            print(f"[DEBUG CCP-AUS][ModeA] contributed = {contributed:,.2f}")
            print(f"[DEBUG CCP-AUS][ModeA] nav         = {nav:,.2f}")
            print(f"[DEBUG CCP-AUS][ModeA] unfunded    = {unfunded:,.2f}")

            result["Extraction Notes"] += (
                " | ModeA: NAV Notification — ending NAV + ITD contributions "
                "extracted. Beginning balance and period distributions not "
                "available in this PDF type."
            )
        else:
            # Values failed sanity — flag it clearly
            result["Extraction Notes"] += (
                f" | ModeA: sanity check FAILED on nums={nums[:3]} — "
                f"check column alignment"
            )
            print(f"[DEBUG CCP-AUS][ModeA] SANITY FAIL: {nums[:3]}")

    elif len(nums) == 2:
        # Only committed + NAV, no contributed
        result["capital_commitment"] = nums[0]
        result["ending_balance_qtd"] = nums[1]
        result["ending_balance_ytd"] = nums[1]
        result["Extraction Notes"] += (
            " | ModeA: only 2 values found — committed + NAV only"
        )
    else:
        result["Extraction Notes"] += (
            f" | ModeA: insufficient numbers found ({len(nums)})"
        )


# ─────────────────────────────────────────────────────────────────────────────
# MODE B parser — Capital Account Statement
# ─────────────────────────────────────────────────────────────────────────────

def _parse_mode_b(work_text: str, result: dict) -> None:
    """
    Capital Account Statement layout (e.g. CCP VII).
    Rows are LABELLED and values appear in 3 columns: QTD | YTD | ITD.

    Layout example (CCP VII PAAF VII Primary):
      Beginning balance   3,869,917.34   1,772,937.24   0.00
      Contributions           0.00       1,495,125.00   3,503,250.00
      (Distributions)         0.00        (153,900.00)  (596,700.00)
      Ending NAV          5,071,249.89   5,071,249.89   5,071,249.89
      Ending Unfunded     3,657,055.85   3,657,055.85   3,657,055.85
      Total Commitment    6,750,000.00

    Strategy: search for each labelled row by regex, then extract
    exactly 3 numbers (QTD, YTD, ITD) from that row's text.
    """

    def _extract_row_values(pattern: str) -> list[float] | None:
        """
        Find a labelled row matching `pattern`, extract up to 3 numbers.
        Returns list of floats [qtd, ytd, itd] or None if not found.
        """
        m = re.search(pattern, work_text, re.IGNORECASE)
        if not m:
            return None
        # Take text from the match position to end of that segment (~200 chars)
        segment = work_text[m.start(): m.start() + 200]
        # Remove the label text itself to avoid label words being parsed
        segment = segment[len(m.group(0)):]
        nums = _extract_financial_numbers(segment)
        # Cap at 3 (QTD, YTD, ITD)
        return nums[:3] if nums else None

    # ── Beginning Balance ─────────────────────────────────────────
    bb = _extract_row_values(r'Beginning\s+balance')
    if bb:
        print(f"[DEBUG CCP-AUS][ModeB] beginning_balance = {bb}")
        if len(bb) >= 1:
            result["beginning_balance_qtd"] = bb[0]
        if len(bb) >= 2:
            result["beginning_balance_ytd"] = bb[1]
        if len(bb) >= 3:
            result["beginning_balance_itd"] = bb[2]
    else:
        print("[DEBUG CCP-AUS][ModeB] beginning_balance NOT found")

    # ── Contributions ─────────────────────────────────────────────
    contrib = _extract_row_values(
        r'(?:^|\n)\s*Contributions?\b'
    )
    if contrib:
        print(f"[DEBUG CCP-AUS][ModeB] contributions = {contrib}")
        if len(contrib) >= 1:
            result["contributions_qtd"] = contrib[0]
        if len(contrib) >= 2:
            result["contributions_ytd"] = contrib[1]
        if len(contrib) >= 3:
            result["contributions_itd"] = contrib[2]
    else:
        print("[DEBUG CCP-AUS][ModeB] contributions NOT found")

    # ── Distributions ─────────────────────────────────────────────
    distrib = _extract_row_values(
        r'\(?\s*Distributions?\s+(?:Paid|Payable|Paid/Payable)\s*\)?'
        r'|\bDistributions?\b'
    )
    if distrib:
        print(f"[DEBUG CCP-AUS][ModeB] distributions = {distrib}")
        # Distributions are stored as negative — ensure sign is correct
        if len(distrib) >= 1:
            result["distributions_qtd"] = distrib[0]
        if len(distrib) >= 2:
            result["distributions_ytd"] = distrib[1]
        if len(distrib) >= 3:
            result["distributions_itd"] = distrib[2]
    else:
        print("[DEBUG CCP-AUS][ModeB] distributions NOT found")

    # ── Ending Balance (NAV) ──────────────────────────────────────
    ending = _extract_row_values(
        r'Ending\s+(?:NAV|Partners[\'']?\s+Capital|Balance|Capital)'
    )
    if ending:
        print(f"[DEBUG CCP-AUS][ModeB] ending_balance = {ending}")
        if len(ending) >= 1:
            result["ending_balance_qtd"] = ending[0]
        if len(ending) >= 2:
            result["ending_balance_ytd"] = ending[1]
        if len(ending) >= 3:
            result["ending_balance_itd"] = ending[2]
    else:
        print("[DEBUG CCP-AUS][ModeB] ending_balance NOT found")

    # ── Unfunded Commitment ───────────────────────────────────────
    # Use "Ending Unfunded Commitment" row (most reliable)
    unfunded = _extract_row_values(r'Ending\s+Unfunded\s+Commitment')
    if unfunded:
        print(f"[DEBUG CCP-AUS][ModeB] unfunded = {unfunded}")
        # All 3 columns should be the same ending unfunded value
        result["unfunded_commitment"] = unfunded[0]
    else:
        # Fallback: look for standalone "Ending Unfunded Commitment" value
        uf_m = re.search(
            r'Ending\s+Unfunded\s+Commitment\s+([\d,]+\.?\d*)',
            work_text, re.IGNORECASE
        )
        if uf_m:
            result["unfunded_commitment"] = _parse_number(uf_m.group(1))
        else:
            print("[DEBUG CCP-AUS][ModeB] unfunded_commitment NOT found")

    # ── Total Commitment ──────────────────────────────────────────
    tc_m = re.search(
        r'Total\s+Commitment\s*[:\s]+([\d,]+\.?\d*)',
        work_text, re.IGNORECASE
    )
    if tc_m:
        result["capital_commitment"] = _parse_number(tc_m.group(1))
        print(f"[DEBUG CCP-AUS][ModeB] capital_commitment = "
              f"{result['capital_commitment']}")

    # ── Validation: beginning + contributions - distributions ≈ ending ──
    _validate_roll_forward(result)

    result["Extraction Notes"] += (
        " | ModeB: Capital Account Statement — QTD/YTD/ITD rows parsed "
        "by label."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Roll-forward validation
# ─────────────────────────────────────────────────────────────────────────────

def _validate_roll_forward(result: dict) -> None:
    """
    For each period (QTD, YTD, ITD), check:
        Beginning + Contributions + Distributions ≈ Ending
    (Distributions are already stored as negative values.)
    Tolerance = 1% of ending balance or $500, whichever is larger.
    Writes result to result["balance_check_qtd/ytd/itd"].
    """
    for period in ("qtd", "ytd", "itd"):
        ob    = result.get(f"beginning_balance_{period}")
        eb    = result.get(f"ending_balance_{period}")
        con   = result.get(f"contributions_{period}") or 0.0
        dis   = result.get(f"distributions_{period}") or 0.0

        if ob is None or eb is None:
            result[f"balance_check_{period}"] = "SKIPPED — missing fields"
            continue

        # distributions are stored negative so we just add
        calculated = ob + con + dis
        tolerance  = max(abs(eb) * 0.01, 500)

        if abs(calculated - eb) <= tolerance:
            result[f"balance_check_{period}"] = (
                f"PASS ✅ (calc={calculated:,.0f} | actual={eb:,.0f})"
            )
        else:
            result[f"balance_check_{period}"] = (
                f"FAIL ❌ (calc={calculated:,.0f} | actual={eb:,.0f} "
                f"| diff={calculated - eb:,.0f})"
            )
        print(f"[DEBUG CCP-AUS] balance_check_{period} = "
              f"{result[f'balance_check_{period}']}")


# ─────────────────────────────────────────────────────────────────────────────
# Main extract function
# ─────────────────────────────────────────────────────────────────────────────

def extract(full_text: str, filename: str) -> dict:

    # ── Base result dict — only fields we actually extract ────────
    result = {
        # Meta
        "Extraction Status":        "PARTIAL",
        "Extraction Notes":         "",
        "Source File":              filename,
        "template_used":            "Crescent_Capital_AUS",
        "pdf_mode":                 None,       # 'A' or 'B'
        "currency":                 "AUD",
        "fund_name":                None,
        "investor_name":            None,
        "report_date":              None,

        # Commitment
        "capital_commitment":       None,
        "unfunded_commitment":      None,

        # Beginning balance
        "beginning_balance_qtd":    None,
        "beginning_balance_ytd":    None,
        "beginning_balance_itd":    None,

        # Contributions
        "contributions_qtd":        None,
        "contributions_ytd":        None,
        "contributions_itd":        None,

        # Distributions (stored as negative)
        "distributions_qtd":        None,
        "distributions_ytd":        None,
        "distributions_itd":        None,

        # Ending balance
        "ending_balance_qtd":       None,
        "ending_balance_ytd":       None,
        "ending_balance_itd":       None,

        # Validation
        "balance_check_qtd":        None,
        "balance_check_ytd":        None,
        "balance_check_itd":        None,
    }

    try:
        # ── Step 1: Detect and fix reversed text ──────────────────
        work_text = full_text
        is_rev = _is_reversed(full_text)
        print(f"[DEBUG CCP-AUS] file        = {filename}")
        print(f"[DEBUG CCP-AUS] is_reversed = {is_rev}")

        if is_rev:
            work_text = _reverse_text(full_text)
            result["Extraction Notes"] = "Reversed PDF — text corrected"

        # ── Step 2: Fund name ──────────────────────────────────────
        fn_m = re.search(
            r'(CRESCENT\s+CAPITAL\s+PARTNERS\s+[^\n]{2,60})',
            work_text, re.IGNORECASE
        )
        result["fund_name"] = (
            re.sub(r'\s+', ' ', fn_m.group(1).strip())[:100]
            if fn_m
            else filename.replace('.pdf', '').replace('_', ' ').strip()[:80]
        )
        print(f"[DEBUG CCP-AUS] fund_name   = {result['fund_name']}")

        # ── Step 3: Investor name ──────────────────────────────────
        inv_m = re.search(
            r'((?:PAAF|PAPEF|PAREF|Portfolio\s+Advisors'
            r'|UOB\s+Portfolio|PA\s+PALACE|CAB)[^\n,]{0,80})',
            work_text, re.IGNORECASE
        )
        if inv_m:
            result["investor_name"] = (
                re.sub(r'\s+', ' ', inv_m.group(1).strip())[:150]
            )
        print(f"[DEBUG CCP-AUS] investor    = {result['investor_name']}")

        # ── Step 4: Report date ────────────────────────────────────
        date_patterns = [
            r'(\d{1,2}[-\s](?:Jan|Feb|Mar|Apr|May|Jun|'
            r'Jul|Aug|Sep|Oct|Nov|Dec)[-\s]\d{2,4})',
            r'((?:January|February|March|April|May|June|July|August|'
            r'September|October|November|December)\s+\d{1,2},?\s+\d{4})',
            r'(\d{1,2}\s+(?:January|February|March|April|May|June|July|'
            r'August|September|October|November|December)\s+\d{4})',
        ]
        for dp in date_patterns:
            dm = re.search(dp, work_text, re.IGNORECASE)
            if dm:
                result["report_date"] = re.sub(
                    r'\s+', ' ', dm.group(1).strip()
                )
                break
        print(f"[DEBUG CCP-AUS] report_date = {result['report_date']}")

        # ── Step 5: Detect PDF mode ────────────────────────────────
        mode = _detect_mode(work_text)
        result["pdf_mode"] = mode
        print(f"[DEBUG CCP-AUS] pdf_mode    = {mode}")

        # ── Step 6: Parse by mode ──────────────────────────────────
        if mode == 'B':
            _parse_mode_b(work_text, result)
        else:
            _parse_mode_a(work_text, result)

        # ── Step 7: Final status ───────────────────────────────────
        # SUCCESS = we have at minimum an ending balance
        # PARTIAL = we found something but ending balance is missing
        # FAILED  = nothing useful extracted
        has_ending  = result["ending_balance_ytd"] is not None or \
                      result["ending_balance_qtd"] is not None
        has_contrib = result["contributions_ytd"]  is not None or \
                      result["contributions_itd"]  is not None

        if has_ending and result["fund_name"]:
            result["Extraction Status"] = "SUCCESS"
        elif has_ending or has_contrib:
            result["Extraction Status"] = "PARTIAL"
        else:
            result["Extraction Status"] = "FAILED"

        print(f"[DEBUG CCP-AUS] status      = {result['Extraction Status']}")

    except Exception as exc:
        result["Extraction Status"] = "ERROR"
        result["Extraction Notes"] += f" | EXCEPTION: {exc}"
        print(f"[DEBUG CCP-AUS] EXCEPTION: {exc}")

    return result
