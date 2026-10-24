"""`jm`: the CLI. Operations go to the running journeyman service; serve/replay/doctor/bench are local."""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path

from . import ops
from .config import Config
from .local_http import internet_reachable, request_json

DEFAULT_URL = os.environ.get("JM_URL", "http://localhost:3000")


def call(url: str, name: str, args: dict):
    return request_json(f"{url}/api/ops/{name}", args, timeout=180)["result"]


def cmd_serve(_args):
    from .core import Core
    from .server import serve
    serve(Core(Config()))


def cmd_doctor(args):
    from . import doctor
    checks = doctor.run(Config(), args.url, offline=args.offline)
    print(doctor.to_json(checks) if args.json else doctor.render(checks))
    sys.exit(1 if doctor.failed(checks) else 0)


def cmd_replay(args):
    """Feed a recording through the running service: script.jsonl lines with at + one of text/wav/frame/op."""
    root = Path(args.dir)
    lines = [json.loads(x) for x in (root / "script.jsonl").read_text().splitlines() if x.strip()]
    start = time.time()
    for line in sorted(lines, key=lambda x: x.get("at", 0)):
        wait = line.get("at", 0) / args.speed - (time.time() - start)
        if wait > 0:
            time.sleep(wait)
        if "op" in line:
            print("op   ", line["op"], call(args.url, line["op"], line.get("args", {})))
        elif "text" in line:
            request_json(f"{args.url}/api/headset/utterance", {"text": line["text"]})
            print("said ", line["text"])
        elif "wav" in line:
            wav = (root / line["wav"]).read_bytes()
            request_json(f"{args.url}/api/headset/utterance", {"wav_b64": base64.b64encode(wav).decode()})
            print("wav  ", line["wav"])
        elif "frame" in line:
            jpg = (root / line["frame"]).read_bytes()
            request_json(f"{args.url}/api/frames", {"jpeg_b64": base64.b64encode(jpg).decode()})
            print("frame", line["frame"])


def cmd_bench(args):
    from . import doctor
    cfg = Config()
    found, url, models = doctor.find_gemma(os.environ.get("JM_GEMMA_URL"), current=cfg.gemma_url)
    if not url:
        print(doctor.render([found]))
        sys.exit(1)
    model, _ = doctor.pick_model(models, cfg.gemma_model)
    jpeg = Path(args.image).read_bytes() if args.image else None
    wav = Path(args.wav).read_bytes() if args.wav else None
    results = doctor.bench(url, model, args.runs, jpeg, wav)
    print(json.dumps({"url": url, "model": model, "results": results}, indent=2) if args.json
          else f"server {url}\n" + doctor.render_bench(model, results))


def cmd_schema(_args):
    print(json.dumps(ops.tool_schemas(), indent=2))


def main(argv=None):
    p = argparse.ArgumentParser(prog="jm", description="Journeyman: a local voice instructor for hands-on work.")
    p.add_argument("--url", default=DEFAULT_URL, help="journeyman service URL")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="run the journeyman service").set_defaults(fn=cmd_serve)
    d = sub.add_parser("doctor", help="check Gemma, the service, the camera and whether you are offline")
    d.add_argument("--offline", action="store_true", help="fail if the internet is reachable")
    d.add_argument("--json", action="store_true", help="machine-readable output")
    d.set_defaults(fn=cmd_doctor)
    r = sub.add_parser("replay", help="feed a recorded session through the running service")
    r.add_argument("dir")
    r.add_argument("--speed", type=float, default=1.0)
    r.set_defaults(fn=cmd_replay)
    b = sub.add_parser("bench", help="time Gemma text, image, audio and tool round trips")
    b.add_argument("--image", help="JPEG to use (default: a built-in red square)")
    b.add_argument("--wav", help="16 kHz WAV to use (default: a built-in 1 s tone)")
    b.add_argument("--runs", type=int, default=3)
    b.add_argument("--json", action="store_true", help="machine-readable output")
    b.set_defaults(fn=cmd_bench)
    sub.add_parser("schema", help="print the agent tool schemas").set_defaults(fn=cmd_schema)
    for o in ops.REGISTRY.values():
        sp = sub.add_parser(o.name.replace("_", "-"), help=o.help)
        lone = len(o.params) == 1 and not o.params[0].required
        for prm in o.params:
            if prm.required or lone:
                sp.add_argument(prm.name, help=prm.help, choices=prm.enum, nargs=None if prm.required else "?")
            else:
                sp.add_argument(f"--{prm.name.replace('_', '-')}", dest=prm.name, help=prm.help, choices=prm.enum)
        sp.set_defaults(fn=None, op=o)
    args = p.parse_args(argv)
    if args.fn:
        return args.fn(args)
    o = args.op
    result = call(args.url, o.name, {prm.name: getattr(args, prm.name) for prm in o.params})
    print(result if isinstance(result, str) else json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
