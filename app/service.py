"""Fetch cycle, alert evaluation, and report refresh."""

from __future__ import annotations

import logging
import math
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta

from apscheduler.schedulers.background import BackgroundScheduler

from app import db, notify
from app.analytics import REAL_SOURCES, condition_met, should_trigger
from app.config import TZ, now, now_iso
from app.market import build_gram, enrich, fallback_quote, fetch_symbol
from app.reports import compose_report

logger = logging.getLogger("borsa.service")

KEEP_SOURCES = {"yahoo", "stooq", "frankfurter", "n8n"}

_lock = threading.Lock()
_status_lock = threading.Lock()
_scheduler: BackgroundScheduler | None = None
_started = False
_status = {
    "running": False,
    "last_run": None,
    "last_error": None,
    "live": 0,
    "sample": 0,
    "failed": 0,
    "failures": [],
    "next_run": None,
    "interval_minutes": 15,
}


def get_status() -> dict:
    with _status_lock:
        data = dict(_status)
        data["failures"] = list(_status["failures"])
        return data


def _update_status(**kwargs) -> None:
    with _status_lock:
        _status.update(kwargs)


def interval_minutes() -> int:
    raw = db.get_setting("fetch_interval_minutes", "15")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 15
    return max(5, min(value, 180))


def _has_real_quote(symbol: str) -> bool:
    row = db.load_full(symbol)
    return bool(row and row.get("source") in KEEP_SOURCES and row.get("price"))


def rebuild_gram() -> None:
    instrument = db.get_instrument("GRAM.ALTIN")
    if not instrument or not instrument["active"]:
        return
    gold = db.load_full("GC=F")
    usd = db.load_full("TRY=X")
    if not gold or not usd or gold.get("price") is None or usd.get("price") is None:
        return
    gram = build_gram(gold, usd)
    if gram:
        db.save_market_quote(gram)


def rows_for(period: str, quotes: list[dict]) -> list[dict]:
    rows = []
    for quote in quotes:
        if period == "weekly":
            amount = quote.get("change_amount_7d")
            pct = quote.get("change_percent_7d")
            previous = None
            if quote.get("price") is not None and amount is not None:
                previous = quote["price"] - amount
        else:
            amount = quote.get("change_amount")
            pct = quote.get("change_percent")
            previous = quote.get("previous_close")
        rows.append(
            {
                "symbol": quote["symbol"],
                "name": quote["name"],
                "category": quote.get("category"),
                "currency": quote.get("currency"),
                "price": quote.get("price"),
                "previous_close": previous,
                "change_amount": amount,
                "change_percent": pct,
                "decimals": quote.get("decimals", 2),
            }
        )
    return rows


def write_reports(quotes: list[dict], allow_email: bool = True) -> tuple[dict, dict]:
    today = now().date()
    daily = compose_report("daily", today, rows_for("daily", quotes))
    weekly = compose_report("weekly", today, rows_for("weekly", quotes))
    db.save_report(daily)
    db.save_report(weekly)
    if allow_email:
        _maybe_daily_email(daily)
    return daily, weekly


def _maybe_daily_email(report: dict) -> None:
    if db.get_setting("send_daily_email") != "1":
        return
    current = now()
    if current.hour < 18:
        return
    today = current.date().isoformat()
    if db.get_setting("last_daily_email_on") == today:
        return
    attempt = db.get_setting("last_daily_email_attempt")
    if attempt:
        try:
            from datetime import datetime

            previous = datetime.fromisoformat(attempt)
            if previous.tzinfo is None:
                previous = previous.replace(tzinfo=TZ)
            if (current - previous).total_seconds() < 3 * 3600:
                return
        except ValueError:
            pass
    db.set_setting("last_daily_email_attempt", current.isoformat(timespec="seconds"))
    status = notify.send_report(report)
    if status == "sent":
        db.set_setting("last_daily_email_on", today)
    else:
        logger.info("Günlük rapor e-postası: %s", status)


def matching_alerts(quotes: list[dict], rules: list[dict], today: str) -> list[tuple[dict, dict]]:
    by_symbol = {quote["symbol"]: quote for quote in quotes}
    found = []
    for rule in rules:
        quote = by_symbol.get(rule["symbol"])
        if not quote or quote.get("price") is None or quote.get("change_percent") is None:
            continue
        if quote.get("source") not in REAL_SOURCES:
            continue
        met = condition_met(rule["condition"], quote["price"], quote["change_percent"], rule["threshold"])
        if should_trigger(rule["enabled"], rule.get("last_triggered_on"), today, met):
            found.append((rule, quote))
    return found


def fire_alerts(quotes: list[dict]) -> None:
    today = now().date().isoformat()
    for rule, quote in matching_alerts(quotes, db.list_rules(), today):
        subject, text, html = notify.alert_content(rule, quote)
        email_status = notify.send_email(subject, text, html)
        hook_status = notify.send_webhook(
            {
                "event": "alert",
                "symbol": quote["symbol"],
                "name": quote.get("name"),
                "condition": rule["condition"],
                "threshold": rule["threshold"],
                "price": quote["price"],
                "change_percent": quote["change_percent"],
                "message": text,
                "triggered_at": now_iso(),
            }
        )
        db.add_event(
            {
                "rule_id": rule["id"],
                "symbol": quote["symbol"],
                "subject": subject,
                "message": text,
                "price": quote["price"],
                "change_percent": quote["change_percent"],
                "email_status": email_status,
                "webhook_status": hook_status,
            }
        )
        db.mark_triggered(rule["id"], today)


def retry_event(event_id: int) -> dict:
    event = db.get_event(event_id)
    if not event:
        raise ValueError("Olay bulunamadı.")
    email_status = notify.send_email(event["subject"], event["message"], notify.simple_html(event["message"]))
    hook_status = notify.send_webhook(
        {
            "event": "alert",
            "symbol": event["symbol"],
            "message": event["message"],
            "price": event.get("price"),
            "change_percent": event.get("change_percent"),
            "triggered_at": now_iso(),
            "retry": True,
        }
    )
    db.update_event_status(event_id, email_status, hook_status)
    refreshed = db.get_event(event_id)
    if not refreshed:
        raise ValueError("Olay bulunamadı.")
    return refreshed


def run_cycle() -> None:
    if not _lock.acquire(blocking=False):
        return
    try:
        _update_status(running=True, last_error=None)
        targets = db.fetch_targets()
        failures = []
        ok = 0
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {pool.submit(fetch_symbol, item["symbol"]): item for item in targets}
            for future in as_completed(futures):
                item = futures[future]
                try:
                    quote = enrich(future.result())
                    db.save_market_quote(quote)
                    ok += 1
                except Exception as exc:
                    logger.warning("Fiyat alınamadı %s: %s", item["symbol"], exc)
                    failures.append({"symbol": item["symbol"], "error": str(exc)[:180]})
                    if not _has_real_quote(item["symbol"]):
                        db.save_market_quote(fallback_quote(item))
        rebuild_gram()
        quotes = db.market_rows()
        fire_alerts(quotes)
        write_reports(quotes)
        live = sum(1 for quote in quotes if quote.get("source") not in {None, "ornek"})
        sample = sum(1 for quote in quotes if quote.get("source") == "ornek")
        _update_status(
            running=False,
            last_run=now_iso(),
            live=live,
            sample=sample,
            failed=len(failures),
            failures=failures,
            last_error=None if ok else (failures[0]["error"] if failures else None),
            interval_minutes=interval_minutes(),
            next_run=(now() + timedelta(minutes=interval_minutes())).isoformat(timespec="seconds"),
        )
        logger.info("Çekim bitti: %s canlı, %s örnek, %s hata", live, sample, len(failures))
    except Exception as exc:
        logger.exception("Çekim döngüsü bozuldu")
        _update_status(running=False, last_error=str(exc)[:180])
    finally:
        _lock.release()


def cycle_safe() -> None:
    try:
        run_cycle()
    except Exception:
        logger.exception("Zamanlanmış çekim hata verdi")


def ingest(items: list[dict]) -> dict:
    saved: list[str] = []
    skipped: list[str] = []
    today = now().date().isoformat()
    for item in items:
        symbol = item["symbol"].strip().upper()
        instrument = db.get_instrument(symbol)
        price = item.get("price")
        if (
            not instrument
            or not instrument["active"]
            or instrument["computed"]
            or price is None
            or not math.isfinite(price)
            or price <= 0
        ):
            skipped.append(symbol)
            continue
        existing = db.load_full(symbol)
        series = list(existing["series"]) if existing else []
        point = {"day": today, "close": float(price), "volume": item.get("volume")}
        if series and series[-1]["day"] == today:
            series[-1] = point
        else:
            series.append(point)
        previous = item.get("previous_close")
        if previous is None:
            if existing and existing.get("previous_close"):
                previous = existing["previous_close"]
            elif len(series) >= 2:
                previous = series[-2]["close"]
            else:
                previous = price
        quote = enrich(
            {
                "symbol": symbol,
                "price": float(price),
                "previous_close": float(previous),
                "volume": item.get("volume"),
                "market_time": now_iso(),
                "source": "n8n",
                "series": series,
            }
        )
        db.save_market_quote(quote)
        saved.append(symbol)
    rebuild_gram()
    quotes = db.market_rows()
    fire_alerts(quotes)
    write_reports(quotes, allow_email=False)
    return {"saved": saved, "skipped": skipped}


def request_refresh() -> dict:
    if _lock.locked() or get_status().get("running"):
        return {"started": False, **get_status()}
    threading.Thread(target=cycle_safe, name="borsa-refresh", daemon=True).start()
    return {"started": True, **get_status()}


def start() -> None:
    global _started, _scheduler
    if _started:
        return
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    db.init_db()
    _started = True
    threading.Thread(target=cycle_safe, name="borsa-fetch", daemon=True).start()
    try:
        _scheduler = BackgroundScheduler(timezone="Europe/Istanbul", daemon=True)
        _scheduler.add_job(
            cycle_safe,
            "interval",
            minutes=interval_minutes(),
            id="fetch",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            next_run_time=now() + timedelta(minutes=interval_minutes()),
        )
        _scheduler.start()
        _update_status(
            interval_minutes=interval_minutes(),
            next_run=(now() + timedelta(minutes=interval_minutes())).isoformat(timespec="seconds"),
        )
    except Exception:
        logger.exception("Zamanlayıcı başlamadı")


def stop() -> None:
    global _scheduler, _started
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
    _started = False


def reschedule() -> None:
    minutes = interval_minutes()
    _update_status(
        interval_minutes=minutes,
        next_run=(now() + timedelta(minutes=minutes)).isoformat(timespec="seconds"),
    )
    if not _scheduler:
        return
    _scheduler.reschedule_job("fetch", trigger="interval", minutes=minutes)
