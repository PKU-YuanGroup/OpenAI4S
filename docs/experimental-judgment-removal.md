# Removing the experimental semantic judgment layer

Follow this list in order on a throwaway branch. Do not merge the branch.
There is **no egress group to delete**: v1.3 never added `api.typesafe.ai` to
`EGRESS_GROUPS`. After the last step, `uv run pytest -q -x` and
`uv run python scripts/check_directory_readmes.py` should pass.

Use symbol names. Line numbers drift.

## 0. Preconditions

```bash
git switch -c chore/judgment-removal-dryrun
# or, for a detached check against feat/judgment:
# git worktree add --detach /tmp/judgment-removal-check feat/judgment
```

## 1. Delete trees and files

### Product

```text
openai4s/judgment/                          # entire package
openai4s/host/judgment.py
openai4s/sdk/judgment.py
skills/text-features/                       # entire Skill
scripts/build_bioskills_area_index.py
harness/providers/typesafe_fake.py
harness/evals/judgment_skills.py
harness/evals/judgment_skills_cases.json
harness/evals/judgment_skills_cases.lock
harness/evals/judgment_skills_zh_en_terms.json
harness/evals/judgment_literature.py
harness/evals/judgment_literature_cases.json
harness/evals/judgment_literature_cases.lock
frontend/src/components/customize/ExperimentsTab.tsx
frontend/src/components/customize/ExperimentsTab.test.tsx
frontend/src/features/judgment/             # entire feature (copy, chips, css, tests, README pair)
docs/experimental-judgment.md
docs/experimental-judgment_zh.md
docs/experimental-judgment-removal.md       # this file
docs/release-notes-judgment.md
```

### Tests

```text
tests/browser_judgment.mjs
tests/fixtures/judgment_default_off/
tests/test_judgment_default_off.py
tests/test_judgment_types.py
tests/test_judgment_flags.py
tests/test_judgment_validate.py
tests/test_judgment_typesafe.py
tests/test_judgment_fake_provider.py
tests/test_judgment_typesafe_live.py
tests/test_judgment_registry.py
tests/test_judgment_service.py
tests/test_judgment_rpc.py
tests/test_judgment_replay.py
tests/test_judgment_settings_route.py
tests/test_judgment_w1_integration.py
tests/test_judgment_w2_integration.py
tests/test_judgment_w3_integration.py
tests/test_judgment_egress.py
tests/test_judgment_doctor.py
tests/test_judgment_skill_suggest.py
tests/test_judgment_skills_eval.py
tests/test_literature_judgment.py
tests/test_judgment_literature_eval.py
tests/test_judgment_safety_shadow.py
tests/test_judgment_task_mode_shadow.py
tests/test_judgment_llm_backend.py
tests/test_bioskills_area_index.py
tests/test_text_features_skill.py
```

`rm -rf` the directories; `rm` the files.

## 2. Restore `literature-review` to pre-judgment helpers

`skills/literature-review/kernel.py`

- Module docstring auto-load list: drop `screen_passages, check_claims`. Keep
  `verify_dois, crossref_lookup, search_openalex, expand_citations,
  extract_dois, style_pass`.
- Delete from the comment
  `# --- Semantic evidence check (experimental; host.judge / literature_check) ---`
  through end of file. That block is `FUZZY_MIN`, `locate_claim`, `_judge`,
  `screen_passages`, `check_claims`, and their helpers. The `FUZZY_MIN` in
  `openai4s/judgment/templates/literature.py` goes away with the package in
  step 1; these two copies must not be left half-deleted.

`skills/literature-review/SKILL.md`

- `capabilities.network.domains`: remove `api.typesafe.ai`.
- `metadata.third_party`: remove the TypeSafe entry and the comment
  `# Experimental literature_check: …`. That entry is the **last** one in the
  frontmatter, directly above the closing `---`. Delete its lines only; a
  block-level cut that runs to "the next entry" runs through the delimiter,
  and the loader then reads the whole file as body and reports the Skill's
  network mode as `unknown` (`tests/test_skill_network_contract.py` fails).
- Delete the section `## Semantic evidence check (experimental)`. It is the
  **last** section of the file, so delete from that heading to end of file.
  `## Style pass before saving` comes *before* it and stays.

## 3. Surgical hooks (file + function + hook)

Undo these mounts. After the edit, the named function must not import
`openai4s.judgment` or `openai4s.host.judgment`.

| File | Function | Hook to remove / restore |
| --- | --- | --- |
| `openai4s/config.py` | `_strict_env_tristate` | Delete the function. It exists only for judgment. Keep `_strict_env_float` (Auto Mode uses it). |
| `openai4s/config.py` | `_JUDGMENT_PROVIDERS`, `_JUDGMENT_DEFAULT_MODEL`, `_judgment_model` | Delete the three names (they sit after `RoadmapFeatureFlags.__post_init__`). |
| `openai4s/config.py` | `ExperimentalJudgmentFlags` | Delete the dataclass. |
| `openai4s/config.py` | `Config` | Delete the last field `experimental_judgment: ExperimentalJudgmentFlags = field(default_factory=ExperimentalJudgmentFlags)`. Leave `trusted_proxy_origins` as the last appended field. |
| `openai4s/host_dispatch.py` | `HostDispatcher.__init__` | Delete `from openai4s.host.judgment import JudgmentService` and the `self._judgment_service = JudgmentService(...)` construction. |
| `openai4s/host_dispatch.py` | `HostDispatcher.__init__` | `SkillService(self.cfg, judgment_service=self._judgment_service)` → `SkillService(self.cfg)`. |
| `openai4s/host_dispatch.py` | `_step_end` | In the `kind == "skill"` / `method == "search_skills"` branch, restore list-only projection: `rows = result if isinstance(result, list) else (r.get("results") or [])` may stay as a defensive read of `results`, but drop the copy of `semantic_status` / `semantic_suggestions` onto the card and the "suggestion" summary. Capability-off already returns a list. |
| `openai4s/host_dispatch.py` | `_m_judge` | Delete the method (`return self._judgment_service.dispatch(spec)`). |
| `openai4s/host_dispatch.py` | `_m_suggest_skills` | Delete the method (`return self._skill_service.suggest(...)`). |
| `openai4s/sdk/host.py` | `Host.judge` | Delete the method and its `from openai4s.sdk.judgment import judge` import. |
| `openai4s/host/skills.py` | `SkillService.__init__` | Drop `judgment_service: Any = None` and `self._judgment_service = judgment_service`. |
| `openai4s/host/skills.py` | `_judgment_flags`, `_judgment`, `_suggest_catalog`, `suggest` | Delete all four. |
| `openai4s/tools/skills.py` | `SearchSkillsTool.execute` | After `rows = runtime.invoke(self.host_method, spec)`, return `self.fit_to_budget(rows)` only. Delete the `suggest_skills` invoke and the wrapper dict. |
| `openai4s/tools/skills.py` | `_tool_rendered_size`, `_semantic_reserve`, `_mark_semantic_truncated`, `_fit_semantic` | Delete all four. `fit_to_budget` keeps its optional `budget=` (default `None` is the old behaviour); leaving that parameter is harmless. |
| `openai4s/security/classifier.py` | `classify_code` | Public function currently computes `verdict = _classify_code(...)`, then `from openai4s.judgment.shadow import submit` / `submit("code", ...)` inside `try/except`, then `return verdict`. Delete the try/except submit. Returning `_classify_code(...)` directly is enough. |
| `openai4s/security/injection.py` | `scan_tool_result` | Same pattern: delete `from openai4s.judgment.shadow import submit` / `submit("injection", ...)`. |
| `openai4s/security/biosecurity.py` | `looks_biosecurity_relevant` | Delete the `submit("bio_prescan", ...)` try/except. Return `hit`. |
| `openai4s/security/biosecurity.py` | `screen_trajectory` | Delete the `submit("trajectory", ...)` try/except after `_screen_trajectory(...)`. |
| `openai4s/agent/task_modes.py` | `resolve_task_mode` | Delete `from openai4s.judgment.task_mode_shadow import submit` / `submit(request=body, rule_mode=mode.value, explicit=False)` in the `try/except` immediately before `return mode`. |
| `openai4s/server/gateway.py` | experimental-judgment routes | Delete the block `if sub == "/experimental/judgment":` (`status`/`update`) and `if sub == "/experimental/judgment/test":` (`probe_connection`). |
| `openai4s/server/team_policy.py` | `INSTANCE_CONFIG_PATHS` | Delete `"/experimental/judgment"` and `"/experimental/judgment/test"` and the comment that names them. |
| `openai4s/doctor.py` | `_judgment` | Delete the function. |
| `openai4s/doctor.py` | `_CHECKS` | Delete the `("judgment", _judgment)` tuple entry. |
| `tests/conftest.py` | module-level purge and `isolated_openai4s_home` | Delete `_JUDGMENT_ENV_LEAK` and both `for name in _JUDGMENT_ENV_LEAK` loops (import-time and per-test). Keep `_ROADMAP_ENV_LEAK`. `OPENAI4S_TYPESAFE_API_KEY` / `OPENAI4S_JUDGMENT_MODEL` remain covered by `_LLM_ENV_LEAK`. |
| `tests/test_egress_surface.py` | `_DECLARED` | Delete the `"openai4s/judgment/typesafe.py": (...)` entry. |
| `tests/test_egress_surface.py` | `test_the_surface_is_small_enough_to_review` | Bound `14` → `13`. Delete the paragraph that says the surface grew from thirteen for `judgment/typesafe.py`. |
| `tests/test_doctor.py` | `test_every_probe_reports_without_a_running_daemon` | Remove `"judgment"` from the expected check-name set. |
| `tests/test_kernel_recovery.py` | `test_bundled_sidecar_recovery_compatibility_is_explicit` | `assert len(sidecars) == 19` → `18` (deleting `skills/text-features/kernel.py`). |
| `pyproject.toml` | `[tool.setuptools.package-data] openai4s` | Delete `"judgment/templates/*.json"`. |
| `pyproject.toml` | `[tool.mypy] files` | Delete every `openai4s/judgment/...` path (disclosure through `llm_backend.py`). |
| `scripts/render_skill_install_sections.py` | `NPM_020_UNAVAILABLE_SKILLS` | Remove `"text-features"` from the frozenset. Keep `"single-cell-rna-analysis"`. |

## 4. UI mounts

| File | Hook |
| --- | --- |
| `frontend/src/components/customize/GeneralTab.tsx` | Delete `import { ExperimentsTab } from "./ExperimentsTab"` and `<ExperimentsTab />`. |
| `frontend/src/features/send/step.ts` | Delete `import { appendSemanticSkillSearch } from "../judgment/chips"` and the `if (appendSemanticSkillSearch(box, out)) return box;` call in the skill-step renderer. |
| `frontend/src/features/customize/api.ts` | Delete `JudgmentFlag`, `JudgmentDisclosureCap`, `JudgmentStatus`, `JudgmentUpdate`, `JudgmentProbe`, `judgmentStatus`, `flagOf` (if only used here), `disclosureCap`, `getJudgmentStatus`, `updateJudgmentSettings`, `testJudgmentConnection`. In `isDiagnosticsConfigWrite`, delete both `route === "/experimental/judgment"` arms (PUT and PATCH). |

Then rebuild dist so the committed SPA shell matches:

```bash
npm ci --ignore-scripts --prefix frontend
npm test --prefix frontend
npm run build --prefix frontend
```

Commit the resulting `openai4s/server/webui/dist/` with the source. A dry-run
that only needs pytest may skip the rebuild; `check_directory_readmes.py`
does not inspect hashed dist assets.

## 5. Settings keys (Store)

No schema migration. After removal nothing reads these rows. Optional cleanup
on a running data dir (not required for the suite):

```text
experimental.judgment.enabled
experimental.judgment.capabilities.skill_suggest
experimental.judgment.capabilities.literature_check
experimental.judgment.capabilities.text_features
experimental.judgment.capabilities.safety_shadow
experimental.judgment.capabilities.task_mode_shadow
experimental.judgment.provider
experimental.judgment.model
experimental.judgment.disclosure_ack
experimental.judgment.audit_raw_state
secret typesafe_api_key  (scope judgment)
```

Environment variables to stop documenting / exporting:

```text
OPENAI4S_EXPERIMENTAL_JUDGMENT
OPENAI4S_JUDGMENT_SKILL_SUGGEST
OPENAI4S_JUDGMENT_LITERATURE
OPENAI4S_JUDGMENT_TEXT_FEATURES
OPENAI4S_JUDGMENT_SAFETY_SHADOW
OPENAI4S_JUDGMENT_TASK_MODE_SHADOW
OPENAI4S_JUDGMENT_PROVIDER
OPENAI4S_JUDGMENT_MODEL
OPENAI4S_JUDGMENT_TIMEOUT_S
OPENAI4S_TYPESAFE_API_KEY
OPENAI4S_JUDGMENT_FAKE_ENDPOINT
OPENAI4S_JUDGMENT_LIVE_KEY
```

## 6. Egress

Do **not** edit `openai4s/egress.py`. There is no group, no domain, no
`enabled` flag. The only egress-adjacent test change is step 3
(`tests/test_egress_surface.py`).

## 7. Skill-count prose (text-features)

Deleting `skills/text-features/` moves the tree 45→44 curated, 606→605
bundled, sidecar census 19→18. `tests/test_skills_installer_contract.py`
(`test_every_prose_count_matches_the_tree` and
`test_the_published_description_counts_the_skills_it_actually_ships`) is the
gate: run it and fix every remaining live 606 / 45 it names. Historical
v0.2.0 figures stay as written.

Easy to miss: the parenthetical `(606 Skills: 45 curated + 561 bioSkills)` in
the root README and installer README; `docs/skills.md` "catalog contains 606"
and "ships all 606 Skills"; `skills/README*` after 45 has already become 44
so the combined "606 … 45" string no longer matches; `docs/TODO.md` splits
`has 606` / `Skills` across two lines.

| File | What to change |
| --- | --- |
| `package.json` | `"description"`: `606 scientific recipes` → `605` |
| `README.md` | every live 606 / 45 that `COUNT_SITES` matches, plus the "What ships today" table `606 Skills (45 curated + 561 …)` |
| `README_zh.md` | matching 606 / 45 sites |
| `CLAUDE.md` / `AGENTS.md` (same file) | `606 bundled Skills: 45 curated` / `561-recipe` |
| `docs/skills.md` | `## Bundled Skills (606)`, `45 curated`, `### Curated OpenAI4S Skills (45)`; drop `text-features` from the **ML methodology / benchmarks** cell |
| `docs/TODO.md` / `docs/TODO_zh.md` | current-tree Skill count |
| `skills/README.md` / `skills/README_zh.md` | live totals; delete the [`text-features/`](text-features/) table row |
| `tools/skills-installer/README.md` / `README_zh.md` | live 606 / 45 sites |
| `tests/test_kernel_recovery.py` | sidecar census 19 → 18 (already in step 3) |

Drop the "Experimental judgment" subsection and the documentation-table /
Experimental-features rows added for this layer (root README, `docs/README*`,
`docs/configuration.md` section **Experimental semantic judgment**,
`docs/security.md` section **Outbound data flow: Semantic judgment
(experimental)**).

Then:

```bash
uv run python scripts/render_skill_install_sections.py
```

so generated Install sections no longer name `text-features`.

## 8. Directory README rows

Delete the table row that names each file or directory removed in step 1.
Do not reorder remaining rows. At least:

| README pair | Rows to delete |
| --- | --- |
| `openai4s/README.md` + `_zh.md` | [`judgment/`](./judgment/) |
| `openai4s/host/README.md` + `_zh.md` | [`judgment.py`](judgment.py) |
| `openai4s/sdk/README.md` + `_zh.md` | [`judgment.py`](judgment.py) |
| `scripts/README.md` + `_zh.md` | [`build_bioskills_area_index.py`](build_bioskills_area_index.py) |
| `harness/providers/README.md` + `_zh.md` | [`typesafe_fake.py`](typesafe_fake.py) |
| `harness/evals/README.md` + `_zh.md` | all `judgment_skills*` and `judgment_literature*` rows |
| `tests/README.md` + `_zh.md` | `browser_judgment.mjs` and every `test_judgment_*` / `test_literature_judgment.py` / `test_bioskills_area_index.py` / `test_text_features_skill.py` row |
| `frontend/src/features/README.md` + `_zh.md` | [`judgment/`](judgment/) |
| `frontend/src/components/customize/README.md` + `_zh.md` | `ExperimentsTab.tsx` and `ExperimentsTab.test.tsx` |
| `docs/README.md` + `_zh.md` | `experimental-judgment.md`, `_zh.md`, `experimental-judgment-removal.md`, `release-notes-judgment.md` |
| `skills/README.md` + `_zh.md` | `text-features/` (also in step 7) |

## 9. Verify

`scripts/check_directory_readmes.py` lists files via `git ls-files --cached`,
so stage the deletions before running it (`git add -u` on the throwaway
branch). Do not push that branch.

```bash
git add -u
uv run python scripts/check_directory_readmes.py
uv run pytest -q -x
uv run pytest tests/test_skills_installer_contract.py tests/test_doctor.py tests/test_egress_surface.py tests/test_kernel_recovery.py -q
```

A leftover `import openai4s.judgment` fails collection. A leftover
`text-features` count fails `test_every_prose_count_matches_the_tree`. A
leftover `"judgment"` in `test_doctor.py` fails the check-name set. A leftover
`typesafe.py` declaration fails the egress-surface bound.

## 10. Frozen HTTP contracts (required for pytest)

Delete these in the same throwaway tree:

| File | What to delete |
| --- | --- |
| `docs/response-contract.json` | keys `"/experimental/judgment"` and `"/experimental/judgment/test"` |
| `docs/response-schemas.json` | every route key containing `/experimental/judgment` (GET/PUT/PATCH/POST/DELETE × both paths) |
| `docs/webapp-api.md` | the three table rows for `GET` / `PUT\|PATCH` / `POST …/test` |

`tests/test_response_contract_coverage.py::test_the_contract_does_not_describe_routes_that_no_longer_exist`
and
`tests/test_response_schemas.py::test_every_frozen_route_is_a_route_the_server_actually_has`
fail if those keys stay. `capture_response_*.py --check` is a separate CI job.

## 11. What this list does not revert

- `fit_to_budget(..., budget=None)` optional argument, if you left it: behaviour
  with `budget` omitted is the pre-judgment path.
- Dispatcher `log_host_call` envelope shape.
