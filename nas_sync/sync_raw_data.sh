#!/bin/bash
# Copy raw data to the NAS. Never deletes anything.
# Settings: sync.conf (same directory) or SYNC_CONF=<file>
# Run by raw-data-sync.timer (see README.md) or by hand:
#    nas_sync/sync_raw_data.sh        # sync now
#    nas_sync/sync_raw_data.sh -n     # dry run (extra args go to rsync)
set -u
CONF=${SYNC_CONF:-$(dirname "$(readlink -f "$0")")/sync.conf}
if [ ! -f "$CONF" ]; then
    echo "ERROR: settings file $CONF not found"
    exit 1
fi
# shellcheck source=sync.conf
source "$CONF"
SOURCE_DIR=${SOURCE_DIR%/}
NAS_MOUNT=${NAS_MOUNT%/}
DEST_DIR=${DEST_DIR%/}
SYNC_PATTERN=${SYNC_PATTERN:-run*/raw}
MIN_AGE=${MIN_AGE:-2}

# destination must be on the NAS: if the NAS is not mounted, rsync
# would fill the local disk under the mount point
case "$DEST_DIR/" in
    "$NAS_MOUNT"/*) ;;
    *) echo "ERROR: DEST_DIR ($DEST_DIR) not inside NAS_MOUNT ($NAS_MOUNT)"
       exit 1 ;;
esac
if ! mountpoint -q "$NAS_MOUNT"; then
    echo "ERROR: $NAS_MOUNT not mounted, skipping"
    exit 1
fi
if [ ! -d "$DEST_DIR" ]; then
    echo "ERROR: $DEST_DIR not found (create it on the NAS first)"
    exit 1
fi

cd "$SOURCE_DIR" || exit 1
shopt -s nullglob
# shellcheck disable=SC2206  # globs expanded on purpose
dirs=($SYNC_PATTERN)
if [ ${#dirs[@]} -eq 0 ]; then
    echo "Nothing matching \"$SYNC_PATTERN\" in $SOURCE_DIR"
    exit 0
fi
echo "Syncing $SOURCE_DIR/{$SYNC_PATTERN} -> $DEST_DIR"

# -rlt: no owner/permissions (CIFS sets them)
find "${dirs[@]}" -type f -mmin +"$MIN_AGE" -print0 |
    rsync -rlt --from0 --files-from=- --modify-window=1 --stats "$@" \
          . "$DEST_DIR/" |
    grep -E '^(Number of regular files transferred|Total transferred file size)'
rc=${PIPESTATUS[1]}

# 24 = source file vanished during transfer (harmless)
if [ "$rc" -ne 0 ] && [ "$rc" -ne 24 ]; then
    echo "ERROR: rsync failed (exit code $rc)"
    exit "$rc"
fi
exit 0
