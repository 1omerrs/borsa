"""Email and n8n webhook delivery."""

from __future__ import annotations

import logging
import smtplib
import threading
import time
from collections import deque
from email.message import EmailMessage
from urllib.parse import urlparse

import httpx

from app import db
from app.analytics import tr_number

logger = logging.getLogger("borsa.notify")

_email_lock = threading.Lock()
_email_times: deque[float] = deque()

CONDITIONS = {
    "above": "fiyat üst eşiğini geçti",
    "below": "fiyat alt eşiğine indi",
    "pct_up": "günlük yükseliş eşiğini geçti",
    "pct_down": "günlük düşüş eşiğini geçti",
}


def _allow_email() -> bool:
    current = time.time()
    with _email_lock:
        while _email_times and current - _email_times[0] > 3600:
            _email_times.popleft()
        if len(_email_times) >= 20:
            return False
        _email_times.append(current)
        return True


def smtp_settings() -> dict:
    data = db.all_settings()
    try:
        port = int(data.get("smtp_port") or 587)
    except ValueError:
        port = 587
    return {
        "host": (data.get("smtp_host") or "").strip(),
        "port": port,
        "user": (data.get("smtp_user") or "").strip(),
        "password": data.get("smtp_password") or "",
        "from": (data.get("smtp_from") or "").strip(),
        "to": (data.get("alert_email") or "").strip(),
    }


def esc(value: object) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def simple_html(message: str) -> str:
    body = esc(message).replace("\n", "<br>")
    return (
        "<div style=\"font-family:Segoe UI,sans-serif;background:#1a0b14;color:#fff4e8;padding:24px\">"
        "<div style=\"max-width:560px;margin:auto;background:#2a1224;border-radius:18px;padding:22px\">"
        "<p style=\"letter-spacing:.16em;color:#ffb703;font-size:12px;margin:0 0 12px\">BORSANABIZ</p>"
        f"<p style=\"font-size:16px;line-height:1.5\">{body}</p>"
        "<p style=\"color:#f0d5c4;font-size:12px\">Bu ileti yatırım tavsiyesi değildir.</p>"
        "</div></div>"
    )


def alert_content(rule: dict, quote: dict) -> tuple[str, str, str]:
    name = quote.get("name") or rule.get("name") or rule["symbol"]
    decimals = int(quote.get("decimals") or rule.get("decimals") or 2)
    currency = quote.get("currency") or rule.get("currency") or ""
    price = tr_number(quote["price"], decimals)
    pct = tr_number(quote["change_percent"], 2)
    reason = CONDITIONS.get(rule["condition"], "koşul oluştu")
    threshold_digits = decimals if rule["condition"] in {"above", "below"} else 2
    threshold = tr_number(rule["threshold"], threshold_digits)
    unit = f" {currency}" if rule["condition"] in {"above", "below"} else "%"
    subject = f"BorsaNabız: {name} {reason}"
    lines = [
        f"{name} ({rule['symbol']}) {reason}.",
        f"Eşik: {threshold}{unit}",
        f"Fiyat: {price} {currency}".rstrip(),
        f"Günlük değişim: %{pct}",
    ]
    if rule.get("note"):
        lines.append(f"Not: {rule['note']}")
    lines.append("Bu ileti yatırım tavsiyesi değildir.")
    text = "\n".join(lines)
    html = (
        "<div style=\"font-family:Segoe UI,sans-serif;background:#1a0b14;color:#fff4e8;padding:24px\">"
        "<div style=\"max-width:560px;margin:auto;background:#2a1224;border-radius:18px;padding:22px\">"
        "<p style=\"letter-spacing:.16em;color:#ffb703;font-size:12px;margin:0 0 8px\">BORSANABIZ UYARI</p>"
        f"<h1 style=\"font-size:26px;margin:0 0 8px\">{esc(name)}</h1>"
        f"<p style=\"margin:0 0 12px\">{esc(reason)}</p>"
        f"<p style=\"font-size:32px;color:#ffb703;margin:0\">{esc(price)} {esc(currency)}</p>"
        f"<p>Günlük değişim: %{esc(pct)}</p>"
        f"<p>Eşik: {esc(threshold)}{esc(unit)}</p>"
        "<p style=\"color:#f0d5c4;font-size:12px\">Bu ileti yatırım tavsiyesi değildir.</p>"
        "</div></div>"
    )
    return subject, text, html


def send_email(subject: str, text: str, html: str) -> str:
    settings = smtp_settings()
    if not settings["host"] or not settings["to"]:
        return "skipped"
    if not _allow_email():
        return "limited"
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings["from"] or settings["user"] or settings["to"]
    message["To"] = settings["to"]
    message.set_content(text)
    message.add_alternative(html, subtype="html")
    try:
        if settings["port"] == 465:
            with smtplib.SMTP_SSL(settings["host"], settings["port"], timeout=20) as smtp:
                if settings["user"]:
                    smtp.login(settings["user"], settings["password"])
                smtp.send_message(message)
        else:
            with smtplib.SMTP(settings["host"], settings["port"], timeout=20) as smtp:
                smtp.ehlo()
                smtp.starttls()
                smtp.ehlo()
                if settings["user"]:
                    smtp.login(settings["user"], settings["password"])
                smtp.send_message(message)
        return "sent"
    except Exception:
        logger.exception("E-posta gönderilemedi")
        return "failed"


def send_test_email() -> str:
    text = "Bu bir deneme mesajıdır. E-posta köprüsü çalışıyor."
    return send_email("BorsaNabız deneme", text, simple_html(text))


def send_webhook(payload: dict) -> str:
    url = db.get_setting("n8n_webhook_url").strip()
    if not url:
        return "skipped"
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        logger.warning("Webhook adresi geçersiz")
        return "failed"
    try:
        response = httpx.post(url, json=payload, timeout=12.0)
        response.raise_for_status()
        return "sent"
    except Exception:
        logger.exception("n8n webhook hatası")
        return "failed"


def send_test_webhook() -> str:
    from app.config import now_iso

    return send_webhook(
        {
            "event": "test",
            "message": "BorsaNabız bağlantı denemesi",
            "sent_at": now_iso(),
        }
    )


def report_html(report: dict) -> str:
    rows = []
    for row in report.get("rows") or []:
        pct = row.get("change_percent")
        pct_text = "—" if pct is None else f"%{tr_number(pct)}"
        price = "—" if row.get("price") is None else tr_number(row["price"], 2)
        color = "#8ac926" if (pct or 0) > 0 else "#ff4d6d" if (pct or 0) < 0 else "#fff4e8"
        rows.append(
            "<tr>"
            f"<td style=\"padding:6px 8px\">{esc(row['name'])}</td>"
            f"<td style=\"padding:6px 8px\">{esc(price)}</td>"
            f"<td style=\"padding:6px 8px;color:{color}\">{esc(pct_text)}</td>"
            "</tr>"
        )
    table = "".join(rows) or "<tr><td>Veri yok</td></tr>"
    return (
        "<div style=\"font-family:Segoe UI,sans-serif;background:#1a0b14;color:#fff4e8;padding:24px\">"
        "<div style=\"max-width:640px;margin:auto;background:#2a1224;border-radius:18px;padding:22px\">"
        f"<h1 style=\"font-size:22px\">{esc(report.get('title', 'Rapor'))}</h1>"
        f"<p>{esc(report.get('summary', ''))}</p>"
        "<table style=\"width:100%;border-collapse:collapse\">"
        f"{table}</table>"
        "<p style=\"color:#f0d5c4;font-size:12px\">Bu ileti yatırım tavsiyesi değildir.</p>"
        "</div></div>"
    )


def send_report(report: dict) -> str:
    lines = [report.get("title") or "Rapor", "", report.get("summary") or "", ""]
    for row in report.get("rows") or []:
        pct = row.get("change_percent")
        pct_text = "—" if pct is None else f"%{tr_number(pct)}"
        price = "—" if row.get("price") is None else tr_number(row["price"], 2)
        lines.append(f"{row['name']}: {price} ({pct_text})")
    lines.append("")
    lines.append("Bu ileti yatırım tavsiyesi değildir.")
    text = "\n".join(lines)
    return send_email(report.get("title") or "BorsaNabız rapor", text, report_html(report))
