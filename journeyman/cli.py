"""`jm`: the CLI. Operations go to the running journeyman service; serve/replay/recordings/doctor/bench are local."""
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
    cfg = Config()
    ok = True
    print(f"gemma url   {cfg.gemma_url}")
    try:
        models = request_json(cfg.gemma_url.rstrip('/') + "/models", timeout=5)
        print(f"  models    {[m['id'] for m in models.get('data', [])]}")
    except Exception as e:
        ok = False
        print(f"  NOT REACHABLE: {e}")
    try:
        status = call(args.url, "status", {})
        print(f"journeyman  {args.url} mode={status['mode']} camera={status['camera']['source']}")
    except Exception as e:
        print(f"journeyman  {args.url} not running ({e})")
    online = internet_reachable()
    print(f"internet    {'REACHABLE (turn Wi-Fi off for the demo)' if online else 'unreachable (offline)'}")
    if args.offline and online:
        ok = False
    sys.exit(0 if ok else 1)


def cmd_replay(args):
    """Feed a recording through the running service: script.jsonl lines with at + one of text/wav/frame/op."""
    from .recorder import play
    speed = 20.0 if args.fast else args.speed
    before = call(args.url, "status", {})

    def send(kind, value, line):
        if kind == "op":
            print("op   ", value, call(args.url, value, line.get("args", {})))
        elif kind == "text":
            request_json(f"{args.url}/api/headset/utterance", {"text": value})
            print("said ", value)
        elif kind == "wav":
            request_json(f"{args.url}/api/headset/utterance", {"wav_b64": base64.b64encode(value).decode()})
            print("wav  ", line["wav"], f"(heard: {line['heard']})" if line.get("heard") else "")
        else:
            request_json(f"{args.url}/api/frames", {"jpeg_b64": base64.b64encode(value).decode()})
            print("frame", line["frame"])

    n = play(Path(args.dir), send, speed)
    after, seq, quiet, deadline = call(args.url, "status", {}), None, 0.0, time.time() + args.settle
    while time.time() < deadline:
        if after["seq"] != seq:
            seq, quiet = after["seq"], time.time()
        elif time.time() - quiet >= 3:
            break
        time.sleep(0.5)
        after = call(args.url, "status", {})
    steps = sum(after["procedures"].values()) - sum(before["procedures"].values())
    asked = after.get("questions_asked", 0) - before.get("questions_asked", 0)
    logged = after["open_questions"] - before["open_questions"]
    print(f"replayed {n} lines: {steps} step{'s' if steps != 1 else ''} saved, {asked} question{'s' if asked != 1 else ''}"
          f" asked, {logged} open question{'s' if logged != 1 else ''} logged; mode now {after['mode']}")


def cmd_recordings(args):
    from .recorder import summarize
    roots = [Path(args.dir)] if args.dir else [Config().data_dir / "recordings", Path(__file__).parent.parent / "recordings"]
    found = False
    for root in roots:
        for d in sorted(p for p in root.glob("*") if (p / "script.jsonl").exists()):
            found = True
            s = summarize(d)
            label = " ".join(x for x in (s["kind"], s["procedure"]) if x)
            print(f"{str(d):40} {s['duration_s']:7.1f}s  {s['utterances']:3} utterances ({s['audio']} audio)"
                  f"  {s['frames']:3} frames  {s['ops']} ops  {label}")
    if not found:
        print(f"no recordings in {', '.join(str(r) for r in roots)}")


def cmd_bench(args):
    from .gemma import Gemma, audio_part, image_part, text_part
    cfg = Config()
    g = Gemma(cfg.gemma_url, cfg.gemma_model)
    t = time.time()
    g.chat([{"role": "user", "content": "Say OK."}], max_tokens=5)
    print(f"text round trip      {time.time() - t:.2f}s  ({g.model})")
    if args.image:
        t = time.time()
        g.chat([{"role": "user", "content": [image_part(Path(args.image).read_bytes()),
                                             text_part("What is in this image? One sentence.")]}], max_tokens=40)
        print(f"image question       {time.time() - t:.2f}s")
    if args.wav:
        t = time.time()
        msg, _ = g.chat([{"role": "user", "content": [audio_part(Path(args.wav).read_bytes()),
                                                      text_part("Transcribe exactly.")]}], max_tokens=100)
        print(f"audio transcription  {time.time() - t:.2f}s  {msg.get('content')!r}")


def cmd_schema(_args):
    print(json.dumps(ops.tool_schemas(), indent=2))


def main(argv=None):
    p = argparse.ArgumentParser(prog="jm", description="Journeyman: a local voice instructor for hands-on work.")
    p.add_argument("--url", default=DEFAULT_URL, help="journeyman service URL")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="run the journeyman service").set_defaults(fn=cmd_serve)
    d = sub.add_parser("doctor", help="check Gemma, the service, the camera and whether you are offline")
    d.add_argument("--offline", action="store_true", help="fail if the internet is reachable")
    d.set_defaults(fn=cmd_doctor)
    r = sub.add_parser("replay", help="feed a recorded session through the running service")
    r.add_argument("dir")
    r.add_argument("--speed", type=float, default=1.0)
    r.add_argument("--fast", action="store_true", help="same as --speed 20")
    r.add_argument("--settle", type=float, default=120, help="max seconds to wait for the service to go quiet")
    r.set_defaults(fn=cmd_replay)
    rl = sub.add_parser("recordings", help="list recorded sessions with duration and counts")
    rl.add_argument("dir", nargs="?", help="default: $JM_DATA/recordings and ./recordings")
    rl.set_defaults(fn=cmd_recordings)
    b = sub.add_parser("bench", help="time Gemma text, image and audio round trips")
    b.add_argument("--image")
    b.add_argument("--wav")
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
