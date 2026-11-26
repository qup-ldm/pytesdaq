#!/bin/bash
# Run after each sync (raw-data-sync.service ExecStopPost, as the
# desktop user): desktop notification when the sync fails (repeated
# at most every NOTIFY_REPEAT_MIN minutes) and once when it works again.
# Nobody logged in to the desktop -> nothing shown.
set -u
source "$(dirname "$(readlink -f "$0")")/sync.conf"
NOTIFY_REPEAT_MIN=${NOTIFY_REPEAT_MIN:-60}
STATE=$HOME/.cache/raw-data-sync.failed    # "<first failure> <last notified>"
BUS=/run/user/$(id -u)/bus
export DBUS_SESSION_BUS_ADDRESS=unix:path=$BUS

notify() {   # urgency, title, body
    [ -S "$BUS" ] || return 0
    notify-send -a "NAS sync" -i drive-harddisk -u "$1" "$2" "$3" \
        2> /dev/null || true
}

if [ "${SERVICE_RESULT:-success}" = success ]; then
    if [ -f "$STATE" ]; then
        rm -f "$STATE"
        notify normal "NAS sync working again" \
               "Raw data is being copied to $DEST_DIR"
    fi
    exit 0
fi

now=$(date +%s)
first=$now
last=0
if [ -f "$STATE" ]; then
    read -r first last < "$STATE"
fi
mkdir -p "$(dirname "$STATE")"
if [ $((now - last)) -ge $((NOTIFY_REPEAT_MIN * 60)) ]; then
    # error messages of this sync
    reason=$(journalctl -q -o cat _SYSTEMD_INVOCATION_ID="${INVOCATION_ID:-}" \
                 2> /dev/null | grep -E 'ERROR|WARNING' | tail -2)
    notify critical "NAS sync failing since $(date -d "@$first" '+%a %H:%M')" \
           "${reason:-Sync failed (${SERVICE_RESULT})}
Data stays on this PC. Details: journalctl -u raw-data-sync"
    last=$now
fi
echo "$first $last" > "$STATE"
exit 0
