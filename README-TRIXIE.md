# NanoHat OLED - Debian Trixie/Bookworm Compatible Fork

This fork includes all necessary fixes to run NanoHat OLED on modern Debian systems (Bookworm, Trixie) with:
- GCC 12+ compatibility
- Pillow 10+ support (textsize → textbbox)
- Python 3.11+ only
- GPIO export handling for modern kernels
- Active-high button support

## Quick Installation
```bash
git clone https://github.com/InnovoDeveloper/NanoHatOLED.git
cd NanoHatOLED
sudo ./install-trixie.sh
```

## What's Fixed

1. **WiringNP Compilation** - Fixed return statements for stricter GCC
2. **Pillow 10+ Compatibility** - Updated textsize() to textbbox()
3. **GPIO Handling** - Automatic export and direction setting
4. **Button Detection** - Support for active-high buttons (NanoPi H3)
5. **Display Flashing** - Removed unnecessary clearDisplay() calls

## Manual Installation

If the automatic installer fails, see [MANUAL-INSTALL.md](MANUAL-INSTALL.md)

## Hardware Configuration

- **I2C Bus**: 0
- **OLED Address**: 0x3C
- **Button GPIOs**: 0, 2, 3 (active-high on press)

## Button Functions

- **K1**: Toggle between display pages
- **K2**: Select/confirm menu options
- **K3**: Open power menu / go back

## Troubleshooting

### Display not working
```bash
sudo i2cdetect -y 0  # Should show 3c
```

### Buttons not responding
```bash
# Test GPIO values (should show 0 normally, 1 when pressed)
cat /sys/class/gpio/gpio0/value
cat /sys/class/gpio/gpio2/value
cat /sys/class/gpio/gpio3/value
```

### Service issues
```bash
sudo systemctl status nanohat-oled.service
sudo journalctl -xeu nanohat-oled.service
```

## Original Project

This is a fork of [FriendlyARM/NanoHatOLED](https://github.com/friendlyarm/NanoHatOLED) with compatibility fixes for modern Debian systems.

## License

MIT License (same as original project)
