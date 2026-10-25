#!/usr/bin/env bash
# Plan B: the whole demo on the Mac. Gemma 4 E4B via local Ollama (never a :cloud tag), the Mac or Brio camera,
# `jm doctor`, `jm serve`, then it prints the headset command. Stop it with scripts/mac-up.sh --stop.
#
#   scripts/mac-up.sh              check Ollama + model (pull if missing), camera, start jm serve and the camera
#   scripts/mac-up.sh --dry-run    check and print every command, start nothing
#   scripts/mac-up.sh --no-pull    fail instead of downloading the model;  --no-doctor  skip `jm doctor`
#   scripts/mac-up.sh --stop       stop jm serve, the camera and an Ollama this script started
#
# Settings (env): JM_MAC_MODEL (gemma4:e4b), JM_OLLAMA_URL (http://127.0.0.1:11434), JM_MAX_LEN (8192 context),
# JM_MAC_CAMERA_VIA (auto|ffmpeg|opencv|none), JM_MAC_CAMERA (auto = Brio first, or an index / part of a name),
# JM_MAC_FPS (1), JM_PORT (3000), JM_RUN_DIR (/tmp/journeyman).
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MODEL="${JM_MAC_MODEL:-gemma4:e4b}"
OLLAMA="${JM_OLLAMA_URL:-http://127.0.0.1:11434}"
CTX="${JM_MAX_LEN:-8192}"
VIA="${JM_MAC_CAMERA_VIA:-auto}"
RUN="${JM_RUN_DIR:-/tmp/journeyman}"
JM_PORT="${JM_PORT:-3000}"
JM_LOCAL="http://127.0.0.1:$JM_PORT"
SERVED="jm-$(echo "$MODEL" | tr ':/' '--')"

DRY=0 PULL=1 DOCTOR=1 FAILS=0
for a in "$@"; do
  case "$a" in
    --dry-run) DRY=1 ;;
    --no-pull) PULL=0 ;;
    --no-doctor) DOCTOR=0 ;;
    --stop) STOP=1 ;;
    -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $a (try --help)" >&2; exit 2 ;;
  esac
done

say() { printf '%s\n' "$*"; }
pass() { say "PASS  $*"; }
fail() { say "FAIL  $*"; FAILS=$((FAILS + 1)); }
run() {
  if [ "$DRY" = 1 ]; then say "+ $*"; else "$@"; fi
}
stop_pid() {
  [ -f "$RUN/$1.pid" ] || return 0
  pid="$(cat "$RUN/$1.pid")"
  if kill -0 "$pid" 2>/dev/null; then
    run kill "$pid"
    say "stopped $1 (pid $pid)"
  fi
  [ "$DRY" = 1 ] || rm -f "$RUN/$1.pid"
}
tags() { curl -sf --max-time 3 "$OLLAMA/api/tags" 2>/dev/null; }

if [ "${STOP:-0}" = 1 ]; then
  stop_pid camera; stop_pid serve; stop_pid ollama
  exit 0
fi

case "$MODEL" in
  *cloud*) fail "$MODEL is an Ollama cloud model; Journeyman runs on-device only (use gemma4:e4b)"; exit 1 ;;
esac
case "$OLLAMA" in
  http://127.0.0.1:*|http://localhost:*|http://\[::1\]:*) ;;
  *) fail "JM_OLLAMA_URL=$OLLAMA is not this Mac"; exit 1 ;;
esac

[ "$DRY" = 1 ] && say "dry run: nothing will be started or downloaded"
[ "$DRY" = 0 ] && mkdir -p "$RUN"

# 1. Ollama
if tags >/dev/null; then
  pass "Ollama answers at $OLLAMA"
elif command -v ollama >/dev/null 2>&1; then
  say "      Ollama not answering; starting it (log: $RUN/ollama.log)"
  if [ "$DRY" = 1 ]; then say "+ OLLAMA_HOST=${OLLAMA#http://} ollama serve > $RUN/ollama.log 2>&1 &"
  else
    OLLAMA_HOST="${OLLAMA#http://}" nohup ollama serve > "$RUN/ollama.log" 2>&1 &
    echo $! > "$RUN/ollama.pid"
    for _ in $(seq 1 30); do tags >/dev/null && break; sleep 1; done
    if tags >/dev/null; then pass "Ollama started at $OLLAMA"; else fail "Ollama did not start; see $RUN/ollama.log"; exit 1; fi
  fi
else
  fail "Ollama not found: install it from ollama.com (or brew install ollama), then re-run"
  [ "$DRY" = 1 ] || exit 1
fi

# 2. The model, plus a derived tag with a context long enough for frames (Ollama's default is shorter)
if tags | grep -q "\"$MODEL\""; then
  pass "model $MODEL is on this Mac"
elif [ "$PULL" = 1 ] && command -v ollama >/dev/null 2>&1; then
  say "      $MODEL not pulled yet; downloading it (several GB)"
  run ollama pull "$MODEL" || { fail "ollama pull $MODEL failed"; exit 1; }
else
  fail "model $MODEL missing: run  ollama pull $MODEL"
  [ "$DRY" = 1 ] || exit 1
fi

if [ "$DRY" = 1 ]; then
  say "+ ollama create $SERVED  (FROM $MODEL, PARAMETER num_ctx $CTX)"
elif command -v ollama >/dev/null 2>&1 \
    && printf 'FROM %s\nPARAMETER num_ctx %s\n' "$MODEL" "$CTX" > "$RUN/Modelfile" \
    && ollama create "$SERVED" -f "$RUN/Modelfile" >/dev/null 2>&1; then
  pass "$SERVED = $MODEL with num_ctx $CTX"
else
  say "WARN  could not create $SERVED; using $MODEL with Ollama's default context (long prompts may be cut)"
  SERVED="$MODEL"
fi

if [ "$DRY" = 1 ]; then say "+ warm up $SERVED with one tiny chat call"
else
  start=$(date +%s)
  say "      loading $SERVED (first load can take a minute)"
  if curl -sf --max-time 300 "$OLLAMA/v1/chat/completions" -H 'Content-Type: application/json' \
      -d "{\"model\":\"$SERVED\",\"messages\":[{\"role\":\"user\",\"content\":\"Say ok.\"}],\"max_tokens\":8,\"reasoning_effort\":\"none\"}" \
      | grep -q '"choices"'; then
    pass "$SERVED answers ($(( $(date +%s) - start ))s)"
  else
    fail "$SERVED did not answer a chat call; try: ollama run $MODEL"
  fi
fi

# 3. Camera: ffmpeg (avfoundation) pushes frames, else OpenCV inside jm serve, else none
if [ "$VIA" = auto ]; then
  if command -v ffmpeg >/dev/null 2>&1; then VIA=ffmpeg
  elif python3 -c 'import cv2' >/dev/null 2>&1; then VIA=opencv
  else VIA=none
  fi
fi
case "$VIA" in
  ffmpeg)
    export JM_CAMERA=off
    pass "camera via ffmpeg avfoundation (JM_MAC_CAMERA=${JM_MAC_CAMERA:-auto}); cameras seen:"
    python3 "$ROOT/scripts/mac_camera.py" --list 2>&1 | sed 's/^/      /' ;;
  opencv)
    export JM_CAMERA="${JM_CAMERA:-0}"
    pass "camera via OpenCV inside jm serve (JM_CAMERA=$JM_CAMERA)" ;;
  *)
    export JM_CAMERA=off
    say "WARN  no camera path: brew install ffmpeg  (or pip3 install opencv-python); running without the camera" ;;
esac
say "      first camera use asks for permission for this terminal (System Settings > Privacy & Security > Camera)"

export JM_GEMMA_URL="$OLLAMA/v1" JM_GEMMA_MODEL="$SERVED" JM_PORT
unset JM_VISION_URL JM_VISION_MODEL
say "      JM_GEMMA_URL=$JM_GEMMA_URL JM_GEMMA_MODEL=$JM_GEMMA_MODEL JM_CAMERA=$JM_CAMERA"
if [ "$DRY" = 0 ]; then
  echo "export JM_GEMMA_URL=$JM_GEMMA_URL JM_GEMMA_MODEL=$JM_GEMMA_MODEL JM_CAMERA=$JM_CAMERA JM_PORT=$JM_PORT" > "$RUN/env.sh"
  say "      saved to $RUN/env.sh (source it before other ./jm commands)"
fi

# 4. jm doctor, jm serve, the camera pusher
if [ "$DOCTOR" = 1 ]; then
  say "      jm doctor:"
  if [ "$DRY" = 1 ]; then say "+ $ROOT/jm doctor"
  elif "$ROOT/jm" doctor | sed 's/^/      /'; [ "${PIPESTATUS[0]}" = 0 ]; then pass "jm doctor"
  else say "WARN  jm doctor reported a FAIL (see above); starting anyway"
  fi
fi

if [ "$DRY" = 1 ]; then
  say "+ $ROOT/jm serve > $RUN/serve.log 2>&1 &"
  [ "$VIA" = ffmpeg ] && say "+ python3 $ROOT/scripts/mac_camera.py --jm $JM_LOCAL > $RUN/camera.log 2>&1 &"
else
  stop_pid camera; stop_pid serve
  nohup "$ROOT/jm" serve > "$RUN/serve.log" 2>&1 &
  echo $! > "$RUN/serve.pid"
  ok=0
  for _ in $(seq 1 30); do
    curl -sf --max-time 2 "$JM_LOCAL/api/state" >/dev/null 2>&1 && { ok=1; break; }
    kill -0 "$(cat "$RUN/serve.pid")" 2>/dev/null || break
    sleep 1
  done
  if [ "$ok" = 1 ]; then pass "jm serve on $JM_LOCAL (log: $RUN/serve.log)"
  else fail "jm serve did not answer on :$JM_PORT; see $RUN/serve.log"; tail -n 20 "$RUN/serve.log" | sed 's/^/      /'
  fi
  if [ "$ok" = 1 ] && [ "$VIA" = ffmpeg ]; then
    nohup python3 "$ROOT/scripts/mac_camera.py" --jm "$JM_LOCAL" > "$RUN/camera.log" 2>&1 &
    echo $! > "$RUN/camera.pid"
    say "      camera pusher started (log: $RUN/camera.log)"
  fi
fi

say ""
say "      monitor:  open $JM_LOCAL"
say "      headset:  python3 $ROOT/headset/headset.py --thor $JM_LOCAL --text"
say "      voice:    python3 $ROOT/headset/headset.py --thor $JM_LOCAL   (Ollama /v1 dropped audio in our tests; use --text if it mishears)"
say "      stop:     $ROOT/scripts/mac-up.sh --stop"
if [ "$DRY" = 1 ]; then say "      dry run done"; elif [ "$FAILS" = 0 ]; then say "PASS  Mac is up"; else say "FAIL  $FAILS problem(s) above"; fi
[ "$FAILS" = 0 ]
