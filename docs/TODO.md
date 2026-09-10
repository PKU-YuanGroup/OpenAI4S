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

- [x] **Publish `@pku-yuangroup/openai4s-skills` to npm.**
      [Version 0.2.0 is public](https://www.npmjs.com/package/@pku-yuangroup/openai4s-skills/v/0.2.0)
      and is the registry's `latest` tag. It uses released `v0.2.0` source
      (`c5e80a38306e`) with the organization package name and npm command
      wording adapted; all 603 Skill payloads remain unchanged. The 16
      installer self-tests, package check and source secret scan pass.
      The registry tarball matches the verified candidate: SHA256
      `c4c31b56e34e8d73c1fe73ee8a2c7246f7e9654c1e3ee0cec4138f449be94d47`.
      A fresh npm cache in an empty directory outside any checkout ran
      `npx @pku-yuangroup/openai4s-skills@0.2.0 list --offline` successfully:
      42 curated + 561 bioSkills, 2,212 packaged files / 6.4 MiB. Organization
      installation docs are synchronized. The current source tree has 604
      Skills; `single-cell-rna-analysis` is not part of npm 0.2.0 and its
      documentation retains the GitHub installation path.

- [ ] **Freeze the six retrosynthesis production datasets.** All six entries
      in `skills/retrosynthesis_planning/scenarios/test_cases/database_sources.json`
      remain `not_frozen`; the bundled one-row cases are protocol checks with
      no scientific-accuracy claim. [Verified source candidates](../skills/retrosynthesis_planning/scenarios/test_cases/README.md#production-source-verification-2026-09-09)
      now identify upstream revisions, files and license metadata, including
      PaRoutes CC BY 4.0 and original USPTO CC0 evidence. Code licenses are not
      treated as permission for a derived dataset. Maintainers must record
      dataset admission/attribution decisions and select an independent
      atom-mapping truth file and reviewer. *Done when:* the actual approved
      data and derived splits have verified SHA256, every row carries the
      required revision/license/split/hash and is marked `frozen`, and the
      fail-closed registry test passes. A nonempty-field test alone does not
      establish licensing, file integrity or independent ground truth.

## CI and supply chain

- [x] **Run the final C1–C7 patch on the required Linux/platform gates.**
      Commit `1b56dc100c142f48c5f8d45631720e7eb6592052` passed the
      [complete CI run](https://github.com/PKU-YuanGroup/OpenAI4S/actions/runs/34443430003)
      on its first attempt: Python 3.10/3.12/3.13/3.14, container smoke,
      Linux Python/R interrupt and enforced sandbox, Chromium/Firefox/WebKit,
      packaging, types, documentation and contracts. The
      [Chromium job](https://github.com/PKU-YuanGroup/OpenAI4S/actions/runs/34443430003/job/102763034866)
      passed the C1/C3/C5/C7 product scenes, admission fault, sandbox preview
      and 10/10 matrix checks. Its workflow now installs the locked science
      extra for the real matplotlib fixture that was missing in the previous
      run; no scenario or assertion was removed. The
      [response capture](https://github.com/PKU-YuanGroup/OpenAI4S/actions/runs/34443430003/job/102763034845)
      passed with 1,165 shapes, 212/212 routes and no breaking change, without
      changing the performance threshold. Local final validation also passed:
      8,767 offline tests / 33 skips, 112 focused tests, 38 harness scenarios,
      full pre-commit, and isolated wheel/sdist installation and import smoke.

- [ ] **Observe the Monday dependency batch across ecosystems.**
      [PR #155](https://github.com/PKU-YuanGroup/OpenAI4S/pull/155) assigns uv,
      npm, Docker, pre-commit and GitHub Actions to one Monday group. The
      selected policy includes major versions, black and isort, with no
      allow/ignore filters. Its 13 governance tests and full pre-commit checks
      pass, as does its [complete CI run](https://github.com/PKU-YuanGroup/OpenAI4S/actions/runs/34441714995).
      *Done when:* after code-owner review and default-branch merge, a
      real Dependabot PR contains multiple ecosystems and the following
      Monday still produces updates normally. The configuration is prepared;
      those scheduled outcomes have not been observed.

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
