# Command-line interface

[中文说明](README_zh.md)

The `openai4s` command lives here: daemon lifecycle (`serve`, `status`, `stop`, `url`), one-shot local task execution (`run`), first-run model configuration (`init`), scientific environment creation (`setup`), and the optional Jupyter adapter commands.

## Where this fits

The CLI composes; it does not orchestrate. `openai4s run` builds the local outer loop out of [`../agent/`](../agent/), and a persistent kernel only starts if a turn actually routes a code cell. `openai4s serve` hands off to the HTTP/WebSocket server. The setup and status commands run outside any agent turn.

## Files

| File | Responsibility |
| --- | --- |
| [`__init__.py`](./__init__.py) | Re-exports `main`, so the package itself is the CLI entry point. |
| [`main.py`](./main.py) | One argparse tree plus its handlers: `serve`, `status`, `stop`, `url`, `run`, `init`, `setup`, and the Jupyter describe/export/install subcommands. It also owns the daemon pidfile and statefile, and drives conda when building environments — `setup --profile standard` for the everyday Python and R pair, `full` for all four, or `--only <name>` for one. An existing environment is left alone unless `--update` says otherwise, and an update never prunes what you installed yourself. |

## Operational contract

- `run` executes in-process and follows the same Engine action and completion rules as the local Agent facade.
- `serve` must keep taking its bind address from `Config`, and the default must stay on loopback — do not hardcode a bind. Exposing the daemon beyond this machine is a job for a trusted reverse proxy or an SSH tunnel. Bind off loopback and the gateway mints one access token for the life of the process and demands it on every path except `/health`, which stays unauthenticated. That token is a thin last line, not the reason it is safe to expose the port.
- The optional Jupyter imports happen only inside the Jupyter command handlers.
- CLI output and exit codes are an operator interface. Change them and you change the tests and the documentation with them.
