# Update system plan

**Status:** plan only, Nov 12, 2026. Nothing here is built yet.

## Today

- **The Mac:** someone pulls the code over SSH, rebuilds, and restarts the app by hand.
- **Each Pi:** someone reruns `scripts/deploy-pi.sh` for it. That needs SSH, rebuilds everything and reboots the Pi.
- **No version check.** Nothing tells us which version each device runs, and nothing stops a bad update.

What happened on Nov 12 shows the risk. `main` dropped the old database migration. The Mac's restart failed on its own database, and the app was down until it was rolled back by hand.

## Goals

1. **One action updates everything.** The Mac updates itself, and the Pis follow the Mac.
2. **Never mid-scan.** No update while a scan, undo or print is in progress.
3. **Back up before every Mac update.**
4. **Roll back on its own if the new version doesn't come up healthy,** and say so on System health.
5. **Show what each device runs.** System health lists each one's version, so a stale device is visible.
6. **Only reviewed code.** Only commits merged to `main` and tagged as a release, never an unmerged branch.

## Design

### Versions

- Each release is a git tag, such as `v2026.11.12-1`.
- A build writes the version and commit into the Mac app and the Pi package (`station-package.json` already exists for the Pi).
- `GET /api/version` returns the Mac's version, plus the version and SHA-256 of the Pi package it's serving.

### The Mac updates itself: `scripts/update-host.sh`, later a button

1. Fetch the release tag; refuse anything that isn't a tag on `main`.
2. **Back up** to `~/fabric-backups/before-<tag>.zip`.
3. Build the new version into a **separate folder** (`releases/<tag>`), while the current version keeps running.
4. **Check the database migration first.** Compare the new version's migrations with the database's current revision. If there's no upgrade path, stop before anything is touched. That's exactly the Nov 12 failure.
5. Stop the app, point `current` at the new release, start it.
6. Wait for `/api/health` to report `ok` (up to 60 s). If it doesn't, point `current` back at the old release, start it, and record "update failed, rolled back" for System health.

A **System health → Update** button runs the same script. The button only appears when a newer tag exists. A login item can check for new tags once a day, but should only *notify*, never install on its own.

### The Pis follow the Mac (pull, not push)

The station agent already reports to the Mac every 30 s. It gains an update step:

1. It reads `/api/version`. If the package version differs from its own, it downloads `/api/station/package.tar` from the Mac through the same nginx proxy.
2. It **checks the SHA-256** against `/api/version`. A mismatch means no install.
3. It unpacks to `/opt/fabric-scanner/releases/<version>/`, alongside the running copy.
4. It **waits until the kiosk is idle**: no pending scan or undo, and no input for 2 minutes. The kiosk page already knows this state; it would expose it on a local endpoint.
5. It switches the `ui` symlink to the new release and restarts only the browser, with no reboot. Changes to nginx or system services still need a full deploy, flagged in the package.
6. If the page doesn't reach "ready" within 60 s, it switches back and reports the failure.
7. It reports its version in its 30 s health post, and System health shows each Pi's version.

So the Pis need no SSH to update, and they don't need internet access: they only talk to the Mac they already talk to.

### Security

- Pis download only from the Mac, with checksums. They never fetch from the internet.
- The Mac installs only tagged commits that are on `main`. Releasing is a deliberate step by a team member.
- The update endpoints change nothing by themselves; the device decides when to install.

## Build order

1. **Version stamps and `/api/version`,** plus versions shown on System health (small).
2. **`update-host.sh`** with a backup, separate release folders, the migration check and automatic rollback. This alone would have prevented the Nov 12 outage.
3. **The Pi agent update step,** with idle detection and rollback.
4. **The System health Update button** and the daily "new version available" notice.

## Not included

- Updating macOS or Raspberry Pi OS. Leave that to the OS's own updates, applied by hand on a quiet day.
- Updating nginx, systemd units or the NFC setup on the Pi automatically. Those still use `deploy-pi.sh`, and the Pi reports "needs full deploy".
