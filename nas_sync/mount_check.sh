#!/bin/bash
# Run as root before each sync (raw-data-sync.service ExecStartPre=+):
# mount the NAS if it is not mounted.
# Wrong username/password (mount error 13) is counted: after
# MAX_AUTH_FAILURES (sync.conf) no more mount attempts (Synology blocks
# the PC's IP after repeated failed logins) until the credentials file
# is changed or the NAS is mounted by hand.
set -u
source "$(dirname "$(readlink -f "$0")")/sync.conf"
NAS_MOUNT=${NAS_MOUNT%/}
MAX_AUTH_FAILURES=${MAX_AUTH_FAILURES:-3}
STATE_DIR=${STATE_DIR:-/var/lib/raw-data-sync}
STATE=$STATE_DIR/auth_failures

if mountpoint -q "$NAS_MOUNT"; then
    rm -f "$STATE"
    exit 0
fi

# credentials file from the fstab entry (changed -> reset count)
cred=$(findmnt -s -n -o OPTIONS "$NAS_MOUNT" | tr ',' '\n' |
       sed -n 's/^credentials=//p')
stamp=$(stat -c %Y "$cred" 2>/dev/null || echo none)
count=0
if [ -f "$STATE" ]; then
    read -r count old_stamp < "$STATE"
    [ "$old_stamp" = "$stamp" ] || count=0
fi
if [ "$count" -ge "$MAX_AUTH_FAILURES" ]; then
    echo "ERROR: NAS login failed $count times (wrong username/password)," \
         "not retrying until $cred is changed"
    exit 1
fi

if out=$(mount "$NAS_MOUNT" 2>&1); then
    rm -f "$STATE"
    echo "Mounted $NAS_MOUNT"
    exit 0
fi
echo "ERROR: mounting $NAS_MOUNT failed: $out"
if grep -q 'error(13)' <<< "$out"; then
    count=$((count + 1))
    mkdir -p "$STATE_DIR"
    echo "$count $stamp" > "$STATE"
    echo "ERROR: NAS login failed ($count/$MAX_AUTH_FAILURES)," \
         "check username/password in $cred"
fi
exit 1
