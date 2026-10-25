"""check_usb reads lsusb/lsblk only and turns them into plain hints."""
from __future__ import annotations

import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from journeyman import ops, usb  # noqa: E402

LSUSB = "Bus 001 Device 004: ID 046d:094c Logitech, Inc. Brio 100\nBus 002 Device 002: ID 21c4:0809 Lexar USB Flash Drive\nBus 001 Device 001: ID 1d6b:0002 Linux Foundation 2.0 root hub\n"
LSBLK = {"blockdevices": [
    {"name": "nvme0n1", "size": "953.9G", "rm": False, "hotplug": False, "tran": "nvme", "children": [
        {"name": "nvme0n1p1", "size": "951.2G", "fstype": "ext4", "label": "APP", "mountpoint": "/"}]},
    {"name": "sda", "size": "231.1G", "rm": True, "hotplug": True, "tran": "usb", "children": [
        {"name": "sda1", "size": "200M", "fstype": "vfat", "label": "EFI", "mountpoint": None},
        {"name": "sda2", "size": "230.9G", "fstype": "exfat", "label": "Lexar", "mountpoint": None}]}]}


def fake_run(cmd):
    return LSUSB if cmd[0] == "lsusb" else json.dumps(LSBLK)


class CheckUsbTest(unittest.TestCase):
    def test_unmounted_drive_gets_a_mount_hint(self):
        with mock.patch.object(usb, "_run", side_effect=fake_run):
            s = usb.status()
        self.assertIn("Logitech, Inc. Brio 100", s["usb_devices"])
        self.assertEqual([d["device"] for d in s["drives"]], ["/dev/sda2"])
        self.assertIn("not mounted", s["hints"][0])
        self.assertIn("mount -o ro /dev/sda2", s["hints"][0])

    def test_no_drive(self):
        with mock.patch.object(usb, "_run", side_effect=lambda cmd: "" if cmd[0] == "lsusb" else '{"blockdevices": []}'):
            self.assertIn("No USB drive", usb.status()["hints"][0])

    def test_tool_is_available_to_the_agent(self):
        self.assertIn("check_usb", [t["function"]["name"] for t in ops.tool_schemas()])


class UsbAnswerTest(unittest.TestCase):
    def test_usb_question_reads_live_state_without_the_tool_loop(self):
        import tempfile
        from tests import fake_gemma
        from journeyman.config import Config
        from journeyman.core import Core
        server, script, url = fake_gemma.start()
        try:
            core = Core(Config(gemma_url=url, data_dir=tempfile.mkdtemp(), camera="none"), sync=True)
            with mock.patch.object(usb, "_run", side_effect=fake_run):
                reply = core.agent.respond("Journeyman, why isn't my drive showing up?")
            self.assertTrue(reply)
            self.assertFalse(any(r.get("tools") for r in script.requests))
            self.assertIn("not mounted", json.dumps(script.requests[-1]))
        finally:
            server.shutdown()
