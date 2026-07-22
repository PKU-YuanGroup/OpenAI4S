"""The kernel routes, pinned before they are moved.

`Handler._api` is one method of ~2,100 lines and the agreed direction is to
carve route groups out of it. The kernel group is the first slice, and an audit
of what would catch a mistake found that it is largely unwatched:

  * `GET /frames/{id}/execution`, `/kernel`, `/status` and `/environments` had
    no frozen response shape and no route-level test at all. The one test that
    mentions `/execution` asserts a 404 raised by an *upstream* guard, so it
    passes with the handler deleted outright.
  * `kernel/restart`, `/stop`, `/start` and `/env` had a frozen shape for the
    403 "REPL disabled" envelope only. The guard was pinned; what the route
    actually does was not. Swapping the restart and stop bodies during the move
    would have passed pytest, passed the response-shape gate, and passed the
    browser smoke run (restart and start have no call site in either).

So these are not tests of the extraction. They are the tests that make the
extraction checkable, and they belong before it rather than after, when they
would be written to match whatever the moved code happens to do.

Two behaviours here look like bugs and are deliberately pinned as they are.
Ten of the twelve routes answer 200 for a frame id that does not exist, and
`kernel/install` is deliberately *not* gated by `notebook_repl` while its six
siblings are. Changing either is a behaviour change; this file's job is to
notice if the move makes one by accident.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.server import gateway as gateway_mod


class _Hub:
    def emitter(self, root_frame_id):
        return lambda event: None

    def broadcast(self, root_frame_id, event):
        return None

    def has_subscriber(self, root_frame_id):
        return False

    def drop_frame(self, root_frame_id):
        return None


def _setup(tmp_path, *, notebook_repl=False):
    config = Config(
        data_dir=tmp_path, llm=LLMConfig(provider="deepseek", api_key="test-key")
    )
    if notebook_repl:
        # The flag is read through cfg at request time, so overriding the
        # attribute is enough and avoids a second SessionRunner.
        config.notebook_repl = True
    hub = _Hub()
    runner = gateway_mod.SessionRunner(config, hub, start_idle_sweeper=False)
    frame_id = runner.store.new_frame(
        kind="turn", project_id="proj-kernel", status="ready"
    )
    handler = object.__new__(gateway_mod.make_handler(config, hub, runner))
    return runner, handler, frame_id


def _call(handler, method, path, *, body=None, query=None):
    replies: list[tuple] = []
    handler._query = lambda: query or {}
    handler._body = lambda: body or {}
    handler._json = lambda value, code=200: replies.append((code, value))
    handler._send = lambda code, data, content_type, extra=None: replies.append(
        (code, data, content_type, extra or {})
    )
    handler._api(method, path)
    return replies[-1] if replies else None


# --------------------------------------------------------------------------
# the four routes with no net at all
# --------------------------------------------------------------------------


@pytest.mark.stubbed_backend
def test_execution_snapshot_reaches_the_coordinator(tmp_path):
    """The handler is three lines and nothing checked that they ran. The only
    existing test asserts a 404 that an upstream guard produces, so it passes
    with this body deleted."""
    runner, handler, fid = _setup(tmp_path)
    seen = []
    runner.executions = SimpleNamespace(
        snapshot=lambda f: seen.append(f) or {"session_id": f, "queue": []}
    )

    code, payload = _call(handler, "GET", f"/frames/{fid}/execution")

    assert code == 200
    assert seen == [fid], "the frame id has to reach the coordinator unchanged"
    assert payload["session_id"] == fid


def test_an_unknown_frame_is_refused_by_the_upstream_guard(tmp_path):
    """This 404 does NOT come from the handler. It comes from the `workbench`
    guard several hundred lines earlier, and it is the reason the extracted
    module cannot be called before that guard runs."""
    _runner, handler, _fid = _setup(tmp_path)

    with pytest.raises(gateway_mod.GatewayError) as raised:
        _call(handler, "GET", "/frames/no-such-frame/execution")
    assert raised.value.code == 404


@pytest.mark.stubbed_backend
def test_kernel_state_is_whatever_the_runner_reports(tmp_path):
    runner, handler, fid = _setup(tmp_path)
    runner.kernel_status = lambda f: {"state": "idle", "alive": True, "frame": f}

    code, payload = _call(handler, "GET", f"/frames/{fid}/kernel")

    assert code == 200
    assert payload == {"state": "idle", "alive": True, "frame": fid}


@pytest.mark.stubbed_backend
def test_status_combines_the_turn_and_the_kernel(tmp_path):
    """Three fields assembled inline in the route body. Nothing verified the
    assembly, so a moved copy could drop or rename one silently."""
    runner, handler, fid = _setup(tmp_path)
    runner.is_running = lambda f: True
    runner.kernel_status = lambda f: {"state": "busy"}

    code, payload = _call(handler, "GET", f"/frames/{fid}/status")

    assert code == 200
    assert payload == {
        "frame_id": fid,
        "running": True,
        "kernel": {"state": "busy"},
    }


@pytest.mark.stubbed_backend
def test_environments_lists_what_the_runner_offers(tmp_path):
    runner, handler, fid = _setup(tmp_path)
    runner.list_environments = lambda f: {"environments": [{"name": "python"}]}

    code, payload = _call(handler, "GET", f"/frames/{fid}/environments")

    assert code == 200
    assert payload["environments"] == [{"name": "python"}]


@pytest.mark.parametrize("route", ["kernel", "status", "environments"])
@pytest.mark.stubbed_backend
def test_these_three_answer_200_for_a_frame_that_does_not_exist(tmp_path, route):
    """Pinned as-is, not endorsed. Ten of the twelve kernel routes answer for an
    unknown id; only `/execution` and `/kernel/variables` refuse. Tidying one
    uniform frame check across the group during the move would be a behaviour
    change wearing a refactor's clothes, so record the asymmetry here where a
    reviewer can see it."""
    runner, handler, _fid = _setup(tmp_path)
    runner.kernel_status = lambda f: {"state": "none"}
    runner.is_running = lambda f: False
    runner.list_environments = lambda f: {"environments": []}

    code, _payload = _call(handler, "GET", f"/frames/ghost-frame/{route}")
    assert code == 200


# --------------------------------------------------------------------------
# the four routes where only the 403 guard was pinned
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "action,attr",
    [
        ("restart", "restart_kernel"),
        ("stop", "stop_kernel"),
        ("start", "start_kernel"),
    ],
)
@pytest.mark.stubbed_backend
def test_each_lifecycle_action_calls_its_own_runner_method(tmp_path, action, attr):
    """The gap that mattered most. With only the 403 pinned, swapping the
    restart and stop bodies passed every gate in the repo."""
    runner, handler, fid = _setup(tmp_path, notebook_repl=True)
    called = []
    for name in ("restart_kernel", "stop_kernel", "start_kernel"):
        setattr(
            runner,
            name,
            lambda frame, project, _n=name: called.append((_n, frame, project))
            or {"ok": _n},
        )

    code, payload = _call(handler, "POST", f"/frames/{fid}/kernel/{action}")

    assert code == 200
    assert [c[0] for c in called] == [attr], f"{action} must call {attr}, alone"
    assert called[0][1] == fid
    assert called[0][2] == "proj-kernel", "the frame's project, not a constant"
    assert payload["ok"] == attr


@pytest.mark.stubbed_backend
def test_a_lifecycle_action_on_an_unknown_frame_falls_back_to_the_default_project(
    tmp_path,
):
    """`store.get_frame(fid) or {}` then `.get("project_id") or "default"`. The
    fallback is load-bearing and repeated across six routes, so a move that
    drops the `or {}` turns a 200 into an AttributeError."""
    runner, handler, _fid = _setup(tmp_path, notebook_repl=True)
    seen = []
    runner.start_kernel = lambda frame, project: seen.append((frame, project)) or {}

    code, _payload = _call(handler, "POST", "/frames/ghost/kernel/start")

    assert code == 200
    assert seen == [("ghost", "default")]


@pytest.mark.stubbed_backend
def test_selecting_an_environment_passes_the_requested_name_through(tmp_path):
    runner, handler, fid = _setup(tmp_path, notebook_repl=True)
    seen = []
    runner.set_env = lambda frame, name, project: seen.append(
        (frame, name, project)
    ) or {"selected": name}

    code, payload = _call(
        handler, "POST", f"/frames/{fid}/kernel/env", body={"name": "r"}
    )

    assert code == 200
    assert seen == [(fid, "r", "proj-kernel")]
    assert payload["selected"] == "r"


@pytest.mark.stubbed_backend
def test_the_environment_name_may_arrive_under_either_key(tmp_path):
    """`b.get("env") or b.get("name")`. Two spellings, one route -- a detail
    that is invisible unless someone writes it down before moving it."""
    runner, handler, fid = _setup(tmp_path, notebook_repl=True)
    seen = []
    runner.set_env = lambda frame, name, project: seen.append(name) or {}

    _call(handler, "POST", f"/frames/{fid}/kernel/env", body={"env": "struct"})
    _call(handler, "POST", f"/frames/{fid}/kernel/env", body={"name": "r"})
    _call(handler, "POST", f"/frames/{fid}/kernel/env", body={})

    assert seen == ["struct", "r", ""]


@pytest.mark.parametrize(
    "action", ["execute", "env", "restart", "stop", "start", "interrupt"]
)
def test_the_six_repl_routes_stay_gated_when_the_notebook_is_read_only(
    tmp_path, action
):
    """Already covered elsewhere, repeated here because the extraction moves
    all six gates at once and the group needs its own regression."""
    runner, handler, fid = _setup(tmp_path)
    assert runner.cfg.notebook_repl is False

    code, payload = _call(handler, "POST", f"/frames/{fid}/kernel/{action}")

    assert code == 403
    assert "disabled" in payload["error"]


@pytest.mark.stubbed_backend
def test_install_is_deliberately_not_gated_by_the_notebook_flag(tmp_path):
    """The asymmetry the comment in the route body explains: installing into a
    prebuilt environment is a Compute affordance, not the code REPL. Unifying
    the six gates into one during the move must not sweep this in."""
    runner, handler, fid = _setup(tmp_path)
    assert runner.cfg.notebook_repl is False
    seen = {}

    def install(packages, **kwargs):
        seen.update({"packages": packages, **kwargs})
        return {"installed": packages}

    runner.install_packages = install
    code, payload = _call(
        handler, "POST", f"/frames/{fid}/kernel/install", body={"package": "numpy"}
    )

    assert code == 200, "install must answer even with the REPL disabled"
    assert seen["packages"] == ["numpy"]
    assert seen["root_frame_id"] == fid
    assert seen["restart"] is True, "restart defaults to True when unstated"


@pytest.mark.stubbed_backend
def test_install_accepts_a_list_as_well_as_a_single_package(tmp_path):
    runner, handler, fid = _setup(tmp_path)
    seen = {}
    runner.install_packages = lambda packages, **kw: seen.update(
        {"packages": packages}
    ) or {"installed": packages}

    _call(
        handler,
        "POST",
        f"/frames/{fid}/kernel/install",
        body={"packages": ["numpy", "scipy"]},
    )
    assert seen["packages"] == ["numpy", "scipy"]


# --------------------------------------------------------------------------
# the query string, and the fallthrough
# --------------------------------------------------------------------------


@pytest.mark.stubbed_backend
def test_variable_inspection_reads_the_language_from_the_query(tmp_path):
    """`q` is an `_api` local, read on exactly one line of the 222 being moved.
    An extraction whose signature omits it raises NameError here and nowhere
    else -- including on the default path, because `q.get` is evaluated before
    the "python" fallback applies."""
    runner, handler, fid = _setup(tmp_path)
    seen = []
    runner.variables = SimpleNamespace(
        inspect=lambda frame, language: seen.append((frame, language))
        or {"variables": []}
    )

    _call(
        handler,
        "GET",
        f"/frames/{fid}/kernel/variables",
        query={"language": ["r"]},
    )
    assert seen == [(fid, "r")]

    seen.clear()
    _call(handler, "GET", f"/frames/{fid}/kernel/variables")
    assert seen == [(fid, "python")], "no query means python, not a crash"


@pytest.mark.stubbed_backend
def test_an_unsupported_inspection_language_is_refused(tmp_path):
    runner, handler, fid = _setup(tmp_path)
    runner.variables = SimpleNamespace(
        inspect=lambda frame, language: pytest.fail("must not reach the kernel")
    )

    code, payload = _call(
        handler,
        "GET",
        f"/frames/{fid}/kernel/variables",
        query={"language": ["julia"]},
    )
    assert code == 400
    assert "python or r" in payload["error"]


def test_a_matched_path_with_the_wrong_method_falls_through_to_404(tmp_path):
    """Every branch is `if m and method == ...`, so a matched regex is not a
    handled request. An extracted module that returns `bool(regex_matched)`
    swallows these twelve 404s into an empty response."""
    _runner, handler, fid = _setup(tmp_path)

    code, payload = _call(handler, "GET", f"/frames/{fid}/kernel/execute")

    assert code == 404
    assert payload["path"] == f"/frames/{fid}/kernel/execute"
    assert payload["method"] == "GET"
