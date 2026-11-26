# NAS mount and raw data sync

Auto-mount `/mnt/synology` on every boot and start copying data
`/home/ldmdaq/tes_tools/data/run*/raw` to `/mnt/synology/Data/run*/raw`
every 2 minutes. 

Paths/timing is configurable in `sync.conf`.

| File                    | Purpose                                     |
|-------------------------|---------------------------------------------|
| `sync.conf`             | **all settings** (paths, timing, NAS share) |
| `install.sh`            | installs the mount (fstab) and the timer    |
| `sync_raw_data.sh`      | Non-user copy script (run by timer)         |
| `raw-data-sync.service` | systemd templates, filled from `sync.conf`  |
| `raw-data-sync.timer`   | by `install.sh` (no need to edit)           |

## Install

Edit `sync.conf`, then:

    sudo bash nas_sync/install.sh          # mount + sync timer
    sudo bash nas_sync/install.sh mount    # mount only
    sudo bash nas_sync/install.sh sync     # sync timer only

`mount` asks for the NAS username/password and stores it in `~/.smbcredentials`. 

## Settings (`sync.conf`)

| Setting         | Default                                   | Apply with                             |
|-----------------|-------------------------------------------|----------------------------------------|
| `SOURCE_DIR`    | `/home/ldmdaq/tes_tools/data`             | nothing (next sync)                    |
| `DEST_DIR`      | `/mnt/synology/Data` (inside `NAS_MOUNT`) | nothing (next sync)                    |
| `SYNC_PATTERN`  | `"run*/raw"` (relative to `SOURCE_DIR`)   | nothing (next sync)                    |
| `MIN_AGE`       | `2` (minutes, skip files being written)   | nothing (next sync)                    |
| `SYNC_INTERVAL` | `10min`                                   | `sudo bash nas_sync/install.sh sync`   |
| `BOOT_DELAY`    | `5min` (first sync after boot)            | `sudo bash nas_sync/install.sh sync`   |
| `NAS_SHARE`     | `//172.28.197.210/Phase_0_CryoLab_Data`   | `sudo bash nas_sync/install.sh mount`  |
| `NAS_MOUNT`     | `/mnt/synology`                           | `sudo bash nas_sync/install.sh` (both) |
| `NAS_USER`      | `kcl-admin` (default username prompt)     | only used when creating credentials    |

Rerunning `install.sh` does not affect syncs that are in-progress

To change username/password: edit `~/.smbcredentials`, then `sudo umount /mnt/synology && sudo mount /mnt/synology`.

## Use

    systemctl list-timers raw-data-sync       # next sync
    journalctl -u raw-data-sync -f            # what each sync copied
    sudo systemctl start raw-data-sync        # sync now
    nas_sync/sync_raw_data.sh -n              # dry run (what would be copied)
    sudo systemctl disable --now raw-data-sync.timer   # stop syncing

## Notes

- Files modified in last `MIN_AGE` minutes due to data writing are skipped
- If the NAS is not mounted, syncs will try to mount it. if it fails,
- FAQ: `umount: target is busy`: close Files windows on the NAS with `nautilus -q` and `fuser -vm /mnt/synology` to list other users and tell them to close stuff
- `mount error(13): Permission denied`: wrong username/password in `~/.smbcredentials`. Synology blocks an IP after repeated failed logins so don't do this too much
- Edit the files in this directory, not the installed copies in
  `/etc/systemd/system` (overwritten by `install.sh`).
- Local data is never deleted: check the NAS copy before deleting runs.

## Failure

- If the password/username are wrong > 3 times it will stop trying to avoid getting locked out of the box
- If the mount is not syncing it will produce desktop notifications saying how long it has been failing for