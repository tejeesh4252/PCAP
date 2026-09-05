import re


def extract(text, filename=""):
    """
    Handler for Abenex VI Capital Account statements.
    Supports both Cash Presentation and P&L Presentation pages.
    Extracts Grand Total row values dynamically.
    """

    result = {}

    # ------------------------------------------------------------------ #
    # SECTION 1 — Identify fund name dynamically
    # ------------------------------------------------------------------ #
    fund_match = re.search(r'(abenex\s+(?:capital\s+)?[IVXLC0-9]+)', text, re.IGNORECASE)
    if fund_match:
        result["fund_name"] = fund_match.group(1).strip()
    else:
        result["fund_name"] = "Abenex"

    # ------------------------------------------------------------------ #
    # SECTION 2 — Cash Presentation Grand Total
    # Columns: Total Commitment | Calls net of Temp | Recallable Contrib |
    #           D=B+C | Distributions | Holdings liquidative value | Callable
    # Grand Total line pattern example:
    # "Grand Total na na 570 505100 441 000442 ..."
    # ------------------------------------------------------------------ #
    cash_grand_total = re.search(
        r'Grand\s+Total\s+na\s+na\s+'
        r'([\d,]+(?:\s[\d,]+)*)\s+'   # Total Commitment
        r'([\d,]+(?:\s[\d,]+)*)\s+'   # Capital calls net (B)
        r'([\d,]+(?:\s[\d,]+)*)\s+'   # Recallable contrib (C)
        r'\(?([\d,]+(?:\s[\d,]+)*)\)?\s+'  # Distributions (E)
        r'([\d,]+(?:\s[\d,]+)*)\s+'   # Holdings liquidative value (F)
        r'([\d,]+(?:\s[\d,]+)*)',      # Callable Contribution (A-B)
        text,
        re.IGNORECASE
    )

    if cash_grand_total:
        def clean(val):
            return val.replace(" ", "").replace(",", "")

        result["cash_total_commitment"]        = clean(cash_grand_total.group(1))
        result["cash_capital_calls_net"]       = clean(cash_grand_total.group(2))
        result["cash_recallable_contribution"] = clean(cash_grand_total.group(3))
        result["cash_distributions"]           = "-" + clean(cash_grand_total.group(4))
        result["cash_holdings_liquidative"]    = clean(cash_grand_total.group(5))
        result["cash_callable_contribution"]   = clean(cash_grand_total.group(6))

    # ------------------------------------------------------------------ #
    # SECTION 3 — P&L Presentation Grand Total
    # Columns: Contribution | Mgmt Fees | Aborted Fees | Other Costs |
    #           Realised G/L | Unrealised G/L | Carried Interest |
    #           Distributions | Holdings liquidative value
    # ------------------------------------------------------------------ #
    pl_grand_total = re.search(
        r'Grand\s+Total\s+'
        r'([\d,]+(?:\s[\d,]+)*)\s+'        # A - Contribution
        r'\(?([\d,]+(?:\s[\d,]+)*)\)?\s+'  # B - Mgmt fees (negative)
        r'\(?([\d,]+(?:\s[\d,]+)*)\)?\s+'  # C - Aborted fees (negative)
        r'\(?([\d,]+(?:\s[\d,]+)*)\)?\s+'  # D - Other costs (negative)
        r'([\d,]+(?:\s[\d,]+)*)\s+'        # E - Realised gains
        r'\(?([\d,]+(?:\s[\d,]+)*)\)?\s+'  # F - Unrealised gains (negative)
        r'0\s+'                             # G - Carried Interest (= 0)
        r'\(?([\d,]+(?:\s[\d,]+)*)\)?\s+'  # H - Distributions (negative)
        r'([\d,]+(?:\s[\d,]+)*)',           # Holdings liquidative value
        text,
        re.IGNORECASE
    )

    if pl_grand_total:
        def clean(val):
            return val.replace(" ", "").replace(",", "")

        result["pl_contribution"]          = clean(pl_grand_total.group(1))
        result["pl_mgmt_fees"]             = "-" + clean(pl_grand_total.group(2))
        result["pl_aborted_fees"]          = "-" + clean(pl_grand_total.group(3))
        result["pl_other_costs"]           = "-" + clean(pl_grand_total.group(4))
        result["pl_realised_gains"]        = clean(pl_grand_total.group(5))
        result["pl_unrealised_gains"]      = "-" + clean(pl_grand_total.group(6))
        result["pl_carried_interest"]      = "0"
        result["pl_distributions"]         = "-" + clean(pl_grand_total.group(7))
        result["pl_holdings_liquidative"]  = clean(pl_grand_total.group(8))

    # ------------------------------------------------------------------ #
    # SECTION 4 — Period / Quarter detection (dynamic, no hardcoded year)
    # ------------------------------------------------------------------ #
    period_match = re.search(
        r'ABENEX\s+[IVXLC0-9]+\s+(Q[1-4][-\s]?\d{4})',
        text,
        re.IGNORECASE
    )
    if period_match:
        result["period"] = period_match.group(1).strip()

    # ------------------------------------------------------------------ #
    # SECTION 5 — Fallback: Holdings liquidative value (final NAV)
    # Used as the primary "ending balance" field for the PCAP output
    # ------------------------------------------------------------------ #
    if result.get("cash_holdings_liquidative"):
        result["ending_balance"] = result["cash_holdings_liquidative"]
    elif result.get("pl_holdings_liquidative"):
        result["ending_balance"] = result["pl_holdings_liquidative"]

    # ------------------------------------------------------------------ #
    # SECTION 6 — Status
    # ------------------------------------------------------------------ #
    if result.get("ending_balance"):
        result["Extraction Status"] = "SUCCESS"
        result["Extraction Notes"] = ""
    elif any(k in result for k in (
            "cash_holdings_liquidative",
            "pl_holdings_liquidative",
            "cash_total_commitment",
            "pl_contribution",
    )):
        result["Extraction Status"] = "PARTIAL"
        result["Extraction Notes"] = "Missing ending balance"
    else:
        result["Extraction Status"] = "PARTIAL"
        result["Extraction Notes"] = "No Grand Total matched — manual review required"

    return result
