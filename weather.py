"""
Lightweight local weather fetcher for MicroPython (Pico W / Pico 2 W)
using the free Open-Meteo API (no API key required).
"""

import gc
import time

try:
    import requests
except ImportError:
    import urequests as requests

CACHE_TTL_MS = 15 * 60 * 1000  # 15 minutes
_cache = None

WMO_CODES = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Foggy",
    48: "Rime fog",
    51: "Light drizzle",
    53: "Drizzle",
    55: "Dense drizzle",
    61: "Light rain",
    63: "Rain",
    65: "Heavy rain",
    71: "Light snow",
    73: "Snow",
    75: "Heavy snow",
    80: "Rain showers",
    81: "Moderate showers",
    82: "Heavy showers",
    95: "Thunderstorm",
    96: "Thunderstorm + hail",
    99: "Severe thunderstorm",
}


def get_weather(lat, lon, city="Manila", force=False):
    global _cache
    now = time.ticks_ms()

    if not force and _cache and time.ticks_diff(now, _cache["fetched_ms"]) < CACHE_TTL_MS:
        age_s = time.ticks_diff(now, _cache["fetched_ms"]) // 1000
        out = dict(_cache["data"])
        out["age_s"] = age_s
        out["error"] = None
        return out

    url = (
        "https://api.open-meteo.com/v1/forecast"
        "?latitude={}&longitude={}"
        "&current=temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m"
        "&daily=temperature_2m_max,temperature_2m_min"
        "&timezone=auto&forecast_days=1"
    ).format(lat, lon)

    resp = None
    try:
        gc.collect()
        resp = requests.get(url, headers={"User-Agent": "PicoW-Dashboard/1.0"})
        if resp.status_code != 200:
            raise RuntimeError("HTTP {}".format(resp.status_code))

        raw = resp.json()
        cur = raw.get("current", {})
        daily = raw.get("daily", {})
        code = cur.get("weather_code")

        hi_list = daily.get("temperature_2m_max") or []
        lo_list = daily.get("temperature_2m_min") or []

        data = {
            "city": city,
            "temp_c": cur.get("temperature_2m"),
            "feels_c": cur.get("apparent_temperature"),
            "humidity": cur.get("relative_humidity_2m"),
            "wind_kmh": cur.get("wind_speed_10m"),
            "condition": WMO_CODES.get(code, "Code {}".format(code)),
            "high_c": hi_list[0] if hi_list else None,
            "low_c": lo_list[0] if lo_list else None,
        }
        _cache = {"data": data, "fetched_ms": time.ticks_ms()}
        out = dict(data)
        out["age_s"] = 0
        out["error"] = None
        return out
    except Exception as exc:
        if _cache:
            out = dict(_cache["data"])
            out["age_s"] = time.ticks_diff(now, _cache["fetched_ms"]) // 1000
            out["error"] = str(exc)
            return out
        return {"city": city, "error": str(exc)}
    finally:
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass
        gc.collect()
