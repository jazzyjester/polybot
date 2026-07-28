"""Forecast data from Open-Meteo (free, no API key required)."""

import requests

from .config import OPEN_METEO_ARCHIVE_URL, OPEN_METEO_ENSEMBLE_URL


def fetch_ensemble_max_temps(
    lat: float, lon: float, timezone: str, target_date: str, unit: str
) -> list[float]:
    """Return one max-temperature value per GFS ensemble member for target_date
    (YYYY-MM-DD, in the city's local timezone). ~30 members -> an empirical
    probability distribution instead of a single point forecast.
    """
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "temperature_2m_max",
        "timezone": timezone,
        "forecast_days": 10,
        "models": "gfs_seamless",
    }
    if unit == "F":
        params["temperature_unit"] = "fahrenheit"

    resp = requests.get(OPEN_METEO_ENSEMBLE_URL, params=params, timeout=20)
    resp.raise_for_status()
    data = resp.json()

    dates = data["daily"]["time"]
    if target_date not in dates:
        raise ValueError(f"{target_date} not in forecast range {dates}")
    idx = dates.index(target_date)

    member_keys = [
        k for k in data["daily"] if k.startswith("temperature_2m_max")
    ]
    values = []
    for key in member_keys:
        v = data["daily"][key][idx]
        if v is not None:
            values.append(v)
    return values


def fetch_actual_max_temp(
    lat: float, lon: float, timezone: str, target_date: str, unit: str
) -> float | None:
    """Look up the observed max temperature for a past date, for resolution
    checks."""
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "temperature_2m_max",
        "timezone": timezone,
        "start_date": target_date,
        "end_date": target_date,
    }
    if unit == "F":
        params["temperature_unit"] = "fahrenheit"

    resp = requests.get(OPEN_METEO_ARCHIVE_URL, params=params, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    values = data.get("daily", {}).get("temperature_2m_max", [])
    return values[0] if values else None
