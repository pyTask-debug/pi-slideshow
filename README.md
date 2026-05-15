# pi-slideshow
A portable photo slideshow system built with a **Raspberry Pi Zero W** and the **Waveshare Zero LCD HAT A** (triple screen HAT).

---

## Hardware

| Screen | Size | Controller | Interface |
|---|---|---|---|
| Central | 1.3" 240×240 | ST7789 | SPI1 device 0 |
| Left | 0.96" 160×80 | ST7735S | SPI0 CE0 |
| Right | 0.96" 160×80 | ST7735S | SPI0 CE1 |

---

## What it does

- **Central screen** — cycles through random images from `~/horizontal` and `~/vertical` every 7 seconds. Horizontal images are resized to fill the screen; vertical images are letterboxed with black bars to preserve aspect ratio.
- **Left screen** — shows RAM usage and CPU % with animated arc gauges, updated every 2 seconds.
- **Right screen** — shows CPU temperature and disk usage %, updated every 2 seconds.
- **Physical button** (GPIO 26) — hold to safely shut down the Pi.
- **Telegram bot** (optional) — send `/shutdown` or `/status` to the bot to control the Pi remotely over Wi-Fi.

---

## Requirements

```bash
pip install pillow psutil spidev RPi.GPIO numpy python-telegram-bot python-dotenv
```

Enable SPI0 and SPI1 in `raspi-config` → Interface Options.

---

## Setup

1. Clone the repo:
   ```bash
   git clone https://github.com/YOUR_USERNAME/pi-slideshow.git
   cd pi-slideshow
   ```

2. Copy the example env file and fill in your values:
   ```bash
   cp .env.example .env
   nano .env
   ```

3. Put your images in the home directory:
   ```
   ~/horizontal/   ← landscape photos
   ~/vertical/     ← portrait photos
   ```

4. Run the script:
   ```bash
   python3 slideshow.py
   ```

5. (Optional) To run automatically on boot, add to crontab:
   ```bash
   @reboot python3 /home/pi/pi-slideshow/slideshow.py
   ```

---

## Telegram bot (optional)

Create a bot via [@BotFather](https://t.me/BotFather) on Telegram and get your token and chat ID, then add them to `.env`:

```
TELEGRAM_TOKEN=your_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
```

Available commands:
- `/status` — returns CPU temp, RAM %, and CPU %
- `/shutdown` — safely shuts down the Pi

> The bot only works when the Pi is connected to Wi-Fi.

---

## Environment variables

Copy `.env.example` to `.env` and fill in your credentials. **Never commit the `.env` file** — it is already listed in `.gitignore`.

| Variable | Description |
|---|---|
| `TELEGRAM_TOKEN` | Token from @BotFather |
| `TELEGRAM_CHAT_ID` | Your personal Telegram chat ID |
