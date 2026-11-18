"""City data and the cheap astronomy / calendar lookups for WorldClock.

Everything here is pure Python (no Qt) so it can be sanity-checked from the CLI.
"""
from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from datetime import datetime, timezone, date
from zoneinfo import ZoneInfo

import holidays
from astral import Observer
from astral.sun import sun as astral_sun


@dataclass(frozen=True)
class City:
    """One clock: a city from cities.json plus the user's label / holiday choice."""
    code: str                 # short label shown on the pill (2-3 letters)
    name: str
    tz: str
    lat: float
    lon: float
    country: str | None       # holiday country code (None = no holiday data)
    subdiv: str | None
    id: str = ""
    country_name: str = ""
    holidays_on: bool = True


MAX_CLOCKS = 8
DB_PATH = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "cities.json"   # built by tools/build_cities.py
_DB: list[dict] | None = None


def database() -> list[dict]:
    global _DB
    if _DB is None:
        _DB = json.loads(DB_PATH.read_text("utf-8"))
    return _DB


def db_entry(city_id: str) -> dict | None:
    return next((c for c in database() if c["id"] == city_id), None)


def city_from(cfg: dict) -> City | None:
    """cfg = {"id": ..., "label": ..., "holidays": bool} (as stored in settings.json)."""
    e = db_entry(cfg.get("id", ""))
    if e is None:
        return None
    hol = e.get("holidays") or {}
    return City(code=(cfg.get("label") or e["label"]).upper()[:3], name=e["name"], tz=e["tz"],
                lat=e["lat"], lon=e["lon"], country=hol.get("country"), subdiv=hol.get("subdiv"),
                id=e["id"], country_name=e["country"],
                holidays_on=bool(cfg.get("holidays", True)) and bool(hol))


# Mario's original four, with his labels
DEFAULT_CLOCKS = [
    {"id": "los-angeles-us", "label": "LA", "holidays": True},
    {"id": "new-york-us", "label": "NY", "holidays": True},
    {"id": "sydney-au", "label": "AU", "holidays": True},
    {"id": "berlin-de", "label": "GE", "holidays": True},
]


def clocks_from_settings(settings: dict) -> list[dict]:
    """The clock list from settings.json, falling back to (and migrating to) the defaults."""
    raw = settings.get("clocks")
    out = []
    if isinstance(raw, list):
        for c in raw:
            if isinstance(c, dict) and db_entry(c.get("id", "")):
                out.append({"id": c["id"], "label": str(c.get("label") or db_entry(c["id"])["label"])[:3].upper(),
                            "holidays": bool(c.get("holidays", True))})
    if not out:
        out = [dict(c) for c in DEFAULT_CLOCKS]
    return out[:MAX_CLOCKS]


def cities_for(clocks: list[dict]) -> list[City]:
    return [c for c in (city_from(x) for x in clocks) if c is not None]


CITIES = cities_for(DEFAULT_CLOCKS)
CODES = [c.code for c in CITIES]

# ---------------------------------------------------------------- moon
SYNODIC = 29.530588853
# A well-known new moon: 2000-01-06 18:14 UTC
_REF_NEW_MOON = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)


def moon_age_fraction(when_utc: datetime) -> float:
    """0 = new, 0.25 = first quarter, 0.5 = full, 0.75 = last quarter."""
    days = (when_utc - _REF_NEW_MOON).total_seconds() / 86400.0
    return (days / SYNODIC) % 1.0


def moon_illumination(age_frac: float) -> float:
    return (1.0 - math.cos(2.0 * math.pi * age_frac)) / 2.0


# ---------------------------------------------------------------- holidays
_hol_cache: dict = {}


def _holiday_set(city: City, year: int):
    key = (city.country, city.subdiv, year)
    if key not in _hol_cache:
        try:
            hs = holidays.country_holidays(city.country, subdiv=city.subdiv,
                                           years=year, language="en_US")
        except Exception:
            hs = holidays.country_holidays(city.country, subdiv=city.subdiv, years=year)
        _hol_cache[key] = hs
    return _hol_cache[key]


def holiday_name(city: City, d: date) -> str | None:
    if not city.country or not city.holidays_on:
        return None
    try:
        return _holiday_set(city, d.year).get(d)
    except Exception:
        return None


# ---------------------------------------------------------------- state
@dataclass
class CityState:
    city: City
    local: datetime
    phase: str            # "day" | "twilight" | "night"
    moon_age: float
    moon_illum: float
    holiday: str | None
    time_text: str = field(init=False)

    def __post_init__(self):
        self.time_text = f"{self.local.hour}:{self.local.minute:02d}"


def _sun_phase(city: City, local: datetime) -> str:
    tz = local.tzinfo
    obs = Observer(latitude=city.lat, longitude=city.lon)
    try:
        s = astral_sun(obs, date=local.date(), tzinfo=tz)
    except ValueError:
        # polar day / night (e.g. Nuuk, Reykjavik in deep winter)
        return "day" if 6 <= local.hour < 18 else "night"
    if s["sunrise"] <= local < s["sunset"]:
        return "day"
    if s["dawn"] <= local < s["sunrise"] or s["sunset"] <= local < s["dusk"]:
        return "twilight"
    return "night"


def compute(now_utc: datetime | None = None, force_holiday: set[str] | None = None,
            force_night: bool = False, cities: list[City] | None = None) -> list[CityState]:
    now_utc = now_utc or datetime.now(timezone.utc)
    force_holiday = force_holiday or set()
    age = moon_age_fraction(now_utc)
    illum = moon_illumination(age)
    out = []
    for c in (cities if cities is not None else CITIES):
        local = now_utc.astimezone(ZoneInfo(c.tz))
        phase = "night" if force_night else _sun_phase(c, local)
        hol = holiday_name(c, local.date())
        if c.code in force_holiday and not hol:
            hol = "Test Holiday (forced)"
        out.append(CityState(c, local, phase, age, illum, hol))
    return out


if __name__ == "__main__":
    for s in compute():
        print(f"{s.city.code} {s.local:%Y-%m-%d %H:%M %Z}  {s.phase:8s} "
              f"moon age {s.moon_age * SYNODIC:4.1f}d illum {s.moon_illum:.0%}  "
              f"holiday={s.holiday}")
