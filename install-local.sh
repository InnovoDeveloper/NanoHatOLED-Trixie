#!/bin/bash
set -e

echo "========================================"
echo "NanoHat OLED Local Installation Script"
echo "For Debian Trixie/Bookworm"
echo "========================================"

# Get the directory where script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Root check
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root: sudo bash $0"
    exit 1
fi

# Commented out for testing - packages already installed
# echo "[1/8] Installing system dependencies..."
# apt-get update
# apt-get install -y \
#     build-essential \
#     gcc \
#     python3 \
#     python3-pip \
#     python3-dev \
#     python3-pil \
#     python3-smbus \
#     i2c-tools \
#     git
# 
# # Create Python symlink if needed
# if [ ! -f /usr/bin/python ]; then
#     ln -sf /usr/bin/python3 /usr/bin/python
# fi
# 
# echo "[2/8] Upgrading Pillow for compatibility..."
# pip3 install pillow --upgrade --break-system-packages 2>/dev/null || \
# pip3 install pillow --upgrade 2>/dev/null || true

echo "[3/8] Fixing WiringNP for modern GCC..."
if [ -d "$SCRIPT_DIR/BakeBit/WiringNP" ]; then
    cd "$SCRIPT_DIR/BakeBit/WiringNP/wiringPi"
    
    # Restore original if it was already modified
    if [ -f wiringPi.c.original ]; then
        cp wiringPi.c.original wiringPi.c
    else
        cp wiringPi.c wiringPi.c.original
    fi
    
    # Apply comprehensive fix for GCC 12+ compatibility
    echo "Applying GCC 12+ compatibility patches..."
    
    # Fix void functions: return -1; → return;
    sed -i '/^void sunxi_set_gpio_mode/,/^}/s/return -1;/return;/g' wiringPi.c
    sed -i '/^void sunxi_digitalWrite/,/^}/s/return -1;/return;/g' wiringPi.c
    sed -i '/^void sunxi_pullUpDnControl/,/^}/s/return -1;/return;/g' wiringPi.c
    sed -i '/^void setPadDrive/,/^}/s/return -1;/return;/g' wiringPi.c
    sed -i '/^void pwmSetMode/,/^}/s/return -1;/return;/g' wiringPi.c
    sed -i '/^void pwmSetRange/,/^}/s/return -1;/return;/g' wiringPi.c
    sed -i '/^void pwmSetClock/,/^}/s/return -1;/return;/g' wiringPi.c
    sed -i '/^void gpioClockSet/,/^}/s/return -1;/return;/g' wiringPi.c
    sed -i '/^static void pinModeDummy/,/^}/s/return -1;/return;/g' wiringPi.c
    sed -i '/^static void pullUpDnControlDummy/,/^}/s/return -1;/return;/g' wiringPi.c
    sed -i '/^static void digitalWriteDummy/,/^}/s/return -1;/return;/g' wiringPi.c
    sed -i '/^static void pwmWriteDummy/,/^}/s/return -1;/return;/g' wiringPi.c
    sed -i '/^static void analogWriteDummy/,/^}/s/return -1;/return;/g' wiringPi.c
    sed -i '/^void pinModeAlt/,/^}/s/return -1;/return;/g' wiringPi.c
    sed -i '/^void pinMode(/,/^}/s/return -1;/return;/g' wiringPi.c
    sed -i '/^void pullUpDnControl(/,/^}/s/return -1;/return;/g' wiringPi.c
    sed -i '/^void digitalWrite(/,/^}/s/return -1;/return;/g' wiringPi.c
    sed -i '/^void pwmWrite/,/^}/s/return -1;/return;/g' wiringPi.c
    sed -i '/^void analogWrite/,/^}/s/return -1;/return;/g' wiringPi.c
    sed -i '/^void digitalWriteByte/,/^}/s/return -1;/return;/g' wiringPi.c
    sed -i '/^void delayMicroseconds/,/^}/s/return -1;/return;/g' wiringPi.c
    
    # Fix int functions: plain return; → return -1;
    sed -i '/^int wiringPiFailure/,/^}/s/^\([[:space:]]*\)return;$/\1return -1;/g' wiringPi.c
    sed -i '/^int wpiPinToGpio/,/^}/s/^\([[:space:]]*\)return;$/\1return -1;/g' wiringPi.c
    sed -i '/^int physPinToGpio/,/^}/s/^\([[:space:]]*\)return;$/\1return -1;/g' wiringPi.c
    sed -i '/^int physPinToPin/,/^}/s/^\([[:space:]]*\)return;$/\1return -1;/g' wiringPi.c
    sed -i '/^int getAlt(/,/^}/s/^\([[:space:]]*\)return;$/\1return -1;/g' wiringPi.c
    sed -i '/^int getAltSilence/,/^}/s/^\([[:space:]]*\)return;$/\1return -1;/g' wiringPi.c
    sed -i '/^int waitForInterrupt/,/^}/s/^\([[:space:]]*\)return;$/\1return -1;/g' wiringPi.c
    sed -i '/^int wiringPiISR/,/^}/s/^\([[:space:]]*\)return;$/\1return -1;/g' wiringPi.c
    sed -i '/^int wiringPiSetup/,/^}/s/^\([[:space:]]*\)return;$/\1return -1;/g' wiringPi.c
    
    cd "$SCRIPT_DIR/BakeBit/WiringNP"
else
    echo "ERROR: BakeBit/WiringNP not found!"
    exit 1
fi

echo "[4/8] Building WiringNP library..."
cd "$SCRIPT_DIR/BakeBit/WiringNP"

# Build the library (ignore gpio utility errors)
chmod +x build
./build clean 2>/dev/null || true

# Build library only, skip gpio utility if it fails
if ! ./build 2>&1; then
    echo "Note: Full build failed, trying library-only build..."
    cd wiringPi
    make clean
    make
    sudo make install
    cd ..
fi

# Verify library was built
if [ -f /usr/local/lib/libwiringPi.so ] || [ -f /usr/lib/libwiringPi.so ]; then
    echo "✓ WiringNP library built successfully"
    ldconfig
else
    echo "ERROR: WiringNP library failed to build"
    exit 1
fi

echo "[5/8] Fixing Python script for Pillow 10+..."
cd "$SCRIPT_DIR/BakeBit/Software/Python"

# Backup original
if [ ! -f bakebit_nanohat_oled.py.original ]; then
    cp bakebit_nanohat_oled.py bakebit_nanohat_oled.py.original
fi

# Apply Pillow textsize → textbbox fix
python3 << 'PYFIX'
import re

try:
    with open('bakebit_nanohat_oled.py', 'r') as f:
        content = f.read()
    
    if 'draw.textsize' in content:
        print("Applying Pillow 10+ compatibility fix...")
        
        # Fix pattern: w, h = draw.textsize(text, font=font)
        content = re.sub(
            r'(\w+),\s*(\w+)\s*=\s*draw\.textsize\((.*?),\s*font=(.*?)\)',
            r'bbox = draw.textbbox((0, 0), \3, font=\4)\n        \1 = bbox[2] - bbox[0]\n        \2 = bbox[3] - bbox[1]',
            content
        )
        
        # Fix pattern: w = draw.textsize(text, font=font)[0]
        content = re.sub(
            r'(\w+)\s*=\s*draw\.textsize\((.*?),\s*font=(.*?)\)\[0\]',
            r'bbox = draw.textbbox((0, 0), \2, font=\3)\n        \1 = bbox[2] - bbox[0]',
            content
        )
        
        with open('bakebit_nanohat_oled.py', 'w') as f:
            f.write(content)
        print("✓ Pillow compatibility fix applied")
    else:
        print("✓ Script already compatible")
except Exception as e:
    print(f"Warning: Could not apply fix: {e}")
PYFIX

echo "[6/8] Determining Python interpreter..."
cd "$SCRIPT_DIR"

# Get actual Python3 interpreter name
if [ -L /usr/bin/python3 ]; then
    PY3_INTERP=$(readlink /usr/bin/python3)
else
    PY3_INTERP="python3"
fi

echo "Using Python interpreter: $PY3_INTERP"

# Update daemonize.h with correct Python interpreter
if [ -f "$SCRIPT_DIR/Source/daemonize.h" ]; then
    sed -i "s|#define PYTHON3_INTERP.*|#define PYTHON3_INTERP \"$PY3_INTERP\"|" "$SCRIPT_DIR/Source/daemonize.h"
fi

echo "[7/8] Compiling NanoHatOLED daemon..."
cd "$SCRIPT_DIR"

gcc Source/daemonize.c Source/main.c -lrt -lpthread -o NanoHatOLED || {
    echo "ERROR: Failed to compile daemon"
    exit 1
}

echo "✓ Daemon compiled successfully"

echo "[8/8] Creating systemd service..."

cat > /etc/systemd/system/nanohat-oled.service << SVCEOF
[Unit]
Description=NanoHat OLED Display Service
After=multi-user.target

[Service]
Type=simple
WorkingDirectory=$SCRIPT_DIR
ExecStartPre=/bin/sleep 2
ExecStart=$SCRIPT_DIR/NanoHatOLED
Restart=on-failure
RestartSec=10
TimeoutStartSec=30
KillMode=control-group
KillSignal=SIGTERM
RemainAfterExit=no

[Install]
WantedBy=multi-user.target
SVCEOF

# Aggressive cleanup of any existing instances
echo "Cleaning up any existing service instances..."
systemctl stop nanohat-oled.service 2>/dev/null || true
systemctl kill nanohat-oled.service 2>/dev/null || true
sleep 1
killall -9 NanoHatOLED 2>/dev/null || true
killall -9 python3 2>/dev/null || true
pkill -9 -f "bakebit_nanohat_oled.py" 2>/dev/null || true
rm -f /var/run/nanohat-oled.pid 2>/dev/null || true
sleep 2

systemctl daemon-reload
systemctl enable nanohat-oled.service

echo ""
echo "========================================"
echo "Installation Complete!"
echo "========================================"
echo ""
echo "Installation directory: $SCRIPT_DIR"
echo ""
echo "To start the service:"
echo "  systemctl start nanohat-oled.service"
echo ""
echo "To check status:"
echo "  systemctl status nanohat-oled.service"
echo ""
echo "To view logs:"
echo "  journalctl -fu nanohat-oled.service"
echo "  tail -f /var/log/oled.log"
echo ""
echo "To test buttons manually:"
echo "  Watch /var/log/oled.log while pressing K1, K2, K3"
echo ""
