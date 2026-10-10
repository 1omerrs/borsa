from datetime import date

import pytest

from app.analytics import (
    condition_met,
    daily_change,
    gram_altin_try,
    percent_change,
    price_on_or_before,
    should_trigger,
)
from app.market import build_gram


def test_daily_change_up_and_down():
    change, pct = daily_change(110, 100)
    assert change == 10
    assert pct == 10
    change, pct = daily_change(90, 100)
    assert change == -10
    assert pct == pytest.approx(-10)


def test_daily_change_zero_previous():
    change, pct = daily_change(5, 0)
    assert change == 5
    assert pct == 0


def test_percent_change_guards_zero():
    assert percent_change(0, 10) is None
    assert percent_change(None, 10) is None


def test_gram_altin_formula():
    assert gram_altin_try(3110.34768, 10) == pytest.approx(1000)


def test_price_window():
    series = [
        (date(2026, 10, 1), 10),
        (date(2026, 10, 8), 12),
        (date(2026, 10, 10), 11),
    ]
    assert price_on_or_before(series, 7) == 10


def test_conditions_and_once_per_day():
    assert condition_met("above", 10, 0, 10) is True
    assert condition_met("below", 9, 0, 10) is True
    assert condition_met("pct_up", 10, 3, 3) is True
    assert condition_met("pct_down", 10, -3.2, 3) is True
    assert condition_met("pct_down", 10, -2, 3) is False
    assert should_trigger(True, None, "2026-10-10", True) is True
    assert should_trigger(True, "2026-10-10", "2026-10-10", True) is False
    assert should_trigger(False, None, "2026-10-10", True) is False
    assert should_trigger(True, None, "2026-10-10", False) is False


def test_build_gram_marks_sample_parents():
    gold = {
        "price": 3110.34768,
        "previous_close": 3110.34768,
        "source": "yahoo",
        "market_time": "2026-10-09T18:00:00+03:00",
        "series": [
            {"day": "2026-10-08", "close": 3110.34768, "volume": None},
            {"day": "2026-10-09", "close": 3110.34768, "volume": None},
        ],
    }
    usd = {
        "price": 10,
        "previous_close": 10,
        "source": "frankfurter",
        "series": [
            {"day": "2026-10-08", "close": 10, "volume": None},
            {"day": "2026-10-09", "close": 10, "volume": None},
        ],
    }
    gram = build_gram(gold, usd)
    assert gram is not None
    assert gram["price"] == pytest.approx(1000)
    assert gram["source"] == "hesaplanan"
    usd["source"] = "ornek"
    assert build_gram(gold, usd)["source"] == "ornek"
