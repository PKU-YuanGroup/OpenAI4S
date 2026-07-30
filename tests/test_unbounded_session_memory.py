"""Two accumulators in the daemon that nothing ever emptied.

**App tiles.** `host.app.render()` appended to a per-session list with no cap
and no size limit on the payload, which is arbitrary agent-supplied data.
Measured: 2000 renders carrying 50 KB of HTML each — a tile per iteration of an
analysis loop, which is what the API is for — held 100 MB in the daemon for the
life of the session. Nothing outside the cell reads a tile: the only consumer
is `host.app.tiles()`, so none of it was ever displayed to anyone.

**Session history.** A `SessionState` left `_sessions` only on an explicit
close or delete. The idle sweeper stops a cold session's kernels and leaves the
state resident, and its provider history is ~1.1 MB for a 200-turn
conversation — essentially the whole cost. A daemon therefore accumulated every
conversation it had served, holding history for kernels that no longer existed.

The history is recoverable: `_seed_messages` rebuilds it from
`restore_action_history` because the store is the canonical provider history.
Dropping it leaves the session exactly as a daemon restart does, which every
reader already handles — after a restart, no session is resident at all.
"""

from __future__ import annotations

import sys

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.host_dispatch import HostDispatcher, build_dispatcher
from openai4s.server import gateway as gateway_mod


def _deep_size(obj, seen=None) -> int:
    seen = seen if seen is not None else set()
    if id(obj) in seen:
        return 0
    seen.add(id(obj))
    total = sys.getsizeof(obj)
    if isinstance(obj, dict):
        for key, value in obj.items():
            total += _deep_size(key, seen) + _deep_size(value, seen)
    elif isinstance(obj, (list, tuple, set)):
        for value in obj:
            total += _deep_size(value, seen)
    return total


# --------------------------------------------------------------------------
# app tiles
# --------------------------------------------------------------------------


@pytest.fixture
def dispatcher(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    cfg = Config(
        data_dir=tmp_path / "data",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    return build_dispatcher(cfg, frame_id="f-1", workspace=workspace)


def test_a_render_loop_does_not_grow_the_daemon_without_limit(dispatcher):
    """The defect, as the number that used to have no ceiling.

    Distinct payloads on purpose: identical ones are a single interned string,
    which makes the leak look 100x smaller than it is.
    """
    for index in range(2000):
        dispatcher._m_app_render(
            {"kind": "html", "payload": f"<div id={index}>" + "x" * 50_000 + "</div>"}
        )
    tiles = dispatcher._m_app_tiles()
    assert len(tiles) == HostDispatcher.MAX_APP_TILES
    assert _deep_size(tiles) < 20_000_000, "the tile list is still unbounded"


def test_the_tiles_kept_are_the_recent_ones(dispatcher):
    """Which end is dropped matters: a cell reads tiles back to see what it
    just rendered, so keeping the oldest would make the API useless under the
    exact loop that triggers the cap."""
    for index in range(HostDispatcher.MAX_APP_TILES + 10):
        dispatcher._m_app_render({"kind": "html", "payload": f"tile-{index}"})
    payloads = [tile["payload"] for tile in dispatcher._m_app_tiles()]
    assert payloads[-1] == f"tile-{HostDispatcher.MAX_APP_TILES + 9}"
    assert "tile-0" not in payloads


def test_a_cell_is_told_when_older_tiles_were_dropped(dispatcher):
    """Silent truncation reads as "here is everything". A cell that knows it
    lost tiles can do something about it; one that does not, cannot."""
    first = dispatcher._m_app_render({"kind": "html", "payload": "a"})
    assert "dropped" not in first
    for index in range(HostDispatcher.MAX_APP_TILES + 5):
        result = dispatcher._m_app_render({"kind": "html", "payload": str(index)})
    assert result["dropped"] >= 5


def test_an_oversized_payload_is_refused_rather_than_clipped(dispatcher):
    """Half a document is not a smaller document. The soft-fail shape reaches
    the cell as a RuntimeError it can catch and act on."""
    result = dispatcher._m_app_render(
        {"kind": "html", "payload": "y" * (HostDispatcher.MAX_APP_TILE_CHARS + 1)}
    )
    assert set(result.keys()) == {"error"}
    assert "limit" in result["error"]
    assert dispatcher._m_app_tiles() == []


def test_an_ordinary_tile_still_renders(dispatcher):
    """The cap must not have turned the API off."""
    result = dispatcher._m_app_render({"kind": "html", "payload": "<b>hi</b>"})
    assert result["ok"] is True
    assert result["tile_id"]
    tiles = dispatcher._m_app_tiles()
    assert len(tiles) == 1 and tiles[0]["payload"] == "<b>hi</b>"


def test_a_non_string_payload_is_measured_not_crashed_on(dispatcher):
    """`payload` is whatever a cell passed — a dict of plot data as often as
    HTML. Sizing it must not raise, and must not let a huge structure through
    just because it is not a string."""
    assert dispatcher._m_app_render({"kind": "json", "payload": {"a": [1, 2, 3]}})["ok"]
    big = {"rows": ["z" * 1000] * 500}  # ~500 KB serialised
    assert set(dispatcher._m_app_render({"kind": "json", "payload": big})) == {"error"}


# --------------------------------------------------------------------------
# session history
# --------------------------------------------------------------------------


class _Hub:
    def emitter(self, root_frame_id):
        return lambda event: None

    def broadcast(self, root_frame_id, event):
        return None


@pytest.fixture
def runner(tmp_path):
    cfg = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        max_turns=1,
    )
    return gateway_mod.SessionRunner(cfg, _Hub())


def _boot_python_kernel(st):
    """A real worker, because the sweeper only releases a session that has one.

    `_release_idle_session` returns False when nothing was stopped, so a state
    with no kernel exercises none of the path under test — the first version of
    this file asserted against exactly that and proved nothing.
    """
    from openai4s.kernel.manager import Kernel

    st.kernels.ensure("python", None, lambda: Kernel(cwd=str(st.workspace)))
    assert st.kernels.status("python").get("alive")


def _session_with_history(runner):
    project = runner.store.create_project(name="p", description="", context="")
    if isinstance(project, dict):
        project = project["project_id"]
    frame = runner.create_session(project)
    st = runner._state(frame, project)
    st.messages = [{"role": "system", "content": "s" * 4000}]
    for index in range(200):
        st.messages.append({"role": "user", "content": f"turn {index} " + "u" * 1500})
        st.messages.append(
            {"role": "assistant", "content": f"reply {index} " + "a" * 3500}
        )
    return st


def test_a_cold_session_costs_a_megabyte_before_it_is_released(runner):
    """The measurement the fix is sized against. If this ever drops on its own
    the fix below stops being worth its risk, and the number here is how anyone
    would notice."""
    st = _session_with_history(runner)
    assert _deep_size(st.messages) > 1_000_000


@pytest.fixture
def expired(runner, monkeypatch):
    """Only the trigger is stubbed. Whether the TTL has elapsed is a clock
    question with its own tests; what is under test here is what the release
    does once it fires."""
    monkeypatch.setattr(runner.recovery, "idle_expired", lambda *_a, **_k: True)
    return runner


def test_releasing_an_idle_session_drops_its_resident_history(expired):
    """The defect. The sweeper stopped the kernels and left the conversation
    resident, so the daemon kept history for a kernel that no longer existed.
    """
    st = _session_with_history(expired)
    _boot_python_kernel(st)
    released = expired._release_idle_session(st, reason="idle")
    assert released, "the sweeper did not release; this test proves nothing"
    assert st.messages == []
    assert _deep_size(st.messages) < 1000


def test_a_session_the_sweeper_declined_keeps_its_history(expired):
    """Dropping history for a session that was NOT released would discard a
    live conversation's context mid-use. The clear belongs inside the branch
    that actually stopped something, and a state with no worker is the case
    that reaches `if not stopped: return False`."""
    st = _session_with_history(expired)
    before = len(st.messages)
    released = expired._release_idle_session(st, reason="idle")
    assert not released
    assert len(st.messages) == before


def test_the_history_comes_back_from_the_store(expired):
    """What makes dropping it safe rather than lossy: the store is the
    canonical provider history, and reseeding is the same path a daemon restart
    takes. Without this, the fix would be data loss with a comment."""
    st = _session_with_history(expired)
    _boot_python_kernel(st)
    assert expired._release_idle_session(st, reason="idle")
    assert st.messages == []
    expired._seed_messages(st)
    assert st.messages, "a released session could not rebuild its history"
    assert st.messages[0]["role"] == "system"


def test_the_session_keeps_the_settings_that_only_live_in_memory(expired):
    """The reason this drops the history rather than the whole SessionState.
    The model override, plan/explore flags and pinned environment exist nowhere
    else; evicting the state would silently reset a user's choices."""
    st = _session_with_history(expired)
    _boot_python_kernel(st)
    st.model = "claude-opus-5"
    st.plan = True
    st.explore = True
    st.desired_env = "protein"
    assert expired._release_idle_session(st, reason="idle")
    assert st.model == "claude-opus-5"
    assert st.plan is True and st.explore is True
    assert st.desired_env == "protein"
