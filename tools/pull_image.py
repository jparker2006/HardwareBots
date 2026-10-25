#!/usr/bin/env python3
"""Download a container image from a public registry into a folder (OCI layout + docker manifest.json),
so it can be carried on a USB drive and loaded on a machine with a slow network:

  python3 tools/pull_image.py ghcr.io/nvidia-ai-iot/vllm:gemma4-jetson-thor /Volumes/Lexar/vllm-image
  # on the target:  tar -C /mnt/usb/vllm-image -cf - . | docker load

Standard library only. Resumable: finished blobs are verified and skipped. Downloads in parallel.
"""
import hashlib, json, os, sys, threading, time, urllib.request
from concurrent.futures import ThreadPoolExecutor

ACCEPT = ", ".join([
    "application/vnd.oci.image.index.v1+json", "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json", "application/vnd.docker.distribution.manifest.v2+json"])


def parse(ref):
    registry, rest = ref.split("/", 1)
    repo, tag = rest.rsplit(":", 1)
    return registry, repo, tag


def token(registry, repo):
    url = f"https://{registry}/token?scope=repository:{repo}:pull&service={registry}"
    return json.load(urllib.request.urlopen(url, timeout=30))["token"]


def get(url, tok, accept=None, offset=0):
    h = {"Authorization": f"Bearer {tok}"}
    if accept:
        h["Accept"] = accept
    if offset:
        h["Range"] = f"bytes={offset}-"
    return urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=60)


class Progress:
    def __init__(self, total):
        self.total, self.done, self.lock, self.t0, self.shown = total, 0, threading.Lock(), time.time(), 0.0

    def add(self, n):
        with self.lock:
            self.done += n
            if time.time() - self.shown < 2 and self.done < self.total:
                return
            self.shown = time.time()
            rate = self.done / max(time.time() - self.t0, 1e-6) / 1e6
            left = (self.total - self.done) / 1e6 / max(rate, 0.01)
            sys.stdout.write(f"\r{self.done / 1e9:6.2f} / {self.total / 1e9:.2f} GB  {rate:5.1f} MB/s  ~{left / 60:4.1f} min left ")
            sys.stdout.flush()


def fetch_blob(base, repo, desc, out, state):
    digest = desc["digest"]
    path = os.path.join(out, "blobs", "sha256", digest.split(":")[1])
    if os.path.exists(path) and os.path.getsize(path) == desc["size"]:
        state["progress"].add(desc["size"])
        return
    for attempt in range(30):
        try:
            have = os.path.getsize(path + ".part") if os.path.exists(path + ".part") else 0
            r = get(f"{base}/v2/{repo}/blobs/{digest}", state["token"], offset=have)
            if have and r.status != 206:
                have = 0
            if attempt == 0:
                state["progress"].add(have)
            with open(path + ".part", "ab" if have else "wb") as f:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
                    state["progress"].add(len(chunk))
            got = os.path.getsize(path + ".part")
            if got < desc["size"]:
                raise IOError(f"connection ended early at {got / 1e9:.2f} of {desc['size'] / 1e9:.2f} GB, resuming")
            if got > desc["size"]:
                os.remove(path + ".part")
                raise IOError("too many bytes, restarting this blob")
            h = hashlib.sha256()
            with open(path + ".part", "rb") as f:
                for chunk in iter(lambda: f.read(1 << 22), b""):
                    h.update(chunk)
            if h.hexdigest() != digest.split(":")[1]:
                os.remove(path + ".part")
                raise IOError(f"checksum mismatch for {digest}")
            os.replace(path + ".part", path)
            return
        except Exception as e:
            sys.stdout.write(f"\n  retry {attempt + 1} for {digest[:19]}: {e}\n")
            time.sleep(3)
            if "401" in str(e):
                state["token"] = token(state["registry"], repo)
    raise SystemExit(f"failed to download {digest}")


def main():
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    ref, out = sys.argv[1], sys.argv[2]
    registry, repo, tag = parse(ref)
    base = f"https://{registry}"
    tok = token(registry, repo)
    raw = get(f"{base}/v2/{repo}/manifests/{tag}", tok, ACCEPT).read()
    m = json.loads(raw)
    if "manifests" in m:
        pick = [x for x in m["manifests"] if x.get("platform", {}).get("architecture") == "arm64"
                and x.get("platform", {}).get("os") == "linux"][0]
        raw = get(f"{base}/v2/{repo}/manifests/{pick['digest']}", tok, ACCEPT).read()
        m = json.loads(raw)
    mtype = m.get("mediaType") or "application/vnd.oci.image.manifest.v1+json"
    os.makedirs(os.path.join(out, "blobs", "sha256"), exist_ok=True)
    mdigest = "sha256:" + hashlib.sha256(raw).hexdigest()
    with open(os.path.join(out, "blobs", "sha256", mdigest.split(":")[1]), "wb") as f:
        f.write(raw)
    blobs = [m["config"]] + m["layers"]
    total = sum(b["size"] for b in blobs)
    print(f"{ref}: {len(m['layers'])} layers, {total / 1e9:.2f} GB -> {out}")
    state = {"token": tok, "registry": registry, "progress": Progress(total)}
    with ThreadPoolExecutor(4) as pool:
        for fut in [pool.submit(fetch_blob, base, repo, b, out, state) for b in sorted(blobs, key=lambda b: -b["size"])]:
            fut.result()
    name = f"{registry}/{repo}:{tag}"
    with open(os.path.join(out, "oci-layout"), "w") as f:
        json.dump({"imageLayoutVersion": "1.0.0"}, f)
    with open(os.path.join(out, "index.json"), "w") as f:
        json.dump({"schemaVersion": 2, "manifests": [{
            "mediaType": mtype, "digest": mdigest, "size": len(raw),
            "annotations": {"io.containerd.image.name": name, "org.opencontainers.image.ref.name": tag}}]}, f)
    with open(os.path.join(out, "manifest.json"), "w") as f:
        json.dump([{"Config": "blobs/sha256/" + m["config"]["digest"].split(":")[1], "RepoTags": [name],
                    "Layers": ["blobs/sha256/" + l["digest"].split(":")[1] for l in m["layers"]]}], f)
    print(f"\ndone. On the target: tar -C <this folder> -cf - . | docker load")


if __name__ == "__main__":
    main()
