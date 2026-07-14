---
title: Data, backup, and restore
description: OpenAI4S data-directory contents, consistent backup procedure, restore validation, and portability boundaries.
canonical: true
last_verified: 2026-07-14
verification: code-and-tests
status: current
audience: [operators, contributors]
verified_commit: a92e736
owner: OpenAI4S maintainers
---

# Data, backup, and restore

An OpenAI4S instance is more than its SQLite database. Durable state is split between `openai4s.db` and files under `OPENAI4S_DATA_DIR`. A recoverable backup must preserve the **entire directory at one stopped point in time**.

The default directory is `~/.openai4s`; a supervised installation should set an explicit path such as `/var/lib/openai4s`. Only one daemon may own that directory at a time.

## Data classification

Treat the whole directory as confidential research data. Depending on use it can contain:

| Location | Contents |
|---|---|
| `openai4s.db` | Sessions, messages, Action Ledger, Cell records, settings and saved model profiles, connector configuration, permission state, Artifact metadata, plans, reviews, memories, checkpoints, branches, and recovery records |
| `agent-workspaces/` | Live per-session and per-branch working files |
| `artifact-versions/` and `artifacts/` | Immutable or legacy Artifact snapshots |
| `workspace-cas/` | Content-addressed workspace blobs and trees referenced by checkpoints |
| `uploads/` | Uploaded files not yet confined to a session workspace |
| `user-skills/` and `dynamic-tools/` | User-authored executable recipes and session/project/global dynamic tool state |
| `session-imports/` | Validated files from imported portable Session packages |
| `compaction-history/`, `tool-results/`, and `logs/` | Historical context slices, tool material, and operational logs |
| `compute-jobs/` and `hpc/` | Command-created local working files and harvested remote-compute output, when used; local job metadata/output buffers themselves are process-memory state |
| `remote_compute.json` | Registered SSH hosts and remote capability metadata, but not SSH private keys |
| `openai4s_tape.json` | Optional replay material when recording is enabled |
| `openai4s.pid` and `daemon.json` | Ephemeral process metadata; harmless in an archive but stale after restore |

SQLite may contain saved API keys. Logs, messages, source Cells, tool output, files, and exports can also contain secrets or regulated research data even when dedicated credential fields are redacted. Apply retention, encryption, and access controls to the complete backup.

## State outside the data directory

A whole-directory backup still does not capture every dependency:

- the deployed code revision and release-specific virtual environment;
- a checkout-local `.env` or a supervisor environment file;
- operating-system keychains and the in-memory `host.credentials` vault;
- `~/.ssh/config`, SSH private keys, and ssh-agent state;
- conda environments and external R/Python installations;
- provider/cloud credentials, Docker images and containers;
- files intentionally left on remote compute hosts;
- the service user's default compute installation identity when it was not explicitly configured under the managed deployment.

Record the source revision, Python/R versions, selected environments, relevant non-secret configuration, and external service dependencies beside each backup. Store credentials in a separate secret-management system and test their reattachment rather than embedding plaintext in a backup manifest.

## File permissions

Set a private umask before first boot and keep the directory owned by the service account:

```bash
umask 077
chmod 0700 "$OPENAI4S_DATA_DIR"
chmod -R go-rwx "$OPENAI4S_DATA_DIR"
```

The application creates several files using the process umask and does not retroactively normalize every existing file. Recheck permissions after restore, manual file copies, and package/tool imports. Do not make the data directory the static web server's document root.

## Consistent instance backup

Stopping the daemon is the reference procedure. It closes kernels, the shared SQLite connection, and active HTTP threads, and prevents the database from drifting away from workspaces, Artifact snapshots, and the workspace CAS while files are copied.

1. Prevent new work and wait for or explicitly cancel active local/remote tasks.
2. Stop the daemon cleanly using the process supervisor or `openai4s stop` with the service's environment.
3. Confirm that the process is gone. Do not trust a stale pidfile alone.
4. Copy the complete data directory with a tool that preserves modes, ownership, timestamps, symlinks, and file names.
5. Hash and encrypt the resulting archive, then restart only after the snapshot has completed.

Representative Linux commands are:

```bash
sudo systemctl stop openai4s
if sudo systemctl is-active --quiet openai4s; then
  echo "OpenAI4S is still active; refusing to copy live state" >&2
  exit 1
fi

sudo install -d -m 0700 /var/backups/openai4s
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
sudo tar -C /var/lib -cpf "/var/backups/openai4s/data-${stamp}.tar" openai4s
sudo sha256sum "/var/backups/openai4s/data-${stamp}.tar" \
  > "/var/backups/openai4s/data-${stamp}.tar.sha256"

sudo systemctl start openai4s
```

Adapt the service and paths to the installation. Avoid placing the backup inside `OPENAI4S_DATA_DIR`, which would recursively include previous backups. Restrict archive and checksum ownership; the checksum protects integrity, not confidentiality.

### Why database-only and live copies are insufficient

An SQLite `.backup` can produce a consistent database image, but it does not atomically capture workspaces, immutable Artifact bytes, checkpoint CAS objects, Skill files, or remote output. Copying a live directory can therefore pair one database transaction with older or newer files. Filesystem snapshots may provide an acceptable hot-backup mechanism only when the operator has verified that the filesystem freezes the whole data directory atomically and has tested restores under write load.

## Backup verification

Verification should happen outside the live path:

1. Verify the archive hash before extraction.
2. Extract as the service user into a new private directory.
3. Check that paths stay under the restore root and that no unexpected ownership or group/other permissions were introduced.
4. Run SQLite integrity checking against the extracted database.
5. Start the matching code release on a different loopback port and the restored directory.
6. Inspect an old session, Notebook Cell, Artifact version, branch/checkpoint listing, user Skill, and permission state. Start Python/R only after passive data inspection succeeds.

The SQLite check uses only Python's standard library:

```bash
python -c \
  'import sqlite3,sys; db=sqlite3.connect(sys.argv[1]); print(db.execute("PRAGMA integrity_check").fetchone()[0])' \
  /restore/openai4s/openai4s.db
```

An `ok` result validates the database file, not its agreement with Artifact and CAS files; the isolated application check remains necessary.

## Full restore

1. Stop the daemon and preserve the current directory under a new name. Never restore over a running or partially copied tree.
2. Restore the archive into a new directory on the same local filesystem when possible.
3. Set ownership to the dedicated account and remove all group/other access.
4. Remove stale `openai4s.pid` and `daemon.json` only from the restored copy after confirming no process owns that instance.
5. Select the code release recorded with the backup. Do not first open the database with a newer release “to inspect it”; opening may apply forward migrations.
6. Start on loopback and validate read-only views before running new Cells or remote jobs.
7. Keep the replaced directory until the restored instance has passed a meaningful workload and a new backup.

There is no general database down-migration contract. For an application rollback, restore the data snapshot taken before the upgrade together with the previous code release.

## Retention and deletion

Deleting a Session or project invokes reference-aware cleanup for database rows, owned workspaces, version snapshots, dynamic state, imports, and unreferenced CAS objects. It is not guaranteed secure erasure from SSDs, copy-on-write filesystems, backups, remote hosts, or external providers.

Define retention separately for:

- live instance data;
- encrypted instance backups;
- exported Session packages and Notebook/Artifact ZIPs;
- remote job directories and provider logs;
- revoked credentials and audit evidence.

## Portability features are not instance backups

- **Artifact version restore** verifies an old snapshot and appends a new current version. It does not restore the database or a Session namespace.
- **Checkpoint/recovery actions** can rebuild selected workspace and runtime state. Arbitrary in-memory Python/R objects are not a backup contract.
- **Notebook export** preserves a view of Cells, not Host RPC, permissions, durable ledger state, or live namespace identity.
- **Session package export** is a deterministic, secret-scrubbed interchange format. Import deliberately creates new identities, downgrades authority, and opens ended/view-only in quarantine until a confirmed fresh restart. It is useful for sharing and inspection, not a byte-for-byte instance restore.
