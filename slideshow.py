#!/usr/bin/env python3
"""
Slideshow with Triple LCD HAT (Waveshare Zero LCD HAT A)

Central screen (1.3" 240x240) - ST7789 - SPI1 device 0
→ random images from ~/horizontal and ~/vertical
→ horizontal: resize to 240x240
→ vertical: letterbox with black bars on the sides (aspect ratio preserved)
→ changes every 10 seconds
Left screen (0.96" 160x80) - ST7735S - SPI0 CE0
→ RAM usage + CPU %
→ updates every 2 seconds
Right screen (0.96" 160x80) - ST7735S - SPI0 CE1
→ Disk used/free + IP address
→ updates every 2 seconds
"""

import time
import threading
import random
import socket
import spidev
import RPi.GPIO as GPIO
import psutil
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path
import gc
import subprocess
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
import os
from dotenv import load_dotenv
load_dotenv()

# ─── PIN CONFIGURATION (GPIO BCM) ────────────────────────────────────────────
# Central screen ST7789 (1.3" 240x240) - SPI1 device 0 (CS via GPIO)
LCD_MAIN_DC  = 22
LCD_MAIN_CS  = 18
LCD_MAIN_RST = 27
LCD_MAIN_BL  = 19

# LEFT SCREEN ST7735S (0.96") - SPI0 CE0 (device 0)
LCD_LEFT_DC  = 4
LCD_LEFT_CS  = 8
LCD_LEFT_RST = 24
LCD_LEFT_BL  = 13

# RIGHT SCREEN ST7735S (0.96") - SPI0 CE1 (device 1)
LCD_RIGHT_DC  = 5
LCD_RIGHT_CS  = 7
LCD_RIGHT_RST = 23
LCD_RIGHT_BL  = 12

SPI_SPEED_MAIN = 40_000_000   # ST7789 central
SPI_SPEED_SIDE = 10_000_000   # ST7735S lateral (Waveshare uses 10MHz)

MAIN_W, MAIN_H = 240, 240
SIDE_W, SIDE_H = 160, 80

HOME      = Path.home()
DIR_HORIZ = HOME / "FOLDER_NAME1"
DIR_VERT  = HOME / "FOLDER_NAME2"
EXTS      = {".jpg", ".jpeg", ".png", ".bmp"}

# ─── TELEGRAM ─────────────────────────────────────────────────────────────────────
'''
In order to sudo shutdown -h now the pi through Linux, I programmed a bot Telegram.
Then I realized my LCD screen had programmable buttons which I eventually used to safely shutdown the
pi. Anyway I leave the script, it may be useful for someone. The annoying thing is 
that in order to be shut down, the pi needs to be connected to Wifi, which is a condition 
not always available.
'''

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID", "0"))

import asyncio

class TelegramBotThread(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True, name="TelegramBot")

    async def _shutdown(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.id != TELEGRAM_CHAT_ID:
            await update.message.reply_text("Not authorized.")
            return
        await update.message.reply_text("Shutdown...")
        subprocess.run(["sudo", "shutdown", "-h", "now"])

    async def _status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.id != TELEGRAM_CHAT_ID:
            return
        ram   = psutil.virtual_memory()
        cpu   = psutil.cpu_percent(interval=1)
        temp  = psutil.sensors_temperatures().get("cpu_thermal", [{}])[0]
        temp_c = temp.current if temp else 0.0
        msg = (
            f" Pi in execution\n"
            f" Temp: {temp_c:.0f}°C\n"
            f" RAM: {ram.percent:.0f}%\n"
            f" CPU: {cpu:.0f}%"
        )
        await update.message.reply_text(msg)

    async def _run_bot(self):
        app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
        app.add_handler(CommandHandler("shutdown", self._shutdown))
        app.add_handler(CommandHandler("status",   self._status))
        async with app:
            await app.start()
            await app.updater.start_polling()
            while True:
                await asyncio.sleep(1)

    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self._run_bot())

# ─── COLORS (RGB) ─────────────────────────────────────────────────────────────
BG      = (10,  10,  20)
ACCENT1 = (80,  200, 120)   #  RAM  
ACCENT2 = (80,  160, 255)   #  CPU 
ACCENT3 = (255, 180,  50)   #  DISK
WHITE   = (255, 255, 255)
GRAY    = (140, 140, 140)


# ─── FONT ─────────────────────────────────────────────────────────────────────
def _load_font(size):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()

FONT_BIG   = _load_font(22)
FONT_MED   = _load_font(14)
FONT_SMALL = _load_font(11)

# ─── ROTATION ──────────────────────────────────────────────────────────────────
from PIL import ImageOps

def open_with_exif(path):
    """Open the image and automatically applies EXIF rotation."""
    img = Image.open(path).convert("RGB")
    return ImageOps.exif_transpose(img)

# ─── UTILITY ──────────────────────────────────────────────────────────────────
def list_images(folder):
    if not folder.exists():
        print(f"[WARN] Folder not found: {folder}")
        return []
    paths = sorted(f for f in folder.iterdir() if f.suffix.lower() in EXTS)
    print(f"[INFO] {folder.name}: found {len(paths)} images")
    return paths


import numpy as np

def to_rgb565(img):
    arr = np.array(img, dtype=np.uint16)
    r = arr[:, :, 0]
    g = arr[:, :, 1]
    b = arr[:, :, 2]
    rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
    # swap byte order (big-endian for display)
    rgb565 = ((rgb565 >> 8) | (rgb565 << 8)) & 0xFFFF
    return rgb565.astype(np.uint16).tobytes()


def open_spi(bus, device, speed):
    spi = spidev.SpiDev()
    spi.open(bus, device)
    spi.max_speed_hz = speed
    spi.mode = 0
    return spi


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "N/A"


def draw_bar(draw, x, y, w, h, pct, fg, bg_bar=(40, 40, 60)):
    """Horizontal percetage bar."""
    draw.rectangle([x, y, x + w - 1, y + h - 1], fill=bg_bar)
    fill_w = max(2, int(w * pct / 100))
    draw.rectangle([x, y, x + fill_w - 1, y + h - 1], fill=fg)


def fit_letterbox(path, width, height):
    src = Image.open(path)
    src.draft("RGB", (width, height))
    src = src.convert("RGB")
    src = ImageOps.exif_transpose(src)   # corrects EXIF orientation
    src = src.rotate(-90, expand=True)   # 90° towards the right 
    src.thumbnail((width, height), Image.LANCZOS)
    canvas = Image.new("RGB", (width, height), (0, 0, 0))
    offset = ((width - src.width) // 2, (height - src.height) // 2)
    canvas.paste(src, offset)
    src.close()
    return canvas


# ─── SYSTEM INFO RENDERING ────────────────────────────────────────────────
import math

def _lerp_color(c1, c2, t):
    return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))

def _draw_arc_gauge(draw, cx, cy, radius, lw, pct, color_start, color_end, steps=60):
    """
    Visualization arch
    """
    start_deg = 225
    total_deg = 270

    # Arch background
    for s in range(steps):
        angle_s = math.radians(start_deg + s * total_deg / steps)
        angle_e = math.radians(start_deg + (s + 1) * total_deg / steps)
        x0 = cx + (radius - lw) * math.cos(angle_s)
        y0 = cy + (radius - lw) * math.sin(angle_s)
        x1 = cx + (radius + lw) * math.cos(angle_s)
        y1 = cy + (radius + lw) * math.sin(angle_s)
        x2 = cx + (radius + lw) * math.cos(angle_e)
        y2 = cy + (radius + lw) * math.sin(angle_e)
        x3 = cx + (radius - lw) * math.cos(angle_e)
        y3 = cy + (radius - lw) * math.sin(angle_e)
        draw.polygon([(x0,y0),(x1,y1),(x2,y2),(x3,y3)], fill=(30, 30, 50))

    fill_steps = max(1, int(steps * pct))
    for s in range(fill_steps):
        t = s / max(fill_steps - 1, 1)
        color = _lerp_color(color_start, color_end, t)
        angle_s = math.radians(start_deg + s * total_deg / steps)
        angle_e = math.radians(start_deg + (s + 1) * total_deg / steps)
        x0 = cx + (radius - lw) * math.cos(angle_s)
        y0 = cy + (radius - lw) * math.sin(angle_s)
        x1 = cx + (radius + lw) * math.cos(angle_s)
        y1 = cy + (radius + lw) * math.sin(angle_s)
        x2 = cx + (radius + lw) * math.cos(angle_e)
        y2 = cy + (radius + lw) * math.sin(angle_e)
        x3 = cx + (radius - lw) * math.cos(angle_e)
        y3 = cy + (radius - lw) * math.sin(angle_e)
        draw.polygon([(x0,y0),(x1,y1),(x2,y2),(x3,y3)], fill=color)

    # Small light dot on the edge
    if pct > 0.01:
        tip_angle = math.radians(start_deg + pct * total_deg)
        tx = cx + radius * math.cos(tip_angle)
        ty = cy + radius * math.sin(tip_angle)
        tip_color = _lerp_color(color_start, color_end, pct)
        r_glow = lw + 2
        draw.ellipse([(tx - r_glow, ty - r_glow), (tx + r_glow, ty + r_glow)],
                     fill=tip_color)


def _draw_panel(draw, cx, cy, radius, lw, pct, label, value_str,
                color_start, color_end):
    _draw_arc_gauge(draw, cx, cy, radius, lw, pct, color_start, color_end)
    tip_color = _lerp_color(color_start, color_end, pct)
    draw.text((cx, cy - 4),  value_str, font=FONT_BIG,   fill=tip_color,   anchor="mm")
    draw.text((cx, cy + 16), label,     font=FONT_SMALL, fill=(160,160,180), anchor="mm")


def render_left(width=SIDE_W, height=SIDE_H):
    """Left: RAM % + CPU %"""
    img  = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(img)

    ram = psutil.virtual_memory()
    cpu = psutil.cpu_percent(interval=None)

    # Left Panel: RAM 
    _draw_panel(draw,
                cx=40, cy=46, radius=28, lw=5,
                pct=ram.percent / 100,
                label="RAM",
                value_str=f"{ram.percent:.0f}%",
                color_start=(167, 139, 250),
                color_end=(96,  165, 250))

    # Vertical separator
    draw.line([(width // 2, 10), (width // 2, height - 10)],
              fill=(40, 40, 60), width=1)

    # Right Panel: CPU 
    _draw_panel(draw,
                cx=120, cy=46, radius=28, lw=5,
                pct=min(cpu, 100) / 100,
                label="CPU",
                value_str=f"{cpu:.0f}%",
                color_start=(52, 211, 153),
                color_end=(6,  182, 212))

    return img


def render_right(width=SIDE_W, height=SIDE_H):
    """Right: Temperature + Disk %"""
    img  = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(img)

    disk = psutil.disk_usage("/")
    temps = psutil.sensors_temperatures()
    temp_list = temps.get("cpu_thermal", temps.get("cpu-thermal", []))
    temp_c = temp_list[0].current if temp_list else 0.0

    # Temperature color: green < 55°, orange < 70°, otherwise red
    if temp_c < 55:
        t_start, t_end = (74, 222, 128), (34, 211, 238)
    elif temp_c < 70:
        t_start, t_end = (250, 204, 21), (251, 146, 60)
    else:
        t_start, t_end = (248, 113, 113), (239, 68, 68)

    # Left Panel: Temperature
    _draw_panel(draw,
                cx=40, cy=46, radius=28, lw=5,
                pct=min(temp_c, 100) / 100,
                label="TEMP",
                value_str=f"{temp_c:.0f}°",
                color_start=t_start,
                color_end=t_end)

    # Vertical separator
    draw.line([(width // 2, 10), (width // 2, height - 10)],
              fill=(40, 40, 60), width=1)

    # Right Panel: Disk 
    _draw_panel(draw,
                cx=120, cy=46, radius=28, lw=5,
                pct=disk.percent / 100,
                label="DISK",
                value_str=f"{disk.percent:.0f}%",
                color_start=(251, 146, 60),
                color_end=(244, 63,  94))

    return img


# ─── DRIVER BASE ──────────────────────────────────────────────────────────────
class LCDBase:
    def __init__(self, spi, dc, rst, cs, bl):
        self.spi = spi
        self.dc  = dc
        self.rst = rst
        self.cs  = cs
        GPIO.setup(dc,  GPIO.OUT)
        GPIO.setup(rst, GPIO.OUT)
        GPIO.setup(cs,  GPIO.OUT)
        GPIO.setup(bl,  GPIO.OUT)
        GPIO.output(cs, GPIO.HIGH)
        GPIO.output(bl, GPIO.HIGH)

    def _reset(self):
        GPIO.output(self.rst, GPIO.HIGH); time.sleep(0.05)
        GPIO.output(self.rst, GPIO.LOW);  time.sleep(0.05)
        GPIO.output(self.rst, GPIO.HIGH); time.sleep(0.15)

    def _cmd(self, cmd):
        GPIO.output(self.dc, GPIO.LOW)
        GPIO.output(self.cs, GPIO.LOW)
        self.spi.writebytes([cmd])
        GPIO.output(self.cs, GPIO.HIGH)

    def _data(self, data):
        GPIO.output(self.dc, GPIO.HIGH)
        GPIO.output(self.cs, GPIO.LOW)
        if isinstance(data, int):
            self.spi.writebytes([data])
        else:
            for i in range(0, len(data), 4096):
                self.spi.writebytes(list(data[i:i+4096]))
        GPIO.output(self.cs, GPIO.HIGH)


# ─── ST7789 (central screen 240x240) ───────────────────────────────────────
class ST7789(LCDBase):
    def __init__(self, spi, dc, rst, cs, bl, width=240, height=240):
        super().__init__(spi, dc, rst, cs, bl)
        self.width  = width
        self.height = height
        self._reset()
        self._cmd(0x01); time.sleep(0.15)
        self._cmd(0x11); time.sleep(0.12)
        self._cmd(0x3A); self._data(0x05)
        self._cmd(0x36); self._data(0x00)
        self._cmd(0x21)
        self._cmd(0x13)
        self._cmd(0x29); time.sleep(0.05)

    def show(self, rgb565_bytes):
        w, h = self.width, self.height
        self._cmd(0x2A)
        self._data([0x00, 0x00, (w-1) >> 8, (w-1) & 0xFF])
        self._cmd(0x2B)
        self._data([0x00, 0x00, (h-1) >> 8, (h-1) & 0xFF])
        self._cmd(0x2C)
        self._data(rgb565_bytes)


# ─── ST7735S (side screens 160x80) ───────────────────────────────────────
class ST7735S(LCDBase):
    def __init__(self, spi, dc, rst, cs, bl, width=160, height=80):
        super().__init__(spi, dc, rst, cs, bl)
        self.width  = width
        self.height = height
        self._reset()
        self._cmd(0x11); time.sleep(0.1)
        self._cmd(0x21)
        self._cmd(0x21)
        self._cmd(0xB1); self._data(0x05); self._data(0x3A); self._data(0x3A)
        self._cmd(0xB2); self._data(0x05); self._data(0x3A); self._data(0x3A)
        self._cmd(0xB3); self._data(0x05); self._data(0x3A); self._data(0x3A)
        self._data(0x05); self._data(0x3A); self._data(0x3A)
        self._cmd(0xB4); self._data(0x03)
        self._cmd(0xC0); self._data(0x62); self._data(0x02); self._data(0x04)
        self._cmd(0xC1); self._data(0xC0)
        self._cmd(0xC2); self._data(0x0D); self._data(0x00)
        self._cmd(0xC3); self._data(0x8D); self._data(0x6A)
        self._cmd(0xC4); self._data(0x8D); self._data(0xEE)
        self._cmd(0xC5); self._data(0x0E)
        self._cmd(0xE0)
        for b in [0x10,0x0E,0x02,0x03,0x0E,0x07,0x02,0x07,
                  0x0A,0x12,0x27,0x37,0x00,0x0D,0x0E,0x10]:
            self._data(b)
        self._cmd(0xE1)
        for b in [0x10,0x0E,0x03,0x03,0x0F,0x06,0x02,0x08,
                  0x0A,0x13,0x26,0x36,0x00,0x0D,0x0E,0x10]:
            self._data(b)
        self._cmd(0x3A); self._data(0x05)   # RGB565
        self._cmd(0x36); self._data(0xA8)   # MADCTL: MY+MV+BGR
        self._cmd(0x29)                      # Display ON

    def show(self, rgb565_bytes):
        # Offset da Waveshare SetWindows: Xstart+1, Ystart+26
        x0 = 1
        y0 = 26
        x1 = x0 + self.width  - 1
        y1 = y0 + self.height - 1
        self._cmd(0x2A)
        self._data([x0 >> 8, x0 & 0xFF, x1 >> 8, x1 & 0xFF])
        self._cmd(0x2B)
        self._data([y0 >> 8, y0 & 0xFF, y1 >> 8, y1 & 0xFF])
        self._cmd(0x2C)
        self._data(rgb565_bytes)


# ─── THREAD: CENTRAL SLIDESHOW (picks random images from the folder) ────────────────────────────
class SlideshowThread(threading.Thread):
    def __init__(self, display, horiz_paths, vert_paths, interval, lock):
        super().__init__(daemon=True, name="Central")
        self.display     = display
        self.all_paths   = horiz_paths + vert_paths
        self.interval    = interval
        self.lock        = lock
        self._stop       = threading.Event()
        random.shuffle(self.all_paths)
        self._idx        = 0

    def stop(self):
        self._stop.set()

    def _next_path(self):
        if not self.all_paths:
            return None
        path = self.all_paths[self._idx]
        self._idx = (self._idx + 1) % len(self.all_paths)
        if self._idx == 0:
            random.shuffle(self.all_paths)
        return path

    def run(self):
        if not self.all_paths:
            print("[Central] No image found, thread in standby.")
            return
        while not self._stop.is_set():
            path = self._next_path()
            print(f"[Central] Uploading: {path.name}")
            try:
                print(f"[Central] fit_letterbox...")
                img  = fit_letterbox(path, MAIN_W, MAIN_H)
                print(f"[Central] to_rgb565...")
                data = to_rgb565(img)
                img.close()
                del img   
                print(f"[Central] show...")
                with self.lock:
                    self.display.show(data)
                print(f"[Central] OK: {path.name}")
                del data
                gc.collect()
            except Exception as e:
                print(f"[Central] Error {path.name}: {e}")
            print(f"[Central] sleep {self.interval}s...")
            self._stop.wait(self.interval)


# ─── THREAD: SYSTEM INFO (side screens) ──────────────────────────────
class SysInfoThread(threading.Thread):
    def __init__(self, display, render_fn, interval, lock, name):
        super().__init__(daemon=True, name=name)
        self.display   = display
        self.render_fn = render_fn
        self.interval  = interval
        self.lock      = lock
        self._stop     = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        psutil.cpu_percent(interval=None)   # warm-up: first value always 0
        time.sleep(0.3)
        while not self._stop.is_set():
            try:
                img  = self.render_fn()
                data = to_rgb565(img)
                img.close()
                with self.lock:
                    self.display.show(data)
            except Exception as e:
                print(f"[{self.name}] Rendering error : {e}")
            self._stop.wait(self.interval)


# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    
    # Shutdown button
    SHUTDOWN_BTN = 26
    GPIO.setup(SHUTDOWN_BTN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    
    def _check_shutdown():
        while True:
            if GPIO.input(SHUTDOWN_BTN) == GPIO.LOW:
                time.sleep(1)
                if GPIO.input(SHUTDOWN_BTN) == GPIO.LOW:
                    import os
                    os.system("sudo shutdown -h now")
            time.sleep(0.1)
    
    threading.Thread(target=_check_shutdown, daemon=True).start()

    spi1  = open_spi(1, 0, SPI_SPEED_MAIN)  # central  (SPI1 device 0)
    spi0a = open_spi(0, 0, SPI_SPEED_SIDE)  # left  (SPI0 CE0)
    spi0b = open_spi(0, 1, SPI_SPEED_SIDE)  # right    (SPI0 CE1)

    lock_main  = threading.Lock()
    lock_left  = threading.Lock()
    lock_right = threading.Lock()

    print("Initializing screens...")
    main_disp  = ST7789( spi1,  LCD_MAIN_DC,  LCD_MAIN_RST,  LCD_MAIN_CS,  LCD_MAIN_BL)
    left_disp  = ST7735S(spi0a, LCD_LEFT_DC,  LCD_LEFT_RST,  LCD_LEFT_CS,  LCD_LEFT_BL)
    right_disp = ST7735S(spi0b, LCD_RIGHT_DC, LCD_RIGHT_RST, LCD_RIGHT_CS, LCD_RIGHT_BL)

    horiz_paths = list_images(DIR_HORIZ)
    vert_paths  = list_images(DIR_VERT)

    t_main  = SlideshowThread(main_disp, horiz_paths, vert_paths, 7, lock_main)
    t_left  = SysInfoThread(left_disp,  render_left,  2, lock_left,  "Left")
    t_right = SysInfoThread(right_disp, render_right, 2, lock_right, "Right")

    t_main.start()
    t_left.start()
    t_right.start()
    
    t_bot = TelegramBotThread()
    t_bot.start()

    print("Started! Ctrl+C to esc.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStop...")
        for t in (t_main, t_left, t_right, t_bot):
            t.stop()
        for t in (t_main, t_left, t_right):
            t.join(timeout=5)

    spi1.close()
    spi0a.close()
    spi0b.close()
    GPIO.cleanup()
    print("Out.")


if __name__ == "__main__":
    main()
