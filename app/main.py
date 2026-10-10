"""Public BorsaNabız site and JSON API."""

from __future__ import annotations

import csv
import io
import logging
import os
import re
import secrets
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import db, notify, service
from app.config import APP_NAME, BASE_DIR, now_iso

logger = logging.getLogger("borsa.main")
STATIC = BASE_DIR / "static"
SYMBOL_RE = re.compile(r"^[A-Z0-9.=\-]{1,24}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
LOCALHOSTS = {"127.0.0.1", "::1", "localhost"}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    service.start()
    yield
    service.stop()


app = FastAPI(title=APP_NAME, version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class InstrumentIn(BaseModel):
    symbol: str
    name: str = Field(min_length=2, max_length=48)
    category: str
    currency: str | None = None


class AlertIn(BaseModel):
    symbol: str
    condition: str
    threshold: float = Field(gt=0)
    note: str = ""


class AlertPatch(BaseModel):
    enabled: bool


class SettingsIn(BaseModel):
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    alert_email: str | None = None
    n8n_webhook_url: str | None = None
    send_daily_email: bool | None = None
    fetch_interval_minutes: int | None = None


class IngestItem(BaseModel):
    symbol: str
    price: float = Field(gt=0)
    previous_close: float | None = None
    volume: float | None = None


class IngestIn(BaseModel):
    quotes: list[IngestItem] = Field(min_length=1, max_length=40)


def normalize_symbol(value: str) -> str:
    symbol = value.strip().upper()
    if not SYMBOL_RE.fullmatch(symbol):
        raise HTTPException(status_code=400, detail="Sembol yalnızca harf, rakam, nokta, tire ve eşittir içerebilir.")
    return symbol


def is_admin(request: Request) -> bool:
    host = request.client.host if request.client else ""
    if host in LOCALHOSTS:
        return True
    expected = os.getenv("ADMIN_TOKEN", "").strip()
    supplied = request.headers.get("x-admin-token", "")
    if expected and supplied and secrets.compare_digest(supplied, expected):
        return True
    return False


def require_admin(request: Request) -> None:
    if not is_admin(request):
        raise HTTPException(
            status_code=403,
            detail="Bu işlem yerelden veya yönetici anahtarı ile yapılabilir.",
        )


def _check_email(value: str) -> None:
    if value and not EMAIL_RE.fullmatch(value):
        raise HTTPException(status_code=400, detail="E-posta adresi geçersiz.")


def _check_webhook(value: str) -> None:
    if not value:
        return
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail="Webhook adresi http veya https olmalı.")


@app.get("/")
def home():
    return FileResponse(STATIC / "index.html")


@app.get("/favicon.svg")
def favicon():
    return FileResponse(STATIC / "favicon.svg")


@app.get("/api/health")
def health():
    return {"ok": True, "name": APP_NAME, "time": now_iso()}


@app.get("/api/access")
def access(request: Request):
    return {"admin": is_admin(request)}


@app.get("/api/status")
def status():
    return service.get_status()


@app.get("/api/market")
def market():
    return {"quotes": db.market_rows(), "generated_at": now_iso()}


@app.get("/api/history/{symbol:path}")
def history(symbol: str):
    symbol = normalize_symbol(symbol)
    instrument = db.get_instrument(symbol)
    if not instrument:
        raise HTTPException(status_code=404, detail="Varlık bulunamadı.")
    return {"symbol": symbol, "name": instrument["name"], "points": db.history(symbol)}


@app.post("/api/refresh")
def refresh(request: Request):
    require_admin(request)
    return service.request_refresh()


@app.post("/api/instruments")
def add_instrument(body: InstrumentIn, request: Request):
    require_admin(request)
    categories = {"hisse", "altin", "doviz", "kripto", "endeks"}
    if body.category not in categories:
        raise HTTPException(status_code=400, detail="Kategori geçersiz.")
    symbol = normalize_symbol(body.symbol)
    currency = (body.currency or ("TRY" if body.category in {"hisse", "endeks", "doviz"} else "USD")).upper()
    if not re.fullmatch(r"[A-Z]{3,4}", currency):
        raise HTTPException(status_code=400, detail="Para birimi geçersiz.")
    decimals = 4 if body.category == "doviz" else 2
    try:
        return db.upsert_instrument(symbol, body.name.strip(), body.category, currency, decimals)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/instruments/{symbol:path}")
def hide_instrument(symbol: str, request: Request):
    require_admin(request)
    symbol = normalize_symbol(symbol)
    try:
        db.deactivate_instrument(symbol)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True}


@app.get("/api/alerts")
def alerts():
    return {"rules": db.list_rules()}


@app.post("/api/alerts")
def create_alert(body: AlertIn, request: Request):
    require_admin(request)
    if body.condition not in {"above", "below", "pct_up", "pct_down"}:
        raise HTTPException(status_code=400, detail="Koşul geçersiz.")
    symbol = normalize_symbol(body.symbol)
    try:
        return db.add_rule(symbol, body.condition, body.threshold, body.note.strip())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.patch("/api/alerts/{rule_id}")
def patch_alert(rule_id: int, body: AlertPatch, request: Request):
    require_admin(request)
    try:
        return db.set_rule_enabled(rule_id, body.enabled)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.delete("/api/alerts/{rule_id}")
def remove_alert(rule_id: int, request: Request):
    require_admin(request)
    db.delete_rule(rule_id)
    return {"ok": True}


@app.get("/api/alerts/events")
def alert_events():
    return {"events": db.list_events()}


@app.post("/api/alerts/events/{event_id}/retry")
def retry_alert(event_id: int, request: Request):
    require_admin(request)
    try:
        return service.retry_event(event_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/reports")
def reports():
    return {"reports": db.list_reports()}


@app.post("/api/reports/generate")
def generate_reports(request: Request):
    require_admin(request)
    daily, weekly = service.write_reports(db.market_rows(), allow_email=False)
    return {"daily": daily, "weekly": weekly}


@app.post("/api/reports/email")
def email_report(request: Request, period: str = "daily"):
    require_admin(request)
    if period not in {"daily", "weekly"}:
        raise HTTPException(status_code=400, detail="Dönem günlük veya haftalık olmalı.")
    found = next((item for item in db.list_reports() if item["period"] == period), None)
    if not found:
        raise HTTPException(status_code=404, detail="Rapor henüz yok.")
    status = notify.send_report(found)
    if status == "skipped":
        raise HTTPException(status_code=400, detail="Önce SMTP ve alıcı adresini kaydet.")
    if status != "sent":
        raise HTTPException(status_code=400, detail="E-posta gönderilemedi. Ayarları ve sunucu günlüğünü kontrol et.")
    return {"status": status}


@app.get("/api/settings")
def get_settings(request: Request):
    require_admin(request)
    return _public_settings()


@app.put("/api/settings")
def put_settings(body: SettingsIn, request: Request):
    require_admin(request)
    if body.alert_email is not None:
        _check_email(body.alert_email.strip())
        db.set_setting("alert_email", body.alert_email.strip())
    if body.smtp_from is not None:
        _check_email(body.smtp_from.strip())
        db.set_setting("smtp_from", body.smtp_from.strip())
    if body.smtp_host is not None:
        db.set_setting("smtp_host", body.smtp_host.strip())
    if body.smtp_user is not None:
        db.set_setting("smtp_user", body.smtp_user.strip())
    if body.smtp_password:
        db.set_setting("smtp_password", body.smtp_password)
    if body.smtp_port is not None:
        if not 1 <= body.smtp_port <= 65535:
            raise HTTPException(status_code=400, detail="SMTP portu geçersiz.")
        db.set_setting("smtp_port", str(body.smtp_port))
    if body.n8n_webhook_url is not None:
        url = body.n8n_webhook_url.strip()
        _check_webhook(url)
        db.set_setting("n8n_webhook_url", url)
    if body.send_daily_email is not None:
        db.set_setting("send_daily_email", "1" if body.send_daily_email else "0")
    if body.fetch_interval_minutes is not None:
        if not 5 <= body.fetch_interval_minutes <= 180:
            raise HTTPException(status_code=400, detail="Aralık 5 ile 180 dakika arasında olmalı.")
        db.set_setting("fetch_interval_minutes", str(body.fetch_interval_minutes))
        try:
            service.reschedule()
        except Exception:
            logger.exception("Çekim aralığı güncellenemedi")
    return _public_settings()


@app.post("/api/settings/test-email")
def test_email(request: Request):
    require_admin(request)
    status = notify.send_test_email()
    if status == "skipped":
        raise HTTPException(status_code=400, detail="Önce SMTP sunucusu ve alıcı adresini kaydet.")
    if status != "sent":
        raise HTTPException(status_code=400, detail="Deneme e-postası gönderilemedi.")
    return {"status": status}


@app.post("/api/settings/test-webhook")
def test_webhook(request: Request):
    require_admin(request)
    if not db.get_setting("n8n_webhook_url").strip():
        raise HTTPException(status_code=400, detail="Önce n8n webhook adresini kaydet.")
    status = notify.send_test_webhook()
    if status != "sent":
        raise HTTPException(status_code=400, detail="n8n webhook yanıt vermedi.")
    return {"status": status}


@app.get("/api/n8n/snapshot")
def snapshot():
    rows = []
    for row in db.market_rows():
        rows.append(
            {
                "symbol": row["symbol"],
                "name": row["name"],
                "category": row["category"],
                "currency": row["currency"],
                "price": row["price"],
                "previous_close": row["previous_close"],
                "change_amount": row["change_amount"],
                "change_percent": row["change_percent"],
                "change_percent_7d": row["change_percent_7d"],
                "source": row["source"],
                "updated_at": row["updated_at"],
            }
        )
    return {"generated_at": now_iso(), "rows": rows}


@app.post("/api/n8n/ingest")
def ingest(body: IngestIn, request: Request):
    require_admin(request)
    return service.ingest([item.model_dump() for item in body.quotes])


@app.get("/api/export.csv")
def export_csv():
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "Sembol",
            "Ad",
            "Kategori",
            "Fiyat",
            "Onceki Kapanis",
            "Fark",
            "Yuzde",
            "Haftalik Yuzde",
            "Para Birimi",
            "Kaynak",
            "Guncelleme",
        ]
    )
    for row in db.market_rows():
        writer.writerow(
            [
                row["symbol"],
                row["name"],
                row["category"],
                row["price"],
                row["previous_close"],
                row["change_amount"],
                row["change_percent"],
                row["change_percent_7d"],
                row["currency"],
                row["source"],
                row["updated_at"],
            ]
        )
    return Response(
        content="\ufeff" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=borsa-nabiz.csv"},
    )


def _public_settings() -> dict:
    data = db.all_settings()
    try:
        port = int(data.get("smtp_port") or 587)
    except ValueError:
        port = 587
    try:
        minutes = int(data.get("fetch_interval_minutes") or 15)
    except ValueError:
        minutes = 15
    return {
        "smtp_host": data.get("smtp_host", ""),
        "smtp_port": port,
        "smtp_user": data.get("smtp_user", ""),
        "smtp_from": data.get("smtp_from", ""),
        "alert_email": data.get("alert_email", ""),
        "n8n_webhook_url": data.get("n8n_webhook_url", ""),
        "send_daily_email": data.get("send_daily_email") == "1",
        "fetch_interval_minutes": minutes,
        "has_password": bool(data.get("smtp_password")),
    }


app.mount("/static", StaticFiles(directory=STATIC), name="static")
