"""Daily and weekly report payloads."""

from __future__ import annotations

from datetime import date

from app.analytics import tr_date, tr_number, week_key


def _brief(row: dict | None) -> dict | None:
    if not row:
        return None
    return {
        "symbol": row["symbol"],
        "name": row["name"],
        "change_percent": row.get("change_percent"),
        "price": row.get("price"),
    }


def _public_row(row: dict) -> dict:
    return {
        "symbol": row["symbol"],
        "name": row["name"],
        "category": row.get("category"),
        "currency": row.get("currency"),
        "price": row.get("price"),
        "previous_close": row.get("previous_close"),
        "change_amount": row.get("change_amount"),
        "change_percent": row.get("change_percent"),
        "decimals": row.get("decimals", 2),
    }


def compose_report(period: str, today: date, rows: list[dict]) -> dict:
    usable = [row for row in rows if row.get("change_percent") is not None and row.get("price") is not None]
    ordered = sorted(usable, key=lambda row: row["change_percent"], reverse=True)
    advancers = sum(1 for row in usable if row["change_percent"] > 0.0001)
    decliners = sum(1 for row in usable if row["change_percent"] < -0.0001)
    unchanged = len(usable) - advancers - decliners
    average = sum(row["change_percent"] for row in usable) / len(usable) if usable else None
    leader = ordered[0] if ordered else None
    laggard = ordered[-1] if ordered else None
    if period == "weekly":
        title = f"{week_key(today)} haftalık rapor"
        period_key = week_key(today)
        when = "Bu hafta"
    else:
        title = f"{tr_date(today)} günlük rapor"
        period_key = today.isoformat()
        when = "Bugün"
    if leader and laggard:
        summary = (
            f"{when} en güçlü yükseliş {leader['name']} ({tr_number(leader['change_percent'])}%). "
            f"En sert düşüş {laggard['name']} ({tr_number(laggard['change_percent'])}%). "
            f"{advancers} varlık yükseldi, {decliners} varlık geriledi."
        )
    else:
        summary = "Karşılaştırılacak yeterli fiyat henüz yok."
    table = sorted(
        rows,
        key=lambda row: (row.get("change_percent") is None, -(row.get("change_percent") or 0)),
    )
    return {
        "period": "weekly" if period == "weekly" else "daily",
        "period_key": period_key,
        "title": title,
        "range_label": period_key if period == "weekly" else tr_date(today),
        "summary": summary,
        "advancers": advancers,
        "decliners": decliners,
        "unchanged": unchanged,
        "average_change_percent": round(average, 4) if average is not None else None,
        "leader": _brief(leader),
        "laggard": _brief(laggard),
        "rows": [_public_row(row) for row in table],
    }
