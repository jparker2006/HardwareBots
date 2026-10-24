#!/usr/bin/env bash
# One-command Thor bring-up: vLLM for Gemma 4 31B NVFP4 (eyes, brain) and E4B (ears, agent), health checks,
# `jm doctor`, then `jm serve` with the camera. Stop it all with scripts/thor-down.sh.
#
#   scripts/thor-up.sh                 start whatever models are available
#   scripts/thor-up.sh --only-31b      just the local 31B folder (no audio: use the headset with --text)
#   scripts/thor-up.sh --only-e4b      just E4B (downloaded into the Hugging Face cache if missing)
#   scripts/thor-up.sh --dry-run       print every command, run nothing
#   scripts/thor-up.sh --no-serve      models only;  --no-doctor  skip `jm doctor`
#
# Settings (env): JM_31B_DIR, JM_E4B_MODEL, JM_E4B_DIR, JM_HF_CACHE, JM_31B_PORT (8001), JM_E4B_PORT (8000),
# JM_31B_MEM (0.50), JM_E4B_MEM (0.25), JM_MAX_LEN (8192), JM_31B_ARGS, JM_E4B_ARGS, JM_VLLM_IMAGE, JM_DOCKER,
# JM_VLLM_HOST (127.0.0.1), JM_UP_TIMEOUT (1200 s per model), JM_RUN_DIR (/tmp/journeyman), JM_CAMERA (0), JM_PORT (3000), HF_TOKEN.
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE="${JM_VLLM_IMAGE:-ghcr.io/nvidia-ai-iot/vllm:gemma4-jetson-thor}"
HF_CACHE="${JM_HF_CACHE:-$HOME/.cache/huggingface}"
E4B_MODEL="${JM_E4B_MODEL:-google/gemma-4-E4B-it}"
E4B_DIR="${JM_E4B_DIR:-}"
P31="${JM_31B_PORT:-8001}"
PE4="${JM_E4B_PORT:-8000}"
MAX_LEN="${JM_MAX_LEN:-8192}"
TIMEOUT="${JM_UP_TIMEOUT:-1200}"
RUN="${JM_RUN_DIR:-/tmp/journeyman}"
VLLM_HOST="${JM_VLLM_HOST:-127.0.0.1}"
JM_PORT="${JM_PORT:-3000}"
export JM_CAMERA="${JM_CAMERA:-0}"
TOOL_FLAGS="--enable-auto-tool-choice --tool-call-parser gemma4 --reasoning-parser gemma4"

DRY=0 WANT31=1 WANTE4=1 SERVE=1 DOCTOR=1 FAILS=0
for a in "$@"; do
  case "$a" in
    --dry-run) DRY=1 ;;
    --only-31b) WANTE4=0 ;;
    --only-e4b) WANT31=0 ;;
    --no-serve) SERVE=0 ;;
    --no-doctor) DOCTOR=0 ;;
    -h|--help) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $a (try --help)" >&2; exit 2 ;;
  esac
done

say() { printf '%s\n' "$*"; }
pass() { say "PASS  $*"; }
fail() { say "FAIL  $*"; FAILS=$((FAILS + 1)); }
run() {
  if [ "$DRY" = 1 ]; then say "+ $*"; else "$@"; fi
}

if [ -n "${JM_DOCKER:-}" ]; then DOCKER="$JM_DOCKER"
elif docker info >/dev/null 2>&1; then DOCKER=docker
else DOCKER="sudo docker"
fi

find_31b() {
  if [ -n "${JM_31B_DIR:-}" ]; then
    [ -d "$JM_31B_DIR" ] && echo "$JM_31B_DIR"
    return
  fi
  for base in /home/jetson "$HOME"; do
    [ -d "$base" ] || continue
    find "$base" -maxdepth 2 -type d -iname '*31b*nvfp4*' 2>/dev/null | sort | head -n 1 | grep . && return
  done
}

e4b_cached() {
  [ -n "$E4B_DIR" ] && [ -f "$E4B_DIR/config.json" ] && return 0
  ls "$HF_CACHE/hub/models--$(echo "$E4B_MODEL" | sed 's#/#--#g')"/snapshots/*/config.json >/dev/null 2>&1
}

running() {
  [ "$($DOCKER inspect -f '{{.State.Running}}' "$1" 2>/dev/null)" = "true" ]
}

# start_vllm NAME PORT MEM SERVED MODEL_ARG EXTRA_ARGS [docker args...]
start_vllm() {
  name=$1 port=$2 mem=$3 served=$4 model=$5 extra=$6
  shift 6
  if [ "$DRY" = 0 ] && running "$name"; then
    say "      $name already running; reusing it"
    return
  fi
  [ "$DRY" = 0 ] && $DOCKER rm -f "$name" >/dev/null 2>&1
  # shellcheck disable=SC2086
  set -- $DOCKER run -d --name "$name" --runtime=nvidia --network host --ipc host \
    -v "$HF_CACHE:/root/.cache/huggingface" "$@" "$IMAGE" \
    vllm serve "$model" --served-model-name "$served" --host "$VLLM_HOST" --port "$port" \
    --gpu-memory-utilization "$mem" --max-model-len "$MAX_LEN" $TOOL_FLAGS $extra
  if [ "$DRY" = 1 ]; then say "+ $*"; return 0; fi
  "$@" >/dev/null || { fail "$name: docker run failed (is the image pulled? $DOCKER pull $IMAGE)"; return 1; }
}

# wait_models NAME URL SERVED: poll /v1/models until it lists SERVED, the container dies, or TIMEOUT passes
wait_models() {
  name=$1 url=$2 served=$3
  if [ "$DRY" = 1 ]; then say "+ wait for $url/models to list $served"; return 0; fi
  start=$(date +%s)
  say "      waiting for $name at $url (first load can take minutes; log: $DOCKER logs -f $name)"
  while :; do
    if curl -sf --max-time 3 "$url/models" 2>/dev/null | grep -q "\"$served\""; then
      pass "$name answers $url/models ($(( $(date +%s) - start ))s)"
      return 0
    fi
    if ! running "$name"; then
      fail "$name exited before answering; last log lines:"
      $DOCKER logs --tail 20 "$name" 2>&1 | sed 's/^/      /'
      return 1
    fi
    if [ $(( $(date +%s) - start )) -ge "$TIMEOUT" ]; then
      fail "$name did not answer $url/models within ${TIMEOUT}s"
      return 1
    fi
    sleep 3
  done
}

[ "$DRY" = 1 ] && say "dry run: nothing will be started"
[ "$DRY" = 0 ] && mkdir -p "$RUN"

UP31=0 UPE4=0
if [ "$WANT31" = 1 ]; then
  DIR31="$(find_31b)"
  if [ -z "$DIR31" ]; then
    if [ "$WANTE4" = 0 ]; then fail "31B: no NVFP4 folder found (set JM_31B_DIR)"; else say "skip  31B: no NVFP4 folder found (set JM_31B_DIR)"; fi
  else
    say "      31B: $DIR31 on :$P31"
    # shellcheck disable=SC2086
    start_vllm jm-gemma-31b "$P31" "${JM_31B_MEM:-0.50}" gemma-4-31b "/models/$(basename "$DIR31")" "${JM_31B_ARGS:-}" \
      -v "$DIR31:/models/$(basename "$DIR31"):ro" \
      && wait_models jm-gemma-31b "http://127.0.0.1:$P31/v1" gemma-4-31b && UP31=1
  fi
fi

if [ "$WANTE4" = 1 ]; then
  e4b_args=""
  if [ -n "$E4B_DIR" ] && [ -f "$E4B_DIR/config.json" ]; then
    e4b_model="/models/$(basename "$E4B_DIR")"
    e4b_args="-v $E4B_DIR:$e4b_model:ro"
    say "      E4B: $E4B_DIR on :$PE4"
  elif e4b_cached; then
    e4b_model="$E4B_MODEL"
    e4b_args="-e HF_HUB_OFFLINE=1"
    say "      E4B: $E4B_MODEL (cached) on :$PE4"
  else
    e4b_model="$E4B_MODEL"
    if [ -n "${HF_TOKEN:-}" ]; then
      [ "$DRY" = 0 ] && (umask 077; echo "HF_TOKEN=$HF_TOKEN" > "$RUN/hf.env")
      e4b_args="--env-file $RUN/hf.env"
    fi
    say "      E4B: $E4B_MODEL not cached; vLLM will download it into $HF_CACHE (needs internet, maybe HF_TOKEN)"
  fi
  # shellcheck disable=SC2086
  start_vllm jm-gemma-e4b "$PE4" "${JM_E4B_MEM:-0.25}" gemma-4-e4b "$e4b_model" "${JM_E4B_ARGS:-}" $e4b_args \
    && wait_models jm-gemma-e4b "http://127.0.0.1:$PE4/v1" gemma-4-e4b && UPE4=1
fi

URL31="http://127.0.0.1:$P31/v1" URLE4="http://127.0.0.1:$PE4/v1"
if [ "$UPE4" = 1 ]; then
  export JM_GEMMA_URL="$URLE4" JM_GEMMA_MODEL=gemma-4-e4b
  if [ "$UP31" = 1 ]; then export JM_VISION_URL="$URL31" JM_VISION_MODEL=gemma-4-31b
  else unset JM_VISION_URL JM_VISION_MODEL; fi
elif [ "$UP31" = 1 ]; then
  export JM_GEMMA_URL="$URL31" JM_GEMMA_MODEL=gemma-4-31b JM_VISION_URL="$URL31" JM_VISION_MODEL=gemma-4-31b
  say "WARN  only the 31B is up: no audio input, so talk with: python3 headset/headset.py --thor http://192.168.55.1:$JM_PORT --text"
else
  fail "no Gemma server is up"
  say "      stop leftovers with scripts/thor-down.sh"
  exit 1
fi

say "      JM_GEMMA_URL=$JM_GEMMA_URL JM_GEMMA_MODEL=$JM_GEMMA_MODEL"
say "      JM_VISION_URL=${JM_VISION_URL:-(same)} JM_VISION_MODEL=${JM_VISION_MODEL:-(same)} JM_CAMERA=$JM_CAMERA"
if [ "$DRY" = 0 ]; then
  {
    echo "export JM_GEMMA_URL=$JM_GEMMA_URL JM_GEMMA_MODEL=$JM_GEMMA_MODEL JM_CAMERA=$JM_CAMERA JM_PORT=$JM_PORT"
    [ -n "${JM_VISION_URL:-}" ] && echo "export JM_VISION_URL=$JM_VISION_URL JM_VISION_MODEL=$JM_VISION_MODEL"
  } > "$RUN/env.sh"
  say "      saved to $RUN/env.sh (source it before other ./jm commands)"
fi

if [ "$DOCTOR" = 1 ]; then
  say "      jm doctor:"
  if [ "$DRY" = 1 ]; then say "+ $ROOT/jm doctor"
  elif "$ROOT/jm" doctor | sed 's/^/      /'; [ "${PIPESTATUS[0]}" = 0 ]; then pass "jm doctor"
  else say "WARN  jm doctor reported a FAIL (see above); starting anyway"
  fi
fi

if [ "$SERVE" = 1 ]; then
  if [ "$DRY" = 1 ]; then
    say "+ $ROOT/jm serve > $RUN/serve.log 2>&1 &"
  else
    if [ -f "$RUN/serve.pid" ] && kill -0 "$(cat "$RUN/serve.pid")" 2>/dev/null; then
      kill "$(cat "$RUN/serve.pid")"; sleep 1
    fi
    nohup "$ROOT/jm" serve > "$RUN/serve.log" 2>&1 &
    echo $! > "$RUN/serve.pid"
    ok=0
    for _ in $(seq 1 30); do
      curl -sf --max-time 2 "http://127.0.0.1:$JM_PORT/api/state" >/dev/null 2>&1 && { ok=1; break; }
      kill -0 "$(cat "$RUN/serve.pid")" 2>/dev/null || break
      sleep 1
    done
    if [ "$ok" = 1 ]; then pass "jm serve on http://127.0.0.1:$JM_PORT (log: $RUN/serve.log)"
    else fail "jm serve did not answer on :$JM_PORT; see $RUN/serve.log"; tail -n 20 "$RUN/serve.log" | sed 's/^/      /'
    fi
  fi
fi

if [ "$DRY" = 1 ]; then say "      dry run done"; elif [ "$FAILS" = 0 ]; then say "PASS  Thor is up"; else say "FAIL  $FAILS problem(s) above"; fi
[ "$FAILS" = 0 ]
