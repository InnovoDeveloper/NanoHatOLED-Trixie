# NanoHatOLED-Trixie

Trixie/Bookworm-compatible NanoHat OLED stack for FriendlyELEC boards and DietPi/Debian systems.

This repo contains:

- A small C daemon (`NanoHatOLED`) that manages the NanoHat OLED panel and buttons.
- Integration with the original FriendlyELEC **BakeBit** drivers.
- A robust installer (`install-trixie.sh`) that:
  - Fixes **WiringNP** so it builds on modern GCC (Debian 12 / Trixie).
  - Patches the OLED Python script for **Pillow 10+**.
  - Detects the correct **Python 3 interpreter** and wires it into the daemon.
  - Installs a **systemd** service `nanohat-oled.service`.

The goal is: **clone, run installer, enjoy a working OLED + buttons** on Trixie/Bookworm with no manual patching.

---

## Supported Targets

- Debian 12 (Bookworm) / Debian 13 (Trixie) based systems
- FriendlyELEC NanoPi boards with NanoHat OLED
- DietPi images running on those boards

You must have:

- Functional I²C bus (e.g. `/dev/i2c-0` or `/dev/i2c-1`)
- Internet access (to fetch BakeBit if not already included)

---

## Quick Start

```bash
# 1) Clone this repo (example under /opt)
cd /opt
git clone https://github.com/<your-account>/NanoHatOLED-Trixie.git
cd NanoHatOLED-Trixie

# 2) First-time install with packages
sudo bash install-trixie.sh --install-pkg

# 3) Start the service
sudo systemctl start nanohat-oled.service

# 4) Check status
systemctl status nanohat-oled.service
