"""Android Mirror: scrcpy launcher + wireless ADB helper + controller GUI."""

import csv
import json
import os
import platform
import re
import shutil
import subprocess
import threading
import tkinter as tk
import urllib.request
import zipfile
from tkinter import filedialog, messagebox, ttk

APP_DIR = os.path.dirname(os.path.abspath(__file__))
SCRCPY_TAG = "v3.3.4"
KNOWN_DEVICES_PATH = os.path.join(APP_DIR, "known_devices.csv")
FIXED_TCPIP_PORT = "5555"
SCRCPY_API_URL = (
    f"https://api.github.com/repos/Genymobile/scrcpy/releases/tags/{SCRCPY_TAG}"
)
SCRCPY_EXTRACT_DIR = os.path.join(APP_DIR, f"scrcpy-{SCRCPY_TAG}")

IP_PORT_RE = re.compile(r"\b(\d+\.\d+\.\d+\.\d+:\d+)\b")


def resolve_adb_path(scrcpy_path):
    if scrcpy_path:
        adb_path = os.path.join(os.path.dirname(scrcpy_path), "adb.exe")
        if os.path.isfile(adb_path):
            return adb_path
    return "adb"


def is_valid_scrcpy_path(path):
    return bool(path) and os.path.isfile(path) and path.lower().endswith("scrcpy.exe")


def run_cmd(args, timeout=15, input_text=None):
    kwargs = {
        "capture_output": True,
        "text": True,
        "timeout": timeout,
    }
    if input_text is not None:
        kwargs["input"] = input_text
    if os.name == "nt":
        kwargs["creationflags"] = 0x08000000
    try:
        result = subprocess.run(args, **kwargs)
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except FileNotFoundError:
        return 1, "", f"Command not found: {args[0]}"
    except subprocess.TimeoutExpired:
        return 1, "", "Command timed out"


def stop_adb_processes():
    if os.name != "nt":
        return
    try:
        subprocess.run(
            ["taskkill", "/F", "/IM", "adb.exe"],
            capture_output=True,
            text=True,
        )
    except OSError:
        pass


def parse_mdns_services(text):
    pairing = []
    connect = []
    generic = []
    for line in text.splitlines():
        addr = IP_PORT_RE.search(line)
        if not addr:
            continue
        value = addr.group(1)
        if "adb-tls-pairing" in line:
            pairing.append(value)
        elif "adb-tls-connect" in line or "adb-tls" in line:
            connect.append(value)
        else:
            generic.append(value)
    return pairing, connect, generic


def parse_wlan_ip(text):
    match = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)", text)
    if match:
        return match.group(1)
    return ""


def get_usb_wlan_ip(adb_path):
    code, out, err = run_cmd(
        [adb_path, "shell", "ip", "addr", "show", "wlan0"], timeout=10
    )
    ip = parse_wlan_ip(out)
    if not ip:
        code, out, err = run_cmd(
            [adb_path, "shell", "ip", "-f", "inet", "addr", "show", "wlan0"],
            timeout=10,
        )
        ip = parse_wlan_ip(out)
    return ip


def format_text_for_adb(value):
    return value.replace(" ", "%s")


def parse_adb_devices(text):
    devices = []
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            devices.append((parts[0], parts[1]))
    return devices


def get_usb_device_serial(adb_path):
    code, out, err = run_cmd([adb_path, "devices"], timeout=5)
    if code != 0:
        return ""
    for serial, status in parse_adb_devices(out):
        if status == "device" and ":" not in serial:
            return serial
    return ""


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Android-Mirror"})
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def pick_scrcpy_asset(assets):
    arch = platform.machine().lower()
    prefer_64 = any(
        key in arch for key in ("amd64", "x86_64", "arm64", "aarch64", "64")
    )
    candidates = []
    for asset in assets:
        name = asset.get("name", "").lower()
        if "scrcpy-win" in name and name.endswith(".zip"):
            candidates.append(asset)
    if not candidates:
        return None
    if prefer_64:
        for asset in candidates:
            if "win64" in asset.get("name", "").lower():
                return asset
    for asset in candidates:
        if "win32" in asset.get("name", "").lower():
            return asset
    return candidates[0]


def find_scrcpy_exe(root_dir):
    for dirpath, _, filenames in os.walk(root_dir):
        for name in filenames:
            if name.lower() == "scrcpy.exe":
                return os.path.join(dirpath, name)
    return ""


def download_and_extract_scrcpy(status_cb):
    data = fetch_json(SCRCPY_API_URL)
    asset = pick_scrcpy_asset(data.get("assets", []))
    if not asset:
        return "", "No Windows scrcpy zip asset found in release"
    url = asset.get("browser_download_url")
    filename = asset.get("name") or "scrcpy.zip"
    if not url:
        return "", "Release asset missing download URL"
    download_path = os.path.join(APP_DIR, filename)

    status_cb(f"Downloading {filename}...")
    try:
        urllib.request.urlretrieve(url, download_path)
    except OSError as exc:
        return "", f"Download failed: {exc}"

    status_cb("Extracting scrcpy...")
    try:
        stop_adb_processes()
        if os.path.isdir(SCRCPY_EXTRACT_DIR):
            shutil.rmtree(SCRCPY_EXTRACT_DIR)
        with zipfile.ZipFile(download_path, "r") as archive:
            archive.extractall(SCRCPY_EXTRACT_DIR)
    except (OSError, zipfile.BadZipFile) as exc:
        return "", f"Extract failed: {exc}"

    scrcpy_path = find_scrcpy_exe(SCRCPY_EXTRACT_DIR)
    if not scrcpy_path:
        return "", "scrcpy.exe not found after extraction"
    return scrcpy_path, ""


def load_known_devices():
    if not os.path.exists(KNOWN_DEVICES_PATH):
        return []
    devices = []
    try:
        with open(KNOWN_DEVICES_PATH, "r", newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            for row in reader:
                if len(row) < 2:
                    continue
                address = row[0].strip()
                port = row[1].strip()
                if not address or not port:
                    continue
                if address.lower() == "address" and port.lower().startswith("port"):
                    continue
                devices.append((address, port))
    except OSError:
        return []
    return devices


def save_known_devices(devices):
    try:
        with open(KNOWN_DEVICES_PATH, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["address", "port"])
            for address, port in devices:
                writer.writerow([address, port])
    except OSError:
        pass


class MirrorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Android Mirror")
        self.root.geometry("820x520")
        self.root.minsize(780, 480)

        self.scrcpy_path_var = tk.StringVar(value="")
        self.adb_path_var = tk.StringVar(
            value=resolve_adb_path(self.scrcpy_path_var.get())
        )
        self.status_var = tk.StringVar(value="Ready")

        self.address_var = tk.StringVar(value="")
        self.pair_port_var = tk.StringVar(value="")
        self.pair_code_var = tk.StringVar(value="")
        self.text_input_var = tk.StringVar(value="")
        self.adb_cmd_var = tk.StringVar(value="")

        self.known_devices = [
            (address, port)
            for address, port in load_known_devices()
            if address != "USB device"
        ]
        self.usb_connected = False
        self.target_serial = ""

        self.build_ui()
        self.root.after(200, self.ensure_scrcpy_ready)
        self.root.after(500, self.refresh_usb_status)

    def build_ui(self):
        header = ttk.Label(
            self.root, text="Android Mirror", font=("Calibri", 14, "bold")
        )
        header.grid(row=0, column=0, sticky="w", padx=12, pady=(10, 4))

        setup = ttk.LabelFrame(self.root, text="Scrcpy Setup")
        setup.grid(row=1, column=0, sticky="ew", padx=12, pady=6)
        setup.columnconfigure(1, weight=1)

        ttk.Label(setup, text="scrcpy.exe").grid(
            row=0, column=0, sticky="w", padx=8, pady=6
        )
        scrcpy_entry = ttk.Entry(setup, textvariable=self.scrcpy_path_var)
        scrcpy_entry.grid(row=0, column=1, sticky="ew", padx=6, pady=6)
        ttk.Button(setup, text="Browse", command=self.browse_scrcpy).grid(
            row=0, column=2, padx=6, pady=6
        )

        ttk.Label(setup, text="adb path").grid(
            row=1, column=0, sticky="w", padx=8, pady=4
        )
        ttk.Label(setup, textvariable=self.adb_path_var).grid(
            row=1, column=1, sticky="w", padx=6, pady=4
        )
        ttk.Button(setup, text="Auto setup", command=self.ensure_scrcpy_ready).grid(
            row=1, column=2, padx=6, pady=4
        )

        ttk.Label(setup, text=f"Release: {SCRCPY_TAG}").grid(
            row=2, column=0, sticky="w", padx=8, pady=(0, 6)
        )

        middle = ttk.Frame(self.root)
        middle.grid(row=2, column=0, sticky="nsew", padx=12, pady=6)
        middle.columnconfigure(0, weight=1)
        middle.columnconfigure(1, weight=1)
        middle.rowconfigure(0, weight=1)

        conn = ttk.LabelFrame(middle, text="New Device (Pair)")
        conn.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        conn.columnconfigure(1, weight=1)

        ttk.Label(conn, text="Address (IP)").grid(
            row=0, column=0, sticky="w", padx=8, pady=6
        )
        ttk.Entry(conn, textvariable=self.address_var).grid(
            row=0, column=1, sticky="ew", padx=6, pady=6
        )

        ttk.Label(conn, text="Pair port").grid(row=1, column=0, sticky="w", padx=8)
        ttk.Entry(conn, textvariable=self.pair_port_var).grid(
            row=1, column=1, sticky="ew", padx=6
        )

        ttk.Label(conn, text="Pair code").grid(row=2, column=0, sticky="w", padx=8)
        ttk.Entry(conn, textvariable=self.pair_code_var).grid(
            row=2, column=1, sticky="ew", padx=6
        )

        ttk.Button(conn, text="New device (pair)", command=self.wireless_pair_new).grid(
            row=3, column=0, padx=6, pady=(6, 8)
        )

        known = ttk.LabelFrame(middle, text="Known Devices")
        known.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        known.columnconfigure(0, weight=1)

        self.known_table = ttk.Treeview(
            known, columns=("address", "port"), show="headings", height=5
        )
        self.known_table.heading("address", text="Address")
        self.known_table.heading("port", text="Port")
        self.known_table.column("address", width=200, anchor="w")
        self.known_table.column("port", width=100, anchor="w")
        self.known_table.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        self.known_table.bind("<<TreeviewSelect>>", self.on_known_select)

        known_buttons = ttk.Frame(known)
        known_buttons.grid(row=1, column=0, sticky="w", padx=6, pady=(0, 6))
        ttk.Button(
            known_buttons, text="Connect selected", command=self.connect_selected_known
        ).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(
            known_buttons, text="Remove selected", command=self.remove_known_device
        ).grid(row=0, column=1)

        self.refresh_known_table()

        controller = ttk.LabelFrame(self.root, text="Controller")
        controller.grid(row=3, column=0, sticky="nsew", padx=12, pady=6)
        self.root.rowconfigure(3, weight=1)
        controller.columnconfigure(5, weight=1)

        ttk.Button(controller, text="TAB", command=lambda: self.keyevent(61)).grid(
            column=0, row=0, padx=5, pady=5
        )
        ttk.Button(controller, text="Left", command=lambda: self.keyevent(21)).grid(
            column=0, row=1, padx=5, pady=5
        )
        ttk.Button(controller, text="Power", command=lambda: self.keyevent(26)).grid(
            column=0, row=2, padx=5, pady=5
        )
        ttk.Button(controller, text="Home", command=lambda: self.keyevent(3)).grid(
            column=0, row=3, padx=5, pady=5
        )

        ttk.Button(controller, text="Up", command=lambda: self.keyevent(19)).grid(
            column=1, row=0, padx=5, pady=5
        )
        ttk.Button(controller, text="Middle", command=lambda: self.keyevent(23)).grid(
            column=1, row=1, padx=5, pady=5
        )
        ttk.Button(controller, text="Down", command=lambda: self.keyevent(20)).grid(
            column=1, row=2, padx=5, pady=5
        )
        ttk.Button(controller, text="Menu", command=lambda: self.keyevent(82)).grid(
            column=1, row=3, padx=5, pady=5
        )

        ttk.Button(controller, text="Notif", command=lambda: self.keyevent(83)).grid(
            column=2, row=0, padx=5, pady=5
        )
        ttk.Button(controller, text="Right", command=lambda: self.keyevent(22)).grid(
            column=2, row=1, padx=5, pady=5
        )
        ttk.Button(controller, text="Enter", command=lambda: self.keyevent(66)).grid(
            column=2, row=2, padx=5, pady=5
        )
        ttk.Button(controller, text="Back", command=lambda: self.keyevent(4)).grid(
            column=2, row=3, padx=5, pady=5
        )

        ttk.Button(controller, text="Vol +", command=lambda: self.keyevent(24)).grid(
            column=3, row=0, padx=5, pady=5
        )
        ttk.Button(controller, text="Vol -", command=lambda: self.keyevent(25)).grid(
            column=3, row=1, padx=5, pady=5
        )
        ttk.Button(controller, text="Search", command=lambda: self.keyevent(84)).grid(
            column=3, row=2, padx=5, pady=5
        )
        ttk.Button(controller, text="Browser", command=lambda: self.keyevent(64)).grid(
            column=3, row=3, padx=5, pady=5
        )

        ttk.Button(controller, text="Power menu", command=self.long_press_power).grid(
            column=4, row=0, padx=5, pady=5
        )
        ttk.Button(controller, text="Install APK", command=self.install_apk).grid(
            column=4, row=1, padx=5, pady=5
        )

        ttk.Label(controller, text="Text").grid(
            column=0, row=4, sticky="w", padx=5, pady=(10, 4)
        )
        ttk.Entry(controller, textvariable=self.text_input_var, width=40).grid(
            column=1, row=4, columnspan=3, sticky="ew", padx=5
        )
        ttk.Button(controller, text="Send text", command=self.send_text).grid(
            column=4, row=4, padx=5
        )

        ttk.Label(controller, text="ADB cmd").grid(
            column=0, row=5, sticky="w", padx=5, pady=4
        )
        ttk.Entry(controller, textvariable=self.adb_cmd_var, width=40).grid(
            column=1, row=5, columnspan=3, sticky="ew", padx=5
        )
        ttk.Button(controller, text="Run", command=self.send_adb).grid(
            column=4, row=5, padx=5
        )

        status = ttk.Label(self.root, textvariable=self.status_var, anchor="w")
        status.grid(row=4, column=0, sticky="ew", padx=12, pady=(4, 10))

    def set_status(self, text):
        self.root.after(0, lambda: self.status_var.set(text))

    def refresh_known_table(self):
        for row in self.known_table.get_children():
            self.known_table.delete(row)
        for address, port in self.known_devices:
            self.known_table.insert("", "end", values=(address, port))
        if self.usb_connected:
            self.known_table.insert("", "end", values=("USB device", "USB"))

    def on_known_select(self, event):
        selected = self.known_table.selection()
        if not selected:
            return
        values = self.known_table.item(selected[0], "values")
        if len(values) >= 2:
            self.address_var.set(values[0])

    def remove_known_device(self):
        selected = self.known_table.selection()
        if not selected:
            return
        values = self.known_table.item(selected[0], "values")
        if len(values) < 2:
            return
        address, port = values[0], values[1]
        self.known_devices = [
            (a, p) for a, p in self.known_devices if not (a == address and p == port)
        ]
        save_known_devices(self.known_devices)
        self.refresh_known_table()

    def connect_selected_known(self):
        selected = self.known_table.selection()
        if not selected:
            messagebox.showwarning("Known device", "Select a known device first")
            return
        values = self.known_table.item(selected[0], "values")
        if len(values) < 2:
            return
        if values[1] == "USB":
            self.usb_mirror()
            return
        self.address_var.set(values[0])
        self.wireless_connect_existing(values[0], values[1])

    def refresh_usb_status(self):
        def work():
            adb_path = self.get_adb_path()
            code, out, err = run_cmd([adb_path, "devices"], timeout=5)
            connected = False
            if code == 0:
                for line in out.splitlines()[1:]:
                    parts = line.split()
                    if len(parts) >= 2 and parts[1] == "device" and ":" not in parts[0]:
                        connected = True
                        break

            self.usb_connected = connected
            if connected and not self.address_var.get().strip():
                ip = get_usb_wlan_ip(adb_path)
                if ip:
                    self.address_var.set(ip)
            self.root.after(0, self.refresh_known_table)
            self.root.after(3000, self.refresh_usb_status)

        threading.Thread(target=work, daemon=True).start()

    def browse_scrcpy(self):
        path = filedialog.askopenfilename(
            title="Locate scrcpy.exe",
            initialdir=APP_DIR,
            filetypes=(("scrcpy.exe", "scrcpy.exe"),),
        )
        if not path:
            return
        self.scrcpy_path_var.set(path)
        self.adb_path_var.set(resolve_adb_path(path))
        self.set_status("scrcpy path updated")

    def ensure_scrcpy_ready(self):
        if is_valid_scrcpy_path(self.scrcpy_path_var.get()):
            return

        def work():
            self.set_status("Setting up scrcpy...")
            scrcpy_path, error = download_and_extract_scrcpy(self.set_status)
            if error:
                messagebox.showwarning("scrcpy", error)
                self.set_status("scrcpy setup failed")
                return
            self.scrcpy_path_var.set(scrcpy_path)
            self.adb_path_var.set(resolve_adb_path(scrcpy_path))
            self.set_status("scrcpy ready")

        threading.Thread(target=work, daemon=True).start()

    def usb_mirror(self):
        adb_path = self.get_adb_path()
        serial = get_usb_device_serial(adb_path)
        if not serial:
            messagebox.showwarning("USB", "No USB device detected")
            return
        self.target_serial = serial
        self.start_scrcpy(serial)

    def detect_wireless_info(self, adb_path):
        pairing, connect, message = self.detect_mdns()
        if pairing:
            addr_parts = pairing[0].split(":")
            if len(addr_parts) >= 2:
                self.address_var.set(addr_parts[0])
                self.pair_port_var.set(addr_parts[1])
        if connect:
            addr_parts = connect[0].split(":")
            if len(addr_parts) >= 2:
                if not self.address_var.get().strip():
                    self.address_var.set(addr_parts[0])

        if not self.address_var.get().strip():
            ip = get_usb_wlan_ip(adb_path)
            if ip:
                self.address_var.set(ip)

    def wireless_connect_existing(self, address=None, port=None):
        def work():
            adb_path = self.get_adb_path()
            local_address = address
            local_port = port
            self.set_status("Connecting to known device...")
            if not local_address:
                self.detect_wireless_info(adb_path)
                local_address = self.address_var.get().strip()
            if not local_address:
                messagebox.showwarning(
                    "Wireless",
                    "Enter address from Wireless debugging.",
                )
                self.set_status("Connect info missing")
                return

            target_port = local_port or FIXED_TCPIP_PORT

            initial_addr = f"{local_address}:{target_port}"
            code, out, err = run_cmd([adb_path, "connect", initial_addr], timeout=15)
            if code != 0 or "connected" not in out.lower():
                messagebox.showerror("Connect", out or err or "Connect failed")
                self.set_status("Connect failed")
                return
            run_cmd([adb_path, "tcpip", FIXED_TCPIP_PORT], timeout=10)
            run_cmd([adb_path, "disconnect"], timeout=5)
            fixed_addr = f"{local_address}:{FIXED_TCPIP_PORT}"
            code, out, err = run_cmd([adb_path, "connect", fixed_addr], timeout=15)
            final_addr = fixed_addr
            if code != 0 or "connected" not in out.lower():
                messagebox.showwarning(
                    "Connect",
                    "Connected but tcpip 5555 setup failed. Reconnect may be needed.",
                )
                final_addr = initial_addr
            self.add_known_device(local_address, FIXED_TCPIP_PORT)
            self.target_serial = final_addr
            self.set_status("Wireless ADB connected")
            self.start_scrcpy(final_addr)

        threading.Thread(target=work, daemon=True).start()

    def wireless_pair_new(self):
        def work():
            adb_path = self.get_adb_path()
            self.set_status("Pairing new device...")
            self.detect_wireless_info(adb_path)

            address = self.address_var.get().strip()
            pair_port = self.pair_port_var.get().strip()
            pair_code = self.pair_code_var.get().strip()
            if not address:
                messagebox.showwarning(
                    "Pair", "Unable to detect device IP. Check USB debugging."
                )
                self.set_status("Pairing failed")
                return
            if not pair_port or not pair_code:
                messagebox.showwarning(
                    "Pair", "Enter pair port and pair code from Wireless debugging."
                )
                self.set_status("Pair info missing")
                return

            pair_addr = f"{address}:{pair_port}"
            code, out, err = run_cmd(
                [adb_path, "pair", pair_addr], input_text=f"{pair_code}\n", timeout=20
            )
            if code != 0 or "success" not in out.lower():
                messagebox.showerror("Pair", out or err or "Pairing failed")
                self.set_status("Pairing failed")
                return

            self.set_status("Pairing succeeded. Use Connect selected to mirror.")

        threading.Thread(target=work, daemon=True).start()

    def add_known_device(self, address, port):
        if not address or not port:
            return
        if address == "USB device":
            return
        exists = any(a == address and p == port for a, p in self.known_devices)
        if not exists:
            self.known_devices.append((address, port))
            save_known_devices(self.known_devices)
            self.refresh_known_table()

    def add_usb_device_to_known(self):
        adb_path = self.get_adb_path()
        serial = get_usb_device_serial(adb_path)
        if not serial:
            return
        exists = any(a == "USB device" for a, _ in self.known_devices)
        if not exists:
            self.known_devices.append(("USB device", "USB"))
            save_known_devices(self.known_devices)
            self.refresh_known_table()

    def start_scrcpy(self, serial=None):
        scrcpy_path = self.scrcpy_path_var.get().strip()
        if not is_valid_scrcpy_path(scrcpy_path):
            self.ensure_scrcpy_ready()
            scrcpy_path = self.scrcpy_path_var.get().strip()
            if not is_valid_scrcpy_path(scrcpy_path):
                messagebox.showwarning(
                    "scrcpy", "Unable to set up scrcpy automatically"
                )
                return
        target = serial or self.target_serial
        if not target:
            adb_path = self.get_adb_path()
            code, out, err = run_cmd([adb_path, "devices"], timeout=5)
            if code == 0:
                devices = [
                    serial
                    for serial, status in parse_adb_devices(out)
                    if status == "device"
                ]
                if len(devices) > 1:
                    messagebox.showwarning(
                        "scrcpy",
                        "Multiple devices detected. Select a device from Known Devices.",
                    )
                    return
                if len(devices) == 1:
                    target = devices[0]
        try:
            args = [scrcpy_path]
            if target:
                args.extend(["-s", target])
            subprocess.Popen(args, cwd=os.path.dirname(scrcpy_path))
            self.set_status("scrcpy started")
        except OSError as exc:
            messagebox.showerror("scrcpy", str(exc))

    def get_adb_path(self):
        return self.adb_path_var.get().strip() or "adb"

    def detect_mdns(self):
        adb_path = self.get_adb_path()
        code, out, err = run_cmd([adb_path, "mdns", "services"])
        pairing, connect, generic = parse_mdns_services(out)
        if not pairing and not connect:
            code, out, err = run_cmd([adb_path, "mdns", "services", "-l"])
            pairing, connect, generic = parse_mdns_services(out)
        if not pairing and generic:
            pairing = [generic[0]]
        if not connect and generic:
            connect = [generic[0]]
        return pairing, connect, out or err

    def detect_pair_connect(self):
        self.wireless_connect_existing()

    def disconnect_adb(self):
        def work():
            adb_path = self.get_adb_path()
            self.set_status("Disconnecting ADB...")
            run_cmd([adb_path, "disconnect"], timeout=10)
            self.set_status("ADB disconnected")

        threading.Thread(target=work, daemon=True).start()

    def keyevent(self, keycode):
        adb_path = self.get_adb_path()
        run_cmd([adb_path, "shell", "input", "keyevent", str(keycode)], timeout=5)

    def long_press_power(self):
        adb_path = self.get_adb_path()
        run_cmd(
            [adb_path, "shell", "input", "keyevent", "--longpress", "KEYCODE_POWER"],
            timeout=5,
        )

    def install_apk(self):
        adb_path = self.get_adb_path()
        apk_path = filedialog.askopenfilename(
            title="Locate apk file",
            initialdir=APP_DIR,
            filetypes=(("Android package", "*.apk"), ("All files", "*.*")),
        )
        if not apk_path:
            return
        code, out, err = run_cmd([adb_path, "install", apk_path], timeout=60)
        messagebox.showinfo("Install APK", out or err or "No output")

    def send_text(self):
        adb_path = self.get_adb_path()
        value = self.text_input_var.get().strip()
        if not value:
            return
        run_cmd(
            [adb_path, "shell", "input", "text", format_text_for_adb(value)], timeout=10
        )
        self.set_status("Text sent")

    def send_adb(self):
        adb_path = self.get_adb_path()
        cmd = self.adb_cmd_var.get().strip()
        if not cmd:
            return
        args = [adb_path] + cmd.split()
        code, out, err = run_cmd(args, timeout=20)
        messagebox.showinfo("ADB", out or err or "No output")


if __name__ == "__main__":
    root = tk.Tk()
    app = MirrorApp(root)
    root.mainloop()
