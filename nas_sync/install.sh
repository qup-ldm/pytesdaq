#!/bin/bash
# Permanent NAS mount (/etc/fstab) + raw data sync timer (systemd),
# settings from sync.conf (same directory)
#
#    sudo bash nas_sync/install.sh          # mount + sync timer
#    sudo bash nas_sync/install.sh mount    # mount only
#    sudo bash nas_sync/install.sh sync     # sync timer only
#
# Rerun after changing [timer] or [mount] settings in sync.conf
set -eu
WHAT=${1:-all}
[ "$(id -u)" -eq 0 ] || { echo "Run with sudo"; exit 1; }

HERE=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=sync.conf
source "$HERE/sync.conf"
NAS_MOUNT=${NAS_MOUNT%/}

RUN_USER=${SUDO_USER:-ldmdaq}
HOME_DIR=$(getent passwd "$RUN_USER" | cut -d: -f6)
RUN_UID=$(id -u "$RUN_USER")
RUN_GID=$(id -g "$RUN_USER")
CRED=$HOME_DIR/.smbcredentials

install_mount() {
    # credentials file (readable by owner only)
    if [ ! -f "$CRED" ]; then
        read -rp "NAS username [$NAS_USER]: " nas_user
        IFS= read -rsp "NAS password: " nas_pass; echo
        (umask 077; printf 'username=%s\npassword=%s\n' \
             "${nas_user:-$NAS_USER}" "$nas_pass" > "$CRED")
        chown "$RUN_USER": "$CRED"
        echo "Created $CRED"
    else
        echo "Using existing $CRED (edit it to change username/password)"
    fi
    chmod 600 "$CRED"

    # fstab: comment out old entries for this mount point, add new one
    # _netdev: wait for network; nofail: boot even if NAS is down
    local line="$NAS_SHARE $NAS_MOUNT cifs credentials=$CRED,uid=$RUN_UID,gid=$RUN_GID,file_mode=0664,dir_mode=0775,_netdev,nofail,x-systemd.mount-timeout=30 0 0"
    if grep -qxF "$line" /etc/fstab; then
        echo "/etc/fstab already up to date"
    else
        local backup=/etc/fstab.bak.$(date +%Y%m%d_%H%M%S)
        cp /etc/fstab "$backup"
        awk -v m="$NAS_MOUNT" \
            '$1 !~ /^#/ && $2 == m {print "# replaced by nas_sync/install.sh: " $0; next} {print}' \
            "$backup" > /etc/fstab
        echo "$line" >> /etc/fstab
        echo "Updated /etc/fstab (backup: $backup)"
    fi
    mkdir -p "$NAS_MOUNT"
    rm -f /var/lib/raw-data-sync/auth_failures   # wrong password guard
    systemctl daemon-reload

    if mountpoint -q "$NAS_MOUNT"; then
        echo "$NAS_MOUNT already mounted. To use the new fstab entry now"
        echo "(nothing writing to the NAS, Files windows closed):"
        echo "   sudo umount $NAS_MOUNT && sudo mount $NAS_MOUNT"
    else
        mount "$NAS_MOUNT"
        echo "Mounted $NAS_MOUNT"
    fi
}

install_sync() {
    # check time spans before installing
    for span in "$SYNC_INTERVAL" "$BOOT_DELAY"; do
        systemd-analyze timespan "$span" > /dev/null ||
            { echo "ERROR: invalid time span \"$span\" in sync.conf"; exit 1; }
    done
    chmod +x "$HERE/sync_raw_data.sh" "$HERE/mount_check.sh" "$HERE/notify.sh"
    sed -e "s#@SCRIPT@#$HERE/sync_raw_data.sh#" -e "s#@USER@#$RUN_USER#" \
        -e "s#@MOUNT_CHECK@#$HERE/mount_check.sh#" \
        -e "s#@NOTIFY@#$HERE/notify.sh#" "$HERE/raw-data-sync.service" \
        > /etc/systemd/system/raw-data-sync.service
    sed -e "s#@SYNC_INTERVAL@#$SYNC_INTERVAL#g" \
        -e "s#@BOOT_DELAY@#$BOOT_DELAY#g" "$HERE/raw-data-sync.timer" \
        > /etc/systemd/system/raw-data-sync.timer
    systemctl daemon-reload
    # restart: apply changed timer settings (a running sync is not stopped)
    systemctl enable raw-data-sync.timer
    systemctl restart raw-data-sync.timer
    echo "Sync timer: every $SYNC_INTERVAL (first $BOOT_DELAY after boot)"
    systemctl list-timers raw-data-sync.timer --no-pager
}

case "$WHAT" in
    all)   install_mount; install_sync ;;
    mount) install_mount ;;
    sync)  install_sync ;;
    *)     echo "Usage: sudo bash $0 [all|mount|sync]"; exit 1 ;;
esac
