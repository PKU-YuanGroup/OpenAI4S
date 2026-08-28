# TODO

[中文说明](TODO_zh.md)

Follow-ups this repository has decided to do and has not done yet. Each row
says what "done" looks like, so a reader can tell a pending item from a
forgotten one. Anything with an owner outside the codebase — a credential, a
registry account, a machine — belongs here rather than in a comment nobody
greps for.

Work that is *planned* rather than pending lives in
[`docs/next-version-progress.md`](docs/next-version-progress.md); that document
is a factual record of the v0.3 plan and is validated by
`tests/test_progress_document.py`. This file is for loose ends.

## Publishing

- [ ] **Publish `openai4s-skills` to npm.** The package is complete and gated
      (`node tools/skills-installer/selftest.mjs`,
      `node tools/skills-installer/check_package.mjs`), and `npm pack` produces
      6.4 MiB carrying all 602 Skills. Until it is published,
      `npx openai4s-skills …` does not resolve; `npx github:PKU-YuanGroup/OpenAI4S install --all`
      works today and is what the README shows alongside it. The name is
      unclaimed on the registry as of 2026-08-23.
      *Done when:* `npm publish --access public` has run from a clean checkout
      of the released tag and `npx openai4s-skills list` works on a machine
      with no checkout. Needs an npm account with publish rights — no automated
      agent should hold that credential.

## CI and supply chain

- [ ] **Validate action pins before merge, not after.** `scorecard.yml`
      triggers on `push: branches: [main]` and the Saturday cron only, so a
      pin edited there never executes for a PR — a SHA that does not resolve
      merges fully green and first shows up as SARIF quietly no longer
      reaching code scanning. `tests/test_governance.py` now requires every
      `uses:` in every workflow to be a 40-hex SHA carrying a `# vX.Y.Z`
      comment, but it cannot check that the comment names the SHA beside it:
      dereferencing a tag needs the network and the suite is offline by
      design. There is no workflow linter either — `actionlint`, `zizmor`,
      `pinact` and `ratchet` appear nowhere in the tree.
      *Done when:* a PR-triggered check fails on a `uses:` line whose SHA does
      not dereference to the tag in its comment. `pinact --check` is the
      smallest thing that does this; an `actionlint` job would also cover the
      schema mistakes no test here looks for.

- [ ] **Batch the Monday dependency PRs across ecosystems.** `groups:` is
      per-ecosystem by construction, so the uv, pre-commit and github-actions
      updates arrive as three PRs and have been consolidated onto one branch by
      hand at least four times (#75, #97, #131). Dependabot supports doing this
      in config: a top-level `multi-ecosystem-groups` key plus
      `multi-ecosystem-group: <name>` on each `updates` entry. Not done here
      because the entries would have to give up their own `schedule:` blocks
      and a misconfiguration stops Dependabot opening PRs at all, which is a
      worse failure than the one it fixes — it wants its own PR and one
      observed Monday.
      *Done when:* a single Dependabot PR carries updates from more than one
      ecosystem, and the following Monday's run still opens PRs normally.

- [ ] **The offline suite does not pass on CPython 3.14, which is now the
      container's interpreter.** `Dockerfile` moved to `python:3.14-slim-bookworm`
      while `ci.yml`'s matrix is `["3.10", "3.12", "3.13"]`, so nothing in CI
      runs the suite there; `Container image builds and serves` boots the
      daemon in the built image but does not run tests. Run by hand on 3.14:
      **6 failed, 7855 passed**. All six share one cause, and it is a CPython
      change rather than a defect here — invoked through a **bare symlink**,
      3.13 reports the symlink path in `sys.executable` while 3.14 reports the
      resolved real binary. `_real_python_prefix` in
      `tests/test_env_kernel_binding.py` builds `prefix/bin/python` as exactly
      such a symlink, and the fixtures observe the env binding through that
      self-report (`test_env_kernel_binding.py` ×2,
      `test_delegation_env_inheritance.py` ×3, `test_benchmark_bringup.py` ×1).
      A control run of the same tests on 3.13 in the same worktree passes, so
      this is 3.14-specific. The kernel execs the interpreter it is handed
      either way; what moved is what the cell reports about itself — which is
      also what artifact provenance records as `interpreter`.
      *Done when:* the suite is green on 3.14 — most likely by giving the
      fixture a real prefix (a `pyvenv.cfg`) instead of a bare symlink, so the
      assertion keeps its strength on both versions rather than being relaxed
      to accept a resolved path — and 3.14 is in the `ci.yml` matrix so it
      cannot regress again.

## Closed recently, recorded so it is not re-investigated

The local kernel worker now spawns into its own session, so a signal aimed at
the daemon's process group is no longer aimed at every cell under it — the
divergence Linux + bubblewrap did not have. It landed with the two things that
make it an improvement rather than a trade: the worker's group is captured at
spawn and `kill` routes through the existing stop ladder, which reaps the cell's
own subprocesses (impossible before, because the worker's group *was* the
daemon's); and `openai4s run` installs a SIGINT handler that does what the
terminal's group-wide Ctrl-C used to do.

The wall-clock budgets in `tests/test_mcp_lifecycle.py`,
`tests/test_local_jobs.py`, `tests/test_cluster_session_production_wiring.py`,
`tests/test_orchestration_routes.py`, `tests/test_telemetry_transmission.py`
and `tests/test_cell_watchdog.py` now wait on conditions rather than clocks.
Worth knowing why, because the audit that flagged them was half wrong: none of
them had ever failed in CI, and two were not flakes at all but silent coverage
loss — a sleep too short left the test green while it exercised the path it was
written to avoid.
