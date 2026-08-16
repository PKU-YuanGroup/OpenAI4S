"""`host.frames` must not read a colleague's cells (external review #2).

The team-mode scoping lived in the SQLite authorizer, which only
`host.query` goes through. `host.frames` reads the same rows -- frame
names, cell code, stdout -- through ordinary Store methods, so a member's
agent could browse every session, regex-search every cell, and open any
colleague's frame by id. A guard on one of two doors into the same data.

What is asserted here is the *principal*, not a parameter. The fix is not
"pass visible_to_user_id" -- that is the same defect with more call sites,
because the argument's absence reads as unscoped rather than as refused.
So the tests below check the three properties that make it a boundary:
absent is refused, `None` is never an operator, and the filtering happens
before the bytes are read.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openai4s import execution_principal as ep
from openai4s.host.data import HostDataService
from openai4s.store import get_store


@pytest.fixture()
def store(tmp_path):
    st = get_store(str(tmp_path / "state.db"))
    try:
        yield st
    finally:
        st.close()


@pytest.fixture()
def team_mode(monkeypatch):
    monkeypatch.setenv("OPENAI4S_TEAM_MODE", "1")


def _seed(store):
    """Alice's private session and Bob's, each with a cell worth hiding."""
    alice = store.team.create_user(username="alice", password="pw-a")
    bob = store.team.create_user(username="bob", password="pw-b")
    a_root = store.new_frame(kind="turn", project_id="p", name="ALICE SESSION")
    b_root = store.new_frame(kind="turn", project_id="p", name="BOB SESSION")
    # Private by default here: the point is that ownership alone hides it.
    store.team.set_session_owner(
        a_root, alice["id"], project_id="p", visibility="private"
    )
    store.team.set_session_owner(
        b_root, bob["id"], project_id="p", visibility="private"
    )
    store.log_cell(
        frame_id=a_root,
        root_frame_id=a_root,
        project_id="p",
        code="password = 'hunter2'",
        result={"stdout": "secret alice output", "stderr": "", "ok": True},
    )
    return alice, bob, a_root, b_root


def _service(store) -> HostDataService:
    return HostDataService(
        store=lambda: store,
        config=SimpleNamespace(artifacts_dir=None),
        frame_id=None,
        resolve_path=lambda p: p,
    )


# --- absent is refused -------------------------------------------------------


def test_team_mode_without_a_principal_refuses(store, team_mode):
    """The propagation is what carries the identity; a gap in it is a bug,
    and the only safe reading of that bug is 'no'. The tempting shape --
    treat a missing principal as unrestricted -- is how this leaked."""
    _seed(store)
    with ep.scope(None):
        with pytest.raises(ep.PrincipalRequired):
            _service(store).frames({"project_id": "all"})


def test_a_single_user_daemon_is_unrestricted_without_team_mode(store):
    """INV-1: no team mode, no principal bound, and the documented
    behaviour is unchanged -- but by resolving to an explicit SINGLE_USER,
    not by reading the absence as permission."""
    _seed(store)
    with ep.scope(None):
        assert ep.resolve() is ep.SINGLE_USER
        payload = _service(store).frames({"project_id": "all"})
    names = {f.get("name") for f in payload["frames"]}
    assert {"ALICE SESSION", "BOB SESSION"} <= names


# --- the leak itself ---------------------------------------------------------


def _as(user) -> ep.Principal:
    return ep.Principal(
        user_id=user["id"], username=user["username"], role="member", kind="user"
    )


def test_browse_hides_another_members_session(store, team_mode):
    alice, bob, _, _ = _seed(store)
    with ep.scope(_as(bob)):
        payload = _service(store).frames({"project_id": "all"})
    names = {f.get("name") for f in payload["frames"]}
    assert "ALICE SESSION" not in names
    assert "BOB SESSION" in names


def test_search_does_not_regex_another_members_cells(store, team_mode):
    """The sharpest of the three: this one reads `code` and `stdout` per
    row, so a filter applied to the results would have loaded them first."""
    alice, bob, _, _ = _seed(store)
    with ep.scope(_as(bob)):
        payload = _service(store).frames({"pattern": "password", "project_id": "all"})
    assert payload["frames"] == []


def test_a_frame_id_does_not_open_another_members_session(store, team_mode):
    alice, bob, a_root, _ = _seed(store)
    with ep.scope(_as(bob)):
        with pytest.raises(KeyError):
            _service(store).frames({"frame_id": a_root})


def test_the_owner_still_sees_their_own(store, team_mode):
    """The half a fail-closed bug looks identical without. A predicate that
    refuses everything passes every 'the stranger cannot' assertion."""
    alice, _, a_root, _ = _seed(store)
    with ep.scope(_as(alice)):
        service = _service(store)
        assert service.frames({"frame_id": a_root})["frame"]["name"] == "ALICE SESSION"
        assert service.frames({"pattern": "password", "project_id": "all"})["frames"]
        names = {f.get("name") for f in service.frames({"project_id": "all"})["frames"]}
    assert "ALICE SESSION" in names


def test_an_admin_sees_everything(store, team_mode):
    _seed(store)
    admin = ep.Principal(
        user_id="u_admin", username="root", role="admin", kind="user"
    )
    with ep.scope(admin):
        names = {
            f.get("name")
            for f in _service(store).frames({"project_id": "all"})["frames"]
        }
    assert {"ALICE SESSION", "BOB SESSION"} <= names


# --- None is never an operator -----------------------------------------------


def test_an_unauthenticated_team_request_has_no_principal():
    """`for_identity` runs only when team mode is on, so None there means
    'nobody is logged in' -- an exempt path like the login page. Mapping it
    to SINGLE_USER would hand every anonymous request the operator's
    reach."""
    assert ep.for_identity(None) is None


def test_the_service_identity_is_explicit():
    class _Id:
        user_id, username, role, kind = "service:cli", "cli", "admin", "service"

    assert ep.for_identity(_Id()) is ep.SERVICE
    assert ep.SERVICE.unrestricted


def test_a_guest_is_restricted_even_with_a_member_row(store, team_mode):
    """`session_visible_to` refuses a global guest before it consults
    project_members; the listing SQL did not, so an admin who added a guest
    to a project listed them every project-visibility session -- which they
    were then 404'd from opening."""
    alice, _, a_root, _ = _seed(store)
    store.team.set_session_owner(
        a_root, alice["id"], project_id="p", visibility="project"
    )
    guest = store.team.create_user(username="gwen", password="pw-g", role="guest")
    store.governance.set_member("p", guest["id"], role="member")
    with ep.scope(
        ep.Principal(
            user_id=guest["id"], username="gwen", role="guest", kind="user"
        )
    ):
        names = {
            f.get("name")
            for f in _service(store).frames({"project_id": "all"})["frames"]
        }
    assert "ALICE SESSION" not in names
