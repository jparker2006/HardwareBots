"""`jm`: the CLI. Operations go to the running journeyman service; serve/replay/recordings/doctor/bench/eval are local."""
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


def cmd_eval(args):
    from .eval import run
    cfg = Config()
    if args.every is not None:
        cfg.observe_every_s = args.every
    run(args.dir, cfg, tail_s=args.tail, everything=args.all)


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
    r.add_argument("--fast", action="store_true", help="same as --speed 20")
    r.add_argument("--settle", type=float, default=120, help="max seconds to wait for the service to go quiet")
    r.set_defaults(fn=cmd_replay)
    rl = sub.add_parser("recordings", help="list recorded sessions with duration and counts")
    rl.add_argument("dir", nargs="?", help="default: $JM_DATA/recordings and ./recordings")
    rl.set_defaults(fn=cmd_recordings)
    b = sub.add_parser("bench", help="time Gemma text, image, audio and tool round trips")
    b.add_argument("--image", help="JPEG to use (default: a built-in red square)")
    b.add_argument("--wav", help="16 kHz WAV to use (default: a built-in 1 s tone)")
    b.add_argument("--runs", type=int, default=3)
    b.add_argument("--json", action="store_true", help="machine-readable output")
    b.set_defaults(fn=cmd_bench)
    e = sub.add_parser("eval", help="run capture prompts offline over a recording against the configured Gemma")
    e.add_argument("dir")
    e.add_argument("--every", type=float, help="observe every N script seconds (default JM_OBSERVE_EVERY)")
    e.add_argument("--tail", type=float, default=15.0, help="seconds to keep watching after the script ends")
    e.add_argument("--all", action="store_true", help="keep going after the capture session ends")
    e.set_defaults(fn=cmd_eval)
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
