# Upgrading from 0.2.x to 0.3.0

[中文说明](upgrading_zh.md)

Read this before the first 0.3.0 start. Two changes cannot be undone by
reinstalling the old version. 0.3.0 upgrades the database in place. It also
removes the switch that let a local daemon run without an access token.

## 1. Back up the database first

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

## 2. Going back to 0.2.x is not supported

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

## 3. The access token is always required

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

## 4. Other changes you may notice

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
