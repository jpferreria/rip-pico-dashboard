"""
Lightweight local weather fetcher for MicroPython (Pico W / Pico 2 W)
using Open-Meteo (weather + geocoding) and ip-api.com (auto location).
No API keys required.
"""

import gc
import json
import time

try:
    import requests
except ImportError:
    import urequests as requests

CACHE_TTL_MS = 15 * 60 * 1000  # 15 minutes
LOCATION_FILE = "location.json"
_cache = None
_location = None

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


def _urlencode(text):
    safe = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.~"
    out = []
    for b in text.encode("utf-8"):
        ch = chr(b)
        if ch in safe:
            out.append(ch)
        else:
            out.append("%{:02X}".format(b))
    return "".join(out)


def _save_location(loc):
    global _location
    _location = loc
    try:
        with open(LOCATION_FILE, "w") as f:
            json.dump(loc, f)
    except OSError:
        pass


def _load_saved_location():
    try:
        with open(LOCATION_FILE, "r") as f:
            data = json.load(f)
            if "city" in data and "lat" in data and "lon" in data:
                return data
    except Exception:
        pass
    return None


def detect_location():
    """Auto-detect approximate city/lat/lon via IP geolocation."""
    resp = None
    try:
        gc.collect()
        resp = requests.get(
            "http://ip-api.com/json/?fields=status,city,lat,lon",
            headers={"User-Agent": "PicoW-Dashboard/1.0"},
        )
        if resp.status_code != 200:
            raise RuntimeError("HTTP {}".format(resp.status_code))
        data = resp.json()
        if data.get("status") != "success":
            raise RuntimeError("Geolocation unavailable")
        return {
            "city": data.get("city") or "Local",
            "lat": str(data["lat"]),
            "lon": str(data["lon"]),
            "mode": "auto",
        }
    finally:
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass
        gc.collect()


def geocode_city(query):
    """Resolve a city name to coordinates using Open-Meteo Geocoding API."""
    resp = None
    try:
        gc.collect()
        url = (
            "https://geocoding-api.open-meteo.com/v1/search"
            "?name={}&count=1&language=en&format=json"
        ).format(_urlencode(query))
        resp = requests.get(url, headers={"User-Agent": "PicoW-Dashboard/1.0"})
        if resp.status_code != 200:
            raise RuntimeError("HTTP {}".format(resp.status_code))
        data = resp.json()
        results = data.get("results") or []
        if not results:
            raise RuntimeError("City not found: " + query)
        r = results[0]
        name = r.get("name", query)
        cc = r.get("country_code", "")
        label = "{}, {}".format(name, cc) if cc else name
        return {
            "city": label,
            "lat": str(r["latitude"]),
            "lon": str(r["longitude"]),
            "mode": "manual",
        }
    finally:
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass
        gc.collect()


def ensure_location(fallback_city="Manila", fallback_lat="14.5995", fallback_lon="120.9842"):
    global _location
    if _location is not None:
        return _location

    saved = _load_saved_location()
    if saved:
        _location = saved
        return _location

    try:
        detected = detect_location()
        _save_location(detected)
        return detected
    except Exception:
        fallback = {
            "city": fallback_city,
            "lat": str(fallback_lat),
            "lon": str(fallback_lon),
            "mode": "auto",
        }
        _location = fallback
        return fallback


def set_location(city="", auto=False, fallback_city="Manila", fallback_lat="14.5995", fallback_lon="120.9842"):
    global _cache
    if auto:
        loc = detect_location()
    else:
        query = str(city).strip()[:60]
        if not query:
            raise RuntimeError("City name is required")
        loc = geocode_city(query)

    _save_location(loc)
    _cache = None
    return get_weather(fallback_city, fallback_lat, fallback_lon, force=True)


def get_weather(fallback_city="Manila", fallback_lat="14.5995", fallback_lon="120.9842", force=False):
    global _cache
    loc = ensure_location(fallback_city, fallback_lat, fallback_lon)
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
    ).format(loc["lat"], loc["lon"])

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
            "city": loc["city"],
            "mode": loc.get("mode", "auto"),
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
        return {"city": loc["city"], "mode": loc.get("mode", "auto"), "error": str(exc)}
    finally:
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass
        gc.collect()
