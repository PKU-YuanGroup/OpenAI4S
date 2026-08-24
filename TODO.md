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
