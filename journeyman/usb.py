"""Read-only view of the Thor's USB state: what's plugged in, which drives exist, which are mounted."""
from __future__ import annotations

import json
import subprocess


def _run(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def devices() -> list[str]:
    out = []
    for line in _run(["lsusb"]).splitlines():
        name = line.split(" ", 6)[-1].strip() if "ID " in line else line
        if not any(w in name.lower() for w in ("root hub", "linux foundation")):
            out.append(name)
    return out


def drives() -> list[dict]:
    raw = _run(["lsblk", "-J", "-o", "NAME,SIZE,FSTYPE,LABEL,MOUNTPOINT,RM,HOTPLUG,TRAN"])
    try:
        tree = json.loads(raw or "{}").get("blockdevices", [])
    except ValueError:
        return []
    found = []
    for disk in tree:
        if disk.get("tran") != "usb" and not disk.get("rm") and not disk.get("hotplug"):
            continue
        for part in disk.get("children") or [disk]:
            if (part.get("size") or "").endswith("M") and part.get("fstype") == "vfat" and part.get("label") == "EFI":
                continue
            found.append({"device": "/dev/" + part["name"], "size": part.get("size"), "fstype": part.get("fstype"),
                          "label": part.get("label"), "mounted_at": part.get("mountpoint")})
    return found


def status() -> dict:
    ds = drives()
    hints = []
    if not ds:
        hints.append("No USB drive is visible to the Jetson. Try a USB-A port, check the cable, wait 5 seconds.")
    for d in ds:
        name = d["label"] or d["device"]
        if not d["mounted_at"]:
            hints.append(f"{name} ({d['size']}, {d['fstype'] or 'unknown format'}) is plugged in but not mounted: "
                         f"sudo mkdir -p /mnt/usb && sudo mount -o ro {d['device']} /mnt/usb")
        else:
            hints.append(f"{name} is mounted at {d['mounted_at']}; unmount before unplugging: sudo umount {d['mounted_at']}")
    return {"usb_devices": devices(), "drives": ds, "hints": hints}
