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

# ============================================================
# Page indices (named so the handlers read clearly; no collisions)
# ============================================================
PAGE_DATE             = 0    # Date/Time + NowPlaying
PAGE_SYSINFO          = 1    # IP/MAC/CPU/temp
SYSTEM_OPTIONS_PAGE   = 2    # top menu -> Volume / Power&Reset / Diagnostics
PAGE_REBOOT_CONFIRM   = 3    # Reboot? Yes/No
PAGE_SHUTDOWN_CONFIRM = 5    # Shutdown? Yes/No
PAGE_REBOOTING        = 7    # "Rebooting / please wait"
PAGE_SHUTTING_DOWN    = 8    # "Shutting down / wait" -- CRITICAL shutdown-safety page
PAGE_RESET_NET_CONFIRM = 9   # Reset Network? Yes/No
AUX_VOLUME_PAGE       = 10   # Aux volume (amixer)
RCA_VOLUME_PAGE       = 11   # RCA volume (LMS)
RESET_AUDIO_PAGE      = 12   # Reset RCA / Reset Aux
FACTORY_CONFIRM_PAGE  = 13   # Factory Reset? Yes/No
FACTORY_PROGRESS_PAGE = 14   # Factory reset running
VOLUME_MENU_PAGE      = 15   # zone picker RCA/Aux
# --- NEW ---
POWER_RESET_MENU_PAGE  = 16  # submenu: Power & Reset (5 items)
DIAGNOSTICS_MENU_PAGE  = 17  # submenu: Diagnostics (2 items)
STATUS_PAGE            = 18  # read-only: cloud/registered/dealer/id (NO IP/MAC - those live on System Info)
SELFHEAL_PROGRESS_PAGE = 21  # "Repairing..." -> "Done"/"Failed"
# --- compatibility only, NEVER navigated to from the shutdown handler ---
SAFE_TO_UNPLUG_PAGE    = 20  # golden's old "Safe to unplug" (external hook draws the real one)
# --- Read-only filesystem recovery (SD corruption -> ext4 emergency_ro) ---
PAGE_RO_WARNING        = 22  # auto-shown when / goes read-only (SD corruption)
PAGE_RO_FIX_CONFIRM    = 23  # "Repair & reboot? Yes/No"
PAGE_RO_FIXING         = 24  # "Scheduling fsck / rebooting..."

# --- Menu item lists (index positions referenced by the K2 handlers; keep in sync) ---
TOP_MENU = ["Volume", "Power & Reset", "Diagnostics"]
POWER_RESET_MENU = ["Reboot", "Shutdown", "Reset Network", "Reset Audio", "Factory Reset"]
DIAGNOSTICS_MENU = ["Repair System", "Restart Audio", "Restart Cloud"]

# How many scrollable items each menu/confirm page has (drives K1 wrap).
MENU_LENS = {
    SYSTEM_OPTIONS_PAGE:   len(TOP_MENU),
    POWER_RESET_MENU_PAGE: len(POWER_RESET_MENU),
    DIAGNOSTICS_MENU_PAGE: len(DIAGNOSTICS_MENU),
    PAGE_REBOOT_CONFIRM:   2,
    PAGE_SHUTDOWN_CONFIRM: 2,
    PAGE_RESET_NET_CONFIRM: 2,
    FACTORY_CONFIRM_PAGE:  2,
}

# --- Device scripts / services for the audio resets + factory reset ---
I2S_RESET_SH = "/mnt/dietpi_userdata/innovo/app/backend/wrappers/i2s-reset.sh"
FACTORY_RESET_SH = "/mnt/dietpi_userdata/innovo/app/backend/cgi-scripts/factory_reset.sh"
RESET_NETWORK_SH = "/mnt/dietpi_userdata/innovo/app/backend/cgi-scripts/reset_network.sh"
SELFHEAL_SH = "/usr/local/bin/innovo-self-heal.sh"
AUX_SERVICES = ["squeezelite-secondary", "raspotify-secondary", "shairport-sync-secondary"]
# Restart Audio (Diagnostics) restarts BOTH zones' player + streaming services.
RESTART_AUDIO_SERVICES = [
    "squeezelite-primary", "squeezelite-secondary",
    "shairport-sync-primary", "shairport-sync-secondary",
    "raspotify-primary", "raspotify-secondary",
]
# Registration files (read-only status screen)
DEVICE_IDENTITY_CONF = "/mnt/dietpi_userdata/innovo/config/device_identity.conf"
MCAGENT_DEALER_TAG = "/mnt/dietpi_userdata/innovo/config/mcagentdealer.tag"
REGISTRATION_JSON = "/mnt/dietpi_userdata/innovo/config/registration.json"

_volume_level = 0          # last-known volume percent, for the on-screen bar
_selfheal_result = None    # None = running, 0 = Done, non-zero/exception = Failed

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

def run_reset_network():
    """Reset the network to DHCP via the CGI reset_network.sh (detached).
    The script emits HTTP/CGI headers to stdout -- harmless when redirected to
    DEVNULL -- and does its own bounded `ifreload` (timeout 30) so it never
    blocks the UI thread."""
    logging.info("Reset Network: launching reset_network.sh")
    try:
        subprocess.Popen(["bash", RESET_NETWORK_SH],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        logging.error(f"reset_network.sh failed to launch: {e}")

def run_restart_audio():
    """Diagnostics -> Restart Audio: restart squeezelite/shairport/raspotify
    primary + secondary (detached, non-blocking)."""
    logging.info("Diagnostics: Restart Audio (all player/stream services)")
    try:
        subprocess.Popen(["systemctl", "restart"] + RESTART_AUDIO_SERVICES,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        logging.error(f"restart audio failed to launch: {e}")

def run_restart_cloud():
    """Diagnostics -> Restart Cloud: restart the mesh agent(s). mcplayum/mcdj run
    MCDealerCloud only; aura also runs MCCloud. Restart each ONLY if its unit
    exists, so this is safe on any platform. Detached, non-blocking."""
    logging.info("Diagnostics: Restart Cloud (mesh agents, if installed)")
    for unit in ("MCDealerCloud.service", "MCCloud.service"):
        try:
            # systemctl cat succeeds only if the unit exists -> restart it.
            if subprocess.run(["systemctl", "cat", unit],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=5).returncode == 0:
                subprocess.Popen(["systemctl", "restart", unit],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                logging.info(f"  restarted {unit}")
            else:
                logging.info(f"  {unit} not installed, skipping")
        except Exception as e:
            logging.error(f"  restart {unit} failed: {e}")

def _selfheal_worker():
    """Run the self-heal script to completion in a worker thread so the UI
    thread never blocks. A full dpkg reinstall can take minutes; bound it
    generously and record the outcome for page 21 to render."""
    global _selfheal_result
    try:
        rc = subprocess.run(
            ["bash", SELFHEAL_SH],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=600
        ).returncode
        _selfheal_result = rc
        logging.info(f"Self-Heal finished rc={rc}")
    except Exception as e:
        _selfheal_result = 1
        logging.error(f"Self-Heal failed: {e}")

def run_self_heal():
    """Kick off the self-heal script in a background thread. Page 21 polls
    _selfheal_result (None=running, 0=Done, else=Failed)."""
    global _selfheal_result
    _selfheal_result = None
    logging.info("Diagnostics: launching self-heal")
    try:
        t = threading.Thread(target=_selfheal_worker, daemon=True)
        t.start()
    except Exception as e:
        _selfheal_result = 1
        logging.error(f"Self-Heal thread failed to start: {e}")

# --- Read-only filesystem detection + button-triggered repair ----------------
# H3 SD cards occasionally corrupt on-card; ext4 detects a bad block (usually a
# directory-block checksum failure, EFSBADCRC) and REMOUNTS ROOT READ-ONLY to
# stop further damage (mount option 'emergency_ro'). The device keeps running
# from RAM (audio + MCUI stay up) but NOTHING can be written to disk, so it's
# silently broken until someone notices. We can't fsck a mounted root in place,
# but we CAN, entirely from RAM / on the raw block device:
#   1. detect the RO state by reading /proc/mounts (no disk access needed), and
#   2. flag a forced fsck for next boot via `tune2fs -C` on the BLOCK DEVICE
#      (writes to the device node, not the RO filesystem — works while RO), then
#      `reboot -f` (needs no writable fs). fsck runs pre-mount on boot, repairs
#      the corruption, and root comes back rw. This is the exact manual sequence
#      proven on .116 (2026-07-26), automated behind a confirm-gated button.

def _root_block_device():
    """Return the block device backing / (e.g. /dev/mmcblk0p1). RAM-only read."""
    try:
        with open("/proc/mounts") as f:
            for line in f:
                p = line.split()
                if len(p) >= 2 and p[1] == "/":
                    return p[0]
    except Exception:
        pass
    return "/dev/mmcblk0p1"  # sane default for these H3 units

def is_root_readonly():
    """True if / is mounted read-only (ext4 emergency_ro or a plain ro mount).
    Reads /proc/mounts only — safe and reliable even when the disk is fully RO."""
    try:
        with open("/proc/mounts") as f:
            for line in f:
                p = line.split()
                if len(p) >= 4 and p[1] == "/":
                    opts = p[3].split(",")
                    # ext4 emergency_ro reports 'ro' in the effective options.
                    return ("ro" in opts) or ("emergency_ro" in opts)
    except Exception:
        return False
    return False

_ro_fix_result = None  # None=not started, set by worker (we reboot, so rarely read)

def run_ro_fix():
    """Schedule a forced fsck on next boot and reboot. Every step here is
    RO-safe: sync flushes RAM, tune2fs -C writes the raw block device, reboot -f
    bypasses systemd/dbus (which may itself be wedged by the RO root)."""
    global _ro_fix_result
    _ro_fix_result = None
    dev = _root_block_device()
    logging.warning(f"RO-FIX: scheduling forced fsck on {dev} + reboot")
    def _step(desc, argv, tmo):
        # Run one prep step, but NEVER let it block the reboot. Each step is
        # individually guarded + timeout-bounded: on a badly-wedged device a
        # single hung command (e.g. sync on a stuck I/O queue) must not stop us
        # from getting to the guaranteed kernel reboot below.
        try:
            subprocess.run(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=tmo)
            logging.info(f"RO-FIX: {desc} ok")
        except Exception as e:
            logging.warning(f"RO-FIX: {desc} failed/timed out ({e}) — continuing")

    def _worker():
        global _ro_fix_result
        # Prep steps — each isolated so one hang can't abort the reboot.
        # -C 100 sets mount count > max so fsck is FORCED next boot (writes the
        # superblock on the DEVICE NODE, allowed while ro).
        _step("tune2fs force-fsck", ["tune2fs", "-C", "100", dev], 15)
        _step("tune2fs max-count",  ["tune2fs", "-c", "1", dev], 15)
        # Recovery breadcrumb: ext4 LABEL RECOVER-RO on the block device
        # (RO-safe, survives reboot, fsck does NOT clear it; fstab mounts by
        # UUID so the label is inert to boot). innovo-post-ro-repair reads it
        # next boot, runs the file/package repair, then clears it.
        _step("set RECOVER-RO label", ["e2label", dev, "RECOVER-RO"], 15)
        _step("sync", ["sync"], 8)
        _ro_fix_result = 0
        time.sleep(2)

        # REBOOT — kernel sysrq FIRST, because it is the ONLY path that cannot
        # stall: it needs no fork, no exec of a disk binary, no writable fs —
        # just a write to a kernel interface. `reboot -f` (which must exec
        # /sbin/reboot off a possibly-hung RO disk) was observed to WEDGE on a
        # severely-stuck device (.23, 2026-07-27: stuck on 'Rebooting' screen,
        # needed a manual power cycle). Enable sysrq, then s(ync) u(nmount-ro)
        # b(oot). If sysrq is unavailable we fall through to reboot -f.
        try:
            # 1 = enable all sysrq functions (often restricted to 0/limited).
            with open("/proc/sys/kernel/sysrq", "w") as f:
                f.write("1")
        except Exception as e:
            logging.warning(f"RO-FIX: could not enable sysrq ({e})")
        try:
            for ch in ("s", "u", "b"):   # sync, remount-ro, reboot
                try:
                    with open("/proc/sysrq-trigger", "w") as s:
                        s.write(ch)
                    time.sleep(0.5)
                except Exception as e:
                    logging.warning(f"RO-FIX: sysrq '{ch}' failed ({e})")
            # If sysrq 'b' didn't take effect within a moment, hard-fallback.
            time.sleep(3)
        except Exception as e:
            logging.error(f"RO-FIX: sysrq reboot path errored ({e})")
        # Fallback: only reached if sysrq did not reboot us.
        logging.warning("RO-FIX: sysrq did not reboot — falling back to reboot -f")
        try:
            subprocess.run(["reboot", "-f"], timeout=10)
        except Exception as e:
            logging.error(f"RO-FIX: reboot -f also failed ({e}) — device may need manual power cycle")
            _ro_fix_result = 1

    try:
        threading.Thread(target=_worker, daemon=True).start()
    except Exception as e:
        _ro_fix_result = 1
        logging.error(f"RO-FIX thread failed to start: {e}")

detect_volume_control()

# Global variables
width = 128
height = 64
pageCount = 3
pageIndex = 0
showPageIndicator = False
pageSleep = 120  # Sleep after 2 minutes (120 seconds)
pageSleepCountdown = pageSleep
selectionIndex = 0  # Start with first item selected
lastActivityTime = time.time()
screenSleeping = False
_ready = False  # True once the main loop is running; gates button actions during startup
nowplaying_scroll_offset = 0
sleepStartTime = 0  # Track when sleep started

# --- Factory-reset "hold to confirm" state -----------------------------------
# The button C-binary only sends discrete presses (no hold duration), so we make
# factory reset deliberate by requiring K2 to be pressed FACTORY_CONFIRM_PRESSES
# times in a row (with 'Yes' selected) within FACTORY_CONFIRM_WINDOW seconds.
# Any other key, leaving the page, or a timeout resets the counter. Combined with
# the confirm page defaulting to 'No', a single stray press can never wipe.
FACTORY_CONFIRM_PRESSES = 3     # K2 presses on 'Yes' required to actually run
FACTORY_CONFIRM_WINDOW = 5.0    # seconds; presses must be within this window
factory_confirm_count = 0       # how many qualifying K2 presses so far
factory_confirm_last = 0.0      # time.time() of the last qualifying press

# Update lock file path
UPDATE_LOCK_FILE = "/tmp/innovo_update.lock"

# Initialize OLED
oled.init()
oled.displayOn()  # ensure panel re-energized on daemon start
# oled.clearDisplay()  # Clear any garbage
oled.setNormalDisplay()
oled.setHorizontalMode()

# Drawing setup
drawing = False
image = Image.new('1', (width, height))
draw = ImageDraw.Draw(image)
fontb18 = ImageFont.truetype('DejaVuSansMono-Bold.ttf', 18)
font14 = ImageFont.truetype('DejaVuSansMono.ttf', 14)
font12 = ImageFont.truetype('DejaVuSansMono.ttf', 12)
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

def get_mac_address(ifname='eth0'):
    try:
        with open(f'/sys/class/net/{ifname}/address', 'r') as f:
            return f.read().strip().upper()
    except:
        try:
            # Try wlan0 if eth0 fails
            with open('/sys/class/net/wlan0/address', 'r') as f:
                return f.read().strip().upper()
        except:
            return "N/A"

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
    global screenSleeping, pageSleepCountdown, lastActivityTime, sleepStartTime
    was_sleeping = screenSleeping
    if screenSleeping:
        sleep_duration = time.time() - sleepStartTime if sleepStartTime > 0 else 0
        logging.info(f"Waking screen after {sleep_duration:.0f}s sleep")
        if sleep_duration > 3600:  # More than 1 hour
            logging.info("Long sleep detected, performing full OLED reinit")
            try:
                oled.reinit()
            except Exception as e:
                logging.warning(f"oled.reinit failed: {e}")
                _panel_on()
        else:
            _panel_on()  # Short sleep: re-enable charge pump and display (hasattr-guarded)
        screenSleeping = False
        sleepStartTime = 0
        logging.info("Screen woken up")
    pageSleepCountdown = pageSleep
    lastActivityTime = time.time()
    return was_sleeping  # Return True if screen was sleeping

# ---- small read-only info helpers (all bounded; used by pages 18/19) ----
def _get_gateway():
    try:
        out = subprocess.check_output(
            "ip route | awk '/default/{print $3; exit}'",
            shell=True, timeout=2).decode('utf-8', 'replace').strip()
        return out or "N/A"
    except Exception:
        return "N/A"

def _get_dns():
    try:
        with open('/etc/resolv.conf', 'r') as f:
            for line in f:
                line = line.strip()
                if line.startswith('nameserver'):
                    parts = line.split()
                    if len(parts) >= 2:
                        return parts[1]
    except Exception:
        pass
    return "N/A"

def _get_cloud_status():
    try:
        rc = subprocess.run(
            ["curl", "-fsS", "--max-time", "5", "https://api.innovo.net/"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=6
        ).returncode
        return "OK" if rc == 0 else "FAIL"
    except Exception:
        return "FAIL"

def _is_registered():
    try:
        if os.path.exists(DEVICE_IDENTITY_CONF):
            return True
        if os.path.exists(MCAGENT_DEALER_TAG):
            return True
    except Exception:
        pass
    return False

def _reg_json_field(field):
    try:
        import json
        if os.path.exists(REGISTRATION_JSON):
            with open(REGISTRATION_JSON, 'r') as f:
                data = json.load(f)
            val = data.get(field)
            if val:
                return str(val)
    except Exception:
        pass
    return ""

def _get_dealer_name():
    name = _reg_json_field("dealerName")
    if name:
        return name
    try:
        if os.path.exists(MCAGENT_DEALER_TAG):
            with open(MCAGENT_DEALER_TAG, 'r') as f:
                tag = f.read().strip()
            if tag:
                return tag
    except Exception:
        pass
    return "N/A"

def _get_device_id():
    for field in ("serialNumber", "activationCode"):
        val = _reg_json_field(field)
        if val:
            return val
    return "N/A"

def draw_page():
    global drawing, pageSleepCountdown, lastActivityTime, screenSleeping, nowplaying_scroll_offset

    lock.acquire()
    is_drawing = drawing
    page_index = pageIndex
    sel_index = selectionIndex
    lock.release()

    if is_drawing or screenSleeping:
        return

    # Screensaver: sleep after pageSleep seconds of no button activity. Use a
    # WALL-CLOCK check (time since lastActivityTime) so the timeout is
    # independent of how often the main loop redraws (the loop runs ~5x/sec for
    # responsive button feedback; a per-call counter would sleep 5x too fast).
    if (time.time() - lastActivityTime) >= pageSleep:
        if not screenSleeping:
            _panel_off()  # Properly turn off display and charge pump (hasattr-guarded)
            screenSleeping = True
            sleepStartTime = time.time()
            logging.info("Screen sleeping")
        return

    lock.acquire()
    drawing = True
    lock.release()

    # Clear the image buffer
    draw.rectangle((0, 0, width, height), outline=0, fill=0)

    # Check if software update is in progress - override all pages
    if os.path.exists(UPDATE_LOCK_FILE):
        try:
            with open(UPDATE_LOCK_FILE, 'r') as uf:
                updating_pkg = uf.read().strip() or "..."
        except:
            updating_pkg = "..."
        # Draw update screen
        draw.text((2, 2), "SOFTWARE UPDATE", font=smartFont, fill=255)
        draw.text((2, 18), "IN PROGRESS", font=fontb12, fill=255)
        draw.text((2, 38), updating_pkg, font=smartFont, fill=255)
        draw.text((2, 52), "Please wait...", font=font10, fill=255)
        # Draw and return early
        oled.drawImage(image)
        lock.acquire()
        drawing = False
        lock.release()
        return

    # --- Page 0: Date/Time + NowPlaying ---
    if page_index == PAGE_DATE:
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
    elif page_index == PAGE_SYSINFO:
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

    # --- Page 18: Network Info (read-only) ---
    elif page_index == STATUS_PAGE:
        # Consolidated read-only Status screen (cloud + registration + dealer +
        # device id). IP/MAC intentionally OMITTED here -- they live on the
        # System Info page (page 1) so no value is duplicated across screens.
        cloud = _get_cloud_status()
        reg = "Y" if _is_registered() else "N"
        dealer = _get_dealer_name()
        dev_id = _get_device_id()

        draw.text((2, 0), 'Status', font=fontb12, fill=255)
        draw.text((2, 16), f"Cloud:{cloud}  Reg:{reg}", font=font10, fill=255)
        draw.text((2, 28), f"Dealer:{dealer}", font=font10, fill=255)
        draw.text((2, 40), f"ID:{dev_id}", font=font10, fill=255)

    # --- Page 2: System Options (top menu) ---
    elif page_index == SYSTEM_OPTIONS_PAGE:
        draw.text((2, 2), 'System Options', font=fontb12, fill=255)
        options = TOP_MENU
        for i, option in enumerate(options):
            y = 18 + i * 13
            if sel_index == i:
                draw.rectangle((2, y, width-4, y+11), outline=255, fill=255)
                draw.text((4, y+1), option, font=font10, fill=0)
            else:
                draw.text((4, y+1), option, font=font10, fill=255)

    # --- Page 16: Power & Reset submenu ---
    elif page_index == POWER_RESET_MENU_PAGE:
        draw.text((2, 2), 'Power & Reset', font=fontb12, fill=255)
        options = POWER_RESET_MENU
        for i, option in enumerate(options):
            y = 14 + i * 10
            if sel_index == i:
                draw.rectangle((2, y, width-4, y+9), outline=255, fill=255)
                draw.text((4, y), option, font=font10, fill=0)
            else:
                draw.text((4, y), option, font=font10, fill=255)

    # --- Page 17: Diagnostics submenu ---
    elif page_index == DIAGNOSTICS_MENU_PAGE:
        draw.text((2, 2), 'Diagnostics', font=fontb12, fill=255)
        options = DIAGNOSTICS_MENU
        for i, option in enumerate(options):
            y = 20 + i * 14
            if sel_index == i:
                draw.rectangle((2, y, width-4, y+12), outline=255, fill=255)
                draw.text((4, y+1), option, font=font10, fill=0)
            else:
                draw.text((4, y+1), option, font=font10, fill=255)

    # --- Page 3: Reboot confirmation ---
    elif page_index == PAGE_REBOOT_CONFIRM:
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
    elif page_index == PAGE_SHUTDOWN_CONFIRM:
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
    elif page_index == PAGE_REBOOTING:
        draw.text((2, 2), 'Rebooting', font=fontb12, fill=255)
        draw.text((2, 20), 'Please wait...', font=font10, fill=255)

    # --- Page 8: Shutting down ---
    elif page_index == PAGE_SHUTTING_DOWN:
        draw.text((2, 2), 'Shutting down', font=fontb12, fill=255)
        draw.text((2, 20), 'Please wait...', font=font10, fill=255)

    # --- Page 20: Safe to unplug (compatibility only; the external system-shutdown
    #     hook draws the REAL frame after filesystems are synced. The daemon never
    #     navigates here from the shutdown-confirm handler.) ---
    elif page_index == SAFE_TO_UNPLUG_PAGE:
        draw.text((2, 2), 'Power off', font=fontb12, fill=255)
        draw.text((2, 18), 'complete.', font=fontb12, fill=255)
        draw.text((2, 40), 'Safe to unplug', font=font10, fill=255)

    # --- Page 9: Reset Network confirmation ---
    elif page_index == PAGE_RESET_NET_CONFIRM:
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

    # --- Page 11: RCA Volume (FIXED - no adjustment) ---
    # The RCA/HiFi zone is raw I2S (PCM5102A) with no usable volume control, so
    # the level is fixed. We keep the screen (so the zone picker stays symmetric)
    # but show that it's fixed instead of a % + bar. B1/B2 do nothing here.
    elif page_index == RCA_VOLUME_PAGE:
        draw.text((2, 2), 'RCA Volume', font=fontb12, fill=255)
        draw.text((2, 26), 'Volume is Fixed', font=fontb12, fill=255)
        draw.text((2, 50), 'B3 back', font=font10, fill=255)

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
        # Bottom line: when 'Yes' is selected, show the triple-press progress so
        # the user knows a single press is not enough; otherwise the warning.
        # A counter that has aged past the confirm window is shown as 0 (it will
        # be reset on the next press anyway).
        _fc = factory_confirm_count
        if _fc > 0 and (time.time() - factory_confirm_last) > FACTORY_CONFIRM_WINDOW:
            _fc = 0
        if sel_index == 0 and _fc > 0:
            draw.text((2, 50), 'Press Yes %d/%d' % (_fc,
                      FACTORY_CONFIRM_PRESSES), font=font10, fill=255)
        elif sel_index == 0:
            draw.text((2, 50), 'Press Yes x%d' % FACTORY_CONFIRM_PRESSES,
                      font=font10, fill=255)
        else:
            draw.text((2, 50), 'wipes to defaults', font=font10, fill=255)

    # --- Page 14: Factory reset in progress ---
    elif page_index == FACTORY_PROGRESS_PAGE:
        draw.text((2, 2), 'Factory Reset', font=fontb12, fill=255)
        draw.text((2, 22), 'Running...', font=font10, fill=255)
        draw.text((2, 38), 'Device will reboot', font=font10, fill=255)

    # --- Page 21: Repair System progress ---
    elif page_index == SELFHEAL_PROGRESS_PAGE:
        draw.text((2, 2), 'Repair System', font=fontb12, fill=255)
        if _selfheal_result is None:
            draw.text((2, 24), 'Repairing...', font=font10, fill=255)
            draw.text((2, 40), 'Please wait', font=font10, fill=255)
        elif _selfheal_result == 0:
            draw.text((2, 24), 'Done', font=fontb12, fill=255)
            draw.text((2, 50), 'B3: Back', font=font10, fill=255)
        else:
            draw.text((2, 24), 'Failed', font=fontb12, fill=255)
            draw.text((2, 38), 'See repair log', font=font10, fill=255)
            draw.text((2, 50), 'B3: Back', font=font10, fill=255)

    # --- Page 22: Read-only filesystem WARNING (auto-shown on SD corruption) ---
    elif page_index == PAGE_RO_WARNING:
        # Inverted header bar so it reads as an alert.
        draw.rectangle((0, 0, width-1, 15), outline=255, fill=255)
        draw.text((4, 2), 'DISK READ-ONLY', font=fontb12, fill=0)
        draw.text((2, 20), 'SD error detected.', font=font10, fill=255)
        draw.text((2, 34), 'Repair needs reboot.', font=font10, fill=255)
        draw.text((2, 50), 'B2: Repair  B3: Skip', font=font10, fill=255)

    # --- Page 23: Repair confirm (Yes/No) ---
    elif page_index == PAGE_RO_FIX_CONFIRM:
        draw.text((2, 2), 'Repair & reboot?', font=fontb12, fill=255)
        options = ['Yes', 'No']
        for i, option in enumerate(options):
            y = 20 + i*14
            if sel_index == i:
                draw.rectangle((2, y, width-4, y+12), outline=255, fill=255)
                draw.text((4, y+1), option, font=font10, fill=0)
            else:
                draw.text((4, y+1), option, font=font10, fill=255)

    # --- Page 24: Repair in progress (device reboots) ---
    elif page_index == PAGE_RO_FIXING:
        draw.text((2, 2), 'Repairing disk', font=fontb12, fill=255)
        draw.text((2, 24), 'Scheduling fsck', font=font10, fill=255)
        draw.text((2, 40), 'Rebooting...', font=font10, fill=255)

    # Clear and redraw
    # oled.clearDisplay()
    oled.drawImage(image)

    lock.acquire()
    drawing = False
    lock.release()

def update_page_index(pi):
    global pageIndex, selectionIndex, lastActivityTime
    global factory_confirm_count, factory_confirm_last
    lock.acquire()
    pageIndex = pi
    # Factory Reset confirm defaults to 'No' (index 1 of ['Yes','No']) so a stray
    # press can never land on Yes; every other page keeps first-item default.
    selectionIndex = 1 if pi == FACTORY_CONFIRM_PAGE else 0
    lastActivityTime = time.time()
    # Any page change (including entering or leaving the factory page) resets the
    # triple-press confirm counter.
    factory_confirm_count = 0
    factory_confirm_last = 0.0
    lock.release()
    wake_screen()

def update_selection_index():
    global selectionIndex, lastActivityTime, pageIndex
    global factory_confirm_count, factory_confirm_last
    lock.acquire()
    n = MENU_LENS.get(pageIndex, 2)  # menus vary in length; default Yes/No = 2
    selectionIndex = (selectionIndex + 1) % n
    lastActivityTime = time.time()
    # Scrolling the selection (e.g. Yes<->No) on the factory page restarts the
    # triple-press confirm sequence.
    if pageIndex == FACTORY_CONFIRM_PAGE:
        factory_confirm_count = 0
        factory_confirm_last = 0.0
    lock.release()
    wake_screen()

def receive_signal(signum, stack):
    global pageIndex, selectionIndex, factory_confirm_count, factory_confirm_last

    logging.info(f"Received signal: {signum}")

    # Startup gate: signal handlers are installed FIRST (before the logo/sleeps)
    # so an early button press cannot kill the process via the default SIGUSR
    # disposition, but we do not ACT on a press until the UI is fully up.
    wake_ws = wake_screen()
    if not _ready:
        return

    # If the screen was asleep, the first press wakes it AND jumps to screen 0
    # (date/home). It does NOT perform the button's normal action - this is a
    # safety choice so a press on a sleeping screen can never trigger a menu
    # action (Reboot/Shutdown/etc.); it lands you on the harmless date page.
    if wake_ws:
        logging.info("Screen was sleeping - waking to screen 0 (date)")
        update_page_index(PAGE_DATE)  # jump to date/home page
        draw_page()
        return

    lock.acquire()
    page_index = pageIndex
    sel_index = selectionIndex
    lock.release()

    if signum == signal.SIGUSR1:  # Button K1 - Navigate / scroll / down
        # Menu/confirm pages: scroll the selection.
        if page_index in (SYSTEM_OPTIONS_PAGE, POWER_RESET_MENU_PAGE, DIAGNOSTICS_MENU_PAGE,
                          PAGE_REBOOT_CONFIRM, PAGE_SHUTDOWN_CONFIRM,
                          PAGE_RESET_NET_CONFIRM, FACTORY_CONFIRM_PAGE,
                          PAGE_RO_FIX_CONFIRM):
            update_selection_index()
        # Read-only info cycle: Date -> SysInfo -> Status -> (wrap) Date.
        elif page_index == PAGE_DATE:
            update_page_index(PAGE_SYSINFO)
        elif page_index == PAGE_SYSINFO:
            update_page_index(STATUS_PAGE)
        elif page_index == STATUS_PAGE:
            update_page_index(PAGE_DATE)
        # Volume picker: K1 = RCA zone.
        elif page_index == VOLUME_MENU_PAGE:
            update_page_index(RCA_VOLUME_PAGE)
        elif page_index == AUX_VOLUME_PAGE:
            change_volume(-VOLUME_STEP)              # Aux vol down
        elif page_index == RCA_VOLUME_PAGE:
            pass                                     # RCA volume is fixed - no-op
        elif page_index == RESET_AUDIO_PAGE:
            reset_rca_audio()                        # K1 = Reset RCA
            update_page_index(PAGE_DATE)
        draw_page()

    elif signum == signal.SIGUSR2:  # Button K2 - Confirm / select / up
        if page_index == SYSTEM_OPTIONS_PAGE:  # top menu
            choice = TOP_MENU[sel_index] if sel_index < len(TOP_MENU) else ""
            if choice == "Volume":
                update_page_index(VOLUME_MENU_PAGE)
            elif choice == "Power & Reset":
                update_page_index(POWER_RESET_MENU_PAGE)
            elif choice == "Diagnostics":
                update_page_index(DIAGNOSTICS_MENU_PAGE)
        elif page_index == POWER_RESET_MENU_PAGE:  # Power & Reset submenu
            choice = POWER_RESET_MENU[sel_index] if sel_index < len(POWER_RESET_MENU) else ""
            if choice == "Reboot":
                update_page_index(PAGE_REBOOT_CONFIRM)
            elif choice == "Shutdown":
                update_page_index(PAGE_SHUTDOWN_CONFIRM)
            elif choice == "Reset Network":
                update_page_index(PAGE_RESET_NET_CONFIRM)
            elif choice == "Reset Audio":
                update_page_index(RESET_AUDIO_PAGE)
            elif choice == "Factory Reset":
                update_page_index(FACTORY_CONFIRM_PAGE)
        elif page_index == DIAGNOSTICS_MENU_PAGE:  # Diagnostics submenu
            choice = DIAGNOSTICS_MENU[sel_index] if sel_index < len(DIAGNOSTICS_MENU) else ""
            if choice == "Repair System":
                run_self_heal()
                update_page_index(SELFHEAL_PROGRESS_PAGE)
            elif choice == "Restart Audio":
                run_restart_audio()
                update_page_index(PAGE_DATE)
            elif choice == "Restart Cloud":
                run_restart_cloud()
                update_page_index(PAGE_DATE)
        elif page_index == PAGE_REBOOT_CONFIRM:  # Reboot confirm
            if sel_index == 0:  # Yes
                update_page_index(PAGE_REBOOTING)
                draw_page()
                time.sleep(3)
                os.system('systemctl reboot')
            else:  # No
                update_page_index(PAGE_DATE)
        elif page_index == PAGE_SHUTDOWN_CONFIRM:  # Shutdown confirm
            if sel_index == 0:  # Yes
                update_page_index(PAGE_SHUTTING_DOWN)
                draw_page()
                # Keep 'Shutting down / Please wait...' up through the ENTIRE
                # poweroff (service-stop + final SD sync/unmount, ~20s). The
                # 'Safe to unplug' frame is drawn by the systemd system-shutdown
                # hook (innovo-oled-safe-to-unplug) AFTER filesystems are synced
                # and root is RO -- i.e. when it is GENUINELY safe to pull power.
                # (Old code drew 'Safe to unplug' here on a timer BEFORE poweroff,
                # while the box was still writing to disk.) Do NOT draw page 20.
                time.sleep(1)
                os.system('systemctl poweroff')
            else:  # No
                update_page_index(PAGE_DATE)
        elif page_index == PAGE_RESET_NET_CONFIRM:  # Reset network confirm
            if sel_index == 0:  # Yes
                run_reset_network()
                update_page_index(PAGE_DATE)
            else:  # No
                update_page_index(PAGE_DATE)
        elif page_index == FACTORY_CONFIRM_PAGE:  # Factory Reset confirm
            if sel_index == 0:  # Yes -- require FACTORY_CONFIRM_PRESSES in a row
                now = time.time()
                # If the previous qualifying press was too long ago, start over.
                if now - factory_confirm_last > FACTORY_CONFIRM_WINDOW:
                    factory_confirm_count = 0
                factory_confirm_count += 1
                factory_confirm_last = now
                if factory_confirm_count >= FACTORY_CONFIRM_PRESSES:
                    factory_confirm_count = 0
                    factory_confirm_last = 0.0
                    update_page_index(FACTORY_PROGRESS_PAGE)
                    draw_page()
                    time.sleep(2)
                    run_factory_reset()
                else:
                    # Not enough presses yet -- the confirm page render shows the
                    # X/N counter; just redraw (draw_page at end of handler).
                    pass
            else:  # No
                update_page_index(PAGE_DATE)
        elif page_index == VOLUME_MENU_PAGE:
            update_page_index(AUX_VOLUME_PAGE)       # picker: K2 = Aux zone
        elif page_index == AUX_VOLUME_PAGE:
            change_volume(VOLUME_STEP)               # Aux vol up
        elif page_index == RCA_VOLUME_PAGE:
            pass                                     # RCA volume is fixed - no-op
        elif page_index == RESET_AUDIO_PAGE:
            reset_aux_audio()                        # K2 = Reset Aux
            update_page_index(PAGE_DATE)
        elif page_index == PAGE_RO_WARNING:          # K2 = Repair -> confirm
            update_page_index(PAGE_RO_FIX_CONFIRM)
        elif page_index == PAGE_RO_FIX_CONFIRM:      # K2 = confirm Yes/No
            if sel_index == 0:                       # Yes
                update_page_index(PAGE_RO_FIXING)
                draw_page()
                run_ro_fix()                         # schedules fsck + reboots
            else:                                    # No
                update_page_index(PAGE_RO_WARNING)   # back to the warning (RO persists)
        else:
            # Info pages: K2 opens the top menu.
            update_page_index(SYSTEM_OPTIONS_PAGE)
        draw_page()

    elif signum == signal.SIGALRM:  # Button K3 - Menu / Back
        if page_index == SYSTEM_OPTIONS_PAGE:
            update_page_index(PAGE_DATE)             # close top menu -> home
        elif page_index in (POWER_RESET_MENU_PAGE, DIAGNOSTICS_MENU_PAGE):
            update_page_index(SYSTEM_OPTIONS_PAGE)   # submenu -> top menu
        elif page_index in (PAGE_REBOOT_CONFIRM, PAGE_SHUTDOWN_CONFIRM,
                            PAGE_RESET_NET_CONFIRM, RESET_AUDIO_PAGE, FACTORY_CONFIRM_PAGE):
            update_page_index(POWER_RESET_MENU_PAGE) # confirm/reset -> Power & Reset submenu
        elif page_index == SELFHEAL_PROGRESS_PAGE:
            update_page_index(DIAGNOSTICS_MENU_PAGE) # self-heal -> Diagnostics submenu
        elif page_index in (AUX_VOLUME_PAGE, RCA_VOLUME_PAGE):
            update_page_index(VOLUME_MENU_PAGE)      # zone page -> Volume picker
        elif page_index == VOLUME_MENU_PAGE:
            update_page_index(SYSTEM_OPTIONS_PAGE)   # Volume picker -> top menu
        elif page_index == PAGE_RO_FIX_CONFIRM:
            update_page_index(PAGE_RO_WARNING)       # confirm -> back to warning
        elif page_index == PAGE_RO_WARNING:
            # K3 = Skip: dismiss to home. The main-loop RO guard re-shows the
            # warning within ~3s if still read-only, so it can't be lost — this
            # just lets the operator peek at other pages between alerts.
            update_page_index(PAGE_DATE)
        elif page_index in (PAGE_DATE, PAGE_SYSINFO, STATUS_PAGE):
            update_page_index(SYSTEM_OPTIONS_PAGE)   # info page -> open top menu
        else:
            update_page_index(SYSTEM_OPTIONS_PAGE)   # fallback -> top menu
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
        # oled.clearDisplay()

    logging.info("Starting main loop...")
    _ready = True
    _ro_check_last = 0.0
    while True:
        try:
            # Read-only-filesystem guard: poll every few seconds (cheap
            # /proc/mounts read). If root goes RO (SD corruption -> ext4
            # emergency_ro) FORCE the warning page so the operator sees it and
            # can trigger the repair, overriding whatever page is up. Never
            # steal focus while the user is already on the RO pages (warning /
            # confirm / fixing) or mid-shutdown.
            now = time.time()
            if now - _ro_check_last >= 3.0:
                _ro_check_last = now
                if is_root_readonly() and pageIndex not in (
                        PAGE_RO_WARNING, PAGE_RO_FIX_CONFIRM, PAGE_RO_FIXING,
                        PAGE_SHUTTING_DOWN, PAGE_REBOOTING):
                    wake_screen()
                    update_page_index(PAGE_RO_WARNING)
            draw_page()
            # Redraw ~5x/sec so a button-driven page/selection change appears
            # near-instantly (a handler's redraw that got skipped by the drawing
            # guard is picked up within 0.2s instead of up to 1s). Screensaver is
            # wall-clock based (see draw_page) so this rate does not affect it.
            time.sleep(0.2)

        except KeyboardInterrupt:
            break
        except Exception as e:
            logging.error(f"Error in main loop: {e}")
            time.sleep(0.5)

except Exception as e:
    logging.error(f"Fatal error: {e}")
    import traceback
    traceback.print_exc()
