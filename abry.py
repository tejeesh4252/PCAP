# templates/abry.py
"""
ABRY Partners template handler.
Boston-based format — QTD | YTD | ITD sections (3 separate pages).
Covers: ABRY Partners VII, VIII
Layout: Opening Equity → line items → Closing Equity
        Investor's Allocation column extracted (not Total Fund).
"""

import re


# ══════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════

def _parse_number(value: str):
    """Convert '1,234,567.89' or '(1,234)' or '-1234' to float."""
    if not value:
        return None
    value = str(value).strip().replace(",", "").replace("$", "")
    negative = value.startswith("(") or value.startswith("-")
    value = value.replace("(", "").replace(")", "").replace("-", "").strip()
    try:
        result = float(value)
        return -result if negative else result
    except ValueError:
        return None


def _get_investor_col(text: str, label_pattern: str):
    """
    Extract investor's allocation column value for a given label.
    Format: Label  TotalFundValue  InvestorValue
    The investor value is the SECOND number on the line.
    Returns float or None.
    """
    m = re.search(
        label_pattern
        + r"[^\n]*?"
        r"(-?[\d,]+\.?\d*|\([\d,]+\.?\d*\))"   # Total Fund value
        r"\s+"
        r"(-?[\d,]+\.?\d*|\([\d,]+\.?\d*\))",   # Investor value
        text, re.IGNORECASE
    )
    if m:
        return _parse_number(m.group(2))
    # Fallback: single value on line
    m2 = re.search(
        label_pattern
        + r"[^\n]*?"
        r"(-?[\d,]+\.?\d*|\([\d,]+\.?\d*\))",
        text, re.IGNORECASE
    )
    if m2:
        return _parse_number(m2.group(1))
    return None


def _isolate_period_block(full_text: str, period: str) -> str:
    """
    Isolate a specific period block (QTD/YTD/ITD).
    period: 'Quarter', 'Year', 'Inception'
    """
    patterns = {
        "Quarter": r"For the Quarter ended.*?(?=For the Year|From Inception|$)",
        "Year":    r"For the Year to.*?(?=From Inception|$)",
        "ITD":     r"From Inception to.*?(?=$)",
    }
    pat = patterns.get(period, "")
    if pat:
        m = re.search(pat, full_text, re.IGNORECASE | re.DOTALL)
        if m:
            return m.group(0)
    return full_text


# ══════════════════════════════════════════════════════════════
# MAIN EXTRACT FUNCTION
# ══════════════════════════════════════════════════════════════

def extract(full_text: str, filename: str) -> dict:

    result = {
        "Extraction Status":  "PARTIAL",
        "Extraction Notes":   "",
        "Source File":        filename,
        "template_used":      "ABRY",
        "currency":           "USD",

        # ── Identity ───────────────────────────────────────────
        "fund_name":           "",
        "investor_name":       "",
        "report_date":         "",

        # ── Commitment ─────────────────────────────────────────
        "capital_commitment":  None,
        "unfunded_commitment": None,

        # ── Beginning Balance ──────────────────────────────────
        "beginning_balance_qtd": None,
        "beginning_balance_ytd": None,

        # ── Contributions ──────────────────────────────────────
        "contributions_qtd":   None,
        "contributions_ytd":   None,
        "contributions_itd":   None,

        # ── Distributions ─────────────────────────────────────
        "distributions_qtd":   None,
        "distributions_ytd":   None,
        "distributions_itd":   None,

        # ── Net Investment Income (Dividend Income proxy) ──────
        "net_investment_income_qtd": None,
        "net_investment_income_ytd": None,
        "net_investment_income_itd": None,

        # ── Realized Gain/Loss ─────────────────────────────────
        "net_realized_gain_loss_qtd": None,
        "net_realized_gain_loss_ytd": None,
        "net_realized_gain_loss_itd": None,

        # ── Unrealized Gain/Loss ───────────────────────────────
        "net_unrealized_gain_loss_qtd": None,
        "net_unrealized_gain_loss_ytd": None,
        "net_unrealized_gain_loss_itd": None,

        # ── Management Fees ────────────────────────────────────
        "management_fees_qtd": None,
        "management_fees_ytd": None,
        "management_fees_itd": None,

        # ── Ending Balance ─────────────────────────────────────
        "ending_balance_qtd":  None,
        "ending_balance_ytd":  None,
        "ending_balance_itd":  None,
    }

    try:
        # ── Fund name ──────────────────────────────────────────
        fund_match = re.search(
            r"(ABRY Partners\s+(?:IX|VIII|VII|VI|V|IV|III|II|I)"
            r"(?:,?\s*L\.?P\.?)?)",
            full_text, re.IGNORECASE
        )
        if fund_match:
            result["fund_name"] = fund_match.group(1).strip()

        # ── Investor name ──────────────────────────────────────
        inv_match = re.search(
            r"Investor:\s*(.+?)(?:\n|$)", full_text, re.IGNORECASE
        )
        if inv_match:
            result["investor_name"] = inv_match.group(1).strip()

        # ── Report date ────────────────────────────────────────
        date_match = re.search(
            r"For the Quarter ended\s+(December \d+,\s*\d{4})",
            full_text, re.IGNORECASE
        )
        if not date_match:
            date_match = re.search(
                r"(December \d+,\s*\d{4})", full_text, re.IGNORECASE
            )
        if date_match:
            result["report_date"] = date_match.group(1).strip()

        # ── Unfunded commitment ────────────────────────────────
        unfunded_m = re.search(
            r"Remaining Commitment\s*@[^\n]*\n?\s*"
            r"\$?[\d,]+\.?\d*\s+"         # Total Fund value
            r"\$?([\d,]+\.?\d*)",          # Investor value
            full_text, re.IGNORECASE
        )
        if unfunded_m:
            result["unfunded_commitment"] = _parse_number(
                unfunded_m.group(1)
            )

        # ── Isolate period blocks ──────────────────────────────
        qtd_block = _isolate_period_block(full_text, "Quarter")
        ytd_block = _isolate_period_block(full_text, "Year")
        itd_block = _isolate_period_block(full_text, "ITD")

        # ── QTD ────────────────────────────────────────────────
        result["beginning_balance_qtd"] = _get_investor_col(
            qtd_block, r"Opening Equity"
        )
        result["contributions_qtd"] = _get_investor_col(
            qtd_block, r"Cash Contributions"
        )
        # Add in-kind if present
        contrib_kind_qtd = _get_investor_col(
            qtd_block, r"Contributions In-Kind"
        )
        if contrib_kind_qtd and result["contributions_qtd"]:
            result["contributions_qtd"] += contrib_kind_qtd
        elif contrib_kind_qtd:
            result["contributions_qtd"] = contrib_kind_qtd

        result["distributions_qtd"] = _get_investor_col(
            qtd_block, r"Cash Distributions"
        )
        result["net_investment_income_qtd"] = _get_investor_col(
            qtd_block, r"Dividend Income"
        )
        result["net_realized_gain_loss_qtd"] = _get_investor_col(
            qtd_block, r"Realized Gain[/\(]Loss\)?"
        )
        result["net_unrealized_gain_loss_qtd"] = _get_investor_col(
            qtd_block, r"Change in Unrealized"
        )
        result["management_fees_qtd"] = _get_investor_col(
            qtd_block, r"Management Fees"
        )
        result["ending_balance_qtd"] = _get_investor_col(
            qtd_block, r"Closing Equity"
        )

        # ── YTD ────────────────────────────────────────────────
        result["beginning_balance_ytd"] = _get_investor_col(
            ytd_block, r"Opening Equity"
        )
        result["contributions_ytd"] = _get_investor_col(
            ytd_block, r"Cash Contributions"
        )
        contrib_kind_ytd = _get_investor_col(
            ytd_block, r"Contributions In-Kind"
        )
        if contrib_kind_ytd and result["contributions_ytd"]:
            result["contributions_ytd"] += contrib_kind_ytd
        elif contrib_kind_ytd:
            result["contributions_ytd"] = contrib_kind_ytd

        result["distributions_ytd"] = _get_investor_col(
            ytd_block, r"Cash Distributions"
        )
        result["net_investment_income_ytd"] = _get_investor_col(
            ytd_block, r"Dividend Income"
        )
        result["net_realized_gain_loss_ytd"] = _get_investor_col(
            ytd_block, r"Realized Gain[/\(]Loss\)?"
        )
        result["net_unrealized_gain_loss_ytd"] = _get_investor_col(
            ytd_block, r"Change in Unrealized"
        )
        result["management_fees_ytd"] = _get_investor_col(
            ytd_block, r"Management Fees"
        )
        result["ending_balance_ytd"] = _get_investor_col(
            ytd_block, r"Closing Equity"
        )

        # ── ITD ────────────────────────────────────────────────
        result["contributions_itd"] = _get_investor_col(
            itd_block, r"Cash Contributions"
        )
        contrib_kind_itd = _get_investor_col(
            itd_block, r"Contributions In-Kind"
        )
        if contrib_kind_itd and result["contributions_itd"]:
            result["contributions_itd"] += contrib_kind_itd
        elif contrib_kind_itd:
            result["contributions_itd"] = contrib_kind_itd

        result["distributions_itd"] = _get_investor_col(
            itd_block, r"Cash Distributions"
        )
        result["net_investment_income_itd"] = _get_investor_col(
            itd_block, r"Dividend Income"
        )
        result["net_realized_gain_loss_itd"] = _get_investor_col(
            itd_block, r"Realized Gain[/\(]Loss\)?"
        )
        result["net_unrealized_gain_loss_itd"] = _get_investor_col(
            itd_block, r"Change in Unrealized"
        )
        result["management_fees_itd"] = _get_investor_col(
            itd_block, r"Management Fees"
        )
        result["ending_balance_itd"] = _get_investor_col(
            itd_block, r"Closing Equity"
        )

        # ── Status ─────────────────────────────────────────────
        if result["ending_balance_ytd"] is not None:
            result["Extraction Status"] = "SUCCESS"
        else:
            result["Extraction Status"] = "PARTIAL"
            result["Extraction Notes"]  = "Missing ending balance"

    except Exception as e:
        result["Extraction Status"] = "PARTIAL"
        result["Extraction Notes"]  = f"Exception: {e}"

    return result
