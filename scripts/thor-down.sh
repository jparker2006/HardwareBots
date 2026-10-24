#!/usr/bin/env bash
# Stop what scripts/thor-up.sh started: `jm serve`, then the vLLM containers. Safe to run twice.
#   scripts/thor-down.sh [--dry-run]
set -u

RUN="${JM_RUN_DIR:-/tmp/journeyman}"
DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1

run() {
  if [ "$DRY" = 1 ]; then printf '+ %s\n' "$*"; else "$@"; fi
}

if [ -n "${JM_DOCKER:-}" ]; then DOCKER="$JM_DOCKER"
elif docker info >/dev/null 2>&1; then DOCKER=docker
else DOCKER="sudo docker"
fi

if [ -f "$RUN/serve.pid" ]; then
  pid="$(cat "$RUN/serve.pid")"
  if kill -0 "$pid" 2>/dev/null; then
    run kill "$pid"
    if [ "$DRY" = 0 ]; then
      for _ in 1 2 3 4 5; do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
      kill -0 "$pid" 2>/dev/null && kill -9 "$pid"
    fi
    echo "stopped jm serve (pid $pid)"
  fi
  run rm -f "$RUN/serve.pid"
fi

for name in jm-gemma-e4b jm-gemma-31b; do
  if [ "$DRY" = 1 ]; then
    run $DOCKER stop "$name"
    run $DOCKER rm "$name"
  elif $DOCKER inspect "$name" >/dev/null 2>&1; then
    $DOCKER stop -t 20 "$name" >/dev/null 2>&1
    $DOCKER rm -f "$name" >/dev/null 2>&1
    echo "stopped $name"
  fi
done
run rm -f "$RUN/hf.env"
echo "PASS  Thor is down"
