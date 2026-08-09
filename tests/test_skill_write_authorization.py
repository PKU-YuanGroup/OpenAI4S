"""Two defects found while tracing CodeQL alerts that were themselves noise.

All three reported alert classes — 30 `py/path-injection`, `js/xss`,
`py/http-response-splitting` — turned out to be false positives with real
barriers behind them. Tracing them found two things the scanner never
mentioned.

**The Skill allowlist confined reads and left writes open.** `_permits` gated
`load`, `get` and `read`, and none of `edit`, `publish` or `delete`. So a
delegated child restricted to `skill_names=["a"]` could not read skill `b` —
and could overwrite its body, publish it, or delete it. That is the worse half
of the pair: the parent goes on to *execute* the recipe a restricted child
rewrote, so an unreadable Skill was a writable one.

Measured before the fix, on user-authored (writable) skills:

    read    -> REFUSED (KeyError)
    edit    -> ALLOWED
    publish -> ALLOWED
    delete  -> ALLOWED

Bundled skills hid it: they are read-only for a different reason, so the first
probe came back all-refused and looked fine. The gap only shows on the skills a
user actually owns.

**A refused job cwd created directories anyway.** `JobManager.submit` called
`mkdir(parents=True)` on the candidate's parent *before* the `realpath` /
`commonpath` containment check, so a cwd that was then correctly refused had
already created directories wherever it pointed. `realpath` does not need the
path to exist, so the early mkdir bought nothing.
"""

from __future__ import annotations

import os
import pathlib
import tempfile

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.host.delegation_policy import child_execution_policy
from openai4s.host_dispatch import build_dispatcher
from openai4s.jobs import JobManager


@pytest.fixture
def restricted(tmp_path):
    """A child allowed one user-authored Skill, with a second one it may not."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    cfg = Config(
        data_dir=tmp_path / "data",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    dispatcher = build_dispatcher(cfg, frame_id="f-child", workspace=workspace)
    service = dispatcher._skill_service
    for name in ("mine_keep", "mine_other"):
        service.edit({"name": name, "body": f"# {name}\n\noriginal {name}\n"})
    dispatcher.set_child_execution_policy(
        child_execution_policy(
            {
                "unrestricted": False,
                "capabilities": ["skills"],
                "skill_names": ["mine_keep"],
            }
        )
    )
    return service


@pytest.mark.security
@pytest.mark.parametrize("operation", ["edit", "publish", "delete"])
def test_a_skill_it_cannot_read_is_a_skill_it_cannot_write(restricted, operation):
    """The defect. Each of the three write paths, because gating one and
    forgetting the others is exactly how this happened."""
    calls = {
        "edit": lambda: restricted.edit(
            {"name": "mine_other", "body": "OVERWRITTEN BY A RESTRICTED CHILD"}
        ),
        "publish": lambda: restricted.publish("mine_other"),
        "delete": lambda: restricted.delete("mine_other"),
    }
    with pytest.raises(KeyError):
        calls[operation]()


@pytest.mark.security
def test_the_body_is_unchanged_after_a_refused_write(restricted):
    """A refusal that still wrote would be the same defect with an exception on
    top. The parent executes this body, so what it contains is the point."""
    with pytest.raises(KeyError):
        restricted.edit({"name": "mine_other", "body": "OVERWRITTEN"})
    # Read it back as an unrestricted caller would.
    from openai4s.skills_loader.loader import SkillLoader

    loader = SkillLoader(cfg=restricted.cfg)
    loader.discover()
    assert "OVERWRITTEN" not in loader.read("mine_other", "SKILL.md")


@pytest.mark.security
def test_a_restricted_child_cannot_author_a_new_name(restricted):
    """Creating `whatever_i_like` and then using it would be the same escape
    with an extra step: `edit` is also the create path."""
    with pytest.raises(KeyError):
        restricted.edit({"name": "brand_new_name", "body": "# new\n"})


def test_the_permitted_skill_is_still_writable(restricted):
    """The fix must not be "deny all writes", which would satisfy every test
    above and break the feature."""
    result = restricted.edit({"name": "mine_keep", "body": "# mine_keep\n\nedited\n"})
    assert not (isinstance(result, dict) and result.get("error")), result


def test_an_unrestricted_service_writes_anything(tmp_path):
    """Most sessions have no allowlist at all; `None` must still mean permit."""
    workspace = tmp_path / "ws2"
    workspace.mkdir()
    cfg = Config(
        data_dir=tmp_path / "data2",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    service = build_dispatcher(cfg, frame_id="f", workspace=workspace)._skill_service
    assert not (service.edit({"name": "anything", "body": "# x\n"}) or {}).get("error")


# --------------------------------------------------------------------------
# the refused cwd that created directories anyway
# --------------------------------------------------------------------------


@pytest.mark.security
def test_a_refused_cwd_creates_nothing():
    """The defect: the refusal was real and the side effect happened first."""
    with tempfile.TemporaryDirectory() as directory:
        base = pathlib.Path(directory)
        root = base / "jobs"
        root.mkdir()
        outside = base / "OUTSIDE"
        escape = os.path.relpath(str(outside / "planted" / "deep"), str(root))

        manager = JobManager(root=root)
        result = manager.submit(kind="bash", command="echo hi", cwd=escape)

        assert result.get("error") == "cwd escapes the jobs root"
        assert not outside.exists(), (
            "the refused cwd created directories outside the jobs root: "
            f"{[str(p) for p in outside.rglob('*')][:5]}"
        )


def test_a_legitimate_cwd_is_still_created():
    """The check must not have become "refuse everything" — a job's working
    directory is created on demand by design."""
    with tempfile.TemporaryDirectory() as directory:
        root = pathlib.Path(directory) / "jobs"
        root.mkdir()
        manager = JobManager(root=root)
        try:
            result = manager.submit(kind="bash", command="true", cwd="run/one")
            assert not result.get("error"), result
            assert (root / "run" / "one").is_dir()
        finally:
            # The submitted job runs on a worker thread that keeps writing its
            # status/log files under ``root``; close() stops it and waits, or
            # TemporaryDirectory cleanup races the writer and dies with
            # "Directory not empty".
            manager.close()


def test_an_uncreatable_directory_is_reported_not_raised():
    """It used to raise `PermissionError` out of `submit`, which reaches the
    caller as a crash rather than as the error dict every other refusal here
    returns."""
    with tempfile.TemporaryDirectory() as directory:
        root = pathlib.Path(directory) / "jobs"
        root.mkdir()
        blocker = root / "blocked"
        blocker.write_text("I am a file, not a directory\n", encoding="utf-8")

        manager = JobManager(root=root)
        try:
            result = manager.submit(kind="bash", command="true", cwd="blocked/under")
            assert isinstance(result, dict) and result.get("error"), result
        finally:
            manager.close()
