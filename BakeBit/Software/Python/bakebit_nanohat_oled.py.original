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

def wake_screen():
    global screenSleeping, pageSleepCountdown, lastActivityTime
    if screenSleeping:
        oled.setNormalDisplay()
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
            # oled.clearDisplay()
            screenSleeping = True
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
    elif page_index == 1:
        try:
            IPAddress = get_ip_address('eth0')
            if IPAddress == "N/A":
                IPAddress = get_ip()
        except:
            IPAddress = get_ip()

        try:
            cmd = "top -bn1 | grep load | awk '{printf \"CPU Load: %.2f\", $(NF-2)}'"
            CPU = subprocess.check_output(cmd, shell=True, timeout=2).decode('utf-8')
        except:
            CPU = "CPU Load: N/A"
            
        try:
            cmd = "free -m | awk 'NR==2{printf \"Mem: %s/%sMB %.2f%%\", $3,$2,$3*100/$2 }'"
            MemUsage = subprocess.check_output(cmd, shell=True, timeout=2).decode('utf-8')
        except:
            MemUsage = "Mem: N/A"
            
        try:
            cmd = "df -h | awk '$NF==\"/\"{printf \"Disk: %d/%dGB %s\", $3,$2,$5}'"
            Disk = subprocess.check_output(cmd, shell=True, timeout=2).decode('utf-8')
        except:
            Disk = "Disk: N/A"
            
        try:
            tempI = int(open('/sys/class/thermal/thermal_zone0/temp').read())
            if tempI > 1000:
                tempI = tempI / 1000
            tempStr = f"CPU TEMP: {int(tempI)}C"
        except:
            tempStr = "CPU TEMP: N/A"

        draw.text((0, 5), f"IP: {IPAddress}", font=smartFont, fill=255)
        draw.text((0, 17), CPU, font=smartFont, fill=255)
        draw.text((0, 29), MemUsage, font=smartFont, fill=255)
        draw.text((0, 41), Disk, font=smartFont, fill=255)
        draw.text((0, 53), tempStr, font=smartFont, fill=255)

    # --- Page 2: Power Options ---
    elif page_index == 2:
        draw.text((2, 2), 'Power Options', font=fontb12, fill=255)
        options = ['Reboot', 'Shutdown', 'Reset Network']
        for i, option in enumerate(options):
            y = 20 + i * 14
            if sel_index == i:
                draw.rectangle((2, y, width-4, y+12), outline=255, fill=255)
                draw.text((4, y+1), option, font=font10, fill=0)
            else:
                draw.text((4, y+1), option, font=font10, fill=255)

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
    if pageIndex == 2:  # Power menu has 3 items
        selectionIndex = (selectionIndex + 1) % 3
    else:  # Yes/No dialogs have 2 items
        selectionIndex = (selectionIndex + 1) % 2
    lastActivityTime = time.time()
    lock.release()
    wake_screen()

def receive_signal(signum, stack):
    global pageIndex, selectionIndex
    
    logging.info(f"Received signal: {signum}")
    wake_screen()

    lock.acquire()
    page_index = pageIndex
    sel_index = selectionIndex
    lock.release()

    if signum == signal.SIGUSR1:  # Button K1 - Navigate/Select
        if page_index in [2, 3, 5, 9]:  # In menu pages
            update_selection_index()
        elif page_index == 0:
            update_page_index(1)
        elif page_index == 1:
            update_page_index(0)
        draw_page()

    elif signum == signal.SIGUSR2:  # Button K2 - Confirm/Select
        if page_index == 2:  # Power menu
            if sel_index == 0:
                update_page_index(3)  # Reboot confirm
            elif sel_index == 1:
                update_page_index(5)  # Shutdown confirm
            else:
                update_page_index(9)  # Reset network confirm
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
        else:
            update_page_index(0)
        draw_page()

    elif signum == signal.SIGALRM:  # Button K3 - Menu/Back
        if page_index == 2:
            update_page_index(0)
        else:
            update_page_index(2)
        draw_page()

# Main execution
try:
    # Display logo if it exists
    logo_path = 'innovo.png'
    if os.path.exists(logo_path):
        logging.info("Loading logo...")
        image0 = Image.open(logo_path).convert('1')
        oled.drawImage(image0)
        time.sleep(2)
        # oled.clearDisplay()

    signal.signal(signal.SIGUSR1, receive_signal)
    signal.signal(signal.SIGUSR2, receive_signal)
    signal.signal(signal.SIGALRM, receive_signal)

    logging.info("Starting main loop...")
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
