"""Offline installation tests execute a fake interpreter, never pip/network."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from openai4s.lab import provider_env
from openai4s.lab.models import ErrorCode, LabError
from openai4s_lab_provider.chemgymrl import PROFILES, SOURCE_SHA

_DRIVER = r"""
import json, os, pathlib, sys
config_path = pathlib.Path(CONFIG_PATH)
config = json.loads(config_path.read_text())
args = sys.argv[1:]
if "find" in args:
    stage = "find"
elif "venv" in args:
    stage = "venv"
elif "--require-hashes" in args:
    stage = "dependencies"
elif "--use-pep517" in args:
    stage = "source"
elif "--describe" in args:
    stage = args[args.index("--describe") + 1]
elif "-c" in args and "sys.implementation" in args[-1]:
    stage = "identity"
elif "-c" in args and "direct_url.json" in args[-1]:
    stage = "receipt"
else:
    raise SystemExit("unexpected invocation: " + repr(args))
pointer = pathlib.Path(config["pointer"])
record = {"stage": stage, "args": args, "env": dict(os.environ),
          "current": pointer.read_text() if pointer.exists() else None,
          "cwd": os.getcwd()}
with pathlib.Path(config["events"]).open("a") as events:
    events.write(json.dumps(record) + "\n")
if stage in ("dependencies", "source"):
    # What pip and git do with these settings: print them, on both streams.
    for name in config.get("echo", []):
        print("Looking in indexes:", os.environ.get(name, ""))
        print("could not connect through", os.environ.get(name, ""), file=sys.stderr)
    for line in config.get("say", []):
        print(line)
if config.get("fail") == stage:
    print("simulated installation failure", file=sys.stderr)
    raise SystemExit(29)
if stage == "find":
    print(config.get("uv_python", config["python"]))
elif stage == "identity":
    print(json.dumps(config.get("identity", {"implementation":"cpython", "version":[3,10]})))
elif stage == "venv":
    target = pathlib.Path(args[-1]) / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(pathlib.Path(sys.argv[0]).read_text())
    target.chmod(0o755)
elif stage == "receipt":
    print(json.dumps(config["receipt"]))
elif stage in config["manifests"]:
    payload = pathlib.Path(config["manifests"][stage]).read_bytes()
    if config.get("mismatch") == stage:
        payload += b" "
    sys.stdout.buffer.write(payload)
"""


@pytest.fixture
def fake_python(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI4S_LAB_CHEMGYMRL_PYTHON", raising=False)
    executable = tmp_path / "fake-python"
    config_path = tmp_path / "fake-config.json"
    config = {
        "python": str(executable),
        "pointer": str(tmp_path / "lab/providers/chemgymrl/current"),
        "events": str(tmp_path / "fake-events.jsonl"),
        "receipt": {
            "version": "2.0.0",
            "direct_url": {
                "url": "https://github.com/chemgymrl/chemgymrl",
                "vcs_info": {"vcs": "git", "commit_id": SOURCE_SHA},
            },
        },
        "manifests": {
            profile: str(
                provider_env._package() / "chemgymrl/manifests" / (profile + ".json")
            )
            for profile in PROFILES
        },
    }
    config_path.write_text(json.dumps(config))
    executable.write_text(
        "#!"
        + sys.executable
        + "\n"
        + _DRIVER.replace("CONFIG_PATH", repr(str(config_path)))
    )
    executable.chmod(0o755)
    return executable, config_path, config


def _events(config):
    return [
        json.loads(line) for line in Path(config["events"]).read_text().splitlines()
    ]


def test_setup_verifies_all_profiles_before_atomic_activation_and_rollback(
    tmp_path, fake_python, monkeypatch
):
    executable, _, config = fake_python
    monkeypatch.setenv("OPENAI4S_LLM_API_KEY", "fixture-install-secret")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "fixture-install-secret")
    monkeypatch.setenv("HTTP_PROXY", "http://fixture:password@invalid.local")
    monkeypatch.setenv("PIP_EXTRA_INDEX_URL", "https://fixture:password@invalid.local")
    monkeypatch.setenv("PYTHONPATH", "/untrusted-imports")
    first = provider_env.setup_provider(tmp_path, python=executable)
    assert first["status"] == "installed"
    assert provider_env.resolve_provider_python(tmp_path) == first["python"]
    records = _events(config)
    assert [row["stage"] for row in records] == [
        "identity",
        "venv",
        "dependencies",
        "source",
        "receipt",
        *PROFILES,
    ]
    assert all(row["current"] is None for row in records)
    assert records[2]["args"][0] == "-I"
    assert "--no-deps" in records[2]["args"]
    assert "--require-hashes" in records[2]["args"]
    assert "--use-pep517" in records[3]["args"]
    # Upstream's build tooling comes from the hash-locked environment.
    assert "--no-build-isolation" in records[3]["args"]
    assert records[3]["args"][-1].endswith("@" + SOURCE_SHA)
    for row in records:
        env = row["env"]
        assert (
            not {
                "OPENAI4S_LLM_API_KEY",
                "AWS_SESSION_TOKEN",
                "PIP_EXTRA_INDEX_URL",
                "PYTHONPATH",
            }
            & env.keys()
        )
        # Only the two downloads keep the operator's proxy; the interpreter
        # probes and the steps that import upstream code never see it.
        assert ("HTTP_PROXY" in env) is (row["stage"] in ("dependencies", "source"))
        for key in (
            "HOME",
            "TMPDIR",
            "TMP",
            "TEMP",
            "PIP_CACHE_DIR",
            "UV_CACHE_DIR",
            "MPLCONFIGDIR",
            "NUMBA_CACHE_DIR",
        ):
            assert Path(env[key]).is_relative_to(tmp_path)
        assert Path(row["cwd"]).is_relative_to(tmp_path)
        assert env["PIP_CONFIG_FILE"] == os.devnull
        assert env["GIT_CONFIG_GLOBAL"] == os.devnull
        assert env["GIT_CONFIG_NOSYSTEM"] == "1"
    pointer = Path(config["pointer"])
    old_pointer = pointer.read_bytes()
    second = provider_env.setup_provider(tmp_path, python=executable)
    assert second["generation"] != first["generation"]
    assert second["previous_generation"] == first["generation"]
    assert all(
        row["current"] == old_pointer.decode()
        for row in _events(config)[len(records) :]
    )
    assert Path(first["python"]).is_file()
    rolled_back = provider_env.setup_provider(tmp_path, rollback=True)
    assert rolled_back["generation"] == first["generation"]
    assert rolled_back["previous_generation"] == second["generation"]
    assert provider_env.resolve_provider_python(tmp_path) == first["python"]
    assert not (pointer.parent / ".setup-lock").exists()


@pytest.mark.parametrize(
    "failure",
    [
        "identity",
        "venv",
        "dependencies",
        "source",
        "receipt",
        *PROFILES,
        "commit",
        "distribution_version",
        "manifest_bytes",
        "publish",
    ],
)
def test_failure_at_each_install_stage_preserves_previous_pointer(
    tmp_path, fake_python, monkeypatch, failure
):
    executable, config_path, config = fake_python
    first = provider_env.setup_provider(tmp_path, python=executable)
    pointer = Path(config["pointer"])
    before = pointer.read_bytes()
    if failure == "commit":
        config["receipt"]["direct_url"]["vcs_info"]["commit_id"] = "0" * 40
    elif failure == "distribution_version":
        config["receipt"]["version"] = "1.5.8"
    elif failure == "manifest_bytes":
        config["mismatch"] = PROFILES[1]
    elif failure == "publish":

        def cannot_replace(source, destination):
            raise OSError("simulated atomic rename failure")

        monkeypatch.setattr(provider_env.os, "replace", cannot_replace)
    else:
        config["fail"] = failure
    config_path.write_text(json.dumps(config))
    with pytest.raises(LabError) as error:
        provider_env.setup_provider(tmp_path, python=executable)
    assert error.value.code == ErrorCode.PROVIDER_UNAVAILABLE
    assert "Installation log:" in error.value.message
    assert pointer.read_bytes() == before
    assert provider_env.resolve_provider_python(tmp_path) == first["python"]
    generations = list(pointer.parent.glob("gen-*"))
    assert len(generations) == 2
    failed = next(path for path in generations if path.name != first["generation"])
    # A failed generation is not an environment: only its log is kept.
    assert [entry.name for entry in failed.iterdir()] == ["setup.log"]
    assert not (pointer.parent / ".setup-lock").exists()


def test_a_successful_setup_keeps_only_the_current_and_previous_generation(
    tmp_path, fake_python
):
    executable, config_path, config = fake_python
    first = provider_env.setup_provider(tmp_path, python=executable)
    root = Path(config["pointer"]).parent
    config["fail"] = "source"
    config_path.write_text(json.dumps(config))
    with pytest.raises(LabError):
        provider_env.setup_provider(tmp_path, python=executable)
    del config["fail"]
    config_path.write_text(json.dumps(config))
    second = provider_env.setup_provider(tmp_path, python=executable)
    # The failed attempt goes with the next success; rollback still works.
    assert {path.name for path in root.glob("gen-*")} == {
        first["generation"],
        second["generation"],
    }
    third = provider_env.setup_provider(tmp_path, python=executable)
    assert {path.name for path in root.glob("gen-*")} == {
        second["generation"],
        third["generation"],
    }
    rolled_back = provider_env.setup_provider(tmp_path, rollback=True)
    assert rolled_back["generation"] == second["generation"]


def test_resolution_override_invalid_pointer_and_missing_install_never_fall_back(
    tmp_path, fake_python, monkeypatch
):
    executable, _, config = fake_python
    assert provider_env.resolve_provider_python(tmp_path) is None
    installed = provider_env.setup_provider(tmp_path, python=executable)
    monkeypatch.setenv("OPENAI4S_LAB_CHEMGYMRL_PYTHON", str(executable))
    assert provider_env.resolve_provider_python(tmp_path) == str(executable)
    assert provider_env.provider_environment_status(tmp_path)["source"] == "override"
    monkeypatch.setenv("OPENAI4S_LAB_CHEMGYMRL_PYTHON", str(tmp_path / "missing"))
    assert provider_env.resolve_provider_python(tmp_path) is None
    monkeypatch.delenv("OPENAI4S_LAB_CHEMGYMRL_PYTHON")
    assert provider_env.resolve_provider_python(tmp_path) == installed["python"]
    pointer = Path(config["pointer"])
    pointer.write_text('{"generation":"../outside","previous_generation":null}')
    assert provider_env.resolve_provider_python(tmp_path) is None
    assert "invalid" in provider_env.provider_environment_status(tmp_path)["detail"]
    pointer.write_text('{"generation":null,"previous_generation":null}')
    assert provider_env.resolve_provider_python(tmp_path) is None


def test_dry_run_creates_nothing_and_does_not_start_interpreters(tmp_path, monkeypatch):
    data_dir = tmp_path / "absent"

    def cannot_spawn(*args, **kwargs):
        raise AssertionError("dry run spawned a process")

    monkeypatch.setattr(provider_env.subprocess, "Popen", cannot_spawn)
    plan = provider_env.setup_provider(data_dir, dry_run=True)
    assert plan["status"] == "dry_run"
    assert plan["commands"][0][-1].startswith(str(data_dir))
    assert len(plan["commands"]) == 4 + len(PROFILES)
    assert not data_dir.exists()
    assert provider_env.provider_environment_status(data_dir)["available"] is False
    assert not data_dir.exists()


@pytest.mark.parametrize("use_uv", [True, False])
def test_discovers_python310_from_uv_then_path(
    tmp_path, fake_python, monkeypatch, use_uv
):
    executable, _, config = fake_python
    uv = tmp_path / "uv"
    uv.write_text(executable.read_text())
    uv.chmod(0o755)
    monkeypatch.setattr(
        provider_env.shutil,
        "which",
        lambda name: (
            str(uv)
            if name == "uv" and use_uv
            else str(executable) if name == "python3.10" else None
        ),
    )
    result = provider_env.setup_provider(tmp_path)
    assert result["status"] == "installed"
    stages = [row["stage"] for row in _events(config)]
    assert (stages[0] == "find") is use_uv
    assert "identity" in stages


def test_wrong_python_and_unverified_rollback_are_refused(tmp_path, fake_python):
    executable, config_path, config = fake_python
    config["identity"] = {"implementation": "cpython", "version": [3, 13]}
    config_path.write_text(json.dumps(config))
    with pytest.raises(LabError, match="CPython 3.10"):
        provider_env.setup_provider(tmp_path, python=executable)
    assert not Path(config["pointer"]).exists()
    with pytest.raises(LabError, match="No verified previous"):
        provider_env.setup_provider(tmp_path, rollback=True)
    config.pop("identity")
    config_path.write_text(json.dumps(config))
    first = provider_env.setup_provider(tmp_path, python=executable)
    provider_env.setup_provider(tmp_path, python=executable)
    pointer = Path(config["pointer"])
    before = pointer.read_bytes()
    (pointer.parent / first["generation"] / "verified.json").unlink()
    with pytest.raises(LabError, match="No verified previous"):
        provider_env.setup_provider(tmp_path, rollback=True)
    assert pointer.read_bytes() == before


def test_a_generation_built_from_another_lock_is_not_verified(
    tmp_path, fake_python, monkeypatch
):
    # After an upgrade changes requirements.lock, the old generation must not
    # keep being reported (and used) as this release's verified environment.
    executable, _, _ = fake_python
    installed = provider_env.setup_provider(tmp_path, python=executable)
    assert provider_env.resolve_provider_python(tmp_path) == installed["python"]
    monkeypatch.setattr(provider_env, "_lock_sha256", lambda: "0" * 64)
    status = provider_env.provider_environment_status(tmp_path)
    assert status["available"] is False and status["python"] is None
    assert "another provider lock" in status["detail"]
    assert provider_env.resolve_provider_python(tmp_path) is None


def test_an_unexpandable_override_is_reported_not_raised(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "OPENAI4S_LAB_CHEMGYMRL_PYTHON", "~no-such-user-for-openai4s/bin/python"
    )
    status = provider_env.provider_environment_status(tmp_path)
    assert status["available"] is False and status["source"] == "override"
    assert provider_env.resolve_provider_python(tmp_path) is None


def test_setup_recovers_from_an_invalid_pointer_and_names_a_stale_lock(
    tmp_path, fake_python
):
    executable, _, config = fake_python
    pointer = Path(config["pointer"])
    pointer.parent.mkdir(parents=True)
    pointer.write_text("not json")
    # The advertised remedy for an invalid pointer must actually work.
    with pytest.raises(LabError) as caught:
        provider_env.setup_provider(tmp_path, rollback=True)
    assert "pointer is invalid" in caught.value.message
    installed = provider_env.setup_provider(tmp_path, python=executable)
    assert json.loads(pointer.read_text())["generation"] == installed["generation"]
    lock = pointer.parent / ".setup-lock"
    lock.write_text("424242")
    with pytest.raises(LabError) as caught:
        provider_env.setup_provider(tmp_path, python=executable)
    assert str(lock) in caught.value.message and "424242" in caught.value.message
    assert json.loads(pointer.read_text())["generation"] == installed["generation"]


_PROXY = "http://alice:pr0xy%2Fpass@proxy.corp.example:8080"
_INDEX = "https://tok_9f8e7d6c5b4a@pypi.corp.example/simple?key=qv_112233"
_SOURCE_LINE = f"Cloning git+https://github.com/chemgymrl/chemgymrl@{SOURCE_SHA}"
_BASIC = base64.b64encode(b"alice:pr0xy/pass").decode()
_LEAKS = (
    "pr0xy%2Fpass",
    "pr0xy/pass",
    "alice:",
    "tok_9f8e7d6c5b4a",
    "qv_112233",
    _BASIC,
)


@pytest.mark.parametrize("fail", [None, "dependencies", "source"])
def test_setup_log_never_holds_what_the_proxy_and_index_settings_carry(
    tmp_path, fake_python, monkeypatch, fail
):
    executable, config_path, config = fake_python
    monkeypatch.setenv("HTTPS_PROXY", _PROXY)
    monkeypatch.setenv("PIP_INDEX_URL", _INDEX)
    monkeypatch.setenv("NO_PROXY", "localhost,.corp.example")
    config["echo"] = ["HTTPS_PROXY", "PIP_INDEX_URL", "NO_PROXY"]
    config["say"] = [
        # Forms a tool derives itself: decoded, masked, as a request header.
        "407 for user alice:pr0xy/pass",
        "Looking in indexes: https://alice:****@proxy.corp.example:8080/simple",
        "Proxy-Authorization: Basic " + _BASIC,
        _SOURCE_LINE,
    ]
    if fail:
        config["fail"] = fail
    config_path.write_text(json.dumps(config))
    root = Path(config["pointer"]).parent
    if fail:
        with pytest.raises(LabError):
            provider_env.setup_provider(tmp_path, python=executable)
    else:
        provider_env.setup_provider(tmp_path, python=executable)
    (log_path,) = root.glob("gen-*/setup.log")
    log = log_path.read_text()
    for leak in _LEAKS:
        assert leak not in log, leak
    # The downloads did receive the settings; only their output is filtered.
    downloads = [e for e in _events(config) if e["stage"] in ("dependencies", "source")]
    assert downloads and all(e["env"]["HTTPS_PROXY"] == _PROXY for e in downloads)
    # What explains a failure is still there: hosts, paths, the exclusion
    # list, the pinned source and the tool's own words, on both streams.
    for kept in (
        "Looking in indexes: https://<redacted>@pypi.corp.example/simple?key=<redacted>",
        "could not connect through http://<redacted>@proxy.corp.example:8080",
        "Looking in indexes: https://<redacted>@proxy.corp.example:8080/simple",
        "Looking in indexes: localhost,.corp.example",
        "407 for user <redacted>",
        "Proxy-Authorization: Basic <redacted>",
        _SOURCE_LINE,
    ):
        assert kept in log, kept
    if fail:
        assert "simulated installation failure" in log


def test_a_killed_download_leaves_only_redacted_output(tmp_path):
    redact = provider_env._download_redactor({"HTTPS_PROXY": _PROXY})
    script = (
        "import os, sys, time\n"
        "url = os.environ['HTTPS_PROXY']\n"
        "print('retrying through', url, flush=True)\n"
        "print('still waiting on', url, file=sys.stderr, flush=True)\n"
        "time.sleep(120)\n"
    )
    with (tmp_path / "setup.log").open("xb") as log:
        with pytest.raises(subprocess.TimeoutExpired):
            provider_env._run(
                # The argument is ignored by the script; the command line is
                # part of the log too.
                [sys.executable, "-I", "-c", script, "--proxy=" + _PROXY],
                env={**os.environ, "HTTPS_PROXY": _PROXY},
                cwd=tmp_path,
                log=log,
                timeout=5,
                redact=redact,
            )
    text = (tmp_path / "setup.log").read_text()
    host = "http://<redacted>@proxy.corp.example:8080"
    assert "retrying through " + host in text
    assert "still waiting on " + host in text
    assert "--proxy=" + host in text
    assert "pr0xy" not in text and "alice" not in text


def test_download_redaction_removes_credentials_and_nothing_else():
    plain = provider_env._download_redactor(
        {"HTTPS_PROXY": "http://proxy.corp.example:8080", "NO_PROXY": "tok_secret"}
    )
    untouched = (
        b"connecting to http://proxy.corp.example:8080 for "
        b"git+https://github.com/chemgymrl/chemgymrl@abc123, "
        b"contact admin@corp.example, bypass tok_secret"
    )
    # No credential was passed, and NO_PROXY is a host list, not a secret.
    assert plain(untouched) == untouched
    # A URL another source put a credential in loses it all the same.
    assert plain(b"index https://x:y@host/simple and https://t0ken@host/x") == (
        b"index https://<redacted>@host/simple and https://<redacted>@host/x"
    )
    short = provider_env._download_redactor({"ALL_PROXY": "socks5://bob:ab@proxy:1"})
    # Too short to replace bare without shredding the log; gone from the URL.
    assert short(b"lab via socks5://bob:ab@proxy:1") == (
        b"lab via socks5://<redacted>@proxy:1"
    )
    # Some indexes carry the token in the path; a derived URL repeats it.
    in_path = provider_env._download_redactor(
        {
            "PIP_INDEX_URL": "https://dl.corp.example/aBcD1234eFgH5678/team/python/simple/"
        }
    )
    assert (
        in_path(
            b"Downloading https://dl.corp.example/aBcD1234eFgH5678/team/python/x.whl"
        )
        == b"Downloading https://dl.corp.example/<redacted>/team/python/x.whl"
    )
    # curl and pip accept a proxy without a scheme.
    bare = provider_env._download_redactor(
        {"HTTP_PROXY": "carol:hunter2pw@proxy.corp.example:3128"}
    )
    assert (
        bare(b"Cannot connect to proxy carol:hunter2pw@proxy.corp.example:3128")
        == b"Cannot connect to proxy <redacted>@proxy.corp.example:3128"
    )
    # A query value is found as given and as a tool would decode it.
    query = provider_env._download_redactor(
        {"PIP_INDEX_URL": "https://pypi.corp.example/simple?sig=a%2Bb%2Bc%2Bd"}
    )
    assert query(b"GET ?sig=a%2Bb%2Bc%2Bd then sig a+b+c+d") == (
        b"GET ?sig=<redacted> then sig <redacted>"
    )
    broken = provider_env._download_redactor({"HTTP_PROXY": "http://[bad:s3cret"})
    assert broken(b"invalid proxy http://[bad:s3cret given") == (
        b"invalid proxy <redacted> given"
    )
    with pytest.raises(ValueError):
        provider_env._run(
            [], env={}, cwd=Path("."), log=None, capture=True, redact=plain
        )
