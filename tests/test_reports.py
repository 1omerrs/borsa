from datetime import date

from app.reports import compose_report


def test_daily_report_names_the_movers():
    today = date(2026, 10, 9)
    rows = [
        {
            "symbol": "A",
            "name": "Alfa",
            "category": "hisse",
            "currency": "TRY",
            "price": 110,
            "change_amount": 10,
            "change_percent": 10,
            "previous_close": 100,
        },
        {
            "symbol": "B",
            "name": "Beta",
            "category": "hisse",
            "currency": "TRY",
            "price": 90,
            "change_amount": -10,
            "change_percent": -10,
            "previous_close": 100,
        },
    ]
    report = compose_report("daily", today, rows)
    assert report["period_key"] == "2026-10-09"
    assert report["leader"]["symbol"] == "A"
    assert report["laggard"]["symbol"] == "B"
    assert report["advancers"] == 1
    assert report["decliners"] == 1
    assert "Alfa" in report["summary"]
    assert report["rows"][0]["symbol"] == "A"
