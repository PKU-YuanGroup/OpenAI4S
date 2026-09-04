# TODO

[中文说明](TODO_zh.md)

Follow-ups this repository has decided to do and has not done yet. Each row
says what "done" looks like, so a reader can tell a pending item from a
forgotten one. Anything with an owner outside the codebase — a credential, a
registry account, a machine — belongs here rather than in a comment nobody
greps for.

Work that is *planned* rather than pending lives in
[`docs/next-version-progress.md`](next-version-progress.md); that document
is a factual record of the v0.3 plan and is validated by
`tests/test_progress_document.py`. This file is for loose ends.

## Publishing

- [ ] **Publish `openai4s-skills` to npm.** The package is complete and gated
      (`node tools/skills-installer/selftest.mjs`,
      `node tools/skills-installer/check_package.mjs`). From a clean checkout,
      the released `v0.2.0` tag passes all 16 installer self-tests and packs
      2,212 files / 603 Skills / 6.4 MB; current `main` packs 2,236 files /
      604 Skills / 6.5 MB. Until it is published,
      `npx openai4s-skills …` does not resolve; `npx github:PKU-YuanGroup/OpenAI4S install --all`
      works today and is what the README shows alongside it. A live
      `npm view openai4s-skills version` still returned `E404` on 2026-09-01.
      *Done when:* `npm publish --access public` has run from a clean checkout
      of the released tag and `npx openai4s-skills list` works on a machine
      with no checkout. Needs an npm account with publish rights — no automated
      agent should hold that credential.

## CI and supply chain

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
      Learned on the first attempt (reverted out of
      [#143](https://github.com/PKU-YuanGroup/OpenAI4S/pull/143) to land alone):
      `update-types`, `exclude-patterns` and `dependency-type` are `groups:`-only
      keys and are rejected at the entry level; a second entry for the same
      ecosystem and directory is a shape only a maintainer's example uses; and
      the complementary `ignore` such a pair needs also filters *security*
      updates, which the current `groups:` never do. `tests/test_governance.py`
      now fails offline on the first two.

## Closed recently, recorded so it is not re-investigated

Action-pin identity is now checked in ordinary pull-request CI. The
`action-pins` job runs a commit-pinned `pinact-action` in validation-only mode
with tag verification enabled, while the offline governance test continues to
require every workflow action to carry an exact 40-hex SHA and `# vX.Y.Z`
claim. A real pinact run accepted the current tree; a negative control that
paired Checkout's `v7.0.1` SHA with a `# v7.0.0` comment failed on the identity
mismatch.

CPython 3.14 is now a classified and CI-tested interpreter, including the
science extra used by the 3.14 container. Environment-binding fixtures create
real pip-free virtual environments instead of bare interpreter symlinks, so
their exact `sys.executable` assertion remains meaningful on 3.13 and 3.14.
The bring-up verifier uses fail-closed `lstat` inspection rather than the
3.14 `Path.is_symlink()` behavior that suppresses probe errors, and the nested
xdist capture test explicitly loads only the plugin its contract exercises.
The final locked Python 3.14.4 run completed with **8094 passed, 23 skipped**.

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
