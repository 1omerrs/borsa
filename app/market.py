"""Pull prices from public market sources and compute changes."""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta
from urllib.parse import quote

import httpx

from app.analytics import (
    daily_change,
    gram_altin_try,
    percent_change,
    price_on_or_before,
)
from app.config import TZ, now, now_iso

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
}

YAHOO_HOSTS = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
    "https://query2.finance.yahoo.com/v8/finance/chart/{symbol}",
)

STOOQ_MAP = {
    "THYAO.IS": "thyao.is",
    "GARAN.IS": "garan.is",
    "AKBNK.IS": "akbnk.is",
    "ASELS.IS": "asels.is",
    "KCHOL.IS": "kchol.is",
    "SISE.IS": "sise.is",
    "EREGL.IS": "eregl.is",
    "BIMAS.IS": "bimas.is",
    "TUPRS.IS": "tuprs.is",
    "XU100.IS": "^xu100",
    "GC=F": "xauusd",
    "TRY=X": "usdtry",
    "EURTRY=X": "eurtry",
    "BTC-USD": "btcusd",
}

BASE_PRICES = {
    "XU100.IS": 10500,
    "THYAO.IS": 300,
    "GARAN.IS": 130,
    "AKBNK.IS": 68,
    "ASELS.IS": 180,
    "KCHOL.IS": 190,
    "SISE.IS": 42,
    "EREGL.IS": 28,
    "BIMAS.IS": 520,
    "TUPRS.IS": 160,
    "GC=F": 3400,
    "TRY=X": 38,
    "EURTRY=X": 44,
    "BTC-USD": 64000,
}

FX_PAIRS = {
    "TRY=X": ("USD", "TRY"),
    "EURTRY=X": ("EUR", "TRY"),
}


def enrich(quote: dict) -> dict:
    price = float(quote["price"])
    previous = float(quote["previous_close"])
    change, pct = daily_change(price, previous)
    pairs = sorted(
        (
            (date.fromisoformat(point["day"]), float(point["close"]))
            for point in quote.get("series") or []
        ),
        key=lambda item: item[0],
    )
    old = price_on_or_before(pairs, 7)
    week_amount = None if old is None else price - old
    week_pct = percent_change(old, price)
    quote["price"] = round(price, 6)
    quote["previous_close"] = round(previous, 6)
    quote["change_amount"] = round(change, 6)
    quote["change_percent"] = round(pct, 6)
    quote["change_amount_7d"] = None if week_amount is None else round(week_amount, 6)
    quote["change_percent_7d"] = None if week_pct is None else round(week_pct, 6)
    return quote


def fetch_symbol(symbol: str) -> dict:
    errors: list[str] = []
    loaders = [load_yahoo, load_stooq]
    if symbol in FX_PAIRS:
        loaders = [load_frankfurter, load_yahoo, load_stooq]
    for loader in loaders:
        try:
            quote = loader(symbol)
        except Exception as exc:
            errors.append(str(exc)[:160])
            continue
        if quote:
            return quote
    detail = " | ".join(errors) if errors else "veri yok"
    raise RuntimeError(detail)


def load_yahoo(symbol: str) -> dict:
    encoded = quote(symbol, safe="")
    last_error: Exception | None = None
    timeout = httpx.Timeout(12.0, connect=8.0)
    with httpx.Client(timeout=timeout, headers=HEADERS, follow_redirects=True) as client:
        for template in YAHOO_HOSTS:
            url = template.format(symbol=encoded)
            try:
                response = client.get(
                    url,
                    params={"interval": "1d", "range": "6mo", "includePrePost": "false"},
                )
                response.raise_for_status()
                payload = response.json()
                chart = payload.get("chart") or {}
                if chart.get("error"):
                    description = chart["error"].get("description") or "yahoo hata"
                    raise RuntimeError(description)
                result = (chart.get("result") or [None])[0]
                if not result:
                    raise RuntimeError("yahoo boş sonuç")
                return _parse_yahoo(symbol, result)
            except Exception as exc:
                last_error = exc
    raise RuntimeError(f"yahoo: {last_error}")


def _parse_yahoo(symbol: str, result: dict) -> dict:
    meta = result.get("meta") or {}
    timestamps = result.get("timestamp") or []
    block = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    closes = block.get("close") or []
    volumes = block.get("volume") or []
    ordered: dict[str, dict] = {}
    for index, ts in enumerate(timestamps):
        close = closes[index] if index < len(closes) else None
        if ts is None or close is None:
            continue
        day = datetime.fromtimestamp(int(ts), TZ).date().isoformat()
        volume = volumes[index] if index < len(volumes) else None
        ordered[day] = {
            "day": day,
            "close": float(close),
            "volume": float(volume) if volume else None,
        }
    series = [ordered[day] for day in sorted(ordered)]
    price = meta.get("regularMarketPrice")
    if price is None and series:
        price = series[-1]["close"]
    if price is None:
        raise RuntimeError("fiyat yok")
    price = float(price)
    if price <= 0:
        raise RuntimeError("fiyat geçersiz")
    # chartPreviousClose is the first bar of the requested range, not yesterday.
    if len(series) >= 2:
        previous = series[-2]["close"]
    else:
        previous = meta.get("previousClose") or meta.get("chartPreviousClose") or price
    previous = float(previous)
    volume = meta.get("regularMarketVolume")
    if series:
        series[-1]["close"] = price
        if volume:
            series[-1]["volume"] = float(volume)
    else:
        series = [{"day": now().date().isoformat(), "close": price, "volume": float(volume) if volume else None}]
    market_time = meta.get("regularMarketTime")
    if market_time:
        stamp = datetime.fromtimestamp(int(market_time), TZ).isoformat(timespec="seconds")
    else:
        stamp = now_iso()
    return {
        "symbol": symbol,
        "price": price,
        "previous_close": previous,
        "volume": float(volume) if volume is not None else None,
        "market_time": stamp,
        "source": "yahoo",
        "series": series[-140:],
    }


def load_stooq(symbol: str) -> dict | None:
    mapped = STOOQ_MAP.get(symbol)
    if not mapped:
        return None
    timeout = httpx.Timeout(12.0, connect=8.0)
    with httpx.Client(timeout=timeout, headers=HEADERS, follow_redirects=True) as client:
        response = client.get("https://stooq.com/q/d/l/", params={"s": mapped, "i": "d"})
        response.raise_for_status()
        text = response.text
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 3 or not lines[0].lower().startswith("date"):
        raise RuntimeError("stooq yanıtı beklenenden farklı")
    series = []
    for line in lines[1:]:
        parts = line.split(",")
        if len(parts) < 5:
            continue
        day, _open, _high, _low, close = parts[:5]
        raw_volume = parts[5] if len(parts) > 5 else ""
        try:
            close_value = float(close)
        except ValueError:
            continue
        if close_value <= 0:
            continue
        volume = None
        if raw_volume and raw_volume != "0":
            try:
                volume = float(raw_volume)
            except ValueError:
                volume = None
        series.append({"day": day, "close": close_value, "volume": volume})
    series = series[-140:]
    if len(series) < 2:
        raise RuntimeError("stooq serisi kısa")
    return {
        "symbol": symbol,
        "price": series[-1]["close"],
        "previous_close": series[-2]["close"],
        "volume": series[-1]["volume"],
        "market_time": f"{series[-1]['day']}T18:00:00+03:00",
        "source": "stooq",
        "series": series,
    }


def load_frankfurter(symbol: str) -> dict | None:
    pair = FX_PAIRS.get(symbol)
    if not pair:
        return None
    start = (now().date() - timedelta(days=180)).isoformat()
    end = now().date().isoformat()
    timeout = httpx.Timeout(12.0, connect=8.0)
    last_error: Exception | None = None
    data = None
    with httpx.Client(timeout=timeout, headers=HEADERS, follow_redirects=True) as client:
        for host in ("https://api.frankfurter.app", "https://api.frankfurter.dev"):
            try:
                response = client.get(
                    f"{host}/{start}..{end}",
                    params={"from": pair[0], "to": pair[1]},
                )
                response.raise_for_status()
                data = response.json()
                break
            except Exception as exc:
                last_error = exc
    if data is None:
        raise RuntimeError(f"frankfurter: {last_error}")
    rates = data.get("rates") or {}
    series = []
    for day in sorted(rates):
        value = (rates[day] or {}).get(pair[1])
        if not value:
            continue
        series.append({"day": day, "close": float(value), "volume": None})
    if len(series) < 2:
        raise RuntimeError("frankfurter serisi kısa")
    return {
        "symbol": symbol,
        "price": series[-1]["close"],
        "previous_close": series[-2]["close"],
        "volume": None,
        "market_time": f"{series[-1]['day']}T18:00:00+03:00",
        "source": "frankfurter",
        "series": series,
    }


def build_gram(gold: dict, usd: dict) -> dict | None:
    live = gram_altin_try(gold.get("price"), usd.get("price"))
    previous = gram_altin_try(gold.get("previous_close"), usd.get("previous_close"))
    if live is None or previous is None:
        return None
    usd_series = sorted(usd.get("series") or [], key=lambda point: point["day"])
    series = []
    cursor = 0
    last_usd = None
    for point in sorted(gold.get("series") or [], key=lambda item: item["day"]):
        while cursor < len(usd_series) and usd_series[cursor]["day"] <= point["day"]:
            last_usd = usd_series[cursor]["close"]
            cursor += 1
        if not last_usd:
            continue
        gram = gram_altin_try(point["close"], last_usd)
        if gram is None:
            continue
        series.append({"day": point["day"], "close": round(gram, 6), "volume": None})
    if series:
        series[-1]["close"] = round(live, 6)
    else:
        series = [{"day": now().date().isoformat(), "close": round(live, 6), "volume": None}]
    source = "hesaplanan"
    if gold.get("source") == "ornek" or usd.get("source") == "ornek":
        source = "ornek"
    return enrich(
        {
            "symbol": "GRAM.ALTIN",
            "price": live,
            "previous_close": previous,
            "volume": None,
            "market_time": gold.get("market_time") or now_iso(),
            "source": source,
            "series": series[-140:],
        }
    )


def synthetic_series(symbol: str, base: float, today: date) -> list[dict]:
    rng = random.Random(symbol)
    price = float(base)
    series = []
    day = today - timedelta(days=160)
    while day <= today:
        if day.weekday() < 5:
            price = max(base * 0.45, price * (1 + rng.uniform(-0.012, 0.013)))
            series.append({"day": day.isoformat(), "close": round(price, 4), "volume": None})
        day += timedelta(days=1)
    return series


def fallback_quote(inst: dict) -> dict:
    base = float(BASE_PRICES.get(inst["symbol"], 100.0))
    series = synthetic_series(inst["symbol"], base, now().date())
    return enrich(
        {
            "symbol": inst["symbol"],
            "price": series[-1]["close"],
            "previous_close": series[-2]["close"],
            "volume": None,
            "market_time": now_iso(),
            "source": "ornek",
            "series": series,
        }
    )
