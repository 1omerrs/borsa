"""Paths, environment, and the Istanbul clock."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

TZ = ZoneInfo("Europe/Istanbul")
APP_NAME = "BorsaNabız"


def now() -> datetime:
    return datetime.now(TZ)


def now_iso() -> str:
    return now().isoformat(timespec="seconds")


def db_path() -> Path:
    return Path(os.getenv("DATABASE_PATH", str(BASE_DIR / "data" / "borsa.db")))
