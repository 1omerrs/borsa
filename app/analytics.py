"""Daily change, weekly window, gram gold, and alert rules."""

from __future__ import annotations

from datetime import date, timedelta

TROY_OUNCE_GRAMS = 31.1034768

MONTHS = (
    "Ocak",
    "Şubat",
    "Mart",
    "Nisan",
    "Mayıs",
    "Haziran",
    "Temmuz",
    "Ağustos",
    "Eylül",
    "Ekim",
    "Kasım",
    "Aralık",
)

REAL_SOURCES = {"yahoo", "stooq", "frankfurter", "n8n", "hesaplanan"}


def daily_change(price: float, previous_close: float) -> tuple[float, float]:
    change = price - previous_close
    if not previous_close:
        return change, 0.0
    return change, (change / previous_close) * 100.0


def percent_change(start: float | None, end: float | None) -> float | None:
    if start in (None, 0) or end is None:
        return None
    return ((end - start) / start) * 100.0


def gram_altin_try(ons_usd: float | None, usdtry: float | None) -> float | None:
    """Approximate spot gram gold in TRY. Not a jeweler retail price."""
    if not ons_usd or not usdtry:
        return None
    return ons_usd / TROY_OUNCE_GRAMS * usdtry


def price_on_or_before(series: list[tuple[date, float]], days: int) -> float | None:
    if len(series) < 2:
        return None
    target = series[-1][0] - timedelta(days=days)
    candidate = None
    for day, close in series:
        if day <= target:
            candidate = close
        else:
            break
    return candidate


def condition_met(condition: str, price: float, change_percent: float, threshold: float) -> bool:
    if condition == "above":
        return price >= threshold
    if condition == "below":
        return price <= threshold
    if condition == "pct_up":
        return change_percent >= threshold
    if condition == "pct_down":
        return change_percent <= -abs(threshold)
    return False


def should_trigger(enabled: bool, last_on: str | None, today: str, met: bool) -> bool:
    return bool(enabled) and met and last_on != today


def tr_date(day: date) -> str:
    return f"{day.day} {MONTHS[day.month - 1]} {day.year}"


def tr_number(value: float, digits: int = 2) -> str:
    sign = "-" if value < 0 else ""
    raw = f"{abs(value):,.{digits}f}"
    return sign + raw.replace(",", "X").replace(".", ",").replace("X", ".")


def week_key(day: date) -> str:
    year, week, _weekday = day.isocalendar()
    return f"{year}-W{week:02d}"


def round_or_none(value: float | None, digits: int = 6) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)
