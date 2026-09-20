# Pre-upgrade database snapshot — design and acceptance preparation

[中文](pre-upgrade-snapshot-design_zh.md)

Status: **P2-01 preparation only**. This document fixes the proposed retention
policy and future acceptance cases. It does not enable snapshots, add a command
or setting, change schema 32, or change migration/restore behavior. The cases
below are requirements for a later implementation, not test results.

## Current boundary

[`run_migrations`](../openai4s/storage/migrations.py) checks integrity, uses
SQLite's backup API to create `<database>.v<version>.bak`, and applies numbered
migrations inside an explicit transaction. Failure retains the backup; success
attempts to remove it (unlink failure is ignored). `test_successful_migration_cleans_up_its_backup` continues to require
that cleanup. A current database takes the no-migration fast path, and
`FutureSchemaError` refuses a newer schema without downgrading its version.

The current backup is not a snapshot from before all application initialization:
[`Store.__init__`](../openai4s/store.py) executes and commits `_SCHEMA` before
calling `_migrate`. A future implementation must place the retained snapshot
before application DDL, seeds and data transforms, while preserving both schema
preflight checks. Reusing the present late hook alone would not meet this design.
SQLite replay of a hot rollback journal restores committed state and is distinct
from application migration; a copy must include committed WAL state as well.
`preflight_schema` returning `None` can mean either a missing file or a hot
journal that a read-only handle cannot recover; it is not permission to skip an
existing database. Do not use `immutable=1` or just the main-file header to bypass
WAL-aware schema checks. Recovery may change source bytes while preserving the
committed logical state. Raw forensic byte preservation, if required separately,
needs the complete stopped DB/journal/WAL file set before recovery.

[`security/permissions.py`](../openai4s/security/permissions.py) currently makes
permission hardening and directory fsync best-effort. Those helpers do not prove
the strict permission and publication guarantees proposed below. Native Windows
is currently refused by the platform gate; this design does not extend support.

## Retention policy

| Decision | Future contract |
| --- | --- |
| Trigger | Only an explicit upgrade of an existing, nonempty configured `openai4s.db` to a higher supported schema. First creation, current-schema opens and future-schema refusal do not create or replace a retained snapshot. |
| Writer ownership | Stop the daemon and all CLI/Jupyter/other processes that can write this data directory, then hold an exclusive upgrade ownership guard through publication and migration. The daemon pidfile alone is insufficient. Failure to establish ownership refuses the upgrade. |
| Location | `<data_dir>/backups/pre-upgrade/`; no workspace, artifact directory, public route, export package or automatic off-host copy. A relocation/symlink is not silently followed. |
| Layout | `current.json` selects one immutable `snapshot-<random-id>/` containing `database.sqlite3` and `manifest.json`. Candidate directories have an explicit `.pending-<random-id>` name on the same filesystem. These paths are proposed, not currently created by OpenAI4S. |
| Kept copy | At rest, one selected, verified most recent **pre-upgrade** snapshot, until the next verified replacement or explicit owner deletion. No age-based deletion and no post-upgrade copy substituted for it. A failed migration keeps the selected pre-upgrade copy. |
| Temporary copies | At most one old selected generation plus one new candidate/published generation during replacement. A crash may leave both; only `current.json` identifies the selected one. Cleanup must finish before starting another candidate. |
| Permissions | POSIX directories `0700`, files `0600`, owned by the upgrade account, from creation onward; reject unexpected symlinks, hard links, owner changes, broad ACLs or a writable parent boundary. Check temporary files and sidecars too. No temporary world-readable interval. |
| Unsupported storage | If owner-only access, same-filesystem atomic replacement or durable publication cannot be verified, refuse managed snapshot creation and leave the old copy intact. Report the unmet property; do not claim that best-effort chmod/fsync established it. Other platforms need separately verified ACL/durability support before admission. |
| Sensitivity | The entire SQLite image may contain research history, plaintext legacy credentials, authorization records and secret references. Do not redact it and still call it restorable. Keep manifests/receipts private; no credential values, row contents or database bytes in logs. |

The selected copy is a recovery option, not a retention archive or an encrypted
backup. Deleting an obsolete generation removes its pathname; it does not promise
secure erasure from SSDs, filesystem snapshots or external backups.

## Capacity and validation

The preparation policy sets a **4 GiB maximum per database image** and an **8 GiB
plus 1 MiB maximum managed snapshot directory** during replacement. Each manifest
is limited to 256 KiB and each pointer to 4 KiB; count old/new manifests and
temporary pointers within the aggregate 1 MiB metadata limit. These are future policy
values, not new runtime configuration. A larger database requires a separately
reviewed policy; the upgrade must not delete its old valid copy to make room.

After writers are stopped and committed state recovered, let `S` be SQLite
`page_count * page_size` and `R = max(64 MiB, ceil(S / 10))`. Preflight requires
`S <= 4 GiB`, free bytes at least `S + R`, and capacity for the existing selected
image, the new image and bounded metadata within the managed limit. Count stale
candidates in actual usage; do not ignore them or unrelated unexpected files.
Check space and actual written bytes throughout copying, since a preflight is
not a reservation. ENOSPC, quota exhaustion or growth over the cap aborts the
candidate and leaves the old selection and source committed data intact.

Existing `<database>.vN.bak` files outside this managed directory are separate
legacy failure backups. Inventory their occupied space in the filesystem free
space check, report them separately, and never silently delete or adopt them to
satisfy this policy. A future integration must reuse the verified pre-upgrade
image for the numbered migration's backup obligation instead of creating another
unbudgeted `.bak` copy. This changes no current backup behavior in this release.

This reserves snapshot headroom only. Space required by the subsequent migration
and its rollback journal must be assessed separately for that migration. `R` is
not a guarantee that arbitrary DDL/data expansion will fit. The local acceptance
fixture injects byte/free-space limits; it need not allocate gigabytes in CI.

Build the image through SQLite's backup API into the private candidate, never
by copying an open main DB file and omitting its WAL/journal. Close the destination
connection and verify it independently in read-only mode. Require all of:

- A self-contained regular image with no required WAL, SHM or hot journal;
  complete `PRAGMA integrity_check` returns exactly the successful result.
- `PRAGMA user_version` equals the source version captured for this upgrade,
  not the intended target version. Preserve and compare existing migration
  records; an unversioned legacy database may legitimately have no such table.
- Actual byte length and streaming SHA-256 match the bounded manifest; re-open
  and re-hash after publication. Checksums detect changed bytes, not authenticity
  against someone able to replace both the image and its private manifest.
- Manifest format version, snapshot ID, UTC creation time, source application
  build, source/target schema versions, relative database name, byte length,
  checksum and validation outcome are present and structurally valid. Any
  unavailable source-build identity is explicitly unknown, never invented.
  The pointer contains only the selected generation ID and manifest checksum;
  both filenames and relative paths reject traversal and ambiguous identities.

## Publish, fail and resume

1. Establish ownership and supported-schema state before application writes.
   Verify any old selected image and reconcile bounded leftovers. An invalid
   pointer or old image is an explicit recovery issue, not an empty history.
2. Preflight permissions and capacity, create one private candidate, back it up,
   close SQLite and perform the independent checks above. The old selection
   remains untouched throughout these steps.
3. Fsync the image and manifest, then the candidate directory containing their
   names. Publish the immutable generation by atomic rename and fsync the managed
   parent directory. Reopen and verify it before preparing the new bounded pointer.
4. Flush the new pointer, atomically replace `current.json` and durably persist
   the parent directory entry. Re-read the selection and verify the selected
   manifest/image. Only then is the new snapshot considered committed. Any error
   after pointer replacement is an **uncertain publication**: the pointer may
   already name the new generation. Preserve both generations, stop before
   migration and require exclusive recovery; do not claim the old pointer is
   unchanged or silently roll it back.
5. Delete the superseded generation only after step 4 has succeeded. If cleanup
   fails, keep the old files, record cleanup pending, and refuse another candidate
   until the unselected old generation is removed and only one generation remains.
   Never delete the selected
   image. An interrupted publication does not authorize an unverified cleanup.
6. Only after successful snapshot publication may the application upgrade start.
   Retain the snapshot whether migration commits or rolls back. Keep the existing
   transactional migration and future-schema protections; do not copy a backup
   over a database with an open connection.

On restart, inspect names, identities, pointer, checksums and ownership while the
exclusive guard is held. Before pointer publication the old selected generation
remains authoritative; after durable publication the new one does. A fully
verified but unselected candidate is not chosen merely because it has the newest
mtime. Reconcile it explicitly or discard only that unselected candidate. A
power-loss case with uncertain publication durability requires verification
before either generation can be removed. If the selected generation validates,
complete its durable publication; otherwise report the invalid/uncertain selection
and require an explicit repair selecting a separately verified generation. Do not
resume migration or infer an old pointer from directory mtimes. Repeated attempts must not accumulate
snapshots or overwrite the only valid copy at a deterministic `.bak` path.

## Independent-directory restore contract

Restoration is an operator-controlled, offline operation. It is not automatic
migration rollback and does not enable an in-place restore command in this work.

1. Stop every writer to the source installation; retain that directory and the
   selected snapshot unchanged. Record the snapshot/build/schema identity and
   inspect the private copy's integrity, length, checksum and permissions.
2. Create a fresh sibling data directory owned only by the operator, on storage
   with sufficient capacity. Reject an existing/nonempty target, a target under
   the source directory, symlinked targets or any target aliasing the source.
   Copy the closed, self-contained snapshot to a new `openai4s.db`; never merge
   it with target WAL/journal files or copy source pidfiles/access tokens.
3. Before starting any application, use read-only SQLite to verify the restored
   image again and compare known fixture rows/migration records in acceptance
   tests. Open no `Store`: its constructor can initialize and migrate a database.
   The immutable selected snapshot must remain byte-identical.
4. Keep the restored directory offline for inspection. A separate application
   trial uses a compatible program in a disposable **synthetic-data** directory
   with outbound networking denied, no inherited credentials and no active
   scheduler, relay, kernel, compute poller or automatic recovery. This design
   does not assume a current universal safe-restore startup flag exists. Real
   restored data must not be served until absolute paths, identities, stored
   authorization and job state have been reviewed independently.
5. A future operator decision to activate a restored installation must reconcile
   credentials/authorization and jobs against current external facts. Preserve
   recorded facts; never automatically replay a Cell, retry a tool, submit/cancel
   remote work or revive a prior approval merely because the DB says it existed.
   A program that rejects a newer schema remains rejected: never lower
   `user_version`, delete migration records or bypass `FutureSchemaError`.

The DB contains metadata and references. It does **not** contain the complete
workspace, immutable artifact bytes/CAS, Python/R memory, environments and
packages, keychain/env-injected secrets, remote jobs or external side effects.
Some file references are absolute. A separate data directory alone does not
isolate those references or turn an old job record into current remote truth.
Missing artifacts/environments/credentials remain unavailable; old links must
not silently resolve to unrelated newer files. DB-only restore is not complete
workspace recovery, a credential rollback or a one-click application downgrade.

## Future local acceptance matrix

All cases below remain **planned**, using synthetic data and isolated temporary
directories. Extend the existing migration tests and a local restore fixture;
do not require real user data, live model calls or a new CI service.

| ID | Controlled case | Required observable result |
| --- | --- | --- |
| S01 | New DB, current schema, future schema | No retained snapshot replacement; future schema refuses before application writes, version unchanged. |
| S02 | Older real schema, including a table added by current `_SCHEMA` | Selected snapshot matches the pre-initialization source, lacks the later table, and contains source rows/migration records; migration success retains it. |
| S03 | Committed WAL and crash-created hot rollback journal | Backup includes committed rows and excludes rolled-back rows; `None` preflight does not skip an existing DB; read-only restored image is self-contained and passes full integrity checks. Distinguish logical preservation from source recovery byte changes. |
| S04 | Before pointer replacement: existing valid snapshot plus injected ENOSPC, byte cap, permission, copy, integrity or hash failure | Old selection/hash/bytes unchanged, no migration begins, bounded private candidate cleanup; explicit failure. |
| S05 | Kill at each copy/flush/rename/pointer/cleanup boundary; validation or fsync failure after pointer replacement | Keep both generations on uncertain publication; no migration or deletion until exclusive recovery verifies the selected generation or explicitly repairs the selection. Never delete the sole valid copy or guess by mtime; respect the two-generation bound. |
| S06 | New verified snapshot then failing migration | Live DB rolls back, selected pre-upgrade image survives; no copy over an open DB and no automatic restore/replay. |
| S07 | Competing upgrade processes and abandoned ownership | At most one publisher/migrator; stale pid alone grants no ownership; loser changes no DB or snapshot identity. |
| S08 | Broad mode/ACL, symlink, hard link, owner change, unsupported durability | Creation/publication refuses before exposure or migration; old valid snapshot remains. POSIX/macOS/Linux tested separately; unsupported platforms remain unverified. |
| S09 | Restore into fresh directory, then mutate the disposable restored copy | Expected rows/schema/hash initially match; selected image and original directory remain unchanged. Existing/nonempty/aliased targets refuse. |
| S10 | Corrupt/truncated image, bad pointer/manifest, missing migration table on valid legacy DB | Corruption and identity mismatch refuse; the legitimate version-0 format is handled explicitly without fabricating migration records. |
| S11 | Missing CAS/workspace/env/secrets plus recorded remote or recovery work | Read-only inspection reports the missing boundary; fixture observes zero Cell/tool execution, network calls, job resubmission or approval revival. |
| S12 | Capacity boundary, legacy `.bak` files and repeated upgrade/restart cycles | Preflight and actual-write cap both enforced; old copy is never evicted to fit a candidate; legacy backups reported and not automatically deleted; at rest one selection, at most two generations while recovering. |

Review completion for this preparation means the policy, failure states and
observable matrix are agreed and existing migration behavior still passes its
tests. Future runtime completion requires executing S01–S12 on the actual
implementation and documenting filesystem/platform limits; this document alone
does not satisfy that later gate.
