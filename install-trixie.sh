#!/bin/bash
set -e

echo "======================================"
echo "NanoHat OLED Installer for Debian Trixie/Bookworm"
echo "======================================"

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    echo "Please run as root (sudo)"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Ensure BakeBit is present
if [ ! -d "$SCRIPT_DIR/BakeBit/Software" ]; then
  echo "BakeBit submodule missing; cloning..."
  rm -rf "$SCRIPT_DIR/BakeBit"
  git clone --depth=1 https://github.com/friendlyarm/BakeBit.git "$SCRIPT_DIR/BakeBit"
fi

# Install dependencies
echo "Installing dependencies..."
apt-get update
apt-get install -y gcc python3 python3-pip python3-dev python3-pil python3-smbus i2c-tools git

# Create python symlink if needed
if [ ! -f /usr/bin/python ]; then
    ln -sf /usr/bin/python3 /usr/bin/python
fi

# Upgrade Pillow
pip3 install pillow --upgrade --break-system-packages 2>/dev/null || pip3 install pillow --upgrade

# Initialize submodules
echo "Initializing submodules..."
git submodule update --init --recursive

# Clone and build WiringNP
echo "Building WiringNP..."
cd BakeBit
if [ ! -d "WiringNP" ]; then
    git clone https://github.com/friendlyarm/WiringNP
fi
cd WiringNP/wiringPi
# Fix for newer GCC
sed -i '2476s/return;/return -1;/' wiringPi.c 2>/dev/null || true
cd ..
chmod +x build
./build clean
./build || echo "Note: GPIO utility failed but library installed"
ldconfig
cd ../..

# Compile the Trixie-compatible daemon
echo "Compiling daemon..."
if [ -f "Source/main_trixie.c" ]; then
    gcc Source/main_trixie.c -o NanoHatOLED
else
    gcc Source/daemonize.c Source/main.c -lrt -lpthread -o NanoHatOLED
fi

# Create systemd service
echo "Creating systemd service..."
cat > /etc/systemd/system/nanohat-oled.service << 'SERVICE'
[Unit]
Description=NanoHat OLED Display
After=multi-user.target

[Service]
Type=forking
PIDFile=/var/run/nanohat-oled.pid
ExecStart=$(pwd)/NanoHatOLED
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICE

# Update service path
sed -i "s|\$(pwd)|$(pwd)|g" /etc/systemd/system/nanohat-oled.service

# Enable and start service
systemctl daemon-reload
systemctl enable nanohat-oled.service
systemctl start nanohat-oled.service

echo "======================================"
echo "Installation complete!"
echo "Check status: systemctl status nanohat-oled.service"
echo "Test buttons: K1 (toggle), K2 (select), K3 (menu)"
echo "======================================"
