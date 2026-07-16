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
    yield
    reset_singletons()
