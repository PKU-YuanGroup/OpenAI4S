# `openai4s/judgment/`

[中文说明](README_zh.md)

Vendor-neutral **experimental semantic judgment layer**. It turns discrete
judgments that today live in keyword rules or "ask the LLM for JSON" into
typed, probabilistic, recorded, evaluable, replayable steps. The first vendor
is TypeSafe Jev. The whole layer is **default-off**; W0 defines types, the
backend port, flags, and disclosure text only. No HTTP client, dispatcher
hook, tool, or gateway route lives here yet.

## Where this fits

Host-side callers (later: `JudgmentService`) talk to a `JudgmentBackend`.
W0 ships `NullBackend`, which always raises `BackendError("disabled")`.
Effective flags are resolved from env, then the Store, then default off,
and the UI path also requires a current-version disclosure acknowledgement.

## Files

| File | Responsibility |
| --- | --- |
| [`__init__.py`](__init__.py) | Public exports: question/answer types, the backend port, flag resolution, disclosure helpers. |
| [`types.py`](types.py) | Frozen dataclasses for `Noul` / `Choice` / `Score`, `Answer`, `JudgmentResult`, `BackendReply`, plus `to_api()` / `to_dict()`. |
| [`port.py`](port.py) | `JudgmentBackend` Protocol, `BackendError` codes, and `NullBackend`. |
| [`flags.py`](flags.py) | `resolve(cfg, store)` and the six-rule precedence in `JUDGMENT_FLAG_PRECEDENCE`. Store access is read-only. |
| [`disclosure.py`](disclosure.py) | `DISCLOSURE_VERSION`, per-capability copy, fixed facts, `is_acknowledged`. |
| [`typesafe.py`](typesafe.py) | stdlib TypeSafe System One client (`TypeSafeBackend`). Resolves the key per request, refuses redirects, caps the body at 2 MiB, and retries only 429/529/503 inside one deadline. |
| [`validate.py`](validate.py) | `parse_response`: exact id set, matching types, in-range probabilities; any violation is `invalid_response` with no partial adoption. |
| [`registry.py`](registry.py) | Template registry (`register_template` / `get_template`) and the builtin `system.probe` connection-test template. |
| [`settings.py`](settings.py) | Settings surface for the gateway and doctor: `status` / `update` / `probe_connection`, TypeSafe key via SecretBroker (never env, never echoed), and an egress *report* of whether `api.typesafe.ai` is already authorized. Does not add an egress group. |
| [`templates/`](templates/) | Skill-suggestion question templates (`skills.suggest`) and the generated bioSkills area index. |
| [`task_mode_shadow.py`](task_mode_shadow.py) | Bounded background channel for `task_mode.classify` shadow records. Never changes `resolve_task_mode` returns and never logs the raw request. |
