# templates/abacus.py
"""
Abacus Multi-Family Partners handler.
Covers:
  - Abacus Multi-Family Partners IV, LP  — PAREF VII (existing)
  - Abacus Multi-Family Partners IV, LP  — PAREF VIII (new Unknown)
  - Abacus Multi-Family Partners V, LP   — PAREF VII (existing)

All variants use the same Revolution-style capital account
rollforward format:
  "As of December 31, 20XX"
  Beginning balance / Contributions / Distributions /
  Net investment income / Realized / Unrealized / Ending

Investor patterns seen across variants:
  PAREF VII  → no explicit investor name line (header only)
  PAREF VIII → "PAREF VIII Secondaries Holding Vehicle"

The itd column is not present in these statements —
only QTD/YTD columns appear.
"""

import re
from datetime import datetime


# ══════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════

def _parse_number(value: str):
    """Convert '1,234,567.89' or '(1,234)' or '-1234' to float."""
    if not value:
        return None
    value = str(value).strip().replace(",", "").replace("$", "")
    value = re.sub(r'[^\d.()\-]', '', value)
    negative = value.startswith("(") or value.startswith("-")
    value = (
        value.replace("(", "")
             .replace(")", "")
             .replace("-", "")
             .strip()
    )
    try:
        result = float(value)
        return -result if negative else result
    except ValueError:
        return None


def _get_report_years(full_text: str):
    """
    Dynamically detect report year and prior year.
    No hardcoded years — works for any future year.
    """
    years_found = re.findall(r'\b(20[1-3]\d)\b', full_text)
    if years_found:
        year_counts = {}
        for y in years_found:
            year_counts[y] = year_counts.get(y, 0) + 1
        current_cal_year = datetime.now().year
        valid_years = {
            int(y): c for y, c in year_counts.items()
            if int(y) <= current_cal_year
        }
        if valid_years:
            report_year = max(
                valid_years,
                key=lambda y: (valid_years[y], y)
            )
            return report_year, report_year - 1
    current = datetime.now().year
    return current, current - 1


def _get_2col(text: str, label_pattern: str):
    """
    Extract up to 2 numeric columns (QTD | YTD) for a label.
    Handles parenthesised negatives and dash placeholders.
    Returns (col1, col2) as floats or None.
    """
    m = re.search(
        label_pattern
        + r"[^\n]{0,60}\n?\s*"
          r"(\([\d,]+\.?\d*\)|[\d,]+\.?\d*|[-−])"
          r"(?:\s+(\([\d,]+\.?\d*\)|[\d,]+\.?\d*|[-−]))?",
        text, re.IGNORECASE
    )
    if m:
        col1 = (
            _parse_number(m.group(1))
            if m.group(1) not in ("-", "−") else None
        )
        col2 = (
            _parse_number(m.group(2))
            if m.group(2) and
               m.group(2) not in ("-", "−") else None
        )
        return col1, col2
    return None, None


# ══════════════════════════════════════════════════════════════
# MAIN EXTRACT FUNCTION
# ══════════════════════════════════════════════════════════════

def extract(full_text: str, filename: str) -> dict:
    result = {
        "Extraction Status":            "PARTIAL",
        "Extraction Notes":             "",
        "Source File":                  filename,
        "template_used":                "Abacus",
        "currency":                     "USD",
        "fund_name":                    "",
        "investor_name":                "",
        "report_date":                  "",
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
        "twr_qtd":                      None,
        "twr_ytd":                      None,
    }

    try:
        report_year, prior_year = _get_report_years(full_text)

        # ── Fund name ──────────────────────────────────────────
        fn_m = re.search(
            r"(Abacus Multi-?Family Partners\s+"
            r"(?:IV|V|VI)[^\n,]{0,30})",
            full_text, re.IGNORECASE
        )
        result["fund_name"] = (
            fn_m.group(1).strip()[:100]
            if fn_m
            else "Abacus Multi-Family Partners"
        )

        # ── Investor name ──────────────────────────────────────
        # PAREF VII variant — no explicit line, derive from
        # filename context or header
        inv_m = re.search(
            r"((?:PAREF|PAPEF|PAAF)"
            r"[^\n,\s]{0,10}"
            r"[^\n,]{0,80})",
            full_text, re.IGNORECASE
        )
        if not inv_m:
            inv_m = re.search(
                r"(Portfolio Advisors[^\n,]{5,80})",
                full_text, re.IGNORECASE
            )
        if inv_m:
            result["investor_name"] = (
                re.sub(r'\s+', ' ',
                       inv_m.group(1).strip())[:150]
            )

        # ── Report date ────────────────────────────────────────
        date_m = re.search(
            r"(?:As\s+of\s+)?"
            r"((?:December|Dec)\.?\s+3[01],?\s*"
            + str(report_year)
            + r")",
            full_text, re.IGNORECASE
        )
        result["report_date"] = (
            date_m.group(1).strip() if date_m
            else f"December 31, {report_year}"
        )

        # ── Capital commitment ─────────────────────────────────
        commit_m = re.search(
            r"(?:Capital\s+)?Commitment[s]?"
            r"[^\d\n]{0,20}\$?\s*([\d,]+\.?\d*)",
            full_text, re.IGNORECASE
        )
        if commit_m:
            val = _parse_number(commit_m.group(1))
            if val and val > 100:
                result["capital_commitment"] = val

        # ══════════════════════════════════════════════════════
        # BEGINNING BALANCE
        # Revolution-style: value appears on same line or
        # immediately after label.
        # Labels seen: "Beginning balance", "Balance at
        # beginning of period", "Beginning capital"
        # ══════════════════════════════════════════════════════
        beg_col1, beg_col2 = _get_2col(
            full_text,
            r"Beginning\s+(?:balance|capital"
            r"|partners['\u2019]?\s*capital)"
        )
        if beg_col1 is not None:
            result["beginning_balance_qtd"] = beg_col1
            result["beginning_balance_ytd"] = (
                beg_col2 if beg_col2 is not None
                else beg_col1
            )

        # ══════════════════════════════════════════════════════
        # CONTRIBUTIONS
        # ══════════════════════════════════════════════════════
        cont_col1, cont_col2 = _get_2col(
            full_text,
            r"(?:Capital\s+)?[Cc]ontributions?"
        )
        if cont_col1 is not None:
            result["contributions_qtd"] = cont_col1
            result["contributions_ytd"] = (
                cont_col2 if cont_col2 is not None
                else cont_col1
            )

        # ══════════════════════════════════════════════════════
        # DISTRIBUTIONS
        # ══════════════════════════════════════════════════════
        dist_col1, dist_col2 = _get_2col(
            full_text,
            r"(?:Cash\s+)?[Dd]istributions?"
        )
        if dist_col1 is not None:
            result["distributions_qtd"] = dist_col1
            result["distributions_ytd"] = (
                dist_col2 if dist_col2 is not None
                else dist_col1
            )

        # ══════════════════════════════════════════════════════
        # NET INVESTMENT INCOME
        # ══════════════════════════════════════════════════════
        nii_col1, nii_col2 = _get_2col(
            full_text,
            r"Net\s+investment\s+(?:income|loss)"
        )
        if nii_col1 is not None:
            result["net_investment_income_qtd"] = nii_col1
            result["net_investment_income_ytd"] = (
                nii_col2 if nii_col2 is not None
                else nii_col1
            )

        # ══════════════════════════════════════════════════════
        # NET REALIZED GAIN / LOSS
        # ══════════════════════════════════════════════════════
        real_col1, real_col2 = _get_2col(
            full_text,
            r"Net\s+realized\s+(?:gain|loss)"
        )
        if real_col1 is not None:
            result["net_realized_gain_loss_qtd"] = real_col1
            result["net_realized_gain_loss_ytd"] = (
                real_col2 if real_col2 is not None
                else real_col1
            )

        # ══════════════════════════════════════════════════════
        # NET UNREALIZED GAIN / LOSS
        # ══════════════════════════════════════════════════════
        unreal_col1, unreal_col2 = _get_2col(
            full_text,
            r"Net\s+(?:change\s+in\s+)?unrealized"
        )
        if unreal_col1 is not None:
            result["net_unrealized_gain_loss_qtd"] = unreal_col1
            result["net_unrealized_gain_loss_ytd"] = (
                unreal_col2 if unreal_col2 is not None
                else unreal_col1
            )

        # ══════════════════════════════════════════════════════
        # MANAGEMENT FEES
        # ══════════════════════════════════════════════════════
        mgmt_col1, mgmt_col2 = _get_2col(
            full_text,
            r"(?:Investment\s+)?[Mm]anagement\s+[Ff]ee"
        )
        if mgmt_col1 is not None:
            result["management_fees_qtd"] = mgmt_col1
            result["management_fees_ytd"] = (
                mgmt_col2 if mgmt_col2 is not None
                else mgmt_col1
            )

        # ══════════════════════════════════════════════════════
        # ENDING BALANCE — attempt 1
        # "Ending balance   ($3,159,576)"
        # or "Ending partners' capital   $X"
        # Revolution-style: value on same line as label
        # ══════════════════════════════════════════════════════
        end_col1, end_col2 = _get_2col(
            full_text,
            r"Ending\s+(?:balance|capital"
            r"|partners['\u2019]?\s*capital)"
        )
        if end_col1 is not None:
            result["ending_balance_qtd"] = end_col1
            result["ending_balance_ytd"] = (
                end_col2 if end_col2 is not None
                else end_col1
            )

        # ══════════════════════════════════════════════════════
        # ENDING BALANCE — attempt 2
        # "Partners' capital, end of period   $X"
        # ══════════════════════════════════════════════════════
        if result["ending_balance_ytd"] is None:
            end_m2 = re.search(
                r"(?:Partners['\u2019]?\s*capital"
                r"|Members['\u2019]?\s*equity)"
                r"[,\s]+end\s+of\s+(?:period|year)"
                r"[^\d\n]*"
                r"(\([\d,]+\.?\d*\)|[\d,]+\.?\d*)",
                full_text, re.IGNORECASE
            )
            if end_m2:
                val = _parse_number(end_m2.group(1))
                result["ending_balance_qtd"] = val
                result["ending_balance_ytd"] = val

        # ══════════════════════════════════════════════════════
        # ENDING BALANCE — attempt 3
        # "Total partners' capital  $X" — fallback for
        # PAREF VIII variant where label may differ
        # ══════════════════════════════════════════════════════
        if result["ending_balance_ytd"] is None:
            tot_m = re.search(
                r"Total\s+(?:partners['\u2019]?\s*capital"
                r"|members['\u2019]?\s*equity)"
                r"[^\d\n]*"
                r"(\([\d,]+\.?\d*\)|[\d,]+\.?\d*)",
                full_text, re.IGNORECASE
            )
            if tot_m:
                val = _parse_number(tot_m.group(1))
                result["ending_balance_qtd"] = val
                result["ending_balance_ytd"] = val

        # ══════════════════════════════════════════════════════
        # ENDING BALANCE — attempt 4
        # "Net Asset Value as of [date]  $995,914"
        # Abacus Multi-Family Partners IV — PAREF VII variant
        # Three columns present: QTD / YTD / ITD (all same value)
        # ══════════════════════════════════════════════════════
        if result["ending_balance_ytd"] is None:
            nav_m = re.search(
                r"Net\s+Asset\s+Value\s+as\s+of"
                r"[^\d\n]*"
                r"(\([\d,]+\.?\d*\)|[\d,]+\.?\d*)",
                full_text, re.IGNORECASE
            )
            if nav_m:
                val = _parse_number(nav_m.group(1))
                result["ending_balance_qtd"] = val
                result["ending_balance_ytd"] = val
                result["ending_balance_itd"] = val

        # ── Final status ───────────────────────────────────────
        if result["ending_balance_ytd"] is not None:
            result["Extraction Status"] = "SUCCESS"
        else:
            result["Extraction Status"] = "PARTIAL"
            result["Extraction Notes"] = "Missing ending balance"

    except Exception as e:
        result["Extraction Status"] = "PARTIAL"
        result["Extraction Notes"] = f"Exception: {e}"

    return result
