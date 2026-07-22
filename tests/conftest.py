"""Pytest fixtures + path setup for the openai4s test suite."""
import os
import sys
from pathlib import Path

import pytest

# These must exist before any test module imports ``openai4s.config`` because
# several dataclass defaults are resolved at module/class definition time.  The
# repository's ignored .env belongs to the running app, never to offline tests.
os.environ["OPENAI4S_LLM_PROVIDER"] = "deepseek"
os.environ["OPENAI4S_DEEPSEEK_API_KEY"] = "test-key"
os.environ["OPENAI4S_LLM_API_KEY"] = "test-key"
os.environ["OPENAI4S_ARK_API_KEY"] = ""
os.environ["OPENAI4S_UNATTENDED_APPROVAL"] = "deny"
os.environ["OPENAI4S_NOTEBOOK_REPL"] = "0"
os.environ["OPENAI4S_ALLOW_PRIVATE_FETCH"] = "0"
# Keep the suite out of the developer's real login keychain, for the same
# reason ~/.openai4s is redirected below. Left on `auto`, every Store that
# touched a credential would write to it — and the broker's resolution
# self-test would round-trip through it on top. Tests that mean to exercise a
# keychain backend construct it explicitly.
os.environ["OPENAI4S_SECRET_STORE"] = "plaintext"

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
    # A developer's git-ignored .env (loaded at import) may configure web sharing;
    # the offline suite must never inherit it (it would try a real relay).
    for var in (
        "OPENAI4S_SHARE_RELAY_URL",
        "OPENAI4S_SHARE_AUTH_TOKEN",
        "OPENAI4S_SHARE_BASE_DOMAIN",
        "OPENAI4S_SHARE_ALLOW_INSECURE",
    ):
        monkeypatch.delenv(var, raising=False)
    yield
    reset_singletons()


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


def pytest_unconfigure(config):
    captured = getattr(config, "_openai4s_recorder", None)
    if not captured:
        return
    recorder, destination = captured
    from openai4s.server import response_capture

    response_capture.save(recorder.document(), destination)
