"""scripts/usb-image.sh and go-live.sh with fakes: a fake image folder, /Volumes, diskutil, lsblk, mount, docker, thor-up and jm."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
USB = os.path.join(ROOT, "scripts", "usb-image.sh")
LIVE = os.path.join(ROOT, "scripts", "go-live.sh")
TAG = "ghcr.io/nvidia-ai-iot/vllm:gemma4-jetson-thor"

FAKES = {
    "diskutil": 'echo "diskutil $*" >> "$FAKE/calls"\n',
    "lsblk": r"""echo "lsblk $*" >> "$FAKE/calls"
case "${@: -1}" in
  /dev/*) grep -F "NAME=\"${@: -1}\"" "$FAKE/lsblk.out" | sed 's/.*MOUNTPOINT=/MOUNTPOINT=/' ;;
  *) cat "$FAKE/lsblk.out" ;;
esac
""",
    "mount": 'echo "mount $*" >> "$FAKE/calls"\nln -s "$FAKE/usb/jm-vllm-image" "${@: -1}/jm-vllm-image"\n',
    "umount": 'echo "umount $*" >> "$FAKE/calls"\nrm -f "$1/jm-vllm-image"\n',
    "docker": r"""echo "docker $*" >> "$FAKE/calls"
case "$1" in
  info) exit 0 ;;
  image) [ -f "$FAKE/loaded" ] ;;
  load) tar -xOf - ./manifest.json > "$FAKE/loaded" ;;
esac
""",
    "thor-up": 'echo "thor-up $* image=$JM_VLLM_IMAGE" >> "$FAKE/calls"\nmkdir -p "$JM_RUN_DIR"\n'
               'echo "export JM_GEMMA_URL=http://127.0.0.1:8001/v1" > "$JM_RUN_DIR/env.sh"\necho thor up\n',
    "jm": 'echo "jm $* url=$JM_GEMMA_URL" >> "$FAKE/calls"\necho "jm $* ok"\n',
}


@unittest.skipUnless(shutil.which("bash") and shutil.which("tar"), "needs bash and tar")
class GoLiveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.fake = os.path.join(self.tmp, "fake")
        os.makedirs(self.fake)
        for name, body in FAKES.items():
            path = os.path.join(self.fake, name)
            with open(path, "w") as f:
                f.write("#!/usr/bin/env bash\n" + body)
            os.chmod(path, 0o755)
        self.image = os.path.join(self.tmp, "jm-vllm-image")
        os.makedirs(os.path.join(self.image, "blobs", "sha256"))
        for name, size in (("aaa", 300000), ("bbb", 5000)):
            with open(os.path.join(self.image, "blobs", "sha256", name), "wb") as f:
                f.write(os.urandom(size))
        with open(os.path.join(self.image, "manifest.json"), "w") as f:
            json.dump([{"Config": "blobs/sha256/bbb", "RepoTags": [TAG], "Layers": ["blobs/sha256/aaa"]}], f)
        for name in ("index.json", "oci-layout"):
            with open(os.path.join(self.image, name), "w") as f:
                f.write("{}")
        self.volumes = os.path.join(self.tmp, "Volumes")
        os.makedirs(os.path.join(self.volumes, "Lexar"))
        os.symlink("/", os.path.join(self.volumes, "Macintosh HD"))
        self.mnt = os.path.join(self.tmp, "mnt")
        os.makedirs(self.mnt)
        self.log = os.path.join(self.tmp, "go-live.log")
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("JM_", "HF_"))}
        f = lambda n: os.path.join(self.fake, n)
        self.env.update(FAKE=self.fake, JM_IMAGE_DIR=self.image, JM_VOLUMES=self.volumes, JM_DISKUTIL=f("diskutil"),
                        JM_LSBLK=f("lsblk"), JM_MOUNT=f("mount"), JM_UMOUNT=f("umount"), JM_DOCKER=f("docker"),
                        JM_THOR_UP=f("thor-up"), JM_JM=f("jm"), JM_USB_MNT=self.mnt, JM_GO_LIVE_LOG=self.log,
                        JM_RUN_DIR=os.path.join(self.tmp, "run"), JM_DROP_CACHES="0",
                        JM_31B_DIR=os.path.join(self.tmp, "Gemma-4-31B-IT-NVFP4"))
        self.lsblk('NAME="/dev/nvme0n1p1" TYPE="part" RM="0" HOTPLUG="0" FSTYPE="ext4" MOUNTPOINT="/"\n'
                   'NAME="/dev/sda1" TYPE="part" RM="1" HOTPLUG="1" FSTYPE="exfat" MOUNTPOINT=""\n')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def lsblk(self, text):
        with open(os.path.join(self.fake, "lsblk.out"), "w") as f:
            f.write(text)

    def sh(self, script, *args, **env):
        p = subprocess.run(["bash", script, *args], env={**self.env, **env}, capture_output=True, text=True,
                           timeout=60)
        return p.returncode, p.stdout + p.stderr

    def calls(self):
        path = os.path.join(self.fake, "calls")
        if not os.path.exists(path):
            return ""
        with open(path) as f:
            return f.read()

    def copy_to_usb(self):
        shutil.copytree(self.image, os.path.join(self.fake, "usb", "jm-vllm-image"))

    def test_usb_dry_run_copies_nothing(self):
        code, out = self.sh(USB, "--dry-run")
        self.assertEqual(code, 0, out)
        self.assertIn(f"drive: {self.volumes}/Lexar", out)
        self.assertIn("5 files", out)
        self.assertIn("+ copy", out)
        self.assertIn("eject", out)
        self.assertFalse(os.path.exists(os.path.join(self.volumes, "Lexar", "jm-vllm-image")))
        self.assertEqual(self.calls(), "")
        self.assertTrue(out.strip().splitlines()[-1].startswith("usage: scripts/usb-image.sh"), out)

    def test_usb_copy_verify_eject_and_resume(self):
        code, out = self.sh(USB, "Lexar")
        self.assertEqual(code, 0, out)
        dest = os.path.join(self.volumes, "Lexar", "jm-vllm-image")
        for rel in ("manifest.json", "index.json", "oci-layout", "blobs/sha256/aaa", "blobs/sha256/bbb"):
            with open(os.path.join(self.image, rel), "rb") as a, open(os.path.join(dest, rel), "rb") as b:
                self.assertEqual(a.read(), b.read(), rel)
        self.assertIn("PASS  5 files", out)
        self.assertIn("sizes match", out)
        self.assertIn(f"diskutil eject {self.volumes}/Lexar", self.calls())
        self.assertIn("usage: scripts/usb-image.sh", out.strip().splitlines()[-1])

        with open(os.path.join(dest, "blobs", "sha256", "aaa"), "wb") as f:
            f.write(b"short")
        code, out = self.sh(USB, "--no-eject", os.path.join(self.volumes, "Lexar"))
        self.assertEqual(code, 0, out)
        self.assertIn("[1/5]", out)
        self.assertNotIn("[2/5]", out)
        self.assertEqual(os.path.getsize(os.path.join(dest, "blobs", "sha256", "aaa")), 300000)
        self.assertEqual(self.calls().count("eject"), 1)

    def test_usb_refuses_bad_states(self):
        os.makedirs(os.path.join(self.volumes, "Other"))
        code, out = self.sh(USB, "--dry-run")
        self.assertEqual(code, 2, out)
        self.assertIn("several drives", out)
        self.assertNotIn("Macintosh HD", out)
        code, out = self.sh(USB, "--dry-run", "Nope")
        self.assertEqual(code, 1, out)
        self.assertIn("is not mounted", out)
        open(os.path.join(self.image, "blobs", "sha256", "ccc.part"), "w").close()
        code, out = self.sh(USB, "--dry-run", "Lexar")
        self.assertEqual(code, 1, out)
        self.assertIn(".part files", out)
        code, out = self.sh(USB, "--dry-run", JM_IMAGE_DIR=os.path.join(self.tmp, "none"))
        self.assertEqual(code, 1, out)
        self.assertIn("tools/pull_image.py", out)

    def test_go_live_dry_run(self):
        code, out = self.sh(LIVE, "--dry-run")
        self.assertEqual(code, 0, out)
        self.assertIn("USB: /dev/sda1", out)
        self.assertIn("sudo is needed only to mount and unmount", out)
        self.assertIn(f"+ {self.fake}/mount -o ro /dev/sda1 {self.mnt}", out)
        self.assertIn(f"+ tar -C {self.mnt}/jm-vllm-image -cf - . | {self.fake}/docker load", out)
        self.assertIn(f"+ {self.fake}/umount {self.mnt}", out)
        self.assertIn("thor-up --only-31b --no-doctor --dry-run", self.calls())
        self.assertIn("jm bench", out)
        self.assertNotIn("mount -o", self.calls())
        self.assertFalse(os.path.exists(self.log))
        self.assertTrue(out.strip().splitlines()[-1].startswith("usage: scripts/go-live.sh"), out)

    def test_go_live_dry_run_with_real_thor_up(self):
        os.makedirs(self.env["JM_31B_DIR"])
        code, out = self.sh(LIVE, "--dry-run", "--dev", "/dev/sdb1", JM_THOR_UP=os.path.join(ROOT, "scripts", "thor-up.sh"))
        self.assertEqual(code, 0, out)
        self.assertIn(f"{TAG} vllm serve /models/Gemma-4-31B-IT-NVFP4", out)
        self.assertIn("+ " + self.fake + "/mount -o ro /dev/sdb1", out)

    def test_go_live_full_run(self):
        self.copy_to_usb()
        code, out = self.sh(LIVE)
        self.assertEqual(code, 0, out)
        calls = self.calls()
        order = [calls.index(s) for s in ("mount -o ro /dev/sda1", "docker load", "umount " + self.mnt,
                                          "thor-up --only-31b --no-doctor image=" + TAG, "jm doctor url=http://127.0.0.1:8001/v1",
                                          "jm bench")]
        self.assertEqual(order, sorted(order), calls)
        with open(os.path.join(self.fake, "loaded")) as f:
            self.assertEqual(json.load(f)[0]["RepoTags"], [TAG])
        self.assertIn(f"PASS  image {TAG} is present", out)
        self.assertFalse(os.path.exists(os.path.join(self.mnt, "jm-vllm-image")), "drive left mounted")
        with open(self.log) as f:
            self.assertEqual(f.read(), out)
        self.assertIn("usage: scripts/go-live.sh", out.strip().splitlines()[-1])

        code, out = self.sh(LIVE, "--no-bench")
        self.assertEqual(code, 0, out)
        self.assertIn("already loaded; skipping docker load", out)
        self.assertEqual(self.calls().count("docker load"), 1)
        self.assertEqual(self.calls().count("jm bench"), 1)

    def test_go_live_already_mounted_needs_no_sudo(self):
        self.copy_to_usb()
        self.lsblk(f'NAME="/dev/sda1" TYPE="part" RM="1" HOTPLUG="1" FSTYPE="exfat" MOUNTPOINT="{self.fake}/usb"\n')
        code, out = self.sh(LIVE, "--no-bench")
        self.assertEqual(code, 0, out)
        self.assertIn(f"mounted at {self.fake}/usb", out)
        self.assertNotIn("mount -o", self.calls())
        self.assertNotIn("umount", self.calls())
        self.assertIn("docker load", self.calls())

    def test_go_live_failures_unmount(self):
        os.makedirs(os.path.join(self.fake, "usb", "jm-vllm-image"))
        code, out = self.sh(LIVE)
        self.assertEqual(code, 1, out)
        self.assertIn("FAIL  no image at", out)
        self.assertIn("umount " + self.mnt, self.calls())
        self.assertNotIn("thor-up", self.calls())
        self.lsblk("")
        code, out = self.sh(LIVE, "--dry-run")
        self.assertEqual(code, 1, out)
        self.assertIn("no USB drive found", out)
        self.lsblk('NAME="/dev/sda1" TYPE="part" RM="1" HOTPLUG="1" FSTYPE="exfat" MOUNTPOINT=""\n'
                   'NAME="/dev/sdb1" TYPE="part" RM="1" HOTPLUG="1" FSTYPE="vfat" MOUNTPOINT=""\n')
        code, out = self.sh(LIVE, "--dry-run")
        self.assertEqual(code, 1, out)
        self.assertIn("pick one with --dev", out)


if __name__ == "__main__":
    unittest.main()
