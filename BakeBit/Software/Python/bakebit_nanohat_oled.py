#!/usr/bin/env python3
import bakebit_128_64_oled as oled
from PIL import Image, ImageFont, ImageDraw
import time
import sys
import subprocess
import threading
import signal
import os
import socket
import fcntl
import struct
import logging

# Add logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/var/log/oled.log'),
        logging.StreamHandler()
    ]
)

logging.info("Starting OLED script...")

# ============================================================
# Volume control (ALSA hardware mixer -- Aux zone)
# ============================================================
# This MC-Playum (NanoPi NEO / H3 codec) exposes TWO sound cards:
#   card 0  H3 Audio Codec  -> the AUX zone (has mixer controls)
#   card 1  I2S simple-card -> the RCA zone (NO mixer controls -- raw I2S)
# So the buttons control the AUX zone only; the RCA zone has nothing to set.
# The aux output volume is the codec's `DAC` control on card 0 (range 0-63),
# which feeds the analog Line Out on the aux jack. We drive it with
# `amixer -M` for perceptual (mapped) stepping. VOLUME_CARD/VOLUME_CONTROL are
# the source of truth; auto-detect only runs as a fallback if `DAC` is absent
# (e.g. a different codec on another unit).

VOLUME_STEP = 5            # percent per button press
VOLUME_CARD = "0"          # card 0 = H3 codec = aux zone
VOLUME_CONTROL = "DAC"     # codec digital playback volume feeding aux Line Out

# --- RCA (HiFi) zone: NO ALSA control by design (softvol on the PCM5102A I2S
# path causes static -- see /etc/asound.conf). RCA volume is controlled
# server-side in LMS instead, on the primary Squeezelite player.
#
# The LMS host and the RCA player id are PER-DEVICE: the player id is the
# primary squeezelite player's MAC (its `-m` arg, == the board's eth0 MAC) and
# the host is its `-s` arg. We auto-detect both from the running
# `squeezelite-primary` (-o hifi) process so one script is correct on every
# device; the constants below are only the fallback if detection fails.
RCA_LMS_HOST = "192.168.0.68"
RCA_LMS_PORT = 9000
RCA_LMS_PLAYERID = "06:52:07:ba:4b:16"   # fallback primary (RCA/HiFi) player

def _detect_rca_lms():
    """Read the primary squeezelite cmdline and pull out its player MAC (-m)
    and LMS server (-s). The primary player is the RCA/HiFi zone (-o hifi).
    Returns (playerid, host) or (None, None) if not found."""
    try:
        out = subprocess.check_output(
            "ps -eo args 2>/dev/null | grep -E 'squeezelite[-]primary|squeezelite.*-o hifi' | grep -v grep",
            shell=True, timeout=3).decode('utf-8', 'replace')
    except Exception:
        out = ""
    playerid = host = None
    for line in out.splitlines():
        toks = line.split()
        # -m is 6 space-separated hex bytes: -m 06 84 A7 E4 16 04
        if '-m' in toks:
            i = toks.index('-m')
            mac = toks[i+1:i+7]
            if len(mac) == 6 and all(len(b) == 2 for b in mac):
                playerid = ':'.join(b.lower() for b in mac)
        if '-s' in toks:
            i = toks.index('-s')
            if i + 1 < len(toks):
                host = toks[i+1]
        if playerid:
            break
    return playerid, host

_pid, _host = _detect_rca_lms()
if _pid:
    RCA_LMS_PLAYERID = _pid
if _host:
    RCA_LMS_HOST = _host
logging.info(f"RCA zone -> LMS {RCA_LMS_HOST}:{RCA_LMS_PORT} player {RCA_LMS_PLAYERID}")

_rca_level = 0

# --- Page indices (named so the handlers read clearly) ---
SYSTEM_OPTIONS_PAGE = 2     # was "Power Options"
AUX_VOLUME_PAGE = 10
RCA_VOLUME_PAGE = 11
RESET_AUDIO_PAGE = 12
FACTORY_CONFIRM_PAGE = 13
FACTORY_PROGRESS_PAGE = 14
VOLUME_MENU_PAGE = 15

# Order of items on the System Options menu (page 2). Index positions are
# referenced by the K2/confirm handler below, so keep them in sync.
SYSTEM_OPTIONS = ["Reboot", "Shutdown", "Reset Network", "Reset Audio", "Factory Reset"]

# --- Device scripts / services for the audio resets + factory reset ---
I2S_RESET_SH = "/mnt/dietpi_userdata/innovo/app/backend/wrappers/i2s-reset.sh"
FACTORY_RESET_SH = "/mnt/dietpi_userdata/innovo/app/backend/cgi-scripts/factory_reset.sh"
AUX_SERVICES = ["squeezelite-secondary", "raspotify-secondary", "shairport-sync-secondary"]
_volume_level = 0          # last-known volume percent, for the on-screen bar

def _amixer(*args, timeout=2):
    return subprocess.check_output(
        ["amixer", "-M", "-c", VOLUME_CARD] + list(args),
        stderr=subprocess.DEVNULL, timeout=timeout
    ).decode("utf-8", "replace")

def _parse_volume_percent(text):
    # amixer prints e.g. "Front Left: Playback 58 [92%] [-5.80dB] [on]"
    import re
    m = re.search(r"\[(\d{1,3})%\]", text)
    return int(m.group(1)) if m else None

def _control_exists(name):
    try:
        _amixer("sget", name)
        return True
    except Exception:
        return False

def detect_volume_control():
    # Prefer the known aux-zone control; only fall back if it's missing.
    global VOLUME_CONTROL
    if _control_exists(VOLUME_CONTROL):
        logging.info(f"Volume control: card {VOLUME_CARD} '{VOLUME_CONTROL}' (aux zone)")
        return VOLUME_CONTROL
    logging.warning(f"'{VOLUME_CONTROL}' not found on card {VOLUME_CARD}; auto-detecting a playback control")
    for name in ("Line Out", "PCM", "Master", "Digital", "Speaker"):
        if _control_exists(name):
            VOLUME_CONTROL = name
            logging.info(f"Volume control fallback: '{name}'")
            return name
    logging.warning("no usable playback control found; volume control disabled")
    VOLUME_CONTROL = None
    return None

def get_volume():
    global _volume_level
    if not VOLUME_CONTROL:
        return _volume_level
    try:
        pct = _parse_volume_percent(_amixer("sget", VOLUME_CONTROL))
        if pct is not None:
            _volume_level = pct
    except Exception as e:
        logging.warning(f"get_volume failed: {e}")
    return _volume_level

def change_volume(delta):
    global _volume_level
    if not VOLUME_CONTROL:
        logging.warning("change_volume called but no mixer control available")
        return _volume_level
    direction = "{}%+".format(abs(delta)) if delta >= 0 else "{}%-".format(abs(delta))
    try:
        out = _amixer("sset", VOLUME_CONTROL, "unmute", direction)
        pct = _parse_volume_percent(out)
        if pct is not None:
            _volume_level = pct
    except Exception as e:
        logging.warning(f"change_volume failed: {e}")
    return _volume_level

def _lms_request(params, timeout=4):
    """POST a JSON-RPC slim.request to the LMS server. Returns the parsed
    'result' dict, or None on any failure (network down, bad JSON, etc.)."""
    import json, urllib.request
    body = json.dumps({"id": 1, "method": "slim.request", "params": params}).encode()
    url = f"http://{RCA_LMS_HOST}:{RCA_LMS_PORT}/jsonrpc.js"
    try:
        req = urllib.request.Request(url, data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode()).get("result")
    except Exception as e:
        logging.warning(f"LMS request failed ({params}): {e}")
        return None

def get_rca_volume():
    global _rca_level
    res = _lms_request([RCA_LMS_PLAYERID, ["mixer", "volume", "?"]])
    if res and "_volume" in res:
        try:
            _rca_level = int(float(res["_volume"]))
        except (TypeError, ValueError):
            pass
    return _rca_level

def change_rca_volume(delta):
    """Adjust the RCA/HiFi zone volume via LMS server-side mixer (relative)."""
    global _rca_level
    sign = "+" if delta >= 0 else "-"
    res = _lms_request([RCA_LMS_PLAYERID, ["mixer", "volume", f"{sign}{abs(delta)}"]])
    # LMS doesn't echo the new level on a relative set; read it back.
    return get_rca_volume()

def reset_rca_audio():
    """RCA/HiFi zone audio reset = re-run the I2S subsystem reset wrapper."""
    logging.info("Reset Audio: RCA zone (i2s-reset.sh)")
    try:
        subprocess.Popen([I2S_RESET_SH],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        logging.error(f"i2s-reset failed to launch: {e}")

def reset_aux_audio():
    """Aux zone audio reset = restart the secondary-zone audio services."""
    logging.info("Reset Audio: Aux zone (restart secondary services)")
    try:
        subprocess.Popen(["systemctl", "restart"] + AUX_SERVICES,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        logging.error(f"aux service restart failed to launch: {e}")

def run_factory_reset():
    """Trigger the device factory-reset script (detached -- it reboots)."""
    logging.info("Factory Reset: launching factory_reset.sh")
    try:
        subprocess.Popen(["bash", FACTORY_RESET_SH],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        logging.error(f"factory_reset.sh failed to launch: {e}")

detect_volume_control()

# Global variables
width = 128
height = 64
pageCount = 3
pageIndex = 0
showPageIndicator = False
pageSleep = 50
pageSleepCountdown = pageSleep
selectionIndex = 0  # Start with first item selected
lastActivityTime = time.time()
screenSleeping = False
_ready = False  # True once the main loop is running; gates button actions during startup
nowplaying_scroll_offset = 0

# Initialize OLED
oled.init()
# oled.clearDisplay()  # Clear any garbage
oled.setNormalDisplay()
oled.setHorizontalMode()

# Drawing setup
drawing = False
image = Image.new('1', (width, height))
draw = ImageDraw.Draw(image)
fontb18 = ImageFont.truetype('DejaVuSansMono-Bold.ttf', 18)
font14 = ImageFont.truetype('DejaVuSansMono.ttf', 14)
smartFont = ImageFont.truetype('DejaVuSansMono-Bold.ttf', 10)
fontb12 = ImageFont.truetype('DejaVuSansMono-Bold.ttf', 12)
font10 = ImageFont.truetype('DejaVuSansMono.ttf', 10)

# Threading lock
lock = threading.Lock()

def get_ip_address(ifname):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        return socket.inet_ntoa(fcntl.ioctl(
            s.fileno(),
            0x8915,
            struct.pack('256s', ifname[:15].encode())
        )[20:24])
    except:
        return "N/A"

def get_mac_address(ifname='eth0'):
    try:
        with open(f'/sys/class/net/{ifname}/address', 'r') as f:
            return f.read().strip().upper()
    except:
        try:
            with open('/sys/class/net/wlan0/address', 'r') as f:
                return f.read().strip().upper()
        except:
            return "N/A"

def get_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('10.255.255.255', 1))
        IP = s.getsockname()[0]
    except:
        IP = '127.0.0.1'
    finally:
        s.close()
    return IP

def _panel_off():
    """Power the OLED panel + charge pump off (true sleep). Falls back to
    blanking the framebuffer on drivers without displayOff() (repo driver)."""
    try:
        if hasattr(oled, "displayOff"):
            oled.displayOff()
        else:
            oled.clearDisplay()
    except Exception as e:
        logging.warning(f"_panel_off failed: {e}")

def _panel_on():
    """Power the OLED panel back on. Uses displayOn() (charge-pump reinit) when
    available; otherwise just restores normal (non-inverted) display."""
    try:
        if hasattr(oled, "displayOn"):
            oled.displayOn()
        else:
            oled.setNormalDisplay()
    except Exception as e:
        logging.warning(f"_panel_on failed: {e}")

def wake_screen():
    global screenSleeping, pageSleepCountdown, lastActivityTime
    if screenSleeping:
        _panel_on()
        screenSleeping = False
    pageSleepCountdown = pageSleep
    lastActivityTime = time.time()

def draw_page():
    global drawing, pageSleepCountdown, lastActivityTime, screenSleeping, nowplaying_scroll_offset

    lock.acquire()
    is_drawing = drawing
    page_index = pageIndex
    sel_index = selectionIndex
    lock.release()

    if is_drawing or screenSleeping:
        return

    if pageSleepCountdown <= 1:
        if not screenSleeping:
            _panel_off()
            screenSleeping = True
            logging.info("Screen sleeping (panel off)")
        pageSleepCountdown = 0
        return
    pageSleepCountdown -= 1

    lock.acquire()
    drawing = True
    lock.release()

    # Clear the image buffer
    draw.rectangle((0, 0, width, height), outline=0, fill=0)

    # --- Page 0: Date/Time + NowPlaying ---
    if page_index == 0:
        text = time.strftime("%A %e %b %Y")
        draw.text((2, 2), text, font=smartFont, fill=255)
        text = time.strftime("%X")
        draw.text((2, 18), text, font=smartFont, fill=255)

        try:
            cmd = "/mnt/dietpi_userdata/innovo/scripts/playername"
            if os.path.exists(cmd):
                PLAYERNAME = subprocess.check_output(cmd, shell=True, timeout=1).decode('utf-8').strip()
            else:
                PLAYERNAME = "MC-DJ Player"
        except:
            PLAYERNAME = "MC-DJ Player"
        draw.text((2, 34), PLAYERNAME, font=smartFont, fill=255)

        try:
            if os.path.exists('/mnt/dietpi_userdata/innovo/scripts/nowplaying'):
                with open('/mnt/dietpi_userdata/innovo/scripts/nowplaying', 'r') as f:
                    NOWPLAYING = f.read().strip()
                if not NOWPLAYING:
                    NOWPLAYING = "Ready"
            else:
                NOWPLAYING = "Ready"
        except:
            NOWPLAYING = "Ready"

        bbox = draw.textbbox((0, 0), NOWPLAYING, font=smartFont)
        text_width = bbox[2] - bbox[0]
        if text_width > width - 4:
            scroll_speed = 2
            nowplaying_scroll_offset += scroll_speed
            if nowplaying_scroll_offset > text_width:
                nowplaying_scroll_offset = -width
            draw.text((2 - nowplaying_scroll_offset, 50), NOWPLAYING, font=smartFont, fill=255)
        else:
            nowplaying_scroll_offset = 0
            draw.text((2, 50), NOWPLAYING, font=smartFont, fill=255)

    # --- Page 1: System Info ---
    # Layout (64px tall): IP on top, MAC big+bold directly under it (the MAC is
    # the field installers read off the screen, so give it the most weight), then
    # CPU load + temp share one line to free the vertical space the bigger MAC
    # takes, with Mem/Disk on the final line.
    elif page_index == 1:
        try:
            IPAddress = get_ip_address('eth0')
            if IPAddress == "N/A":
                IPAddress = get_ip()
        except:
            IPAddress = get_ip()

        MACAddress = get_mac_address('eth0')

        # CPU load average (1-min), kept short so it fits beside the temp.
        try:
            cmd = "awk '{printf \"%.2f\", $1}' /proc/loadavg"
            cpu_load = subprocess.check_output(cmd, shell=True, timeout=2).decode('utf-8').strip()
            if not cpu_load:
                cpu_load = "N/A"
        except:
            cpu_load = "N/A"

        try:
            cmd = "free | awk 'NR==2{printf \"%d\", $3*100/$2}'"
            mem_usage = subprocess.check_output(cmd, shell=True, timeout=2).decode('utf-8').strip()
            if not mem_usage:
                mem_usage = "N/A"
        except:
            mem_usage = "N/A"

        try:
            cmd = "df -h | awk '$NF==\"/\"{print $5}' | sed 's/%//'"
            disk_usage = subprocess.check_output(cmd, shell=True, timeout=2).decode('utf-8').strip()
            if not disk_usage:
                disk_usage = "N/A"
        except:
            disk_usage = "N/A"

        try:
            tempI = int(open('/sys/class/thermal/thermal_zone0/temp').read())
            if tempI > 1000:
                tempI = tempI / 1000
            temp_c = int(tempI)
            temp = f"{temp_c}C"
            temp_flash = temp_c >= 80   # flash the line only when critical
        except:
            temp = "N/A"
            temp_flash = False

        # IP address (regular 14) on top.
        draw.text((2, 0), IPAddress, font=font14, fill=255)

        # MAC address directly under the IP, bold 12 (the widest a full 17-char
        # MAC fits on a 128px line) so it stands out from the IP above it.
        draw.text((2, 17), MACAddress, font=fontb12, fill=255)

        # CPU load + temp on ONE line. Suppress on the flash-off half-second when
        # the temp is critical, so the whole stats line blinks as a warning.
        if not (temp_flash and int(time.time()) % 2 == 0):
            draw.text((2, 34), f"CPU:{cpu_load} T:{temp}", font=font10, fill=255)

        # Mem + Disk on the final line.
        draw.text((2, 50), f"Mem:{mem_usage}% Disk:{disk_usage}%", font=font10, fill=255)

    # --- Page 2: System Options ---
    elif page_index == SYSTEM_OPTIONS_PAGE:
        draw.text((2, 2), 'System Options', font=fontb12, fill=255)
        options = SYSTEM_OPTIONS
        for i, option in enumerate(options):
            y = 14 + i * 10
            if sel_index == i:
                draw.rectangle((2, y, width-4, y+9), outline=255, fill=255)
                draw.text((4, y), option, font=font10, fill=0)
            else:
                draw.text((4, y), option, font=font10, fill=255)

    # --- Page 3: Reboot confirmation ---
    elif page_index == 3:
        draw.text((2, 2), 'Reboot?', font=fontb12, fill=255)
        options = ['Yes', 'No']
        for i, option in enumerate(options):
            y = 20 + i*14
            if sel_index == i:
                draw.rectangle((2, y, width-4, y+12), outline=255, fill=255)
                draw.text((4, y+1), option, font=font10, fill=0)
            else:
                draw.text((4, y+1), option, font=font10, fill=255)

    # --- Page 5: Shutdown confirmation ---
    elif page_index == 5:
        draw.text((2, 2), 'Shutdown?', font=fontb12, fill=255)
        options = ['Yes', 'No']
        for i, option in enumerate(options):
            y = 20 + i*14
            if sel_index == i:
                draw.rectangle((2, y, width-4, y+12), outline=255, fill=255)
                draw.text((4, y+1), option, font=font10, fill=0)
            else:
                draw.text((4, y+1), option, font=font10, fill=255)

    # --- Page 7: Rebooting ---
    elif page_index == 7:
        draw.text((2, 2), 'Rebooting', font=fontb12, fill=255)
        draw.text((2, 20), 'Please wait...', font=font10, fill=255)

    # --- Page 8: Shutting down ---
    elif page_index == 8:
        draw.text((2, 2), 'Shutting down', font=fontb12, fill=255)
        draw.text((2, 20), 'Please wait...', font=font10, fill=255)

    # --- Page 9: Reset Network confirmation ---
    elif page_index == 9:
        draw.text((2, 2), 'Reset Network?', font=fontb12, fill=255)
        options = ['Yes', 'No']
        for i, option in enumerate(options):
            y = 20 + i*14
            if sel_index == i:
                draw.rectangle((2, y, width-4, y+12), outline=255, fill=255)
                draw.text((4, y+1), option, font=font10, fill=0)
            else:
                draw.text((4, y+1), option, font=font10, fill=255)

    # --- Page 15: Volume (zone picker) ---
    elif page_index == VOLUME_MENU_PAGE:
        draw.text((2, 2), 'Volume', font=fontb12, fill=255)
        draw.text((4, 22), 'B1: RCA', font=font10, fill=255)
        draw.text((4, 36), 'B2: Aux', font=font10, fill=255)
        draw.text((4, 50), 'B3: Back', font=font10, fill=255)

    # --- Page 10: Aux Volume ---
    elif page_index == AUX_VOLUME_PAGE:
        draw.text((2, 2), 'Aux Volume', font=fontb12, fill=255)
        vol = get_volume()
        # Numeric percent
        draw.text((96, 2), f"{vol:3d}%", font=fontb12, fill=255)
        # Volume bar: outline + filled proportion
        bx, by, bw, bh = 4, 28, width - 8, 16
        draw.rectangle((bx, by, bx + bw, by + bh), outline=255, fill=0)
        fill_w = int((bw - 2) * max(0, min(100, vol)) / 100)
        if fill_w > 0:
            draw.rectangle((bx + 1, by + 1, bx + 1 + fill_w, by + bh - 1), outline=255, fill=255)
        draw.text((2, 50), 'B1 -  B2 +  B3 back', font=font10, fill=255)

    # --- Page 11: RCA Volume (LMS server-side) ---
    elif page_index == RCA_VOLUME_PAGE:
        draw.text((2, 2), 'RCA Volume', font=fontb12, fill=255)
        vol = get_rca_volume()
        draw.text((96, 2), f"{vol:3d}%", font=fontb12, fill=255)
        bx, by, bw, bh = 4, 28, width - 8, 16
        draw.rectangle((bx, by, bx + bw, by + bh), outline=255, fill=0)
        fill_w = int((bw - 2) * max(0, min(100, vol)) / 100)
        if fill_w > 0:
            draw.rectangle((bx + 1, by + 1, bx + 1 + fill_w, by + bh - 1), outline=255, fill=255)
        draw.text((2, 50), 'B1 -  B2 +  B3 back', font=font10, fill=255)

    # --- Page 12: Reset Audio ---
    elif page_index == RESET_AUDIO_PAGE:
        draw.text((2, 2), 'Reset Audio', font=fontb12, fill=255)
        draw.text((4, 22), 'B1: Reset RCA', font=font10, fill=255)
        draw.text((4, 36), 'B2: Reset Aux', font=font10, fill=255)
        draw.text((4, 50), 'B3: Back', font=font10, fill=255)

    # --- Page 13: Factory Reset confirmation ---
    elif page_index == FACTORY_CONFIRM_PAGE:
        draw.text((2, 2), 'Factory Reset?', font=fontb12, fill=255)
        options = ['Yes', 'No']
        for i, option in enumerate(options):
            y = 20 + i*14
            if sel_index == i:
                draw.rectangle((2, y, width-4, y+12), outline=255, fill=255)
                draw.text((4, y+1), option, font=font10, fill=0)
            else:
                draw.text((4, y+1), option, font=font10, fill=255)
        draw.text((2, 50), 'wipes to defaults', font=font10, fill=255)

    # --- Page 14: Factory reset in progress ---
    elif page_index == FACTORY_PROGRESS_PAGE:
        draw.text((2, 2), 'Factory Reset', font=fontb12, fill=255)
        draw.text((2, 22), 'Running...', font=font10, fill=255)
        draw.text((2, 38), 'Device will reboot', font=font10, fill=255)

    # Clear and redraw
    # oled.clearDisplay()
    oled.drawImage(image)

    lock.acquire()
    drawing = False
    lock.release()

def update_page_index(pi):
    global pageIndex, selectionIndex, lastActivityTime
    lock.acquire()
    pageIndex = pi
    selectionIndex = 0  # Reset selection to first item
    lastActivityTime = time.time()
    lock.release()
    wake_screen()

def update_selection_index():
    global selectionIndex, lastActivityTime, pageIndex
    lock.acquire()
    if pageIndex == SYSTEM_OPTIONS_PAGE:
        selectionIndex = (selectionIndex + 1) % len(SYSTEM_OPTIONS)
    else:  # Yes/No dialogs have 2 items
        selectionIndex = (selectionIndex + 1) % 2
    lastActivityTime = time.time()
    lock.release()
    wake_screen()

def receive_signal(signum, stack):
    global pageIndex, selectionIndex

    logging.info(f"Received signal: {signum}")
    wake_screen()
    if not _ready:
        # Button pressed during startup -- handlers are installed early to avoid
        # being killed by the default SIGUSR disposition, but we don't act on a
        # press until the UI is fully up.
        return

    lock.acquire()
    page_index = pageIndex
    sel_index = selectionIndex
    lock.release()

    if signum == signal.SIGUSR1:  # Button K1 - Navigate / RCA / Vol Down
        if page_index in [SYSTEM_OPTIONS_PAGE, 3, 5, 9, FACTORY_CONFIRM_PAGE]:
            update_selection_index()                 # move menu selection
        elif page_index == 0:
            update_page_index(1)                     # NowPlaying -> SysInfo
        elif page_index == 1:
            update_page_index(VOLUME_MENU_PAGE)      # SysInfo -> Volume picker
        elif page_index == VOLUME_MENU_PAGE:
            update_page_index(RCA_VOLUME_PAGE)       # picker: K1 = RCA zone
        elif page_index == AUX_VOLUME_PAGE:
            change_volume(-VOLUME_STEP)              # Aux vol down
        elif page_index == RCA_VOLUME_PAGE:
            change_rca_volume(-VOLUME_STEP)          # RCA vol down
        elif page_index == RESET_AUDIO_PAGE:
            reset_rca_audio()                        # K1 = Reset RCA
            update_page_index(0)
        draw_page()

    elif signum == signal.SIGUSR2:  # Button K2 - Confirm / Vol Up
        if page_index == SYSTEM_OPTIONS_PAGE:  # System Options menu
            choice = SYSTEM_OPTIONS[sel_index] if sel_index < len(SYSTEM_OPTIONS) else ""
            if choice == "Reboot":
                update_page_index(3)
            elif choice == "Shutdown":
                update_page_index(5)
            elif choice == "Reset Network":
                update_page_index(9)
            elif choice == "Reset Audio":
                update_page_index(RESET_AUDIO_PAGE)
            elif choice == "Factory Reset":
                update_page_index(FACTORY_CONFIRM_PAGE)
        elif page_index == 3:  # Reboot confirm
            if sel_index == 0:  # Yes
                update_page_index(7)
                draw_page()
                time.sleep(3)
                os.system('systemctl reboot')
            else:  # No
                update_page_index(0)
        elif page_index == 5:  # Shutdown confirm
            if sel_index == 0:  # Yes
                update_page_index(8)
                draw_page()
                time.sleep(3)
                os.system('systemctl poweroff')
            else:  # No
                update_page_index(0)
        elif page_index == 9:  # Reset network confirm
            if sel_index == 0:  # Yes
                # Add reset network logic here
                update_page_index(0)
            else:  # No
                update_page_index(0)
        elif page_index == FACTORY_CONFIRM_PAGE:  # Factory Reset confirm
            if sel_index == 0:  # Yes
                update_page_index(FACTORY_PROGRESS_PAGE)
                draw_page()
                time.sleep(2)
                run_factory_reset()
            else:  # No
                update_page_index(0)
        elif page_index == VOLUME_MENU_PAGE:
            update_page_index(AUX_VOLUME_PAGE)       # picker: K2 = Aux zone
        elif page_index == AUX_VOLUME_PAGE:
            change_volume(VOLUME_STEP)               # Aux vol up
        elif page_index == RCA_VOLUME_PAGE:
            change_rca_volume(VOLUME_STEP)           # RCA vol up
        elif page_index == RESET_AUDIO_PAGE:
            reset_aux_audio()                        # K2 = Reset Aux
            update_page_index(0)
        else:
            update_page_index(0)
        draw_page()

    elif signum == signal.SIGALRM:  # Button K3 - Menu / Back
        if page_index == SYSTEM_OPTIONS_PAGE:
            update_page_index(0)                     # close menu -> NowPlaying
        elif page_index in [AUX_VOLUME_PAGE, RCA_VOLUME_PAGE]:
            update_page_index(VOLUME_MENU_PAGE)      # zone page -> back to Volume picker
        elif page_index == VOLUME_MENU_PAGE:
            update_page_index(1)                     # Volume picker -> back to SysInfo
        elif page_index in [RESET_AUDIO_PAGE, FACTORY_CONFIRM_PAGE]:
            update_page_index(SYSTEM_OPTIONS_PAGE)   # back to System Options
        else:
            update_page_index(SYSTEM_OPTIONS_PAGE)   # open System Options
        draw_page()

# Main execution
try:
    # Install signal handlers FIRST -- before any startup work (logo, sleeps).
    # The default disposition of SIGUSR1/SIGUSR2 is to terminate the process, so
    # a button press during startup would otherwise kill the OLED UI. receive_signal
    # guards on `_ready` and safely ignores presses until the main loop is up.
    signal.signal(signal.SIGUSR1, receive_signal)
    signal.signal(signal.SIGUSR2, receive_signal)
    signal.signal(signal.SIGALRM, receive_signal)

    # Display logo if it exists
    logo_path = 'innovo.png'
    if os.path.exists(logo_path):
        logging.info("Loading logo...")
        image0 = Image.open(logo_path).convert('1')
        oled.drawImage(image0)
        time.sleep(2)

    logging.info("Starting main loop...")
    _ready = True
    while True:
        try:
            draw_page()
            time.sleep(1)
                
        except KeyboardInterrupt:
            break
        except Exception as e:
            logging.error(f"Error in main loop: {e}")
            time.sleep(0.5)

except Exception as e:
    logging.error(f"Fatal error: {e}")
    import traceback
    traceback.print_exc()
