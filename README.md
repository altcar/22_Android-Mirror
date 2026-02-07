# Android Mirror (scrcpy GUI)
Mirror and control your Android phone on Windows with an ADB-aware GUI.

## What it originally did
- Prompted for scrcpy.exe location and stored it in config.txt
- Asked whether to use wireless
- Enabled adb tcpip 5555
- Asked you to type the IP manually
- Started scrcpy and opened a basic controller window

## What it does now
- Auto-downloads scrcpy v3.3.4 (Windows zip) and resolves adb automatically
- Handles wireless pairing (Android 11+) with mDNS detection and USB IP fallback
- Supports fixed-port workflow (connect → tcpip 5555 → reconnect) so you can reuse ip:5555 until reboot
- Keeps a Known Devices list in `known_devices.csv` with one-click connect
- Adds a USB device entry in the Known Devices list when USB is detected
- Launches scrcpy with `-s <serial>` to avoid multi-device errors
- Includes a controller panel for key events, text input, and APK install

## Why this is better than typical web scrcpy GUIs
- Uses real ADB state (mDNS + USB shell) instead of manual IP entry only
- Handles Android 11+ pairing flow and fixed-port workflow in one place
- Persists known devices and avoids "multiple device" errors by auto-selecting serials
- Bundles a controller panel, not just mirroring
- No browser, no drivers, no extra runtime required beyond adb/scrcpy

## How to use
1. Run `python scrcpyrun.py`.
2. The app auto-downloads scrcpy v3.3.4 (or use Browse to pick your own scrcpy.exe).
3. For a new phone:
   - Open Wireless debugging on the phone and choose Pair device.
   - Click **New device (pair)** and enter the Pair port + Pair code shown on the phone.
4. For a known phone:
   - Select it from **Known Devices** and click **Connect selected**.
   - The app connects and pins the device to `ip:5555` (until reboot).
5. If USB is plugged in, a **USB device** row appears in Known Devices. Select it and click **Connect selected**.

## Quick start
1. Connect phone via USB and enable USB debugging.
2. Run `python scrcpyrun.py`.
3. If it is your first time, go to Wireless debugging on the phone and tap Pair device.
4. Click **New device (pair)** and enter the Pair port + Pair code.
5. Select the device from **Known Devices** and click **Connect selected** to launch scrcpy.

## Screenshot
Add a screenshot at `docs/screenshot.png`, then it will render here:

![Android Mirror GUI](docs/screenshot.png)

## Files
- `scrcpyrun.py` - main GUI
- `known_devices.csv` - saved known devices (address, port)
