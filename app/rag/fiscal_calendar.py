"""Deterministic INR/paise currency normalizer and IST Indian Fiscal Year date parser."""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Literal
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
CENT_QUANT = Decimal("0.01")

PAISE_FIELDS: frozenset[str] = frozenset({
    "amountpaise", "basefeepaise", "listpricepaise", "peradminpaise",
    "perclientpaise", "perdepartmentpaise", "discountpaise", "taxpaise", "subtotalpaise",
})
INR_FIELDS: frozenset[str] = frozenset({
    "amount", "subtotal", "cgst", "sgst", "igst", "utgst", "discount",
    "rate", "unitprice", "total", "paidamount", "targetvalue", "actualvalue",
})

_MONTH_MAP: dict[str, int] = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}


@dataclass(frozen=True)
class NormalizedAmount:
    """Immutable monetary value converted cleanly from paise or INR without double scaling."""

    original_value: Decimal
    original_unit: Literal["paise", "INR"]
    inr_amount: Decimal
    paise_amount: int
    source_field: str
    currency: str = "INR"
    already_normalized: bool = True


@dataclass(frozen=True)
class FiscalPeriod:
    """UTC query window derived from IST calendar or Indian Financial Year (Apr-Mar)."""

    start_utc: datetime
    end_utc: datetime
    label: str
    granularity: str
    fiscal_year: str


def to_decimal(raw: Any, default: Decimal = Decimal("0")) -> Decimal:
    """Safely coerces numeric input into Decimal without binary float rounding drift."""
    if isinstance(raw, Decimal):
        return raw if raw.is_finite() else default
    if raw is None or isinstance(raw, bool):
        return default
    if isinstance(raw, NormalizedAmount):
        return raw.inr_amount
    text = str(raw).replace(",", "").strip()
    if not text:
        return default
    try:
        dec = Decimal(text)
        return dec if dec.is_finite() else default
    except InvalidOperation:
        return default


def quantize_inr(amount: Decimal) -> Decimal:
    """Rounds a rupee Decimal value deterministically to two decimal places."""
    return amount.quantize(CENT_QUANT, rounding=ROUND_HALF_UP)


def format_inr(amount: Decimal) -> str:
    """Formats an INR Decimal value with currency symbol and two decimals."""
    q = quantize_inr(amount)
    sign = "-" if q < 0 else ""
    return f"{sign}₹{abs(q):,.2f}"


def normalize_currency_amount(
    raw_value: Any,
    source_field: str = "amount",
    unit_hint: Literal["paise", "INR"] | None = None,
) -> NormalizedAmount:
    """Converts paise to INR exactly once while preserving original values and units."""
    if isinstance(raw_value, NormalizedAmount):
        return raw_value
    field_key = source_field.strip().lower()
    inferred_unit: Literal["paise", "INR"] = unit_hint or (
        "paise" if (field_key in PAISE_FIELDS or field_key.endswith("paise")) else "INR"
    )
    orig = to_decimal(raw_value)
    if inferred_unit == "paise":
        inr = quantize_inr(orig / Decimal("100"))
        paise_val = int(orig.to_integral_value(rounding=ROUND_HALF_UP))
    else:
        inr = quantize_inr(orig)
        paise_val = int((inr * Decimal("100")).to_integral_value(rounding=ROUND_HALF_UP))
    return NormalizedAmount(
        original_value=orig,
        original_unit=inferred_unit,
        inr_amount=inr,
        paise_amount=paise_val,
        source_field=source_field,
    )


def get_indian_fy_start_year(dt_ist: datetime) -> int:
    """Returns starting calendar year of the Indian Financial Year (Apr 1 - Mar 31)."""
    return dt_ist.year if dt_ist.month >= 4 else dt_ist.year - 1


def format_fy_label(start_year: int) -> str:
    """Formats an Indian FY start year into canonical FY25-26 notation."""
    return f"FY{start_year % 100:02d}-{(start_year + 1) % 100:02d}"


def _ist_bounds_to_period(
    start_ist: datetime,
    end_ist: datetime,
    label: str,
    granularity: str,
) -> FiscalPeriod:
    """Converts IST inclusive bounds into UTC FiscalPeriod for MongoDB comparisons."""
    fy = format_fy_label(get_indian_fy_start_year(start_ist))
    return FiscalPeriod(
        start_utc=start_ist.astimezone(timezone.utc),
        end_utc=end_ist.astimezone(timezone.utc),
        label=label,
        granularity=granularity,
        fiscal_year=fy,
    )


def _day_end(dt_ist: datetime) -> datetime:
    """Returns end-of-day IST timestamp (23:59:59.999999)."""
    return dt_ist.replace(hour=23, minute=59, second=59, microsecond=999999)


def _day_start(dt_ist: datetime) -> datetime:
    """Returns start-of-day IST timestamp (00:00:00)."""
    return dt_ist.replace(hour=0, minute=0, second=0, microsecond=0)


def _parse_iso_bound(raw: str, is_end: bool) -> datetime | None:
    """Parses ISO date/datetime string into IST datetime bound."""
    clean = raw.strip()
    if not clean:
        return None
    try:
        dt = datetime.fromisoformat(clean.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
        if len(clean) <= 10:
            return _day_end(dt) if is_end else _day_start(dt)
    return dt.astimezone(IST)


def _month_period(year: int, month: int, label: str) -> FiscalPeriod:
    """Builds FiscalPeriod covering one full calendar month in IST."""
    last_day = calendar.monthrange(year, month)[1]
    start_ist = datetime(year, month, 1, 0, 0, 0, tzinfo=IST)
    end_ist = datetime(year, month, last_day, 23, 59, 59, 999999, tzinfo=IST)
    return _ist_bounds_to_period(start_ist, end_ist, label, "month")


def _fy_quarter_period(fy_start_year: int, quarter: int) -> FiscalPeriod:
    """Builds Indian FY quarter window (Q1=Apr-Jun, Q2=Jul-Sep, Q3=Oct-Dec, Q4=Jan-Mar)."""
    q_starts = {1: (fy_start_year, 4), 2: (fy_start_year, 7), 3: (fy_start_year, 10), 4: (fy_start_year + 1, 1)}
    q_ends = {1: (fy_start_year, 6), 2: (fy_start_year, 9), 3: (fy_start_year, 12), 4: (fy_start_year + 1, 3)}
    sy, sm = q_starts[quarter]
    ey, em = q_ends[quarter]
    last_day = calendar.monthrange(ey, em)[1]
    s_ist = datetime(sy, sm, 1, 0, 0, 0, tzinfo=IST)
    e_ist = datetime(ey, em, last_day, 23, 59, 59, 999999, tzinfo=IST)
    return _ist_bounds_to_period(s_ist, e_ist, f"Q{quarter} {format_fy_label(fy_start_year)}", "quarter")


def _full_fy_period(fy_start_year: int) -> FiscalPeriod:
    """Builds full Indian Financial Year window (Apr 1 .. Mar 31 IST)."""
    s_ist = datetime(fy_start_year, 4, 1, 0, 0, 0, tzinfo=IST)
    e_ist = datetime(fy_start_year + 1, 3, 31, 23, 59, 59, 999999, tzinfo=IST)
    return _ist_bounds_to_period(s_ist, e_ist, format_fy_label(fy_start_year), "fiscal_year")


def _parse_relative_keyword(norm: str, now_ist: datetime) -> FiscalPeriod | None:
    """Handles relative date keywords (today, yesterday, this/last week, month, FY)."""
    cur_fy = get_indian_fy_start_year(now_ist)
    if norm in ("today", "aaj"):
        return _ist_bounds_to_period(_day_start(now_ist), _day_end(now_ist), "Today (IST)", "day")
    if norm in ("yesterday", "kal"):
        yest = now_ist - timedelta(days=1)
        return _ist_bounds_to_period(_day_start(yest), _day_end(yest), "Yesterday (IST)", "day")
    if norm in ("this_week", "this week", "current_week", "is hafte"):
        s = _day_start(now_ist - timedelta(days=now_ist.weekday()))
        return _ist_bounds_to_period(s, _day_end(s + timedelta(days=6)), "This Week (Mon-Sun IST)", "week")
    if norm in ("last_week", "last week", "previous_week", "pichle hafte"):
        s = _day_start(now_ist - timedelta(days=now_ist.weekday() + 7))
        return _ist_bounds_to_period(s, _day_end(s + timedelta(days=6)), "Last Week (Mon-Sun IST)", "week")
    if norm in ("this_month", "this month", "current_month", "is mahine"):
        return _month_period(now_ist.year, now_ist.month, now_ist.strftime("%B %Y"))
    if norm in ("last_month", "last month", "previous_month", "pichle mahine", "pichle month"):
        prev_y, prev_m = (now_ist.year - 1, 12) if now_ist.month == 1 else (now_ist.year, now_ist.month - 1)
        return _month_period(prev_y, prev_m, datetime(prev_y, prev_m, 1).strftime("%B %Y"))
    if norm in ("current_fy", "this_fy", "this fy", "current fy", "current_financial_year"):
        return _full_fy_period(cur_fy)
    if norm in ("previous_fy", "last_fy", "last fy", "previous fy", "previous_financial_year"):
        return _full_fy_period(cur_fy - 1)
    return None


def parse_fiscal_date_range(
    period_expr: str | None = None,
    start_str: str | None = None,
    end_str: str | None = None,
    now_utc: datetime | None = None,
) -> FiscalPeriod | None:
    """Parses relative periods, Indian FY quarters, month names, or ISO bounds into UTC windows."""
    ref_utc = now_utc or datetime.now(timezone.utc)
    if ref_utc.tzinfo is None:
        ref_utc = ref_utc.replace(tzinfo=timezone.utc)
    now_ist = ref_utc.astimezone(IST)
    cur_fy = get_indian_fy_start_year(now_ist)

    if start_str or end_str:
        s_ist = _parse_iso_bound(start_str, is_end=False) if start_str else _day_start(now_ist - timedelta(days=30))
        e_ist = _parse_iso_bound(end_str, is_end=True) if end_str else _day_end(now_ist)
        if s_ist and e_ist and s_ist <= e_ist:
            label = f"{s_ist.strftime('%Y-%m-%d')} to {e_ist.strftime('%Y-%m-%d')} IST"
            return _ist_bounds_to_period(s_ist, e_ist, label, "custom")
        return None

    if not period_expr:
        return None
    norm = re.sub(r"\s+", " ", period_expr.strip().lower())
    if rel := _parse_relative_keyword(norm, now_ist):
        return rel

    if q_match := re.search(r"\bq([1-4])\b(?:\s*(?:fy)?\s*(\d{2,4})(?:\s*[-_]\s*\d{2,4})?)?", norm):
        q_num = int(q_match.group(1))
        fy_yr = cur_fy if not q_match.group(2) else (int(q_match.group(2)) if len(q_match.group(2)) == 4 else 2000 + int(q_match.group(2)))
        return _fy_quarter_period(fy_yr, q_num)

    if fy_match := re.search(r"\bfy[\s_-]*(\d{2,4})(?:\s*[-_]\s*(\d{2,4}))?\b", norm):
        y_raw = int(fy_match.group(1))
        fy_yr = y_raw if y_raw >= 1900 else 2000 + y_raw
        return _full_fy_period(fy_yr)

    for token, m_num in _MONTH_MAP.items():
        if re.search(rf"\b{token}\b", norm):
            ym = re.search(r"\b(20\d{2})\b", norm)
            yr = int(ym.group(1)) if ym else (now_ist.year if m_num <= now_ist.month else now_ist.year - 1)
            return _month_period(yr, m_num, datetime(yr, m_num, 1).strftime("%B %Y"))
    return None
