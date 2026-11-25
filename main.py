"""
Raspberry Pi Pico W - Web Control & Telemetry Dashboard (MicroPython)

Requires no additional hardware. Uses:
- CYW43439 Wi-Fi + onboard LED
- RP2040 internal temperature sensor (ADC4)
- Async HTTP server serving index.html + JSON API
"""

import gc
import json
import machine
import network
import os
import time

import env
import news
import weather

try:
    import asyncio
except ImportError:
    import uasyncio as asyncio

env.load_env(".env")

def load_wifi_networks():
    """Load primary + optional fallback Wi-Fi credentials from .env (WIFI_SSID[_2.._5])."""
    nets = []
    primary_ssid = env.getenv("WIFI_SSID")
    primary_pass = env.getenv("WIFI_PASSWORD")
    if primary_ssid:
        nets.append((primary_ssid, primary_pass or ""))
    for idx in range(2, 6):
        ssid = env.getenv("WIFI_SSID_{}".format(idx))
        if ssid:
            pwd = env.getenv("WIFI_PASSWORD_{}".format(idx)) or ""
            nets.append((ssid, pwd))
    return nets


WIFI_NETWORKS = load_wifi_networks()
HOSTNAME = env.getenv("HOSTNAME", "dashboard")
MDNS_HOST = HOSTNAME.strip().lower().rstrip(".")
if not MDNS_HOST.endswith(".local"):
    MDNS_HOST += ".local"
PORT = int(env.getenv("PORT", "80"))
WEATHER_CITY = env.getenv("WEATHER_CITY", "Manila")
WEATHER_LAT = env.getenv("WEATHER_LAT", "14.5995")
WEATHER_LON = env.getenv("WEATHER_LON", "120.9842")
NOTES_FILE = "notes.txt"
MAX_NOTES_BYTES = 4096
TITLE_FILE = "title.txt"
DEFAULT_TITLE = "Raspberry Pi Pico W"
TZ_FILE = "timezone.txt"

if not WIFI_NETWORKS:
    raise RuntimeError(
        "Missing WIFI_SSID! Copy .env.example to .env and set your Wi-Fi credentials."
    )

connected_ssid = WIFI_NETWORKS[0][0]
connected_password = WIFI_NETWORKS[0][1]

# Hardware peripherals
led = machine.Pin("LED", machine.Pin.OUT)
temp_adc = machine.ADC(4)
wlan = network.WLAN(network.STA_IF)

# Telemetry & LED state
START_MS = time.ticks_ms()
MAX_HISTORY = 30
temp_history = []
latest_temp_c = 0.0
requests_total = 0
client_last_seen = {}  # peer_ip -> ticks_ms
ACTIVE_CLIENT_WINDOW_MS = 30_000

led_mode = "off"       # "off" | "on" | "blink" | "heartbeat" | "morse"
led_state = False
morse_text = ""
led_version = 0
led_event = asyncio.Event()

MORSE_TABLE = {
    "A": ".-",   "B": "-...", "C": "-.-.", "D": "-..",  "E": ".",
    "F": "..-.", "G": "--.",  "H": "....", "I": "..",   "J": ".---",
    "K": "-.-",  "L": ".-..", "M": "--",   "N": "-.",   "O": "---",
    "P": ".--.", "Q": "--.-", "R": ".-.",  "S": "...",  "T": "-",
    "U": "..-",  "V": "...-", "W": ".--",  "X": "-..-", "Y": "-.--",
    "Z": "--..",
    "0": "-----", "1": ".----", "2": "..---", "3": "...--", "4": "....-",
    "5": ".....", "6": "-....", "7": "--...", "8": "---..", "9": "----.",
}


def set_led_hw(on):
    global led_state
    led_state = bool(on)
    led.value(1 if led_state else 0)


def set_led_mode(mode, text=""):
    global led_mode, morse_text, led_version
    led_mode = mode
    morse_text = text[:40] if mode == "morse" else ""
    led_version += 1
    led_event.set()


def read_temperature_c(samples=10):
    """Read RP2040 internal temperature sensor in Celsius (averaged to reduce ADC noise)."""
    total = 0
    for _ in range(samples):
        total += temp_adc.read_u16()
    voltage = (total / samples) * (3.3 / 65535.0)
    return round(27.0 - (voltage - 0.706) / 0.001721, 2)


def get_flash_stats():
    try:
        st = os.statvfs("/")
        bsize = st[0]
        total = bsize * st[2]
        free = bsize * st[3]
        return total, free, total - free
    except Exception:
        return 0, 0, 0


def scan_wifi(limit=12):
    """Scan nearby 2.4 GHz Wi-Fi networks, deduplicated by SSID and sorted by RSSI."""
    sec_map = {0: "Open", 1: "WEP", 2: "WPA", 3: "WPA2", 4: "WPA/WPA2", 5: "WPA2/WPA3"}
    best = {}
    try:
        for entry in wlan.scan():
            ssid = entry[0].decode("utf-8", "ignore").strip() or "(hidden)"
            channel = entry[2]
            rssi = entry[3]
            sec = sec_map.get(entry[4], "Secured")
            if ssid not in best or rssi > best[ssid]["rssi"]:
                best[ssid] = {"ssid": ssid, "channel": channel, "rssi": rssi, "security": sec}
    except Exception as exc:
        return {"networks": [], "error": str(exc)}

    networks = sorted(best.values(), key=lambda x: x["rssi"], reverse=True)[:limit]
    return {"networks": networks, "error": None}


def read_notes():
    try:
        with open(NOTES_FILE, "r") as f:
            return f.read(MAX_NOTES_BYTES)
    except OSError:
        return ""


def write_notes(text):
    clean = str(text)[:MAX_NOTES_BYTES]
    with open(NOTES_FILE, "w") as f:
        f.write(clean)
    return clean


def read_title():
    try:
        with open(TITLE_FILE, "r") as f:
            t = f.read(60).strip()
            return t if t else DEFAULT_TITLE
    except OSError:
        return DEFAULT_TITLE


def write_title(text):
    global dashboard_title
    clean = str(text).strip()[:60] or DEFAULT_TITLE
    with open(TITLE_FILE, "w") as f:
        f.write(clean)
    dashboard_title = clean
    return clean


dashboard_title = read_title()


def read_custom_tz():
    try:
        with open(TZ_FILE, "r") as f:
            return f.read(64).strip()
    except OSError:
        return ""


def write_custom_tz(tz):
    global custom_tz
    clean = str(tz).strip()[:64]
    with open(TZ_FILE, "w") as f:
        f.write(clean)
    custom_tz = clean
    return clean


custom_tz = read_custom_tz()


async def delayed_reboot():
    await asyncio.sleep_ms(300)
    machine.reset()


def get_status(client_ip=""):
    """Collect current telemetry snapshot."""
    gc.collect()
    temp_c = latest_temp_c if temp_history else read_temperature_c()
    temp_f = round(temp_c * 9.0 / 5.0 + 32.0, 2)
    flash_total, flash_free, flash_used = get_flash_stats()

    try:
        rssi = wlan.status("rssi")
    except Exception:
        rssi = None

    now = time.ticks_ms()
    active_clients = sum(
        1 for ts in client_last_seen.values() if time.ticks_diff(now, ts) <= ACTIVE_CLIENT_WINDOW_MS
    )

    return {
        "temp_c": temp_c,
        "temp_f": temp_f,
        "temp_history": temp_history,
        "led_mode": led_mode,
        "led_state": led_state,
        "morse_text": morse_text,
        "uptime_s": time.ticks_diff(now, START_MS) // 1000,
        "mem_free": gc.mem_free(),
        "mem_alloc": gc.mem_alloc(),
        "flash_total": flash_total,
        "flash_free": flash_free,
        "flash_used": flash_used,
        "rssi": rssi,
        "ip": wlan.ifconfig()[0] if wlan.isconnected() else "0.0.0.0",
        "client_ip": client_ip,
        "ssid": connected_ssid,
        "mdns_host": MDNS_HOST,
        "cpu_mhz": machine.freq() // 1_000_000,
        "active_clients": active_clients,
        "unique_clients": len(client_last_seen),
        "requests_total": requests_total,
        "dashboard_title": dashboard_title,
        "custom_tz": custom_tz,
    }


def _order_candidates():
    """Prioritize configured networks currently visible in a Wi-Fi scan, keeping priority order."""
    if len(WIFI_NETWORKS) <= 1:
        return list(WIFI_NETWORKS)

    visible = set()
    try:
        for entry in wlan.scan():
            name = entry[0].decode("utf-8", "ignore").strip()
            if name:
                visible.add(name)
    except Exception:
        return list(WIFI_NETWORKS)

    in_range = [n for n in WIFI_NETWORKS if n[0] in visible]
    not_seen = [n for n in WIFI_NETWORKS if n[0] not in visible]
    return in_range + not_seen


def _try_connect_one(ssid, password, timeout_s=12):
    """Attempt to connect to a single Wi-Fi network; returns True on success."""
    print("Connecting to Wi-Fi:", ssid)
    try:
        wlan.disconnect()
    except Exception:
        pass
    time.sleep_ms(100)
    wlan.connect(ssid, password)

    start = time.ticks_ms()
    while not wlan.isconnected():
        status = wlan.status()
        if status < 0:
            print("Failed to connect to {} (status={})".format(ssid, status))
            return False
        if time.ticks_diff(time.ticks_ms(), start) > timeout_s * 1000:
            print("Timed out connecting to", ssid)
            return False
        set_led_hw(not led_state)
        time.sleep_ms(200)
    return True


def _escape_wifi_qr(val):
    out = []
    for ch in str(val):
        if ch in ("\\", ";", ",", ":"):
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


def get_wifi_qr_info():
    ip = wlan.ifconfig()[0] if wlan.isconnected() else "0.0.0.0"
    if connected_password:
        payload = "WIFI:T:WPA;S:{};P:{};;".format(
            _escape_wifi_qr(connected_ssid),
            _escape_wifi_qr(connected_password),
        )
    else:
        payload = "WIFI:T:nopass;S:{};;".format(_escape_wifi_qr(connected_ssid))
    return {
        "ssid": connected_ssid,
        "wifi_payload": payload,
        "dashboard_url": "http://{}/".format(MDNS_HOST),
        "ip_url": "http://{}/".format(ip),
    }


def connect_wifi(timeout_s=12):
    """Connect to the first available configured 2.4 GHz Wi-Fi network (with fallback)."""
    global connected_ssid, connected_password
    # Set hostname BEFORE activating WLAN so lwIP's built-in mDNS advertises `<HOSTNAME>.local`
    try:
        current_host = network.hostname()
    except Exception:
        current_host = None

    if current_host != HOSTNAME:
        if wlan.active():
            wlan.active(False)
            time.sleep_ms(100)
        try:
            network.hostname(HOSTNAME)
        except AttributeError:
            pass

    wlan.active(True)

    if not wlan.isconnected():
        candidates = _order_candidates()
        for ssid, password in candidates:
            if _try_connect_one(ssid, password, timeout_s=timeout_s):
                connected_ssid = ssid
                connected_password = password
                break
        else:
            set_led_hw(False)
            raise RuntimeError("Could not connect to any configured Wi-Fi network")

    # Confirm connection with a brief double-blink
    set_led_hw(False)
    for _ in range(2):
        set_led_hw(True)
        time.sleep_ms(120)
        set_led_hw(False)
        time.sleep_ms(120)

    ip = wlan.ifconfig()[0]
    print("Connected to {}! Open http://{}/ or http://{}/".format(connected_ssid, MDNS_HOST, ip))
    return ip


async def wait_or_cancel(ms, version):
    """Sleep for `ms` milliseconds, returning False immediately if LED mode changes."""
    if led_version != version:
        return False
    try:
        await asyncio.wait_for_ms(led_event.wait(), ms)
        return False
    except asyncio.TimeoutError:
        return led_version == version


async def led_loop():
    """Background task driving onboard LED modes without blocking HTTP requests."""
    global led_mode, morse_text
    while True:
        led_event.clear()
        version = led_version
        mode = led_mode

        if mode == "off":
            set_led_hw(False)
            await led_event.wait()

        elif mode == "on":
            set_led_hw(True)
            await led_event.wait()

        elif mode == "blink":
            while led_version == version:
                set_led_hw(True)
                if not await wait_or_cancel(500, version):
                    break
                set_led_hw(False)
                if not await wait_or_cancel(500, version):
                    break

        elif mode == "heartbeat":
            while led_version == version:
                set_led_hw(True)
                if not await wait_or_cancel(120, version):
                    break
                set_led_hw(False)
                if not await wait_or_cancel(140, version):
                    break
                set_led_hw(True)
                if not await wait_or_cancel(120, version):
                    break
                set_led_hw(False)
                if not await wait_or_cancel(900, version):
                    break

        elif mode == "morse":
            unit = 140  # ms per Morse dot
            text = morse_text.upper()
            completed = True

            for ch in text:
                if led_version != version:
                    completed = False
                    break
                if ch == " ":
                    set_led_hw(False)
                    if not await wait_or_cancel(unit * 4, version):
                        completed = False
                        break
                    continue

                code = MORSE_TABLE.get(ch)
                if not code:
                    continue

                for symbol in code:
                    set_led_hw(True)
                    dur = unit if symbol == "." else unit * 3
                    if not await wait_or_cancel(dur, version):
                        completed = False
                        break
                    set_led_hw(False)
                    if not await wait_or_cancel(unit, version):
                        completed = False
                        break

                if not completed:
                    break
                if not await wait_or_cancel(unit * 2, version):
                    completed = False
                    break

            if completed and led_version == version:
                set_led_hw(False)
                led_mode = "off"
                morse_text = ""
        else:
            set_led_hw(False)
            await led_event.wait()


async def telemetry_loop():
    """Sample internal temperature every 2 seconds into a ring buffer."""
    global latest_temp_c
    while True:
        latest_temp_c = read_temperature_c()
        temp_history.append(latest_temp_c)
        if len(temp_history) > MAX_HISTORY:
            temp_history.pop(0)
        await asyncio.sleep(2)


async def send_json(writer, payload, status="200 OK"):
    body = json.dumps(payload).encode("utf-8")
    headers = (
        "HTTP/1.1 {}\r\n"
        "Content-Type: application/json\r\n"
        "Content-Length: {}\r\n"
        "Cache-Control: no-store\r\n"
        "Connection: close\r\n\r\n"
    ).format(status, len(body))
    writer.write(headers.encode("utf-8"))
    writer.write(body)
    await writer.drain()


async def send_file(writer, filename, content_type="text/html; charset=utf-8"):
    try:
        with open(filename, "rb") as f:
            writer.write(
                (
                    "HTTP/1.1 200 OK\r\n"
                    "Content-Type: {}\r\n"
                    "Connection: close\r\n\r\n"
                ).format(content_type).encode("utf-8")
            )
            await writer.drain()
            while True:
                chunk = f.read(1024)
                if not chunk:
                    break
                writer.write(chunk)
                await writer.drain()
    except OSError:
        await send_json(writer, {"error": "File not found: " + filename}, "404 Not Found")


def parse_query(qs):
    params = {}
    if not qs:
        return params
    for pair in qs.split("&"):
        if "=" in pair:
            k, v = pair.split("=", 1)
            params[k] = v
        elif pair:
            params[pair] = ""
    return params


async def handle_client(reader, writer):
    """Handle an incoming HTTP request."""
    global requests_total
    try:
        requests_total += 1
        try:
            peer = writer.get_extra_info("peername")
            peer_ip = peer[0] if peer else "unknown"
        except Exception:
            peer_ip = "unknown"
        if len(client_last_seen) >= 32 and peer_ip not in client_last_seen:
            oldest = min(client_last_seen, key=lambda k: client_last_seen[k])
            del client_last_seen[oldest]
        client_last_seen[peer_ip] = time.ticks_ms()

        request_line = await asyncio.wait_for(reader.readline(), 5)
        if not request_line:
            return

        parts = request_line.decode("utf-8", "ignore").strip().split()
        if len(parts) < 2:
            return
        method = parts[0]
        raw_path = parts[1]
        if "?" in raw_path:
            path, qs = raw_path.split("?", 1)
        else:
            path, qs = raw_path, ""
        query = parse_query(qs)

        content_length = 0
        while True:
            header_line = await asyncio.wait_for(reader.readline(), 5)
            if not header_line or header_line == b"\r\n":
                break
            header_str = header_line.decode("utf-8", "ignore")
            if header_str.lower().startswith("content-length:"):
                try:
                    content_length = int(header_str.split(":", 1)[1].strip())
                except ValueError:
                    content_length = 0

        body = b""
        if content_length > 0:
            body = await asyncio.wait_for(reader.readexactly(min(content_length, 4608)), 5)

        if method == "GET" and path in ("/", "/index.html"):
            await send_file(writer, "index.html")

        elif method == "GET" and path == "/qr.js":
            await send_file(writer, "qr.js", "application/javascript; charset=utf-8")

        elif method == "GET" and path == "/api/status":
            await send_json(writer, get_status(client_ip=peer_ip))

        elif method == "GET" and path == "/api/wifi/qr":
            await send_json(writer, get_wifi_qr_info())

        elif method == "GET" and path == "/api/news":
            feed = query.get("feed", "world").lower()
            refresh = query.get("refresh", "0") in ("1", "true", "yes")
            await send_json(writer, news.get_headlines(feed_id=feed, force=refresh))

        elif method == "GET" and path == "/api/weather":
            refresh = query.get("refresh", "0") in ("1", "true", "yes")
            await send_json(
                writer,
                weather.get_weather(WEATHER_CITY, WEATHER_LAT, WEATHER_LON, force=refresh),
            )

        elif method == "POST" and path == "/api/weather/location":
            try:
                data = json.loads(body.decode("utf-8")) if body else {}
            except ValueError:
                await send_json(writer, {"error": "Invalid JSON"}, "400 Bad Request")
                return
            try:
                result = weather.set_location(
                    city=data.get("city", ""),
                    auto=bool(data.get("auto", False)),
                    fallback_city=WEATHER_CITY,
                    fallback_lat=WEATHER_LAT,
                    fallback_lon=WEATHER_LON,
                )
                await send_json(writer, result)
            except Exception as exc:
                await send_json(writer, {"error": str(exc)}, "400 Bad Request")

        elif method == "GET" and path == "/api/wifi/scan":
            await send_json(writer, scan_wifi())

        elif method == "GET" and path == "/api/notes":
            await send_json(writer, {"text": read_notes(), "max_len": MAX_NOTES_BYTES})

        elif method == "POST" and path == "/api/notes":
            try:
                data = json.loads(body.decode("utf-8")) if body else {}
            except ValueError:
                await send_json(writer, {"error": "Invalid JSON"}, "400 Bad Request")
                return
            saved = write_notes(data.get("text", ""))
            await send_json(writer, {"ok": True, "length": len(saved)})

        elif method == "POST" and path == "/api/title":
            try:
                data = json.loads(body.decode("utf-8")) if body else {}
            except ValueError:
                await send_json(writer, {"error": "Invalid JSON"}, "400 Bad Request")
                return
            saved = write_title(data.get("title", ""))
            await send_json(writer, {"ok": True, "dashboard_title": saved})

        elif method == "POST" and path == "/api/timezone":
            try:
                data = json.loads(body.decode("utf-8")) if body else {}
            except ValueError:
                await send_json(writer, {"error": "Invalid JSON"}, "400 Bad Request")
                return
            saved = write_custom_tz(data.get("timezone", ""))
            await send_json(writer, {"ok": True, "custom_tz": saved})

        elif method == "POST" and path == "/api/reboot":
            await send_json(writer, {"ok": True, "message": "Rebooting Pico W..."})
            asyncio.create_task(delayed_reboot())

        elif method == "POST" and path == "/api/led":
            try:
                data = json.loads(body.decode("utf-8")) if body else {}
            except ValueError:
                await send_json(writer, {"error": "Invalid JSON"}, "400 Bad Request")
                return

            mode = str(data.get("mode", "")).lower()
            if mode not in ("off", "on", "blink", "heartbeat", "morse"):
                await send_json(writer, {"error": "Unsupported LED mode"}, "400 Bad Request")
                return

            text = str(data.get("text", "")).strip()
            if mode == "morse" and not text:
                await send_json(writer, {"error": "Morse mode requires non-empty text"}, "400 Bad Request")
                return

            set_led_mode(mode, text)
            await send_json(writer, {"ok": True, "led_mode": led_mode, "morse_text": morse_text})

        else:
            await send_json(writer, {"error": "Not found"}, "404 Not Found")

    except Exception as exc:
        print("Request error:", exc)
    finally:
        try:
            await writer.aclose()
        except Exception:
            pass
        gc.collect()


async def main():
    connect_wifi()

    asyncio.create_task(telemetry_loop())
    asyncio.create_task(led_loop())

    server = await asyncio.start_server(handle_client, "0.0.0.0", PORT)
    print("Dashboard server listening on port", PORT)

    while True:
        # Keep Wi-Fi alive if the router drops the connection
        if not wlan.isconnected():
            print("Wi-Fi disconnected, reconnecting...")
            try:
                connect_wifi()
            except Exception as exc:
                print("Reconnect failed:", exc)
        await asyncio.sleep(10)


try:
    asyncio.run(main())
finally:
    set_led_hw(False)
    asyncio.new_event_loop()
