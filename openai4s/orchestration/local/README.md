# `openai4s/orchestration/local/`

The default resource plane: this machine. Same `AllocationBackend` contract as any cluster, so the reconciler, the routes and the CLI have one code path whether or not a scheduler exists — "no cluster configured" is a different *backend*, not a different program.

| file | what it is |
|---|---|
| [`__init__.py`](__init__.py) | Re-exports `LocalBackend`. |
| [`backend.py`](backend.py) | Allocations as child processes. Two details matter more than they look. The submission token is honoured here exactly as a cluster honours it — a repeated token returns `Existing` rather than forking a second process — because this is the backend every install has, and a local backend that answered "of course it's new" would leave INV-8's reconciliation path untested everywhere except a cluster nobody has in CI. And a tracked process that is simply gone (daemon restarted, someone killed it) is `LOST`, never `COMPLETED`: we report success only for an exit status we actually reaped. Children get their own process group so cancel kills the tree rather than the wrapper, a named environment rather than the daemon's (which holds API keys), and `MAX_CONCURRENT` refuses with `UNSCHEDULABLE` — the reason a cluster gives — so no caller needs a local-only branch. |
