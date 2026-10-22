# Backups and recovery

The staff app's Backups page manages portable database checkpoints. Each export is a ZIP containing a JSON snapshot and checksum manifest. It is intended for records recovery and moving a dataset to another compatible installation; it contains no executable SQL.

## Create and retain checkpoints

Select **Create backup now**, then **Download** to keep a copy away from the host. The app reads all database tables at one PostgreSQL repeatable-read snapshot, writes the archive locally, and records its SHA-256 digest. A download is checked against that digest before it is served.

Daily snapshots default to **2 a.m. America/New_York**, enabled, with **30 days** of local retention. The scheduler checks roughly once a minute while the API is running. If the Mac was unavailable at the scheduled hour, it creates the day's checkpoint after the host resumes at or after that hour. It does not wake a sleeping or powered-off Mac. DST is handled using the named Eastern time zone.

The Backups page can change the hour, enable/disable automatic snapshots, set retention in days, and configure an additional destination folder. A successful manual checkpoint also counts as that day's backup. Retention is based on age, not a fixed number of copies; newly created and retained recent archives are not removed simply because many manual checkpoints exist.

The app always keeps its local checkpoint under the host state directory, separated by database name:

    ~/Library/Application Support/Fabric Conditioning/backups/DATABASE_NAME/

A system installation uses /Library/Application Support/Fabric Conditioning instead. Archives and local metadata are private to the service account. Do not put the live PostgreSQL data directory on a network share or in a synchronization folder.

For a command-line export of the same checked ZIP while the host is running:

    python3 deploy/macos/host.py backup /absolute/path/checkpoint.zip

The destination must not already exist. This native command downloads the app checkpoint format; it is not a SQL dump or a database-reset command.

## Additional copy destination

Enter an absolute folder path on the host Mac, such as a mounted lab share or an existing locally synchronized SharePoint/OneDrive directory. After creating its local archive, the app copies to that destination and verifies the copied bytes before replacing the destination's temporary file.

**Copied to destination means a local filesystem copy succeeded.** It does not prove that a synchronization provider uploaded it, that a remote server retained it, or that it is available from another computer. Microsoft 365 login, tenant permissions, sync configuration, and cloud verification are separate deployment work.

The destination must be writable by the account running the Mac service. A mounted network share or OneDrive process may not be available before desktop login. Keep a local destination available for pre-login snapshots, then synchronize exported checkpoints when the account/session permits it. A failed additional copy leaves the local checkpoint intact and is shown as a copy failure. Local retention cleanup is skipped on that failed copy run. Automatic deletion of old files in an additional destination is not implemented; set its retention separately.

## What is in the archive

The archive includes the registered projects, materials, treatments, stacks, specimens, conditioning records, original audit events and amendments, prep-draft rows/provenance, movement-request results, room/station records, and recorded print-job state. Original uploaded workbooks are not embedded as files; only the values/provenance stored in the database are included.

It does not include Mac service credentials/configuration, Python/runtime files, compiled application code, printer queues/drivers, backup settings, network mounts, or Microsoft 365 account setup. Configure those on a fresh host separately. Restore requires a compatible application/schema; the validator rejects unsupported archive formats, table/column mismatches, invalid counts, corrupted snapshots, and data that violates database constraints.

ZIP is a container, not encryption. Its SHA-256 checks detect corruption; they do not authenticate who created an archive. Store and transfer checkpoints using the lab's chosen access controls.

## Validate and restore

1. Open **Backups → Import & restore → Validate backup archive** and choose an exported ZIP.
2. The app checks the manifest and snapshot digest, then loads the records into a new isolated staging database. Uploading and validating do not change the active dataset.
3. Review the checkpoint date and table counts. Validation expires after one day; re-import the archive if needed.
4. Type **RESTORE** and select **Restore backup**. This explicitly replaces the active dataset with the validated copy.
5. Refresh staff pages and let scanner stations reconcile their state before resuming work. Check specimen IDs, counts, ENTRY/EXIT values, and representative audit events.

During the final switch, the API drains in-flight traffic and rejects new work until replacement finishes. It creates a pre-restore checkpoint and retains the previous complete database under a separate generated name. The previous database is not deleted by this flow. Recovery of that retained database is a maintenance operation rather than another automatic switch.

Restored records represent the checkpoint's point in time. Movements recorded after that point are not present in the restored dataset; reconcile physical specimen locations before routine scanning resumes. Printer-job history is restored as data and is not an instruction to print those jobs again.

If validation fails, the active database remains unchanged. If the additional destination fails, recover from a verified local or downloaded copy. Keep at least one copy off the host and perform a restore rehearsal on a fresh disposable installation before relying on unattended recovery.

## Verification boundaries

The backend tests exercise archive integrity, validation in a staging database, explicit RESTORE confirmation, preserved audit records, and the retained previous database. A real cold-start recovery drill, lab-share availability, SharePoint upload confirmation, and restore on a second physical Mac remain deployment acceptance checks.
