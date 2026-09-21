# Online update

[中文说明](README_zh.md)

This slice provides channel detection, release discovery and payload verification. The apply transaction, CLI and HTTP routes are planned follow-ups; they are not shipped here.

`openai4s update` replaces the code this daemon runs without losing the history and configuration it accumulated. The second half is the hard one, and it is hard for a reason this repository already wrote down: migrations in [`../storage/migrations.py`](../storage/migrations.py) are forward-only with no reverse step, an older binary refuses a newer database at every entry point, and the migration runner deletes its own pre-upgrade copy the moment the upgrade commits. Everything in this package is plumbing around one primitive — an independent pre-update snapshot the updater takes and keeps.

Nothing here is reachable from a turn. No `Tool` subclass, no `host.*` capability and no Skill may import this package: an installer reachable from a model turn converts every prompt injection into persistent code execution. The package is also import-cheap by construction — [`__init__.py`](__init__.py) resolves its whole public surface through PEP 562's module-level `__getattr__` and imports nothing at module scope, so `import openai4s.update` neither pulls in the transaction nor opens a socket.

## Where this fits

1. [`channel.py`](channel.py) answers what kind of install this is. It is pure: environment variables and the filesystem, no network, no subprocess, no Store. An install it cannot identify is `unknown`, which is a refusal rather than a guess.
2. [`discovery.py`](discovery.py) answers whether a newer release exists. One version oracle (pypi.org's project document) and two digest witnesses (the per-version document and the release `SHA256SUMS`). Every failure resolves to `unknown`; none of them resolves to "up to date".
3. [`verify.py`](verify.py) answers what has to be true about a payload before anything is stopped or replaced. Digest grammar, two-witness agreement, archive member validation, the wheel structure gate, and an out-of-process execution probe of the new code — all of it before the daemon is quiesced, because a bad build must never cost a restart.
4. The apply transaction then stops the daemon, snapshots, commits, relaunches and journals. It lands in `store.py`, `preserve.py`, `apply.py`, `commit.py` and `applier.py`.

The egress surface stays frozen **by mechanism**: no module in this package names `urlopen`, `Request`, `build_opener` or `create_connection`. Every byte moves through [`../webtools.py`](../webtools.py), which is the only code in the tree that follows a redirect manually and re-applies the egress allowlist and the SSRF guard on every hop. Both sources redirect, so a client that follows redirects internally checks the first hop and trusts the rest.

## Files

| File | Responsibility |
| --- | --- |
| [`__init__.py`](__init__.py) | The lazy public surface (`detect`, `check`, `plan`, `apply_update`, `rollback`, `recover`, `prune`, `Channel`) plus the two exception types. The exceptions are defined here rather than re-exported, because a lazily-resolved exception class cannot be caught. Note the one name collision the docstring spells out: `openai4s.update.apply` is both a module and a callable, so call it through `apply_update`. |
| [`channel.py`](channel.py) | `detect(cfg) -> Channel`, resolving in order: explicit `OPENAI4S_CHANNEL`, container, macOS `.app`, WSL managed bundle, Linux relocatable bundle, source checkout, venv, unknown. The container probe reads four positive signals rather than two, because containerd and CRI-O — what Kubernetes runs — write neither `/.dockerenv` nor `/run/.containerenv`, and a root pod without them has a writable site-packages: the venv probe would fire and the update would land in a layer the next restart discards. Each branch records the probe and the absolute path that decided it in `Channel.evidence`, which is admin-only and omitted from `as_dict()` unless asked for. `require_self_update` is the single place a refused channel is refused. |
| [`discovery.py`](discovery.py) | `check(cfg)` and the vocabulary around it: `STATUSES` (three members), `REASONS` (the frozen failure codes), the `ReleaseSource` seam, `SHA256SUMS` parsing, version comparison without `packaging`, index validation for `OPENAI4S_UPDATE_INDEX`, and the 6-hour `<data_dir>/updates/check.json` cache with a much shorter retry after a failure. A failure overwrites the cache — `retry_after_at` is what stops a broken network being re-dialled on every invocation — so it carries the last answer that *was* an answer under `previous`, which is what lets a surface print "last known 0.4.0, checked three hours ago". `OPENAI4S_UPDATE_SOURCE=offline` short-circuits before the cache is read and before the source is resolved. |
| [`verify.py`](verify.py) | `REFUSAL_CODES`, digest grammar, `Witnesses`/`agree`/`digest_for`, `validate_zip`/`validate_tar`, `wheel_structure`, `extract_wheel`, `rehash`, and `probe_installation`. `Witnesses` is sealed so only `agree()` can build one, which is what makes the ordering rule structural: a `SHA256SUMS` digest is honoured for a bundle only after the wheel already matched PyPI's. |

Strict discovery (`allow_single_witness=False`) always fetches fresh witnesses, including after a cached read. Cache publication uses a unique, owner-only temporary file per writer. Distribution metadata must identify the package actually loaded before it can establish a writable install channel.

Archive validation resolves tar link chains and treats hardlink targets as archive-root-relative. Duplicate paths, link cycles and members beneath links are refused. Extraction requires an empty staging directory; wheel extraction enforces that requirement itself. Wheel metadata must name the requested version, and dependency markers must require a nonempty extra even through boolean expressions.

The execution probe supports Python 3.10 and later, excludes the caller's working directory and user site, and checks that an explicitly staged package was actually imported. It uses a keyless loopback model configuration with networking disabled. Doctor's expected offline/optional-runtime warnings are accepted only with a valid diagnostic report; failed checks and scratch database errors still refuse the payload.

## Planned follow-up modules (not shipped here)

| File | Planned responsibility |
| --- | --- |
| `store.py` | The durable update store under `<data_dir>/updates/`: the flock, the staged payloads, the generations, the journal, and `prune`. |
| `preserve.py` | What survives the swap — the pre-update database snapshot, the configuration, the Skills, and the count of at-risk recovery checkpoints. |
| `apply.py` | `plan`, `apply_update`, `rollback` and `recover`: the transaction's phase order, its three-case rollback decision procedure, and its refusals. |
| `commit.py` | The per-channel commit. The venv fork is the one this repository had to learn: the daemon's own `.venv` ships without a `pip` module, so the commit probes `importlib.util.find_spec("pip")` and otherwise runs `uv pip install --python sys.executable`, and refuses naming both remedies when neither is available. |
| `applier.py` | The out-of-process applier the daemon spawns detached with a fixed argv and a plan file it wrote itself. Nothing from an HTTP request body reaches its argv, and it re-validates every field — including re-hashing the payload — before touching anything. |

## What this package refuses, on purpose

- **No re-exec.** A pointer read at process start that `execve`s into a generation under `<data_dir>` turns "can write a file in the data directory" into "executes as the daemon forever", on exactly the channels whose install tree the daemon uid cannot write.
- **No database down-migration and no automatic restore.** The snapshot is kept and named; putting it back is an operator's decision.
- **No claim of authorship.** Two independent digest witnesses establish integrity. They publish from the same repository through Trusted Publishing, so they share a root of trust, and nothing in a release is signed with a key a client holds. The trust line says both halves; the second is the one that usually goes missing.
- **No check at boot, no poller, no background thread.** The check fires on an explicit user action only.

## Change rules

- Keep it standard-library only. `pyproject.toml` declares `dependencies = []`, and the release gate refuses a wheel with a non-extra `Requires-Dist`. In particular there is no `packaging` import: the release tag grammar is fixed and prerelease-free, so versions compare as an integer 3-tuple.
- Never take a host, a path or a version from a response body, a settings row, a tool argument or a model message without validating it against something decided in this package first.
- A failure is never "up to date". If a new failure mode appears, give it a `REASONS` entry; do not fold it into an existing one and do not let it reach a caller as a success.
- Do not add an outbound primitive here. If `webtools` cannot express what a read needs, widen `webtools`.
