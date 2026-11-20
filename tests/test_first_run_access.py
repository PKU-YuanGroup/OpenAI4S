"""Whether a first-run user can get into their own daemon.

The access token is required on loopback. Single-user ``openai4s url`` and the
local browser auto-open still carry ``?token=``. Startup logs, the
already-running line, and the gateway banner do not. Team mode points both
explicit ``url`` and auto-open at ``/login``. An unauthorised browser
navigation gets an HTML page that names ``openai4s url`` plus the desktop and
container recovery paths. A script still gets JSON.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest

from openai4s.cli.main import _url
from openai4s.config import Config, LLMConfig
from openai4s.server import gateway as gateway_mod
from openai4s.server import local_auth

# --------------------------------------------------------------------------
# the URL the CLI hands over
# --------------------------------------------------------------------------


def test_the_printed_url_carries_the_token(tmp_path):
    """The defect. Every human-facing caller used this string."""
    cfg = Config(data_dir=tmp_path, llm=LLMConfig(provider="deepseek", api_key="k"))
    token = local_auth.load_or_mint(tmp_path)
    assert token, "no token minted; this test proves nothing"
    assert f"token={token}" in _url(cfg)


def test_a_daemon_with_no_token_gets_a_plain_url(tmp_path):
    """Appending `?token=None` would be worse than the bare origin."""
    cfg = Config(data_dir=tmp_path, llm=LLMConfig(provider="deepseek", api_key="k"))
    assert _url(cfg) == f"http://{cfg.host}:{cfg.port}/"
    assert "token" not in _url(cfg)


def test_machine_callers_opt_out(tmp_path):
    """`status` builds `_url(cfg) + "health"` and the API helper appends a
    path. Against a URL carrying a query string those become
    `...?token=Xhealth` — so the same change that fixes the human path breaks
    both of them unless they opt out."""
    cfg = Config(data_dir=tmp_path, llm=LLMConfig(provider="deepseek", api_key="k"))
    local_auth.load_or_mint(tmp_path)
    plain = _url(cfg, with_token=False)
    assert plain.endswith("/") and "token" not in plain
    assert (plain + "health").endswith("/health")


def test_no_caller_concatenates_onto_the_token_url():
    """The regression this guards is silent: a 404 on a health probe reads as
    "daemon down" rather than "we built a nonsense URL"."""
    source = Path("openai4s/cli/main.py").read_text(encoding="utf-8")
    for line in source.splitlines():
        if "_url(cfg)" in line and "with_token" not in line:
            # Human-facing print/open only — never string concatenation.
            assert not any(
                bad in line for bad in ("_url(cfg) +", "_url(cfg).rstrip")
            ), line


# --------------------------------------------------------------------------
# what a browser gets
# --------------------------------------------------------------------------


class _Hub:
    def emitter(self, root_frame_id):
        return lambda event: None

    def broadcast(self, root_frame_id, event):
        return None


@pytest.fixture
def unauthenticated(tmp_path, monkeypatch):
    """A real handler with the token gate armed and no credential presented."""
    monkeypatch.setenv("OPENAI4S_DATA_DIR", str(tmp_path))
    cfg = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        max_turns=1,
    )
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    handler_class = gateway_mod.make_handler(cfg, _Hub(), runner)

    def get(path, accept, accept_language=None):
        handler = object.__new__(handler_class)
        handler._correlation_id = "req-1"
        sent: dict = {}

        def _send(code, body, ctype, extra=None):
            sent.update(code=code, body=body, ctype=ctype)

        handler._send = _send
        handler.command = "GET"
        handler.path = path
        handler.headers = {"Content-Length": "0", "Accept": accept}
        if accept_language is not None:
            handler.headers["Accept-Language"] = accept_language
        handler.close_connection = False
        handler._route("GET")
        return sent

    return get


def test_a_browser_gets_a_page_it_can_act_on(unauthenticated):
    """The defect, as what a person actually sees. JSON in a browser window is
    not a recovery path."""
    sent = unauthenticated("/", "text/html,application/xhtml+xml,*/*")
    assert sent["code"] == 401
    assert "text/html" in sent["ctype"]
    body = sent["body"].decode("utf-8")
    assert "openai4s url" in body, "the page does not say what to run"
    assert "printed on startup" not in body
    assert "already signed in" in body
    assert "docker exec" in body
    assert "&lt;container&gt;" in body


def test_the_page_names_a_command_that_works_without_path(unauthenticated):
    """NAV-5: ./setup.sh installs into the checkout's .venv, which a new
    terminal does not have on PATH, so `openai4s url` alone answered
    "command not found". The page names the forms that work from the folder."""
    body = unauthenticated("/", "text/html").get("body", b"").decode("utf-8")
    assert "uv run openai4s url" in body
    assert ".venv/bin/openai4s url" in body
    assert "OPENAI4S_DATA_DIR" in body


def test_the_page_follows_a_chinese_browser(unauthenticated):
    """NAV-5: the page was English-only; the SPA's language setting is behind
    the same gate, so the browser's Accept-Language is all there is."""
    zh = unauthenticated("/", "text/html", "zh-CN,zh;q=0.9,en;q=0.8")
    assert zh["code"] == 401
    body = zh["body"].decode("utf-8")
    assert "需要访问令牌" in body
    assert "openai4s url" in body and "uv run openai4s url" in body
    assert "docker exec &lt;container&gt; openai4s url" in body
    en = unauthenticated("/", "text/html", "en-US,en;q=0.9,zh;q=0.8")
    body = en["body"].decode("utf-8")
    assert "Access token required" in body and "需要" not in body


def test_a_script_still_gets_json(unauthenticated):
    """Turning every 401 into HTML would break every client that parses the
    error, which is the opposite failure."""
    sent = unauthenticated("/", "application/json")
    assert sent["code"] == 401
    assert "json" in sent["ctype"]
    error = json.loads(sent["body"].decode("utf-8"))["error"]
    assert "openai4s url" in error
    assert "printed URL" not in error
    assert "query string" not in error


def test_the_page_carries_no_credential(unauthenticated):
    """It is served to an unauthenticated caller by definition. Embedding the
    token would hand it to exactly whoever was being refused."""
    token = local_auth.read_token(Path(_data_dir(unauthenticated))) or ""
    body = unauthenticated("/", "text/html").get("body", b"").decode("utf-8")
    assert token, "no token to leak; test is vacuous"
    assert token not in body


def _data_dir(_get) -> str:
    import os

    return os.environ["OPENAI4S_DATA_DIR"]


def test_the_page_fetches_nothing(unauthenticated):
    """Every asset it could reference — app.js, style.css — is behind this same
    gate, so an external reference would render a blank page."""
    body = unauthenticated("/", "text/html")["body"].decode("utf-8")
    for referencing in ("<script src", '<link rel="stylesheet"', "fetch("):
        assert referencing not in body, referencing


def test_health_is_still_open(unauthenticated):
    """The liveness probe must not start demanding a credential — `status`
    uses it to tell "running" from "down"."""
    assert unauthenticated("/health", "*/*")["code"] == 200


# --------------------------------------------------------------------------
# end to end, against a real daemon
# --------------------------------------------------------------------------


@pytest.mark.slow
def test_the_url_the_cli_prints_actually_opens(tmp_path):
    """The whole claim, on the wire. Everything above could pass while the two
    halves still disagreed about the token's shape. Startup streams are kept
    and must not contain the token."""
    import os

    token = "tok-03-" + uuid.uuid4().hex
    (tmp_path / "access-token").write_text(token, encoding="utf-8")
    stdout_path = tmp_path / "serve-stdout.txt"
    stderr_path = tmp_path / "serve-stderr.txt"
    env = dict(os.environ)
    env.update(
        OPENAI4S_DATA_DIR=str(tmp_path),
        OPENAI4S_SECRET_STORE="plaintext",
        OPENAI4S_PORT="8791",
        OPENAI4S_NO_OPEN="1",
        PYTHONUNBUFFERED="1",
    )
    out_fh = stdout_path.open("w", encoding="utf-8")
    err_fh = stderr_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "openai4s.cli.main", "serve", "--no-open"],
        env=env,
        stdout=out_fh,
        stderr=err_fh,
    )
    ready = False
    try:
        base = "http://127.0.0.1:8791"
        for _ in range(60):
            try:
                urllib.request.urlopen(base + "/health", timeout=2)
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.5)
        else:
            pytest.skip("daemon did not come up")
        ready = True

        assert local_auth.read_token(tmp_path) == token

        # The bare origin: what every human-facing caller used to hand over.
        request = urllib.request.Request(base + "/", headers={"Accept": "text/html"})
        try:
            urllib.request.urlopen(request, timeout=5)
            bare_status = 200
            bare_body = ""
        except urllib.error.HTTPError as err:
            bare_status = err.code
            bare_body = err.read().decode("utf-8", "replace")
        assert bare_status == 401
        assert "openai4s url" in bare_body
        assert token not in bare_body

        # The URL `openai4s url` prints now. Do NOT follow the redirect:
        # the 303 IS the bootstrap — it sets the cookie and sends the browser
        # to the same path with the credential stripped. urllib would follow it
        # without carrying the cookie and land on a legitimate 401, which says
        # nothing about whether the token was accepted.
        class _NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *_args, **_kwargs):
                return None

        opener = urllib.request.build_opener(_NoRedirect)
        try:
            with opener.open(f"{base}/?token={token}", timeout=5) as response:
                assert response.status == 200
        except urllib.error.HTTPError as err:
            assert err.code == 303, f"the printed URL was refused: {err.code}"
            cookie = err.headers.get("Set-Cookie") or ""
            assert "os_token=" in cookie, "the bootstrap set no cookie"
    finally:
        if proc.poll() is None:
            proc.terminate()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
        out_fh.close()
        err_fh.close()
        leaked = token if (tmp_path / "access-token").exists() else None
        if leaked:
            combined = stdout_path.read_text(encoding="utf-8") + stderr_path.read_text(
                encoding="utf-8"
            )
            assert leaked not in combined
            if ready:
                assert "openai4s url" in combined
                assert "listening" in combined


# --------------------------------------------------------------------------
# startup logs, banners, and the browser open
# --------------------------------------------------------------------------

_SINGLE_HINT = "sign in: run `openai4s url` to print a sign-in link"


def _plant_token(data_dir: Path) -> str:
    token = "tok-03-" + uuid.uuid4().hex
    (data_dir / "access-token").write_text(token, encoding="utf-8")
    return token


def _serve_config(tmp_path: Path, *, team_mode: bool = False, port: int = 8760):
    from openai4s.config import Config, LLMConfig

    cfg = Config(
        data_dir=tmp_path,
        host="127.0.0.1",
        port=port,
        llm=LLMConfig(provider="deepseek", api_key="k"),
        team_mode=team_mode,
    )
    cfg.ensure_dirs()
    return cfg


def _streams_omit(token: str, captured) -> None:
    assert token not in captured.out
    assert token not in captured.err
    assert "?token=" not in captured.out
    assert "?token=" not in captured.err


def test_startup_auth_banner_names_the_recovery_and_brackets_ipv6():
    single = local_auth.startup_auth_banner("::1", 8760, team_mode=False)
    team = local_auth.startup_auth_banner("::1", 8760, team_mode=True)
    wild = local_auth.startup_auth_banner("0.0.0.0", 9, team_mode=True)
    assert single == (
        "[openai4s] access token required.\n"
        "  sign in: run `openai4s url` to print a sign-in link "
        "(in the OpenAI4S folder without it on PATH: `uv run openai4s url`)"
    )
    assert team == "[openai4s] team mode: sign in at http://[::1]:8760/login"
    assert wild == "[openai4s] team mode: sign in at http://localhost:9/login"
    assert "token" not in single.replace("access token required", "")
    assert "?token=" not in single + team + wild


def test_gateway_banner_omits_the_token(tmp_path, capsys):
    cfg = _serve_config(tmp_path)
    token = _plant_token(tmp_path)
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    try:
        gateway_mod.make_handler(cfg, _Hub(), runner)
        captured = capsys.readouterr()
    finally:
        runner.close()
    _streams_omit(token, captured)
    expected = local_auth.startup_auth_banner(cfg.host, cfg.port, team_mode=False)
    assert expected in captured.err
    assert "access token required" in captured.err
    assert "openai4s url" in captured.err


def test_team_mode_gateway_banner_points_at_login(tmp_path, capsys):
    cfg = _serve_config(tmp_path, team_mode=True, port=8766)
    token = _plant_token(tmp_path)
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    try:
        gateway_mod.make_handler(cfg, _Hub(), runner)
        captured = capsys.readouterr()
    finally:
        runner.close()
    _streams_omit(token, captured)
    expected = local_auth.startup_auth_banner(cfg.host, cfg.port, team_mode=True)
    assert expected in captured.err
    assert "access token required" not in captured.err
    assert f"http://127.0.0.1:{cfg.port}/login" in captured.err


def _patch_serve(monkeypatch, cfg):
    import importlib

    import openai4s.server as server_pkg

    cli_main = importlib.import_module("openai4s.cli.main")

    monkeypatch.setattr(cli_main, "get_config", lambda initialize_dirs=True: cfg)
    monkeypatch.setattr(server_pkg, "build_server", lambda _cfg: object())
    monkeypatch.setattr(server_pkg, "run_server", lambda _httpd: None)
    return cli_main


def test_foreground_serve_logs_omit_the_access_token(tmp_path, monkeypatch, capsys):
    cfg = _serve_config(tmp_path, port=8771)
    token = _plant_token(tmp_path)
    cli_main = _patch_serve(monkeypatch, cfg)
    monkeypatch.setenv("OPENAI4S_NO_OPEN", "1")

    rc = cli_main.cmd_serve(type("A", (), {"detached": False, "no_open": True})())

    captured = capsys.readouterr()
    assert rc == 0
    _streams_omit(token, captured)
    assert f"openai4s listening at http://127.0.0.1:{cfg.port}/" in captured.out
    assert "(model=" in captured.out
    # The gateway's startup notice (stubbed out here) is the one sign-in line;
    # serve printing its own as well showed it twice in two wordings (CLI-9).
    assert "sign in" not in captured.out + captured.err


def test_team_foreground_serve_points_at_login(tmp_path, monkeypatch, capsys):
    cfg = _serve_config(tmp_path, team_mode=True, port=8772)
    token = _plant_token(tmp_path)
    cli_main = _patch_serve(monkeypatch, cfg)
    monkeypatch.setenv("OPENAI4S_NO_OPEN", "1")

    rc = cli_main.cmd_serve(type("A", (), {"detached": False, "no_open": True})())

    captured = capsys.readouterr()
    assert rc == 0
    _streams_omit(token, captured)
    assert f"openai4s listening at http://127.0.0.1:{cfg.port}/" in captured.out
    # /login is named once, by the gateway's team-mode notice (stubbed here).
    assert "sign in" not in captured.out + captured.err
    assert "openai4s url" not in captured.out


@pytest.mark.parametrize("detached", [False, True])
def test_already_running_serve_omits_the_token(tmp_path, monkeypatch, capsys, detached):
    cfg = _serve_config(tmp_path, port=8773)
    token = _plant_token(tmp_path)
    cfg.pidfile.write_text("4321", encoding="utf-8")
    cli_main = _patch_serve(monkeypatch, cfg)
    monkeypatch.setattr(cli_main, "_daemon_alive", lambda _cfg, _pid: True)

    rc = cli_main.cmd_serve(type("A", (), {"detached": detached, "no_open": True})())

    captured = capsys.readouterr()
    assert rc == 1
    _streams_omit(token, captured)
    assert (
        f"daemon already running (pid 4321) at http://127.0.0.1:{cfg.port}/"
        in captured.out
    )
    assert _SINGLE_HINT in captured.out


def test_already_running_hint_follows_the_live_daemon(tmp_path, monkeypatch, capsys):
    cfg = _serve_config(tmp_path, team_mode=False, port=8774)
    token = _plant_token(tmp_path)
    cfg.pidfile.write_text("4321", encoding="utf-8")
    cfg.statefile.write_text(
        json.dumps(
            {
                "pid": 4321,
                "pid_start": "daemon-start",
                "host": "172.25.100.5",
                "port": 9876,
                "team_mode": True,
            }
        ),
        encoding="utf-8",
    )
    cli_main = _patch_serve(monkeypatch, cfg)
    monkeypatch.setattr(cli_main, "_daemon_alive", lambda _cfg, _pid: True)
    monkeypatch.setattr(cli_main, "_process_start_token", lambda _pid: "daemon-start")

    rc = cli_main.cmd_serve(type("A", (), {"detached": False, "no_open": True})())

    captured = capsys.readouterr()
    assert rc == 1
    _streams_omit(token, captured)
    assert "daemon already running (pid 4321) at http://172.25.100.5:9876/" in (
        captured.out
    )
    assert "sign in: http://172.25.100.5:9876/login" in captured.out


def _capture_open(monkeypatch):
    import threading
    import webbrowser

    opened: list[str] = []

    class _InlineThread:
        def __init__(self, target, daemon=False):
            self._target = target

        def start(self):
            self._target()

    monkeypatch.setattr(threading, "Thread", _InlineThread)
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url))
    monkeypatch.delenv("OPENAI4S_NO_OPEN", raising=False)
    return opened


def test_foreground_auto_open_uses_the_sign_in_url(tmp_path, monkeypatch, capsys):
    cfg = _serve_config(tmp_path, port=8775)
    token = _plant_token(tmp_path)
    cli_main = _patch_serve(monkeypatch, cfg)
    monkeypatch.setattr(cli_main.time, "sleep", lambda _seconds: None)
    opened = _capture_open(monkeypatch)

    rc = cli_main.cmd_serve(type("A", (), {"detached": False, "no_open": False})())

    captured = capsys.readouterr()
    assert rc == 0
    _streams_omit(token, captured)
    assert opened == [f"http://127.0.0.1:{cfg.port}/?token={token}"]


def test_team_foreground_auto_open_uses_login(tmp_path, monkeypatch, capsys):
    cfg = _serve_config(tmp_path, team_mode=True, port=8776)
    token = _plant_token(tmp_path)
    cli_main = _patch_serve(monkeypatch, cfg)
    monkeypatch.setattr(cli_main.time, "sleep", lambda _seconds: None)
    opened = _capture_open(monkeypatch)

    rc = cli_main.cmd_serve(type("A", (), {"detached": False, "no_open": False})())

    captured = capsys.readouterr()
    assert rc == 0
    _streams_omit(token, captured)
    assert opened == [f"http://127.0.0.1:{cfg.port}/login"]


def test_detached_serve_omits_the_token_and_opens_the_sign_in_url(
    tmp_path, monkeypatch, capsys
):
    import importlib
    import os

    if os.name != "posix":
        pytest.skip("detached server sessions are a POSIX/WSL feature")

    cli_main = importlib.import_module("openai4s.cli.main")
    cfg = _serve_config(tmp_path, port=8777)
    token = _plant_token(tmp_path)

    class Process:
        pid = 4321

        @staticmethod
        def poll():
            return None

    def fake_popen(command, **kwargs):
        cfg.pidfile.write_text("4321", encoding="utf-8")
        return Process()

    monkeypatch.setattr(cli_main.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(cli_main, "_health_ready", lambda _cfg: True)
    opened = _capture_open(monkeypatch)

    rc = cli_main._cmd_serve_detached(type("A", (), {"no_open": False})(), cfg)

    captured = capsys.readouterr()
    assert rc == 0
    _streams_omit(token, captured)
    assert f"daemon started (pid 4321) at http://127.0.0.1:{cfg.port}/" in captured.out
    assert _SINGLE_HINT in captured.out
    assert opened == [f"http://127.0.0.1:{cfg.port}/?token={token}"]


def test_team_detached_auto_open_uses_login(tmp_path, monkeypatch, capsys):
    import importlib
    import os

    if os.name != "posix":
        pytest.skip("detached server sessions are a POSIX/WSL feature")

    cli_main = importlib.import_module("openai4s.cli.main")
    cfg = _serve_config(tmp_path, team_mode=True, port=8778)
    token = _plant_token(tmp_path)

    class Process:
        pid = 4321

        @staticmethod
        def poll():
            return None

    def fake_popen(command, **kwargs):
        cfg.pidfile.write_text("4321", encoding="utf-8")
        return Process()

    monkeypatch.setattr(cli_main.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(cli_main, "_health_ready", lambda _cfg: True)
    opened = _capture_open(monkeypatch)

    rc = cli_main._cmd_serve_detached(type("A", (), {"no_open": False})(), cfg)

    captured = capsys.readouterr()
    assert rc == 0
    _streams_omit(token, captured)
    assert f"sign in: http://127.0.0.1:{cfg.port}/login" in captured.out
    assert opened == [f"http://127.0.0.1:{cfg.port}/login"]


def test_sign_in_url_keeps_the_single_user_token_and_brackets_ipv6(tmp_path):
    from openai4s.cli.main import _sign_in_url
    from openai4s.config import Config, LLMConfig

    token = _plant_token(tmp_path)
    cfg = Config(
        data_dir=tmp_path,
        host="::1",
        port=9,
        llm=LLMConfig(provider="deepseek", api_key="k"),
    )
    assert _sign_in_url(cfg) == f"http://[::1]:9/?token={token}"
    assert _sign_in_url(cfg, team_mode=True) == "http://[::1]:9/login"
    cfg.host = "0.0.0.0"
    assert _sign_in_url(cfg, team_mode=True) == "http://localhost:9/login"


_LAUNCHERS = ("scripts/build_macos_dmg.sh", "scripts/build_linux_bundle.sh")
_RELAUNCH_TOKEN_URL = "http://127.0.0.1:8760/?token=tok-relaunch"


def _relaunch_block(script: str) -> str:
    text = Path(script).read_text(encoding="utf-8")
    begin, end = "# relaunch-sign-in:begin", "# relaunch-sign-in:end"
    assert text.count(begin) == 1 and text.count(end) == 1
    return text[text.index(begin) : text.index(end)]


@pytest.mark.parametrize("script", _LAUNCHERS)
@pytest.mark.parametrize(
    ("status_rc", "url_out", "expected"),
    [
        # Something answers on the port, but `status` cannot verify this data
        # dir's daemon: whatever is listening must not receive the token.
        (1, _RELAUNCH_TOKEN_URL, ""),
        (2, _RELAUNCH_TOKEN_URL, ""),
        # The verified daemon: its sign-in URL goes to the browser.
        (0, _RELAUNCH_TOKEN_URL, _RELAUNCH_TOKEN_URL),
        # A verified daemon whose `url` printed something that is not a URL.
        (0, "error: no token yet", ""),
    ],
)
def test_desktop_relaunch_signs_in_only_to_a_verified_daemon(
    tmp_path, script, status_rc, url_out, expected
):
    """The relaunch branch is entered on a bare TCP connect, which anything
    bound to the port passes. The launcher's own block runs here, unmodified,
    under ``set -e`` with a stand-in interpreter."""
    stub = tmp_path / "python"
    stub.write_text(
        "#!/bin/bash\n"
        f'if [ "$1 $2 $3" = "-m openai4s status" ]; then exit {status_rc}; fi\n'
        f'if [ "$1 $2 $3" = "-m openai4s url" ]; then echo \'{url_out}\'; exit 0; fi\n'
        "exit 97\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    program = (
        'set -e\nPY="$1"\n' + _relaunch_block(script) + 'printf "%s" "$SIGN_IN_URL"\n'
    )
    done = subprocess.run(
        ["/bin/bash", "-c", program, "launcher", str(stub)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout == expected


def test_desktop_relaunch_opens_the_verified_url_or_the_bare_origin():
    macos = Path(_LAUNCHERS[0]).read_text(encoding="utf-8")
    linux = Path(_LAUNCHERS[1]).read_text(encoding="utf-8")
    assert 'exec /usr/bin/open "${SIGN_IN_URL:-$URL}"' in macos
    assert 'open_url "${SIGN_IN_URL:-$URL}"' in linux
    # First start is unchanged: `serve` opens the browser itself.
    assert macos.index("# relaunch-sign-in:end") < macos.index(
        'exec "$PY" -u -m openai4s serve'
    )
    assert linux.index("# relaunch-sign-in:end") < linux.index("( sleep 2; open_url )")


def test_linux_open_url_fallback_never_prints_the_sign_in_url(tmp_path):
    """With no opener installed the launcher reports where it is serving on
    stderr, which a desktop session sends to the journal or
    ~/.xsession-errors. That line must name the bare origin only."""
    text = Path(_LAUNCHERS[1]).read_text(encoding="utf-8")
    start = text.index("open_url() {")
    end = text.index("\n}\n", start) + 3
    program = (
        'URL="http://127.0.0.1:8760/"\n'
        + text[start:end]
        + f'open_url "{_RELAUNCH_TOKEN_URL}"\n'
    )
    done = subprocess.run(
        ["/bin/bash", "-c", program],
        capture_output=True,
        text=True,
        env={"PATH": str(tmp_path)},
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    assert "tok-relaunch" not in done.stdout + done.stderr
    assert "http://127.0.0.1:8760/" in done.stderr
