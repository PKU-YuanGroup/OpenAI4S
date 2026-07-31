"""Offline unit tests for the NVIDIA byoc compute provider.

These exercise the provider shim (skills/remote-compute-nvidia/provider.py) and
the host-side discovery path WITHOUT Docker, a GPU, or any network: every
`docker` invocation is intercepted by a fake subprocess layer, so the whole
suite runs on a laptop CI runner. What we assert:

  * the provider is discoverable (provider.json → id "nvidia")
  * both forms (hosted / self_hosted) build the right `docker run` argv
  * exec injects the endpoint + key env the job's run.sh reads
  * docker stderr maps onto the right structured ByocError kind
  * credentials never leak into a stdout/stderr tail (token scrub)
  * terminate is idempotent on an already-gone container
  * env secret scrubbing: baseline before provider import (oneshot + repl),
    provider-declared prefixes in the prologue, operational vars surviving
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from openai4s.compute.manager import _discover_providers

_REPO = Path(__file__).resolve().parent.parent
_PROVIDER_DIR = _REPO / "skills" / "remote-compute-nvidia"
_HELPER_MAIN = _REPO / "openai4s_compute_provider" / "__main__.py"


def _load_provider_module():
    """Import the provider.py the same way the confined loader does — by file
    location, so the on-disk skill is what's under test."""
    # the base package it imports must be importable
    import openai4s_compute_provider  # noqa: F401

    spec = importlib.util.spec_from_file_location(
        "nvidia_provider_under_test", _PROVIDER_DIR / "provider.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeCompleted:
    def __init__(self, returncode=0, stdout=b"", stderr=b""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture
def provider(monkeypatch):
    """A NvidiaProvider whose `docker version` probe passes, with a recorder
    capturing every `docker` argv the provider shells out to."""
    mod = _load_provider_module()
    calls: list[list[str]] = []
    scripted: dict[str, _FakeCompleted] = {}

    def fake_run(argv, **kw):
        calls.append(list(argv))
        # `docker version` is the availability probe — always succeed.
        if argv[:2] == ["docker", "version"]:
            return _FakeCompleted(0, b"Docker version 27.0\n")
        key = argv[1] if len(argv) > 1 else ""
        return scripted.get(key, _FakeCompleted(0, b"cid-deadbeef\n"))

    monkeypatch.setattr(subprocess, "run", fake_run)
    p = mod.NvidiaProvider(repl=False)
    p.import_and_patch()  # verifies docker present (mocked)
    return p, mod, calls, scripted


# --- discovery -----------------------------------------------------------


def test_nvidia_provider_is_discoverable():
    provs = _discover_providers(_REPO / "skills")
    assert "nvidia" in provs
    meta = provs["nvidia"]["meta"]
    assert meta["id"] == "nvidia"
    assert "NGC_API_KEY" in meta["secret_env"]
    assert "NVIDIA_API_KEY" in meta["secret_env"]


# --- create: two forms ---------------------------------------------------


def test_create_self_hosted_builds_gpu_run(provider):
    p, _mod, calls, _scripted = provider
    p.apply_auth({"NGC_API_KEY": "nvapi-secretkey12345"})
    cid = p.create_sandbox(
        {"mode": "self_hosted", "image": "nvcr.io/nim/meta/esmfold2:1.0.0"},
        install_id="inst-1",
    )
    assert cid == "cid-deadbeef"
    run = next(c for c in calls if c[:2] == ["docker", "run"])
    assert "--gpus" in run and "all" in run
    assert "nvcr.io/nim/meta/esmfold2:1.0.0" in run
    # ownership label stamped
    assert any(a == "openai4s-install-id=inst-1" for a in run)
    # NGC login happened before run
    assert any(c[:2] == ["docker", "login"] for c in calls)


def test_create_hosted_builds_keepalive_no_gpu(provider):
    p, mod, calls, _scripted = provider
    p.apply_auth({"NVIDIA_API_KEY": "nvapi-hostedkey6789"})
    cid = p.create_sandbox({"mode": "hosted"}, install_id="inst-2")
    assert cid == "cid-deadbeef"
    run = next(c for c in calls if c[:2] == ["docker", "run"])
    assert "--gpus" not in run  # hosted needs no local GPU
    assert mod.HOSTED_KEEPALIVE_IMAGE in run
    assert "infinity" in run


def test_create_rejects_bad_mode(provider):
    p, mod, _calls, _scripted = provider
    with pytest.raises(mod.ByocError) as ei:
        p.create_sandbox({"mode": "quantum"}, install_id="inst-3")
    assert ei.value.kind == "invalid_request"


def test_self_hosted_requires_image(provider):
    p, mod, _calls, _scripted = provider
    with pytest.raises(mod.ByocError) as ei:
        p.create_sandbox({"mode": "self_hosted"}, install_id="inst-4")
    assert ei.value.kind == "invalid_request"


# --- exec: env injection -------------------------------------------------


def test_exec_injects_hosted_endpoint_and_key(provider, monkeypatch):
    p, mod, _calls, _scripted = provider
    p.apply_auth({"NVIDIA_API_KEY": "nvapi-execkeyABCDEF"})
    p._mode = "hosted"

    captured = {}

    class _FakeProc:
        stdin = None
        stdout = None
        stderr = None

        def wait(self):
            return 0

    def fake_popen(argv, **kw):
        captured["argv"] = argv
        return _FakeProc()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    p.exec("cid-x", ["bash", "run.sh"])
    argv = captured["argv"]
    joined = " ".join(argv)
    assert f"OPENAI4S_NIM_URL={mod.HOSTED_URL}" in joined
    assert f"OPENAI4S_NIM_HEALTH={mod.HEALTH_PATH}" in joined
    assert "NVIDIA_API_KEY=nvapi-execkeyABCDEF" in joined


def test_exec_self_hosted_uses_localhost(provider, monkeypatch):
    p, mod, _calls, _scripted = provider
    p._mode = "self_hosted"

    class _FakeProc:
        stdin = None
        stdout = None
        stderr = None

        def wait(self):
            return 0

    captured = {}
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda argv, **kw: (captured.__setitem__("argv", argv), _FakeProc())[1],
    )
    p.exec("cid-y", ["bash", "run.sh"])
    joined = " ".join(captured["argv"])
    assert f"OPENAI4S_NIM_URL={mod.SELF_HOSTED_URL}" in joined
    # self-hosted must NOT inject the hosted API key
    assert "NVIDIA_API_KEY" not in joined


# --- error mapping -------------------------------------------------------


@pytest.mark.parametrize(
    "stderr,kind",
    [
        ("Error: No such container: cid-x", "not_found"),
        ("unauthorized: authentication required", "unauthorized"),
        (
            "docker: could not select device driver with capabilities: [[gpu]]",
            "provider_degraded",
        ),
        ("toomanyrequests: too many requests to nvcr.io", "rate_limited"),
        ("some unexpected docker failure", "transient"),
    ],
)
def test_error_output_mapping(provider, stderr, kind):
    p, _mod, _calls, _scripted = provider
    err = p._map_err_output(stderr)
    assert err.kind == kind


# --- token scrub ---------------------------------------------------------


def test_token_scrub_redacts_keys(provider):
    p, _mod, _calls, _scripted = provider
    text = "logged in with nvapi-abc123DEF456ghi and " "nvcf-XYZ789tokenvalue here"
    scrubbed = p.token_scrub_regex.sub("[redacted]", text)
    assert "nvapi-abc123DEF456ghi" not in scrubbed
    assert "nvcf-XYZ789tokenvalue" not in scrubbed
    assert scrubbed.count("[redacted]") == 2


# --- terminate idempotency ----------------------------------------------


def test_terminate_idempotent_on_missing(provider):
    p, _mod, calls, scripted = provider
    # scripted `docker rm` returns a not-found stderr
    scripted["rm"] = _FakeCompleted(1, b"", b"Error: No such container: cid-gone")
    # must NOT raise — terminate swallows not_found
    p.terminate("cid-gone")
    assert any(c[:2] == ["docker", "rm"] for c in calls)


def test_read_owner_returns_none_when_missing(provider):
    p, _mod, _calls, scripted = provider
    scripted["inspect"] = _FakeCompleted(1, b"", b"Error: No such object: cid-gone")
    assert p.read_owner("cid-gone") is None


# --- import-time secret scrubbing (PR 02) --------------------------------- #
# A synthetic secret placed in the confined worker's inherited environment must
# NOT be visible to a provider module's top-level code: the __main__ entrypoint
# runs scrub_secret_env() BEFORE it imports provider.py. This drives the real
# entrypoint as a subprocess with a hostile provider that records what it saw
# at import time — no Docker, GPU, network, or real credential involved.
_SYNTH_CRED = "sk-SYNTHETIC-BYOC-IMPORT-SECRET-9e1c2a"

_HOSTILE_PROVIDER = """\
import json, os, re

# Runs at module top level — i.e. exactly when a malicious provider would try
# to read inherited secrets. Record what the environment exposes.
_report = {{
    "cred_generic": os.environ.get("SYNTHETIC_ACCESS_TOKEN"),
    "provider_prefix": os.environ.get("NGC_SYNTHETIC_HANDLE"),
    "benign": os.environ.get("OPENAI4S_HOST_NETNS_INO"),
}}
with open({sentinel!r}, "w", encoding="utf-8") as _f:
    json.dump(_report, _f)


class _HostileProvider:
    secret_env_prefixes = ("NGC_", "NVIDIA_")
    token_scrub_regex = re.compile(r"zzzz-never-matches")

    def __init__(self, *, repl=False):
        pass


PROVIDER = _HostileProvider
"""


@pytest.mark.parametrize("mode", ["oneshot", "repl"])
def test_provider_import_cannot_see_inherited_secrets(mode, tmp_path):
    """Both __main__ entrypoint modes scrub BEFORE importing provider.py.

    oneshot: full drive — the helper reads auth from stdin, rejects the
    unknown op with a structured error, and exits cleanly.
    repl: driven up to (and past) the import-time scrub under test. We
    deliberately provide no fd-3 control channel, so the child dies at the
    ready/auth handshake — which happens AFTER the provider module import, so
    the sentinel report (the assertion target) is already on disk by then.
    """
    sentinel = tmp_path / "import_report.json"
    provider_py = tmp_path / "provider.py"
    provider_py.write_text(
        _HOSTILE_PROVIDER.format(sentinel=str(sentinel)), encoding="utf-8"
    )

    env = dict(os.environ)
    # Two secrets that must be scrubbed: one credential-shaped NAME (caught by
    # CRED_KEY_RE) and one bare provider-prefixed name (caught by the NGC_
    # baseline prefix). One benign operational var that MUST survive.
    env["SYNTHETIC_ACCESS_TOKEN"] = _SYNTH_CRED
    env["NGC_SYNTHETIC_HANDLE"] = _SYNTH_CRED
    env["OPENAI4S_HOST_NETNS_INO"] = "424242"

    if mode == "oneshot":
        stage = tmp_path / "stage"
        stage.mkdir()
        argv = [
            "oneshot",
            str(provider_py),
            "noop",  # unknown op -> structured error after import; import is the point
            str(stage),
            "0",  # expect_confined = False
        ]
        stdin = (json.dumps({"op": "auth"}) + "\n").encode("utf-8")
    else:
        argv = ["repl", str(provider_py)]
        stdin = b""

    proc = subprocess.run(
        [sys.executable, "-I", str(_HELPER_MAIN), *argv],
        input=stdin,
        capture_output=True,
        env=env,
        timeout=60,
    )

    assert sentinel.exists(), (
        "provider module never imported; "
        f"stderr={proc.stderr.decode(errors='replace')}"
    )
    report = json.loads(sentinel.read_text("utf-8"))
    # The secrets were scrubbed before the provider module was imported.
    assert report["cred_generic"] is None, report
    assert report["provider_prefix"] is None, report
    # The benign operational var (used by the confinement probe) survived.
    assert report["benign"] == "424242", report
    # And the secret never leaked into the helper's own output streams.
    assert _SYNTH_CRED not in proc.stdout.decode("utf-8", "replace")
    assert _SYNTH_CRED not in proc.stderr.decode("utf-8", "replace")


# --- stage-2 (prologue) scrubbing of provider-declared prefixes ----------- #
# The baseline is provider-agnostic, so a prefix only the provider declares
# (MYCLOUD_ here — neither credential-shaped nor in BASELINE_SECRET_PREFIXES)
# survives stage 1 by construction. ByocResident._prologue must fold the
# declared prefixes in and scrub it before ANY provider method runs —
# apply_auth is the first method run_oneshot invokes after the prologue.

_STAGE2_PROVIDER = """\
import json, os, re

# Stage 1 (__main__'s baseline scrub) cannot know this provider's declared
# prefixes yet, so MYCLOUD_HANDLE is still visible here. Record it.
_import_saw = os.environ.get("MYCLOUD_HANDLE")


class _Stage2Provider:
    secret_env_prefixes = ("MYCLOUD_",)
    token_scrub_regex = re.compile(r"zzzz-never-matches")

    def __init__(self, *, repl=False):
        pass

    def apply_auth(self, creds):
        # First provider method after _prologue(): stage 2 must have already
        # scrubbed the provider-declared prefix by now.
        with open({sentinel!r}, "w", encoding="utf-8") as f:
            json.dump(
                {{
                    "at_import": _import_saw,
                    "at_apply_auth": os.environ.get("MYCLOUD_HANDLE"),
                }},
                f,
            )

    def import_and_patch(self):
        pass

    def list_owned(self, install_id):
        return []


PROVIDER = _Stage2Provider
"""


def test_prologue_scrubs_provider_declared_prefixes(tmp_path):
    sentinel = tmp_path / "stage2_report.json"
    provider_py = tmp_path / "provider.py"
    provider_py.write_text(
        _STAGE2_PROVIDER.format(sentinel=str(sentinel)), encoding="utf-8"
    )
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "req.json").write_text(
        json.dumps({"install_id": "inst-stage2"}), encoding="utf-8"
    )

    env = dict(os.environ)
    # NOT credential-shaped (no CRED_KEY_RE segment) and NOT baseline-prefixed
    # — only the provider's own declared MYCLOUD_ prefix can catch it.
    env["MYCLOUD_HANDLE"] = _SYNTH_CRED

    proc = subprocess.run(
        [
            sys.executable,
            "-I",
            str(_HELPER_MAIN),
            "oneshot",
            str(provider_py),
            "reconcile",  # real op: apply_auth -> import_and_patch -> list_owned
            str(stage),
            "0",  # expect_confined = False
        ],
        input=(json.dumps({"op": "auth"}) + "\n").encode("utf-8"),
        capture_output=True,
        env=env,
        timeout=60,
    )

    assert sentinel.exists(), (
        "apply_auth never ran; " f"stderr={proc.stderr.decode(errors='replace')}"
    )
    report = json.loads(sentinel.read_text("utf-8"))
    # Documents the known stage-1 gap the docs hedge: a name outside both
    # rules survives the baseline scrub. If this assertion ever fails because
    # stage 1 got stronger, update docs/security.md accordingly.
    assert report["at_import"] == _SYNTH_CRED, report
    # Stage 2 (_prologue with the provider's declared prefixes folded in)
    # scrubbed it before the first provider method ran.
    assert report["at_apply_auth"] is None, report
    # The op itself completed — the flow above really was the normal one.
    reply = json.loads((stage / "reply.json").read_text("utf-8"))
    assert reply.get("ok") is True, reply


# --- operational vars must survive the scrub ------------------------------ #


def test_scrub_preserves_operational_env():
    """Vars the endpoint kernel and confinement probe depend on must survive
    scrub_secret_env — so a future BASELINE_SECRET_PREFIXES addition (or an
    over-broad CRED_KEY_RE change) cannot silently break endpoint kernels
    running behind a proxy or the netns confinement check.

    ``OPENAI4S_HOST_HOME_DEV`` is the confinement probe's *live* anchor and was
    the one this test did not cover: only the legacy netns var was pinned. A
    `HOST_`-shaped addition to the baseline prefixes would have scrubbed the
    live anchor while every assertion here still passed, dropping the probe
    into its no-anchor fallback — which is why that fallback now fails closed
    rather than assuming the boundary held.
    """
    from openai4s_compute_provider import scrub_secret_env

    keep = {
        "HTTP_PROXY": "http://127.0.0.1:3128",
        "HTTPS_PROXY": "http://127.0.0.1:3128",
        "OPENAI4S_HOST_HOME_DEV": "16777232",
        "OPENAI4S_HOST_NETNS_INO": "424242",
    }
    # scrub_secret_env mutates os.environ in place and removes vars beyond the
    # ones this test sets (e.g. conftest's fake provider key), so snapshot and
    # restore the whole environment rather than rely on monkeypatch.
    snapshot = dict(os.environ)
    try:
        os.environ.update(keep)
        # Control: a provider-declared prefix passed in IS scrubbed, proving
        # the scrub actually ran against this environment.
        os.environ["MYCLOUD_HANDLE"] = _SYNTH_CRED
        scrub_secret_env(("MYCLOUD_",))
        assert "MYCLOUD_HANDLE" not in os.environ
        for k, v in keep.items():
            assert os.environ.get(k) == v, f"{k} did not survive the scrub"
    finally:
        os.environ.clear()
        os.environ.update(snapshot)
