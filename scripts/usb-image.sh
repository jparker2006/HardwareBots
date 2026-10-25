#!/usr/bin/env bash
# Mac: copy the vLLM image folder (made by tools/pull_image.py) onto a USB drive for Thor, verify, eject.
#
#   scripts/usb-image.sh                  the only USB drive in /Volumes (lists them if there are several)
#   scripts/usb-image.sh Lexar            /Volumes/Lexar (or a full path)
#   scripts/usb-image.sh --dry-run [VOL]  check the image, the drive and the free space; copy nothing
#   scripts/usb-image.sh --no-eject VOL   leave the drive mounted
#
# Settings (env): JM_IMAGE_DIR (~/jm-vllm-image), JM_VOLUMES (/Volumes), JM_DISKUTIL (diskutil).
# Re-running resumes: files already on the drive with the right size are skipped.
set -u

SRC="${JM_IMAGE_DIR:-$HOME/jm-vllm-image}"
VOLROOT="${JM_VOLUMES:-/Volumes}"
DISKUTIL="${JM_DISKUTIL:-diskutil}"
USAGE="usage: scripts/usb-image.sh [--dry-run] [--no-eject] [VOLUME]   then on Thor: scripts/go-live.sh"

DRY=0 EJECT=1 VOL=""
for a in "$@"; do
  case "$a" in
    --dry-run) DRY=1 ;;
    --no-eject) EJECT=0 ;;
    -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) echo "unknown option: $a (try --help)" >&2; exit 2 ;;
    *) VOL="$a" ;;
  esac
done

say() { printf '%s\n' "$*"; }
die() { say "FAIL  $*"; say "$USAGE"; exit 1; }
fsize() { stat -c %s "$1" 2>/dev/null || stat -f %z "$1"; }
gb() { awk -v b="$1" 'BEGIN { printf "%.2f GB", b / 1e9 }'; }

[ -f "$SRC/manifest.json" ] && [ -d "$SRC/blobs" ] \
  || die "no finished image in $SRC (run: python3 tools/pull_image.py ghcr.io/nvidia-ai-iot/vllm:gemma4-jetson-thor $SRC)"
[ -z "$(find "$SRC" -name '*.part' | head -n 1)" ] || die "$SRC still has .part files; finish tools/pull_image.py first"

if [ -z "$VOL" ]; then
  vols=""
  for v in "$VOLROOT"/*; do
    [ -d "$v" ] && [ ! -L "$v" ] && case "$(basename "$v")" in "Macintosh HD"*) ;; *) vols="$vols$v
" ;; esac
  done
  n=$(printf '%s' "$vols" | grep -c .)
  [ "$n" = 0 ] && die "no USB drive in $VOLROOT; plug one in"
  if [ "$n" != 1 ]; then
    say "several drives; pick one:"
    printf '%s' "$vols" | sed 's/^/  /'
    say "$USAGE"
    exit 2
  fi
  VOL="${vols%?}"
fi
case "$VOL" in /*) ;; *) VOL="$VOLROOT/$VOL" ;; esac
[ -d "$VOL" ] || die "$VOL is not mounted"
DEST="$VOL/jm-vllm-image"

FILES="$(cd "$SRC" && find . -type f ! -name '.*' | sed 's#^\./##' | sort)"
TOTAL=0 BIG=0 COUNT=0
while read -r f; do
  s=$(fsize "$SRC/$f"); TOTAL=$((TOTAL + s)); COUNT=$((COUNT + 1))
  [ "$s" -gt "$BIG" ] && BIG=$s
done <<EOF
$FILES
EOF
NEED=$TOTAL
while read -r f; do
  [ -f "$DEST/$f" ] && [ "$(fsize "$DEST/$f")" = "$(fsize "$SRC/$f")" ] && NEED=$((NEED - $(fsize "$SRC/$f")))
done <<EOF
$FILES
EOF
FREE=$(( $(df -Pk "$VOL" | awk 'NR==2 {print $4}') * 1024 ))
FS="$(mount | grep -F " on $VOL " | head -n 1)"
say "      image: $SRC ($COUNT files, $(gb "$TOTAL"), biggest $(gb "$BIG"))"
say "      drive: $VOL ($(gb "$FREE") free; $(gb "$NEED") still to copy)"
case "$FS" in
  *msdos*|*vfat*|*fat32*) [ "$BIG" -ge 4294967296 ] && die "$VOL is FAT32 (4 GB file limit); erase it as exFAT in Disk Utility" ;;
esac
[ "$NEED" -le "$FREE" ] || die "not enough space on $VOL: need $(gb "$NEED"), have $(gb "$FREE")"

if [ "$DRY" = 1 ]; then
  say "+ copy $SRC -> $DEST (skip files already there with the right size), verify sizes"
  [ "$EJECT" = 1 ] && say "+ $DISKUTIL eject $VOL"
  say "      dry run done"
  say "$USAGE"
  exit 0
fi

mkdir -p "$DEST" || die "cannot write to $VOL"
DONE=0 T0=$(date +%s) i=0
while read -r f; do
  i=$((i + 1)); s=$(fsize "$SRC/$f")
  if [ -f "$DEST/$f" ] && [ "$(fsize "$DEST/$f")" = "$s" ]; then DONE=$((DONE + s)); continue; fi
  mkdir -p "$(dirname "$DEST/$f")"
  cat "$SRC/$f" > "$DEST/$f.part" &
  pid=$!
  while kill -0 "$pid" 2>/dev/null; do
    got=$(( DONE + $(fsize "$DEST/$f.part" 2>/dev/null || echo 0) )); t=$(( $(date +%s) - T0 ))
    printf '\r      [%d/%d] %s / %s  %s MB/s ' "$i" "$COUNT" "$(gb "$got")" "$(gb "$TOTAL")" \
      "$(awk -v b="$got" -v t="$t" 'BEGIN { printf "%.0f", b / 1e6 / (t > 0 ? t : 1) }')"
    sleep 1
  done
  wait "$pid" || { say ""; die "copy failed for $f (drive full or unplugged?)"; }
  mv "$DEST/$f.part" "$DEST/$f" || die "cannot rename $DEST/$f.part"
  DONE=$((DONE + s))
done <<EOF
$FILES
EOF
say ""
say "      copied in $(( $(date +%s) - T0 ))s; syncing"
sync

BAD=0
while read -r f; do
  if [ ! -f "$DEST/$f" ] || [ "$(fsize "$DEST/$f")" != "$(fsize "$SRC/$f")" ]; then say "FAIL  size differs: $f"; BAD=$((BAD + 1)); fi
done <<EOF
$FILES
EOF
[ "$BAD" = 0 ] || die "$BAD file(s) differ on $DEST; run this again to recopy them"
say "PASS  $COUNT files, $(gb "$TOTAL") on $DEST, sizes match"

if [ "$EJECT" = 1 ]; then
  if "$DISKUTIL" eject "$VOL"; then say "PASS  ejected $VOL; unplug it and take it to Thor"
  else say "WARN  could not eject $VOL (something still open?); eject it in Finder"
  fi
fi
say "$USAGE"
