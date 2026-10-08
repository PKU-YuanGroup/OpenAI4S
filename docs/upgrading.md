# Upgrading

[中文说明](upgrading_zh.md)

An install that is already on 0.3.0 reads the next section before the first
start of the next release. A database still on 0.2.x is migrated by the next
release straight to schema 35 in one open: read the later section for the
steps up to schema 32 and the next section for 33 through 35.

## Upgrading to the next release (schema 32 → 35)

Schema 33 rewrites judge audit rows that are already stored, and a successful
migration then deletes its own pre-upgrade copy. That copy is not a way back
to the previous release. Copy the data directory yourself before you upgrade.

### Stop the daemon and copy the data directory

Stop the daemon before you install or start the next release. Run
`openai4s stop`, or quit the app, and check with `openai4s status` that no
daemon is still running. A process still running a build that has
`host.judge` but not this release's audit projection, such as a source
checkout of `main` after 0.3.0 (it still reports 0.3.0), can store a `judge`
row with the original arguments after this upgrade has committed, and step 33
does not run again. The published 0.3.0 package has no `host.judge` and writes
no `judge` rows. The committed `user_version` is 35.

Then copy the whole data directory while nothing is running. It holds
artifacts, logs, and the access token, as well as the database. Include any
`openai4s.db-wal` or `openai4s.db-journal` file beside `openai4s.db`:

```bash
cp -a ~/.openai4s ~/.openai4s-before-next
```

The data directory is `~/.openai4s` unless `OPENAI4S_DATA_DIR` names another
one.

The container image keeps its data at `/data` (`OPENAI4S_DATA_DIR=/data`).
That is the `openai4s-data` named volume in `compose.yaml`, or the
`openai4s-data` PersistentVolumeClaim in `deploy/kubernetes.yaml`. Stop the
container, or scale the Deployment to zero, and back up the volume or the PVC
before you start the next image.

### What schemas 33 through 35 change

The first command of the next release that opens the database migrates
`<data_dir>/openai4s.db` from schema 32 to schema **35**. Starting the daemon
or running `openai4s run` both do this. `openai4s doctor` and
`openai4s diagnostics` still only read the schema version. The upgrade
commits the new schema in one transaction. A failure rolls the database back
to the schema it had.

The copy beside the database is named for the schema being left. A database
at schema 32 is copied to `openai4s.db.v32.bak`. A successful migration
deletes `openai4s.db.v32.bak`. A database still below schema 32 uses the name
in the 0.2.x section (`openai4s.db.v27.bak` when it is leaving schema 27) and
is migrated through to the new schema in that same open. On failure the
database stays at the schema it had, the copy is kept, and the command stops
with one `error:` line that names the copy. `openai4s serve` and
`openai4s run` then exit status 2. Re-running the upgrade is safe.

The copy holds the original judge text. After a successful upgrade it is
gone. Keep the directory copy from the previous section if you may need the
old data.

| Version | Change |
| --- | --- |
| 33 | `redact_judge_host_call_args`: rewrite stored `judge` rows in `host_call_log` |
| 34 | `background_exec_receipts`: add the table; existing rows stay as they are |
| 35 | `lab_ledger`: add six session-scoped Lab tables and indexes |

Schema 35 (`lab_ledger`) only adds six `lab_*` tables and their indexes;
existing rows stay unchanged. All six tables are in `QUERY_DENYLIST`, so
agent SQL cannot read them. Their rows are deleted with the session.

During the rewrite the connection sets `PRAGMA secure_delete = ON`, then
restores the previous mode by name (`OFF`, `ON`, or `FAST`) before the
transaction commits. With `secure_delete` on, SQLite overwrites the bytes
that the `UPDATE` replaces with zeros.

A preview that is already byte for byte what a new write stores is left
unchanged, including `<invalid template>`, `<unknown template>`, and a params
marker. A preview that names a template id counts as one only while the
judgment registry still resolves that id; otherwise it is treated as raw,
becomes `<unknown template>`, and loses its params marker. A
raw preview that begins with a template id the judgment registry can resolve
keeps that id, replaces the state with `<redacted judge state>`, and drops
params. A raw preview that begins with an id matching
`^[A-Za-z0-9_.:-]{1,100}$` which the registry does not resolve becomes
`<unknown template>`. Every other raw preview becomes the state-only marker
`[{"state": "<redacted judge state>"}]`.

Bytes from a row deleted before the upgrade can remain in free pages. This
migration does not read those pages. Stop the daemon and run `VACUUM` when
those leftover bytes have to be gone:

```bash
sqlite3 ~/.openai4s/openai4s.db 'VACUUM'
```

The container image has no `sqlite3` command. With the service stopped, run
`VACUUM` on `/data/openai4s.db` through Python's `sqlite3` module from a
one-off container that mounts the same volume.

### Downgrade

Downgrade to the previous release is not supported.

A 0.3.0 daemon pointed at the upgraded database stops before it writes and
reports `future_schema` with both version numbers. That stop does not restore
the previous rows. 0.2.0 does not check the schema version, so pointed at
this database it opens it and writes. Stop the next release first. The way
back to the previous data is the directory copy you made before upgrading:

```bash
mv ~/.openai4s ~/.openai4s-next
cp -a ~/.openai4s-before-next ~/.openai4s
```

Anything created under the next release is absent from that copy. A
`.v32.bak` kept after a failed migration is the pre-migration file for that
failed attempt. It is the wrong file to keep as your rollback copy: a
successful migration deletes it, and until then it holds the unredacted judge
text.

### Egress allowlist

`OPENAI4S_EGRESS=allowlist` still keeps host-side `web_fetch`, `web_search`,
and the authorized `host.bash` preflight on the domain allowlist. A new
Python or R Cell is admitted only when that Cell's kernel has measured a raw
network block: `enforced` is true, `self_test_passed` is true, and
`network_policy` is `blocked`. Otherwise the Cell ends with
`egress_boundary_unavailable`.

`OPENAI4S_KERNEL_SANDBOX=auto` admits the Cell when the self-test passes and
still reports `network_policy=blocked`. `enforce` refuses to start the kernel
instead of degrading. A remote kernel reports `network_policy` `unproven` and
is refused for as long as allowlist stays on. The default
`OPENAI4S_EGRESS=off` does not add this gate. A Cell that is already running
is left alone; the mode is read again at the next Cell.

In an unprivileged container, bubblewrap cannot create its namespaces, so
`auto` degrades and allowlist refuses every new Python and R Cell. Admitting
Cells there means `OPENAI4S_EGRESS=off`, or a container privileged enough for
`enforce` to establish the boundary. See [docker.md](docker.md).

A Web Agent Cell carries `egress_boundary` on its result. The Notebook REPL
route returns only an `error` that starts with
`egress_boundary_unavailable:`. Inside the daemon, a Python bootstrap that
fails before the worker is published raises an error that starts with
`kernel bootstrap failed: egress_boundary_unavailable:`, and a new R worker's
refusal reaches the Cell service as
`R kernel unavailable: R kernel bootstrap failed: egress_boundary_unavailable:`.
While allowlist is on, both end the Cell as `egress_boundary_refused`.

These limits match [security.md](security.md) and stay for a later version:

- Lifecycle entry points do not project `egress_boundary_refused`.
  `start_kernel` and `set_env` answer a boundary refusal with a generic
  HTTP 500. `restart_kernel` on a live worker reports success, and the next
  Cell is refused. A pending environment change applied at the start of an
  Agent turn fails that turn.
- Recovery replay, the Jupyter bridge, and benchmark steps let
  `EgressBoundaryUnavailable` propagate as an ordinary error.
- A cluster allocation is released the first time a Cell is refused for this
  boundary.
- A delegated sub-agent inside the Web daemon may write the refusal line to
  stderr.
- The gate runs once per Cell. Code the interpreter runs while the worker
  process starts, including `.pth` files and `sitecustomize`, is outside the
  Cell admission check.

### Access token in logs and launchers

Startup logs and container logs omit the access token. The listening line,
the already-running notice, and daemon logs print the plain origin. Under
that line, single-user mode tells you to run `openai4s url`, and team mode
prints `/login`.

In single-user mode, `openai4s url` prints the sign-in URL. That URL carries
`?token=`. Team mode prints `/login`. Inside a container, run
`docker exec <container> openai4s url`. A script that used to read the token
out of the log has to call `openai4s url` instead.

`openai4s serve` still opens a local browser unless `OPENAI4S_NO_OPEN` is
set or `--no-open` is passed. Single-user mode opens the token URL. Team mode
opens `/login`.

When the desktop app is opened again while something already listens on its
port, it opens the sign-in URL from `openai4s url` (the token URL in
single-user mode, `/login` in team mode) only when `openai4s status` verifies
this data directory's daemon (pidfile, process start token, and `/health`).
Otherwise it opens the bare origin.

### Judge audit rows

New `host_call_log` rows with `method="judge"` are projected by
`HostCallRepository.log` before they are stored. A template id is kept only
when the judgment registry resolves it. A string that matches
`^[A-Za-z0-9_.:-]{1,100}$` and is not registered is stored as
`<unknown template>`. Any other template value, including a missing one, is
stored as `<invalid template>`. State is always `<redacted judge state>`. When the
call carried params, those are `<redacted judge params>`. Arguments that are
not a one-element list of an object store only the state marker.

`result_preview` is unchanged. A soft-fail error result is stored with no
`result_digest`: the error text can repeat the caller's template id or
params, and a short id can be recovered by trying SHA-256 candidates. Other
results still store a digest.

The original text can remain outside that row. The places listed in
[security.md](security.md) are the rollback journal or a WAL file, free
pages, a backup you took yourself, `openai4s_tape.json` written while
`OPENAI4S_RECORD_TAPE` is set, a session package exported before the upgrade,
and `openai4s.db.v32.bak` until a successful migration deletes it. This build
leaves `journal_mode` on the rollback journal. Stop the daemon and run
`VACUUM`, as in the schema section, when free-page bytes have to be gone.

### Background-cell receipts

A Web session's background cell writes a row to `background_exec_receipts`
before the worker starts. The row keeps the code's SHA-256 and character
count. The submitted source is not stored. A failed cell's stored traceback,
and the output it printed, can still quote lines of that source.

Stdout kept for one job is a head of at most 256 KiB. While the job prints,
that head is written at least once a second.

A cleanup pass runs the first time a session runtime in a daemon process
uses background execution (`exec_background`, `exec_peek`, `exec_list`, or
`exec_interrupt`), and again after a terminal receipt is written, at most once
every ten minutes in that process. Each pass first stores `outcome_unknown` on
rows that another daemon process left unfinished, with `ended_at` set to the
time of that pass; from then on they are terminal rows like any other. It
then clears the output of each owner's oldest terminal rows until that
owner's stored output is within 128 MiB, and deletes terminal rows that ended
more than seven days ago. The output quota is per owner: in team mode one
member's background jobs never clear another member's stored output. A pass never deletes an unfinished row. Between passes the table can
run over either limit.

A row that had not finished reads as `outcome_unknown` when another daemon
recorded it, which is the case after a restart, and also when this process
no longer holds the job. The daemon does not replay it and does not reattach
a worker that is still running.

A CLI job and a sub-agent job are not stored. Their result has
`persistent: false`. Web `exec_*` results include `persistent`. A result
read from the receipt also has `source: "receipt"`, `output_truncated`,
`code_sha256`, and `created_at`. When the receipt write failed, `exec_peek`
includes `receipt_degraded: true`. That key is present only in that case.

When `exec_interrupt` finds only the receipt, `interrupt_undelivered` and
`reason` are set to the same sentence: `the daemon has restarted; delivery
cannot be confirmed` when another daemon process recorded the job;
`another session runtime in this process holds this job; this session cannot
deliver the stop` when the job is still running elsewhere in this process;
otherwise `this process no longer has a handle for this job; delivery cannot
be confirmed`. When a live worker did not receive
the stop, `interrupt_undelivered` is that delivery's reason, or
`the stop request did not reach the worker`.

The table is on `QUERY_DENYLIST`. Deleting the session removes the rows. The
data directory is mode `0700` and the database file is mode `0600`.

### REST clients

`GET /frames/{fid}/delegations` changes shape. The child objects omit the
`result` and `output` keys. The keys are absent, not `null`: a client that
reads `child["result"]` or compares it with `=== null` has to test whether the
key exists. `artifact_refs` omit `durable_path`, and they omit an
absolute `path` or `filename`. When the stored result has evidence, the child
carries `artifact_evidence`, including `checked_at`. The key is absent when
the result has none.

`POST /frames/{fid}/delegations/{child}/stop` and
`POST /frames/{fid}/delegations/{child}/continue` return that same
projection. Continue returns the stored child, not this run's result
envelope.

Delegate step cards keep their keys, on the `step_update` WebSocket event
and on `GET /frames/{fid}/steps`. Their `raw` string now drops host paths
before it is truncated: absolute paths inside `environment` (the
interpreter, `env_root`), every `durable_path`, and an absolute `path` or
`filename`. The child's output text, completion bullets, and conclusion stay
as the child wrote them, so text that names a host path still carries it.
Steps stored before the upgrade are not rewritten.

`docs/response-schemas.json` was captured again from real responses.

### Workbench

The right dock adds a **Lab** tab for simulation-only extraction runs. Install
its optional CPython 3.10 provider with `openai4s lab setup chemgymrl`, inspect
`openai4s lab status`, then open a session's Lab tab. Agent/`host.lab` create and
execute calls default to approval; manual controls are direct user actions.
**End experiment** (`end_action`) and the safety **Stop** (`stopped`) are
distinct, and neither is the agent's Stop. Under **Results**, a run exports as
exact Artifact versions and replays read-only; simulation ground truth is only
ever a browser download, never stored in the session. Restarting the daemon
ends lost provider runs instead of replaying them. See [Lab](lab.md)
for installation, sensor limits and the optional upstream GPL license.

The control that branches from a user message is shown only when the session
capability `fork_from_message` is true. The workbench sets
`documentElement.dataset.forkFromMessage` to `on` or `off`, and the `.msg-fork`
rule shows the button from that flag. The request is
`POST /frames/{fid}/branches/fork` with exactly `{from_message_id}`. A message
with no checkpoint returns 409. The button stays unavailable and shows the
server's sentence; an empty message shows "Could not branch from this
question." The new branch has its own workspace and stays inactive until you
activate it.

`@` completion asks the project artifact index for one filename page (20
rows) and merges this session's files. Hidden artifacts, those with
`priority` below 0, stay out of the list. The popup shows at most 8. While
that search is in flight, Enter, Tab, and the send button wait for the page,
then complete the token that was in the composer when the key was pressed. A
request that runs past 8 seconds shows "Project file search failed. Showing
only this session's files." This session's files stay in the list.

The delegation evidence panel is a record-consistency check made when the
sub-agent finishes. It compares the version record, the checksum, the
snapshot file's presence and size, and the producing Cell. The host records
the producing Cell as the Cell it is executing when it reads the call,
provided the worker's own claim is absent or names that same Cell; a claim
that disagrees is recorded as `unattributed` and never verifies. It does not
authorize the child, and a later read shows the stored record (`checked_at`)
instead of running the check again while the panel is open. At most 12 items
are kept. When there are more, the panel says it is showing the first 12.

### Auto Mode

This release ships the specification only. The last section of
[auto-mode.md](auto-mode.md), "Workbench status surface (specification — not
shipped)", says: this section specifies how the default workbench will show
Auto Mode. It is not implemented. This version adds no control, no menu row,
and no call to `/auto-mode` or `/auto-audits`.

### Known limits left for a later version

- The Windows launcher warns that the URL carries no sign-in token when
  `openai4s url` has an empty query, which is what team mode's `/login`
  address looks like.
- The egress gate applies per Cell. `.pth` files and `sitecustomize` that run
  while the worker starts sit outside it, and `start_kernel`, `set_env`, and
  `restart_kernel` do not project `egress_boundary_refused`.
- Under `OPENAI4S_KERNEL_SANDBOX=enforce`, R has no automated test that can
  tell a raw-network block apart from the message an unsandboxed Rscript
  prints for a refused connection.

## Upgrading from 0.2.x to 0.3.0

Read this before the first 0.3.0 start. Two changes cannot be undone by
reinstalling the old version. 0.3.0 upgrades the database in place. It also
removes the switch that let a local daemon run without an access token.

### 1. Back up the database first

The first 0.3.0 command that opens the database migrates
`<data_dir>/openai4s.db` from schema **27** to schema **32**. Starting the
daemon or running `openai4s run` both do this. `openai4s doctor` does not: it
reads the schema version without opening the database for writing, reports
the pending upgrade as a warning (so it does not exit 0) and leaves the
database unchanged. `openai4s diagnostics` does not either: its bundle records
the pending upgrade in `report.json` and leaves the database unchanged.
The data directory is `~/.openai4s` unless `OPENAI4S_DATA_DIR` names another
one. A `pip` install, the Linux tarball and the macOS app all use that
default.

The migration copies the database to `openai4s.db.v27.bak` before it changes
anything. It keeps that copy if the migration fails, and **deletes it once the
migration succeeds**. After a normal upgrade, no copy of the 0.2.x database is
left.

If the migration fails, the database is rolled back and stays at schema 27, the
copy is kept, and the command stops with one `error:` line that names where the
copy is. `openai4s serve` (with or without `--detached`) and `openai4s run`
then exit with status 2. Until an upgrade succeeds, `openai4s doctor` fails its
data check (exit 2) and names the kept copy.

If you might want to go back to 0.2.x, make your own copy first:

1. Stop OpenAI4S: run `openai4s stop` or quit the app. Check with
   `openai4s status` that no daemon is still running.
2. Copy the whole data directory. It also holds your artifacts, logs and the
   access token, not only the database:

   ```bash
   cp -a ~/.openai4s ~/.openai4s-0.2-backup
   ```

   If the directory is too large to copy, copy at least `openai4s.db` together
   with any `openai4s.db-wal` or `openai4s.db-journal` file next to it. Copy
   them while nothing is running.
3. Install 0.3.0 and start it.

The container image keeps its data directory at `/data`
(`OPENAI4S_DATA_DIR=/data`). That is the `openai4s-data` named volume in
`compose.yaml` or the `openai4s-data` PersistentVolumeClaim in
`deploy/kubernetes.yaml`. The 0.3.0 image migrates that database the same way.
For Docker or Kubernetes, stop the container (or scale the Deployment to zero)
and back up the volume or the PVC before you start the 0.3.0 image.

Migrations 28 to 32 add tables, columns and indexes and remove nothing:

| Version | Adds |
| --- | --- |
| 28 | the generation id on execution records and a task status on delegated children |
| 29 | Auto Mode budget admission |
| 30 | delegation requests and attempts |
| 31 | model capability receipts |
| 32 | an index for browsing artifacts |

### 2. Going back to 0.2.x is not supported

0.2.0 does not check the schema version of the database it opens: it skips
migration whenever the stored version is at or above the one it knows. Pointed
at a data directory that 0.3.0 has already upgraded, it starts without a warning
and writes to that database, but it does not maintain anything migrations 28 to
32 added. Those migrations only add tables, columns and indexes. That does not
make the combination supported: nothing checks that the two versions agree
about the rows they both write.

To go back, stop 0.3.0 and restore the copy you made before upgrading:

```bash
mv ~/.openai4s ~/.openai4s-0.3
cp -a ~/.openai4s-0.2-backup ~/.openai4s
```

Anything you created under 0.3.0 is not in that copy.

From 0.3.0 on, the refusal exists. A 0.3.x release that opens a database from a
newer schema stops before it writes anything and reports `future_schema` with
both version numbers.

### 3. The access token is always required

0.2.x let a loopback daemon run without a credential when
`OPENAI4S_REQUIRE_TOKEN=0` was set, and printed a warning that the switch would
be removed in the next minor release. 0.3.0 removes it. The variable is ignored,
and every daemon requires its access token, including one bound to
`127.0.0.1`.

* Open the workbench through the URL that `openai4s serve` opens and prints, or
  print it again with `openai4s url`. The bare `http://127.0.0.1:8760/` answers
  with the "access token required" page.
* `openai4s status` no longer prints the token-bearing URL. Use `openai4s url`.
* The token is stored in `<data_dir>/access-token` and survives restarts. Scripts
  send it as `Authorization: Bearer <token>` or `X-OpenAI4S-Token: <token>`.
  A `token` query parameter on a request that changes state is refused.

### 4. Other changes you may notice

* **The workbench is new.** The Preact/TypeScript workbench is the default UI.
  `OPENAI4S_WEBUI=legacy` still serves the old `app.js` UI, which receives no new
  features. Artifact links that 0.2.x wrote into chat messages
  (`/api/artifacts/<id>`) open only in the default workbench, which rewrites
  them to `/api/v1`. The legacy UI uses them as written, and the server answers
  them with 404.
* **Artifacts made by 0.2.x.** Their environment provenance now shows a Python
  kernel whose package list is unknown. 0.2.x recorded a Python kernel by its
  mode, `repl`, and did not read its packages. 0.3.0 corrects that label when it
  reads the record; it does not re-measure the environment or invent a package
  list.
* **`openai4s run` exit status.** The command exits `0` only when the run
  submitted a result. A run that stops for any other reason, such as the turn
  limit, no progress or cancellation, exits `3`. The `--json` output still
  carries `stop_reason`. A script that treated any finished run as success
  should check the exit status. A refusal before the run starts also exits
  `2`: an empty task, an invalid `--allow-test-command`, an explicit code mode
  whose test command nothing can authorize, or a database it will not open,
  one newer than this build (`future_schema`) or one whose upgrade failed
  (`migration_failed`, see section 1). With `--json` the error and its code
  are printed on stdout.
* **`openai4s stop` waits longer.** 0.2.x waited about 5s for the daemon to
  exit before it reported failure or, with `--force`, sent SIGKILL. 0.3.0 waits
  up to `--timeout`, 30s by default, first. A daemon still running after the
  first 5s gets a `shutting down…` line on stderr while the wait goes on; one
  that exits sooner prints only `daemon stopped`. A script that relied on the
  short wait can pass `--timeout 5` (with `--force` for the old SIGKILL).
* **Container image.** The image runs Python 3.14; the 0.2.0 image ran 3.12. If
  you extended the image or installed packages into a running container,
  rebuild or reinstall them for 3.14.
* **The macOS disk image is a preview.** v0.3.0 carries an ad-hoc-signed,
  un-notarized `.dmg` for Apple Silicon; on an Intel Mac, install from PyPI (see
  the [startup guide](startup-guide.md)). Replacing the v0.2.0 app with it
  upgrades the data directory on first launch, so back up first. The v0.2.0 app
  still opens a 0.2.x data directory, but section 2 applies to it: do not point
  it at a data directory that 0.3.0 has upgraded.
* **Skills installer.** The npm package `@pku-yuangroup/openai4s-skills@0.2.0`
  stays installable and contains 603 Skills. The repository and the 0.3.0 wheel
  carry 604.
