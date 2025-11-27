#!/usr/bin/env python3
import signal
import time
import sys
import bakebit_128_64_oled as oled
from PIL import Image, ImageFont, ImageDraw

# Initialize OLED
oled.init()
oled.clearDisplay()
oled.setNormalDisplay()
oled.setHorizontalMode()

# Setup display
image = Image.new('1', (128, 64))
draw = ImageDraw.Draw(image)
font = ImageFont.truetype('DejaVuSansMono-Bold.ttf', 14)

page = 0

def update_display():
    global page
    draw.rectangle((0,0,128,64), outline=0, fill=0)
    draw.text((10,10), f"PAGE {page}", font=font, fill=255)
    draw.text((10,30), time.strftime("%H:%M:%S"), font=font, fill=255)
    
    if page == 0:
        draw.text((10,45), "Main", font=font, fill=255)
    elif page == 1:
        draw.text((10,45), "Info", font=font, fill=255)
    elif page == 2:
        draw.text((10,45), "Menu", font=font, fill=255)
    
    oled.drawImage(image)
    print(f"Display updated: Page {page}")

def button_handler(signum, frame):
    global page
    if signum == signal.SIGUSR1:
        page = (page + 1) % 3
        print(f"K1 pressed - switched to page {page}")
    elif signum == signal.SIGUSR2:
        print(f"K2 pressed on page {page}")
    elif signum == signal.SIGALRM:
        print(f"K3 pressed on page {page}")
    update_display()

signal.signal(signal.SIGUSR1, button_handler)
signal.signal(signal.SIGUSR2, button_handler)
signal.signal(signal.SIGALRM, button_handler)

print("Test display ready")
update_display()

# Just sleep forever, only update on button press
while True:
    time.sleep(10)
