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
import time

import env
import news

try:
    import asyncio
except ImportError:
    import uasyncio as asyncio

env.load_env(".env")

WIFI_SSID = env.getenv("WIFI_SSID")
WIFI_PASSWORD = env.getenv("WIFI_PASSWORD")
HOSTNAME = env.getenv("HOSTNAME", "pico-w-dashboard")
PORT = int(env.getenv("PORT", "80"))

if not WIFI_SSID or not WIFI_PASSWORD:
    raise RuntimeError(
        "Missing WIFI_SSID or WIFI_PASSWORD! Copy .env.example to .env and set your Wi-Fi credentials."
    )

# Hardware peripherals
led = machine.Pin("LED", machine.Pin.OUT)
temp_adc = machine.ADC(4)
wlan = network.WLAN(network.STA_IF)

# Telemetry & LED state
START_MS = time.ticks_ms()
MAX_HISTORY = 30
temp_history = []
latest_temp_c = 0.0

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


def get_status():
    """Collect current telemetry snapshot."""
    gc.collect()
    temp_c = latest_temp_c if temp_history else read_temperature_c()
    temp_f = round(temp_c * 9.0 / 5.0 + 32.0, 2)

    try:
        rssi = wlan.status("rssi")
    except Exception:
        rssi = None

    return {
        "temp_c": temp_c,
        "temp_f": temp_f,
        "temp_history": temp_history,
        "led_mode": led_mode,
        "led_state": led_state,
        "morse_text": morse_text,
        "uptime_s": time.ticks_diff(time.ticks_ms(), START_MS) // 1000,
        "mem_free": gc.mem_free(),
        "mem_alloc": gc.mem_alloc(),
        "rssi": rssi,
        "ip": wlan.ifconfig()[0] if wlan.isconnected() else "0.0.0.0",
        "ssid": WIFI_SSID,
        "cpu_mhz": machine.freq() // 1_000_000,
    }


def connect_wifi(timeout_s=20):
    """Connect to 2.4 GHz Wi-Fi with onboard LED visual feedback."""
    wlan.active(True)
    try:
        network.hostname(HOSTNAME)
    except AttributeError:
        pass

    if not wlan.isconnected():
        print("Connecting to Wi-Fi:", WIFI_SSID)
        wlan.connect(WIFI_SSID, WIFI_PASSWORD)

        start = time.ticks_ms()
        while not wlan.isconnected():
            status = wlan.status()
            if status < 0:
                set_led_hw(False)
                raise RuntimeError("Wi-Fi connection failed (status={})".format(status))
            if time.ticks_diff(time.ticks_ms(), start) > timeout_s * 1000:
                set_led_hw(False)
                raise RuntimeError("Wi-Fi connection timed out")
            set_led_hw(not led_state)
            time.sleep_ms(200)

    # Confirm connection with a brief double-blink
    set_led_hw(False)
    for _ in range(2):
        set_led_hw(True)
        time.sleep_ms(120)
        set_led_hw(False)
        time.sleep_ms(120)

    ip = wlan.ifconfig()[0]
    print("Connected! Open http://{}/ in your browser.".format(ip))
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
    try:
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
            body = await asyncio.wait_for(reader.readexactly(min(content_length, 1024)), 5)

        if method == "GET" and path in ("/", "/index.html"):
            await send_file(writer, "index.html")

        elif method == "GET" and path == "/api/status":
            await send_json(writer, get_status())

        elif method == "GET" and path == "/api/news":
            feed = query.get("feed", "world").lower()
            refresh = query.get("refresh", "0") in ("1", "true", "yes")
            await send_json(writer, news.get_headlines(feed_id=feed, force=refresh))

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
