#!/usr/bin/env bash
# Thor: load the vLLM image from the USB drive (made by scripts/usb-image.sh), then bring Journeyman up:
# find the drive, mount it read-only, `docker load`, check the tag, unmount, scripts/thor-up.sh --only-31b,
# jm doctor, jm bench. All output also goes to ~/go-live.log.
#
#   scripts/go-live.sh                 find the USB drive by itself
#   scripts/go-live.sh --dev /dev/sda1 use this partition
#   scripts/go-live.sh --dir PATH      the image folder is already here (no mount)
#   scripts/go-live.sh --dry-run       find the drive and print every command, run nothing
#   scripts/go-live.sh --reload        docker load even if the image is already there
#   scripts/go-live.sh --no-bench      skip jm bench
#
# sudo is needed only for mount/umount (and for docker if your user is not in the docker group;
# thor-up.sh also uses it to drop the page cache, JM_DROP_CACHES=0 skips that).
# Settings (env): JM_VLLM_IMAGE, JM_USB_MNT (/mnt/jm-usb), JM_GO_LIVE_LOG (~/go-live.log), JM_DOCKER, JM_RUN_DIR,
# JM_MOUNT ("sudo mount"), JM_UMOUNT ("sudo umount"), JM_LSBLK (lsblk), JM_THOR_UP, JM_JM.
set -u -o pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE="${JM_VLLM_IMAGE:-ghcr.io/nvidia-ai-iot/vllm:gemma4-jetson-thor}"
MNT="${JM_USB_MNT:-/mnt/jm-usb}"
LOG="${JM_GO_LIVE_LOG:-$HOME/go-live.log}"
RUN="${JM_RUN_DIR:-/tmp/journeyman}"
MOUNT="${JM_MOUNT:-sudo mount}"
UMOUNT="${JM_UMOUNT:-sudo umount}"
LSBLK="${JM_LSBLK:-lsblk}"
THOR_UP="${JM_THOR_UP:-$ROOT/scripts/thor-up.sh}"
JM="${JM_JM:-$ROOT/jm}"
JM_PORT="${JM_PORT:-3000}"
USAGE="usage: scripts/go-live.sh [--dry-run] [--dev /dev/sdX1 | --dir PATH] [--reload] [--no-bench]   (log: $LOG)"

DRY=0 DEV="" DIR="" RELOAD=0 BENCH=1
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1 ;;
    --dev) DEV="${2:-}"; shift ;;
    --dir) DIR="${2:-}"; shift ;;
    --reload) RELOAD=1 ;;
    --no-bench) BENCH=0 ;;
    -h|--help) sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1 (try --help)" >&2; exit 2 ;;
  esac
  shift
done

if [ -n "${JM_DOCKER:-}" ]; then DOCKER="$JM_DOCKER"
elif docker info >/dev/null 2>&1; then DOCKER=docker
else DOCKER="sudo docker"
fi

say() { printf '%s\n' "$*"; }
pass() { say "PASS  $*"; }
die() { say "FAIL  $*"; say "$USAGE"; exit 1; }

MOUNTED=0 NEEDMOUNT=0
unmount() {
  [ "$MOUNTED" = 1 ] || return 0
  MOUNTED=0
  # shellcheck disable=SC2086
  if $UMOUNT "$MNT"; then pass "unmounted $MNT; you can unplug the drive"
  else say "WARN  could not unmount $MNT; run: sudo umount $MNT"
  fi
}

# find_usb: print the one removable partition with a filesystem as "DEV MOUNTPOINT" (mountpoint may be empty)
find_usb() {
  $LSBLK -Pnpo NAME,TYPE,RM,HOTPLUG,FSTYPE,MOUNTPOINT 2>/dev/null | while read -r line; do
    NAME="" TYPE="" RM="" HOTPLUG="" FSTYPE="" MOUNTPOINT=""
    eval "$line"
    [ -n "$FSTYPE" ] && [ "$TYPE" != loop ] && { [ "$RM" = 1 ] || [ "$HOTPLUG" = 1 ]; } && say "$NAME $MOUNTPOINT"
  done
}

main() {
  say "go-live $(date '+%F %T'): image $IMAGE"
  [ "$DRY" = 1 ] && say "dry run: nothing will be mounted, loaded or started"

  if [ -z "$DIR" ]; then
    if [ -z "$DEV" ]; then
      found="$(find_usb)"
      n=$(printf '%s' "$found" | grep -c .)
      [ "$n" = 0 ] && die "no USB drive found; plug it in (check: lsblk) or pass --dev /dev/sdX1"
      if [ "$n" != 1 ]; then say "several USB partitions:"; say "$found" | sed 's/^/  /'; die "pick one with --dev"; fi
      DEV="${found%% *}" at="${found#* }"
    else
      at="$($LSBLK -Pnpo MOUNTPOINT "$DEV" 2>/dev/null | head -n 1 | sed -n 's/^MOUNTPOINT="\(.*\)"$/\1/p')"
    fi
    say "      USB: $DEV${at:+ (mounted at $at)}"
    if [ -n "$at" ]; then DIR="$at/jm-vllm-image"
    else
      NEEDMOUNT=1
      say "      sudo is needed only to mount and unmount the drive (read-only)"
      if [ "$DRY" = 1 ]; then say "+ $MOUNT -o ro $DEV $MNT"
      else
        [ -d "$MNT" ] || mkdir -p "$MNT" 2>/dev/null || sudo mkdir -p "$MNT"
        # shellcheck disable=SC2086
        $MOUNT -o ro "$DEV" "$MNT" || die "could not mount $DEV read-only at $MNT"
        MOUNTED=1
        pass "mounted $DEV read-only at $MNT"
      fi
      DIR="$MNT/jm-vllm-image"
    fi
  fi
  trap unmount EXIT

  if [ "$DRY" = 1 ]; then
    say "+ tar -C $DIR -cf - . | $DOCKER load"
    say "+ $DOCKER image inspect <tag from $DIR/manifest.json>"
    [ "$NEEDMOUNT" = 1 ] && say "+ $UMOUNT $MNT"
  else
    [ -f "$DIR/manifest.json" ] || die "no image at $DIR (made on the Mac with scripts/usb-image.sh)"
    tag="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[0]["RepoTags"][0])' "$DIR/manifest.json")" \
      || die "cannot read the tag from $DIR/manifest.json"
    say "      image on the drive: $tag"
    if [ "$RELOAD" = 0 ] && $DOCKER image inspect "$tag" >/dev/null 2>&1; then
      pass "$tag already loaded; skipping docker load (--reload forces it)"
    else
      t0=$(date +%s)
      say "      docker load (several minutes for ~20 GB)"
      tar -C "$DIR" --exclude='._*' -cf - . | $DOCKER load || die "docker load failed (drive unplugged or image incomplete?)"
      pass "docker load ($(( $(date +%s) - t0 ))s)"
    fi
    $DOCKER image inspect "$tag" >/dev/null 2>&1 || die "$tag is not in docker images after the load"
    pass "image $tag is present"
    if [ "$tag" != "$IMAGE" ]; then say "WARN  expected $IMAGE; using $tag"; IMAGE="$tag"; fi
    unmount
  fi

  export JM_VLLM_IMAGE="$IMAGE"
  up=("$THOR_UP" --only-31b --no-doctor)
  [ "$DRY" = 1 ] && up+=(--dry-run)
  say "      ${up[*]}"
  "${up[@]}" || die "thor-up failed (see above; fixes in docs/VLLM-THOR.md)"
  if [ "$DRY" = 1 ]; then
    say "+ source $RUN/env.sh; $JM doctor"
    [ "$BENCH" = 1 ] && say "+ $JM bench"
  else
    # shellcheck disable=SC1091
    . "$RUN/env.sh"
    say "      jm doctor:"
    if "$JM" doctor; then pass "jm doctor"; else say "WARN  jm doctor reported a FAIL (see above)"; fi
    if [ "$BENCH" = 1 ]; then
      say "      jm bench:"
      "$JM" bench || say "WARN  jm bench failed (see above)"
    fi
  fi
  if [ "$DRY" = 1 ]; then say "      dry run done"
  else pass "live: monitor http://127.0.0.1:$JM_PORT; on the Mac: python3 headset/headset.py --thor http://192.168.55.1:$JM_PORT --text"
  fi
  say "$USAGE"
}

if [ "$DRY" = 1 ]; then main; exit $?; fi
main 2>&1 | tee "$LOG"
exit "${PIPESTATUS[0]}"
