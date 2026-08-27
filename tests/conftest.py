"""Pytest fixtures + path setup for the openai4s test suite."""

import os
import re
import sys
from pathlib import Path

import pytest

# The repository's ignored .env belongs to the running app, never to offline
# tests — but ``openai4s.config`` loads it into os.environ the moment any test
# imports it. Refusing the load outright is what makes the suite hermetic: a
# developer's real OPENAI4S_ARK_BASE_URL / OPENAI4S_LLM_MODEL turned three
# model-catalog contracts red, and definition-time defaults such as
# OPENAI4S_LLM_MAX_TOKENS are frozen at import, where no fixture can reach a
# leaked value afterwards.
os.environ["OPENAI4S_SKIP_DOTENV"] = "1"

# Shell-exported config leaks exactly the same way the .env did, so drop every
# endpoint/model/key variable ``LLMConfig.__post_init__`` consults before the
# fakes below re-pin the ones the suite needs. The pattern spans the
# per-provider names (OPENAI4S_ARK_MODEL, OPENAI4S_CLAUDE_BASE_URL, ...) and
# the whole generic OPENAI4S_LLM_* family.
_LLM_ENV_LEAK = re.compile(
    r"^OPENAI4S_(?:LLM_[A-Z0-9_]+|[A-Z0-9_]*(?:BASE_URL|MODEL|API_KEY))$"
)
_ROADMAP_ENV_LEAK = (
    "OPENAI4S_STAGE1_TRUSTED_DELIVERY",
    "OPENAI4S_STAGE2_AUTO_RUN_STORAGE",
    "OPENAI4S_STAGE3_SCIENTIFIC_REVIEW_SHADOW",
    "OPENAI4S_STAGE4_REVIEW_COMPLETION_GATE",
    "OPENAI4S_STAGE5_AUTO_REPAIR",
    "OPENAI4S_STAGE6_GUARDIAN_SHADOW",
    "OPENAI4S_STAGE7_GUARDIAN_ENFORCEMENT",
    "OPENAI4S_STAGE8_LIVE_NOTEBOOK_LINEAGE",
    "OPENAI4S_STAGE9_ARTIFACT_WORKBENCH",
    "OPENAI4S_STAGE10_SCIENTIFIC_CONNECTORS",
    "OPENAI4S_STAGE11_DURABLE_REMOTE_COMPUTE",
    "OPENAI4S_STAGE12_AUTO_MODE_GA",
)
for _name in [n for n in os.environ if _LLM_ENV_LEAK.match(n)]:
    del os.environ[_name]
for _name in _ROADMAP_ENV_LEAK:
    os.environ.pop(_name, None)

# The non-LLM definition-time defaults leak the same way and are frozen at the
# same moment: ``Config``'s field defaults read these at class definition, so
# a developer's `export OPENAI4S_PORT=9760` (the documented second-daemon
# setup) or a stray OPENAI4S_RECORD_TAPE=1 silently reshapes every test in the
# session with no fixture able to undo it.
for _name in (
    "OPENAI4S_HOST",
    "OPENAI4S_PORT",
    "OPENAI4S_MAX_TURNS",
    "OPENAI4S_EXPLORE_MAX_TURNS",
    "OPENAI4S_CONTEXT_WINDOW",
    "OPENAI4S_COMPACTION_TRIGGER_RATIO",
    "OPENAI4S_RECORD_TAPE",
    # Team-server posture. `Config` reads both on *every* construction
    # (`_env_flag("OPENAI4S_TEAM_MODE")`, `_data_roots()`), and
    # docs/team-server.md tells operators to export them — so the developer
    # most likely to have them set is the one running this suite. Measured
    # rather than assumed: a green subset produced 11 failures in
    # tests/test_gateway.py under OPENAI4S_TEAM_MODE=1 (token gate,
    # DNS-rebind, WS upgrade, error envelopes), and OPENAI4S_DATA_ROOTS
    # turned test_team_files.py's unconfigured-roots shape from 404 into
    # 200. Neither is matched by the `_LLM_ENV_LEAK` regex above, which
    # wants a MODEL/BASE_URL/API_KEY suffix — `MODE` is not `MODEL`.
    "OPENAI4S_TEAM_MODE",
    "OPENAI4S_DATA_ROOTS",
    "OPENAI4S_TRUSTED_PROXY_ORIGINS",
    # The worker listener, for the same reason: an exported value would
    # start a real socket in tests that assert the feature is off.
    "OPENAI4S_WORKER_LISTEN",
    "OPENAI4S_WORKER_CONNECT",
    "OPENAI4S_WORKER_ADVERTISE",
    "OPENAI4S_RECONCILE_INTERVAL",
):
    os.environ.pop(_name, None)

# These must exist before any test module imports ``openai4s.config`` because
# several dataclass defaults are resolved at module/class definition time.
os.environ["OPENAI4S_LLM_PROVIDER"] = "deepseek"
os.environ["OPENAI4S_DEEPSEEK_API_KEY"] = "test-key"
os.environ["OPENAI4S_LLM_API_KEY"] = "test-key"
os.environ["OPENAI4S_UNATTENDED_APPROVAL"] = "deny"
os.environ["OPENAI4S_NOTEBOOK_REPL"] = "0"
os.environ["OPENAI4S_ALLOW_PRIVATE_FETCH"] = "0"
os.environ["OPENAI4S_ALLOW_FAKE_IP_DNS"] = "0"
# Keep the suite out of the developer's real login keychain, for the same
# reason ~/.openai4s is redirected below. Left on `auto`, every Store that
# touched a credential would write to it — and the broker's resolution
# self-test would round-trip through it on top. Tests that mean to exercise a
# keychain backend construct it explicitly.
os.environ["OPENAI4S_SECRET_STORE"] = "plaintext"
# Nothing in the offline suite may reach the real telemetry endpoint, for the
# same reason the share relay is cleared below — and this one is not
# hypothetical. A benchmark case granted consent to itself, sealed a payload
# under that just-minted identity and called `sender.send`, so every refusal in
# `_send_locked` passed and `uv run pytest` really POSTed a fresh install id to
# log.openai4s.org from every developer machine and CI runner.
#
# Belt to the braces: the two live-send paths are fixed at their source (the
# benchmark step takes a recording transport, and the overflow test stubs its
# opener like every sibling), so this is what catches the *next* one rather
# than what rescues those.
#
# Loopback rather than an unroutable name: `endpoint()` must still return a
# *valid* https URL so its own validation keeps running normally and the
# transmission tests still exercise a send, and 127.0.0.1 means no DNS query
# either — the sender's docstring counts a lookup of the real host as egress in
# itself. Port 1 needs root to bind, so nothing can be listening. A test that
# means to assert the built-in default clears this var explicitly.
os.environ["OPENAI4S_TELEMETRY_ENDPOINT"] = "https://127.0.0.1:1/v1/events"

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


@pytest.fixture(autouse=True)
def isolated_openai4s_home(tmp_path, monkeypatch):
    """Keep tests off the developer's real ~/.openai4s database."""
    import openai4s.config as config_mod
    import openai4s.store as store_mod

    def reset_singletons():
        for st in list(store_mod._STORES.values()):
            try:
                st.close()
            except Exception:
                pass
        store_mod._STORES.clear()
        config_mod._CONFIG = None

    reset_singletons()
    # Re-applied per test, for the same reason as the module-level purge:
    # ``LLMConfig.__post_init__`` reads these fresh on every construction, and
    # a test that exported one directly into os.environ (instead of via
    # monkeypatch) must not hand it to the next test.
    monkeypatch.setenv("OPENAI4S_SKIP_DOTENV", "1")
    for name in [n for n in os.environ if _LLM_ENV_LEAK.match(n)]:
        monkeypatch.delenv(name, raising=False)
    # Rollout flags are also configuration inputs. A developer exercising an
    # opt-in stage locally must not silently change the default response-shape
    # capture or activate later-stage behavior in the offline suite. Tests
    # that cover a stage set an explicit Config or set the variable after this
    # fixture has established the off baseline.
    for name in _ROADMAP_ENV_LEAK:
        monkeypatch.delenv(name, raising=False)
    # The provider-native last-resort keys (ANTHROPIC_API_KEY, OPENAI_API_KEY,
    # ...) feed the same resolver: a developer's real export would make a
    # keyless-config test see a configured provider.
    for native in sorted(
        {v for names in config_mod._NATIVE_KEY_ENV.values() for v in names}
    ):
        monkeypatch.delenv(native, raising=False)
    monkeypatch.setenv("OPENAI4S_DATA_DIR", str(tmp_path / "openai4s-data"))
    monkeypatch.setenv("OPENAI4S_LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("OPENAI4S_DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI4S_LLM_API_KEY", "test-key")
    # A developer may intentionally run the local app fail-open via .env, but
    # the offline suite must keep a deterministic deny-by-default baseline.
    monkeypatch.setenv("OPENAI4S_UNATTENDED_APPROVAL", "deny")
    monkeypatch.setenv("OPENAI4S_NOTEBOOK_REPL", "0")
    monkeypatch.setenv("OPENAI4S_ALLOW_PRIVATE_FETCH", "0")
    # Never the developer's real keychain — see the module-level default.
    monkeypatch.setenv("OPENAI4S_SECRET_STORE", "plaintext")
    # Re-applied per test: a test that overrode the endpoint for its own reasons
    # must not leave the next one pointed at the real host. See the module-level
    # default for why this is set at all.
    monkeypatch.setenv("OPENAI4S_TELEMETRY_ENDPOINT", "https://127.0.0.1:1/v1/events")
    # A developer's git-ignored .env (loaded at import) may configure web sharing;
    # the offline suite must never inherit it (it would try a real relay). The
    # same rule, not the same subject: a machine that sets the MCP deadline
    # would give every timeout assertion in the suite a different budget from
    # CI's, which is a test that measures the developer's environment.
    for var in (
        "OPENAI4S_SHARE_RELAY_URL",
        "OPENAI4S_SHARE_AUTH_TOKEN",
        "OPENAI4S_SHARE_BASE_DOMAIN",
        "OPENAI4S_SHARE_ALLOW_INSECURE",
        "OPENAI4S_MCP_DEADLINE_S",
    ):
        monkeypatch.delenv(var, raising=False)
    # The BYOC confinement self-test caches its verdict process-wide, keyed by
    # the backend binary and the home directory. A test that stubs
    # `subprocess.Popen` for its own reasons can make that probe answer
    # something meaningless, and the answer would then stand for every test
    # after it. Cleared on both sides so no verdict is inherited.
    _reset_confinement_self_test()
    yield
    _reset_confinement_self_test()
    reset_singletons()


def _reset_confinement_self_test() -> None:
    try:
        from openai4s.security import byoc_confinement

        byoc_confinement.reset_self_test_cache()
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# response-shape capture (off unless asked for)
# ---------------------------------------------------------------------------
#
# `scripts/capture_response_schemas.py` sets OPENAI4S_CAPTURE_SCHEMAS to a path
# and reruns this suite; every gateway response the tests provoke along the way
# is generalised into a shape and frozen. Without the variable this costs one
# environment lookup at collection time and changes nothing.


def pytest_configure(config):
    destination = os.environ.get("OPENAI4S_CAPTURE_SCHEMAS")
    if not destination:
        return
    from openai4s.server import gateway as gateway_mod
    from openai4s.server import response_capture

    recorder = response_capture.Recorder()
    response_capture.install(gateway_mod, recorder)
    config._openai4s_recorder = (recorder, Path(destination))


@pytest.fixture(autouse=True)
def _pause_capture_for_stubbed_backends(request):
    """A test that stubs the service must not publish a response shape.

    `docs/response-schemas.json` claims to be captured from real responses. A
    test that replaces `runner.restart_kernel` with a lambda returning
    `{"ok": ...}` would otherwise freeze that fabrication as the route's
    contract -- provenance that is wrong rather than absent, which is the worse
    failure because a reader believes it.
    """
    captured = getattr(request.config, "_openai4s_recorder", None)
    if not captured or request.node.get_closest_marker("stubbed_backend") is None:
        yield
        return
    recorder = captured[0]
    previous, recorder.paused = recorder.paused, True
    try:
        yield
    finally:
        recorder.paused = previous


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    config = session.config
    captured = getattr(config, "_openai4s_recorder", None)
    if not captured:
        return
    recorder, destination = captured
    from openai4s.server import response_capture

    # Split runs. Publish inside sessionfinish, before xdist's hookwrapper sends
    # `workerfinished`; pytest_unconfigure is too late, because a write failure
    # there occurs after the controller has already accepted exit status 0.
    worker_input = getattr(config, "workerinput", {})
    worker = worker_input.get("workerid")
    if worker:
        try:
            response_capture.save_partial(
                recorder,
                destination,
                worker,
                worker_count=worker_input.get("workercount"),
                run_id=worker_input.get("testrunuid"),
            )
        except Exception as error:
            # Xdist snapshots the original exit status before yielding to this
            # hook and otherwise reports success even when share publication
            # raises. Mutate the worker output it is about to send as well as
            # the local session; assembly's expected-count check remains the
            # independent backstop if the worker disappears entirely.
            message = (
                f"response capture share for {worker} could not be published: "
                f"{type(error).__name__}: {error}"
            )
            session.shouldfail = message
            session.exitstatus = pytest.ExitCode.INTERNAL_ERROR
            worker_output = getattr(config, "workeroutput", None)
            if isinstance(worker_output, dict):
                worker_output["shouldfail"] = message
                worker_output["exitstatus"] = int(pytest.ExitCode.INTERNAL_ERROR)
            raise
        return
    # The controller of a split run executes no tests, so its recorder is
    # empty. Writing it would put an empty capture where the merge is about to
    # put the real one -- harmless today because `assemble` overwrites it, and
    # a fabricated "the suite reached no route" the moment anything reads the
    # file before the merge.
    if getattr(config.option, "numprocesses", None):
        return
    response_capture.save(recorder.document(), destination)
