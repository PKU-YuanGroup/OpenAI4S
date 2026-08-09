# Command-line interface

[中文说明](README_zh.md)

The `openai4s` command lives here: daemon lifecycle (`serve`, `status`, `stop`, `url`), one-shot local task execution (`run`), first-run model configuration (`init`), scientific environment creation (`setup`) and the transactional `env` generations beside it, the support surfaces (`doctor`, `diagnostics`, `verify-package`), the workflow `benchmark`, read-only session sharing (`share`) with the public `relay` that fronts it, and the optional Jupyter adapter commands.

## Where this fits

The CLI composes; it does not orchestrate. `openai4s run` builds the local outer loop out of [`../agent/`](../agent/), and a persistent kernel only starts if a turn actually routes a code cell. `openai4s serve` hands off to the HTTP/WebSocket server. The setup and status commands run outside any agent turn.

One argparse tree covers three kinds of command, and the difference decides what a failure means. `run`, `init`, `setup`, `env`, `doctor`, `diagnostics`, `verify-package`, `benchmark` and the Jupyter commands do their work in this process. `serve` becomes the daemon. `share` is a REST client of a daemon that must already be running, so it fails on a missing daemon or a missing credential before it ever reaches the feature.

## Files

| File | Responsibility |
| --- | --- |
| [`__init__.py`](./__init__.py) | Re-exports `main`, so the package itself is the CLI entry point. |
| [`main.py`](./main.py) | One argparse tree plus its handlers. In-process: `run`, `init`, `setup`, `env` (`plan`/`apply`/`list`/`rollback`/`recover`), `doctor`, `diagnostics`, `verify-package`, `benchmark`, and `jupyter` describe/export/install. Daemon-facing: `serve`, `status`, `stop`, `url`, and the eight `share` subcommands; `relay serve` / `relay gen-token` run the public share relay on a VPS rather than on this machine. It also owns the daemon pidfile and statefile, and drives conda when building environments — `setup --profile standard` for the everyday Python and R pair, `full` for all four, or `--only <name>` for one. An existing environment is left alone unless `--update` says otherwise, an update never prunes what you installed yourself, and it names the prefix discovery actually found rather than the spec's `name:` — conda resolves a bare name against its own root, so the alternative is a second environment built somewhere the agent never runs, reported as success. |

## Operational contract

- `run` executes in-process and follows the same Engine action and completion rules as the local Agent facade.
- `serve` must keep taking its bind address from `Config`, and the default must stay on loopback — do not hardcode a bind. Exposing the daemon beyond this machine is a job for a trusted reverse proxy or an SSH tunnel. The access token is required by default now, on loopback as well: the gateway mints one into an owner-only file under the data dir, keeps it across restarts, and demands it everywhere except `/health` and `/api/v1/auth/status` — a client cannot be told it needs a token by a response it is not allowed to read. `OPENAI4S_REQUIRE_TOKEN=0` still opts out, but only on a loopback bind and only until the removal release the gateway names. That token is a thin last line, not the reason it is safe to expose the port.
- Because the gate is on, the human-facing URL that `serve`, `status` and `url` print carries `?token=…`, or the SPA answers 401 before it can offer any way in. Every use that is not being handed to a person asks for the same URL without the token: a credential does not belong in a log line or a window title.
- A daemon-backed subcommand presents the token as a header instead, since the daemon refuses query tokens on mutations. It reads the owner-only token file, or `OPENAI4S_TOKEN` when the daemon runs under another account. The request path is joined from `contract.API_ROOT`, and passing an already-prefixed path is an error rather than a 404 — hard-coding `/api/` is how every `share` subcommand came to receive the daemon's own "the API is versioned" 404, with not one of them ever reaching a route.
- `setup` edits an environment in place; `env` treats one as a transaction — a generation is built from a spec staged under the apply lock, verified by starting the interpreter (a file at the path is not an environment), and only then pointed at. `env recover` reports what a restart should know, including an apply still holding the lock.
- Exit codes are the verdict, not decoration. `doctor` answers 0 ok / 1 degraded / 2 failed and needs no daemon, because the situation that motivates running it is usually one where the daemon will not start; it also falls back to a plain `Config` rather than raising when the data directory is the thing that is broken. `benchmark` fails on zero workflows instead of reporting a clean run over nothing.
- The optional Jupyter imports happen only inside the Jupyter command handlers.
- CLI output and exit codes are an operator interface. Change them and you change the tests and the documentation with them.
