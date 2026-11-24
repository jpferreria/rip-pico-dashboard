# Raspberry Pi Pico W — Web Control & Telemetry Dashboard

A standalone MicroPython Wi-Fi web app for the **Raspberry Pi Pico W / Pico 2 W** requiring **no additional hardware**.

![Pico W Dashboard Screenshot](docs/dashboard-screenshot.jpg)

## Features
- **Onboard LED Control:** Switch between `Off`, `On`, `Blink`, `Heartbeat`, or flash custom text in **Morse code**.
- **Live Temperature Chart:** Reads the built-in temperature sensor (`ADC(4)`) every 2 seconds and renders a 60-second SVG sparkline.
- **System Telemetry:** Live Wi-Fi signal strength (RSSI), RAM usage, uptime, IP address, and CPU frequency.
- **News Headlines:** Fetches live headlines directly from the Pico W for **BBC World**, **BBC Tech**, **BBC Science**, and **Hacker News** with 10-minute in-memory caching.
- **Zero External Dependencies:** `index.html` is streamed directly from flash and adapts automatically to light/dark mode.

## Setup

### 1. Flash MicroPython (if not already installed)
1. Download the latest **Raspberry Pi Pico W** `.uf2` firmware from [micropython.org/download/RPI_PICO_W/](https://micropython.org/download/RPI_PICO_W/).
2. Hold down the **BOOTSEL** button on the Pico W while plugging it into your Mac via USB.
3. Drag and drop the `.uf2` file onto the `RPI-RP2` drive. The board will reboot automatically.

### 2. Configure Environment Variables
Copy `.env.example` to `.env` and enter your **2.4 GHz** Wi-Fi network credentials:

```bash
cp .env.example .env
```

Edit `.env`:
```dotenv
WIFI_SSID=your-2.4ghz-ssid
WIFI_PASSWORD=your-wifi-password
HOSTNAME=pico-w-dashboard
PORT=80
```

### 3. Upload Files to the Pico W

**Option A — Using `mpremote` (terminal):**
```bash
pip install mpremote
mpremote cp .env env.py news.py index.html main.py :
mpremote reset
mpremote repl
```

**Option B — Using Thonny IDE:**
1. Open Thonny and select **MicroPython (Raspberry Pi Pico)** in the bottom-right interpreter menu.
2. Upload `.env`, `env.py`, `news.py`, `index.html`, and `main.py` to the Raspberry Pi Pico root directory.
3. Click **Run** (or press the reset/replug USB).

### 4. Open the Dashboard
- While connecting, the onboard LED blinks rapidly; once connected, it double-blinks and prints the URL in the serial console:
  ```text
  Connected! Open http://192.168.x.x/ in your browser.
  ```
- Open that URL from any phone or computer on the same Wi-Fi network.

## API Endpoints
- `GET /` — Serves `index.html`
- `GET /api/status` — Returns JSON telemetry and LED state
- `POST /api/led` — Updates LED mode, e.g. `{"mode": "morse", "text": "SOS"}`
- `GET /api/news?feed=world&refresh=0` — Returns cached or refreshed headlines (`world`, `tech`, `science`, `hn`)
