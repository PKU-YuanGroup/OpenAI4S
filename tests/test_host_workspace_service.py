"""Compatibility contracts for the host workspace file boundary."""

from pathlib import Path

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.host_dispatch import HostDispatcher
from openai4s.tools import get_tool_by_host_method


def _dispatcher(tmp_path: Path, frame_id: str | None = "frame-1") -> HostDispatcher:
    cfg = Config(
        data_dir=tmp_path / "data",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    return HostDispatcher(cfg=cfg, frame_id=frame_id)


def test_workspace_follows_frame_id_assigned_after_construction(tmp_path):
    dispatcher = _dispatcher(tmp_path, frame_id=None)
    default_workspace = dispatcher._workspace()

    dispatcher.frame_id = "frame-late"
    result = dispatcher("write_file", [{"path": "result.txt", "content": "late-bound"}])

    assert default_workspace.name == "default"
    assert result["path"] == "result.txt"
    assert dispatcher._workspace().name == "frame-late"
    assert (dispatcher._workspace() / "result.txt").read_text() == "late-bound"
    assert not (default_workspace / "result.txt").exists()


def test_dispatcher_envelope_calls_registered_file_tool_class(tmp_path, monkeypatch):
    dispatcher = _dispatcher(tmp_path)
    tool = get_tool_by_host_method("list_dir")
    assert tool is not None
    seen = []

    def execute(_self, context, arguments):
        seen.append((context, arguments))
        return {"path": ".", "count": 0, "entries": []}

    monkeypatch.setattr(type(tool), "execute", execute)

    result = dispatcher("list_dir", [{"path": "."}])

    assert result == {"path": ".", "count": 0, "entries": []}
    assert seen == [(dispatcher._tool_context, {"path": "."})]
    assert seen[0][0].workspace() == dispatcher._workspace()


def test_workspace_service_keeps_legacy_operation_facade(tmp_path):
    dispatcher = _dispatcher(tmp_path)
    service = dispatcher._files

    written = service.write_file({"path": "compat.txt", "content": "hello"})
    read = service.read_file({"path": "compat.txt"})

    assert written == {"path": "compat.txt", "bytes": 5}
    assert read["content"] == "hello"
    for method in ("edit_file", "glob", "grep", "list_dir"):
        assert callable(getattr(service, method))


def test_resolve_confines_parent_absolute_and_symlink_paths(tmp_path):
    dispatcher = _dispatcher(tmp_path)
    workspace = dispatcher._workspace()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("outside")

    with pytest.raises(ValueError, match="path escapes the workspace"):
        dispatcher("read_file", [{"path": "../../outside/secret.txt"}])

    with pytest.raises(ValueError, match="path escapes the workspace"):
        dispatcher("read_file", [{"path": str(outside / "secret.txt")}])

    (workspace / "linked").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="path escapes the workspace"):
        dispatcher("read_file", [{"path": "linked/secret.txt"}])

    absolute_inside = workspace / "inside.txt"
    result = dispatcher(
        "write_file", [{"path": str(absolute_inside), "content": "inside"}]
    )
    assert result["path"] == "inside.txt"


def test_read_file_preserves_text_window_and_binary_shapes(tmp_path):
    dispatcher = _dispatcher(tmp_path)
    workspace = dispatcher._workspace()
    (workspace / "lines.txt").write_text("one\ntwo\nthree\n")
    (workspace / "binary.bin").write_bytes(b"\xff\x00")

    text = dispatcher("read_file", [{"path": "lines.txt", "offset": -4, "limit": -1}])
    binary = dispatcher("read_file", [{"path": "binary.bin"}])

    assert text == {
        "path": "lines.txt",
        "total_lines": 3,
        "offset": 0,
        "content": "one",
        "truncated": True,
    }
    assert binary == {
        "path": "binary.bin",
        "binary": True,
        "size_bytes": 2,
        "content": "",
    }


def test_edit_file_keeps_single_key_errors_and_replace_all_behavior(tmp_path):
    dispatcher = _dispatcher(tmp_path)
    target = dispatcher._workspace() / "notes.txt"
    target.write_text("same\nsame\n")

    missing = dispatcher(
        "edit_file",
        [{"path": "notes.txt", "old_string": "missing", "new_string": "new"}],
    )
    duplicate = dispatcher(
        "edit_file",
        [{"path": "notes.txt", "old_string": "same", "new_string": "new"}],
    )
    replaced = dispatcher(
        "edit_file",
        [
            {
                "path": "notes.txt",
                "old_string": "same",
                "new_string": "new",
                "replace_all": True,
            }
        ],
    )

    assert missing == {"error": "edit_file: old_string not found"}
    assert set(duplicate) == {"error"}
    assert "not unique (2 matches)" in duplicate["error"]
    assert replaced == {"path": "notes.txt", "replaced": 2}
    assert target.read_text() == "new\nnew\n"


def test_glob_and_grep_filter_secret_files_but_keep_normal_results(tmp_path):
    dispatcher = _dispatcher(tmp_path)
    workspace = dispatcher._workspace()
    (workspace / ".env").write_text("TOKEN=NEEDLE")
    (workspace / "private.pem").write_text("NEEDLE")
    (workspace / "notes.txt").write_text("NEEDLE")

    globbed = dispatcher("glob", [{"pattern": "*"}])
    grepped = dispatcher("grep", [{"pattern": "NEEDLE"}])

    assert globbed["matches"] == ["notes.txt"]
    assert [(hit["file"], hit["line"]) for hit in grepped["matches"]] == [
        ("notes.txt", 1)
    ]


def test_list_dir_missing_directory_keeps_soft_fail_shape(tmp_path):
    dispatcher = _dispatcher(tmp_path)

    result = dispatcher("list_dir", [{"path": "missing"}])

    assert result == {"error": "list_dir: no such directory: missing"}


def test_grep_include_filters_recursively_like_the_unfiltered_search(tmp_path):
    """`include` made the search narrower than the default, silently.

    `Path.glob("*.py")` matches only direct children while the unfiltered
    branch uses `rglob("*")`, so passing the schema's own documented example
    searched one directory level instead of the tree. A model that followed the
    documentation got a confident empty or partial answer -- the worst shape a
    search result can have, because nothing about it looks wrong.
    """
    dispatcher = _dispatcher(tmp_path)
    workspace = dispatcher._workspace()
    (workspace / "nested").mkdir(parents=True, exist_ok=True)
    (workspace / "top.py").write_text("NEEDLE here\n", encoding="utf-8")
    (workspace / "nested" / "deep.py").write_text("NEEDLE there\n", encoding="utf-8")
    (workspace / "nested" / "other.txt").write_text(
        "NEEDLE ignored\n", encoding="utf-8"
    )

    filtered = dispatcher("grep", [{"pattern": "NEEDLE", "include": "*.py"}])
    found = sorted(hit["file"] for hit in filtered["matches"])
    assert found == ["nested/deep.py", "top.py"]

    # Still a filter: the non-matching extension stays out.
    assert all(name.endswith(".py") for name in found)


def test_glob_reports_the_number_it_returned_not_the_number_it_found(tmp_path):
    """`count` was the pre-slice total beside a sliced list.

    A 5000-file glob answered `count: 5000` next to 1000 entries and no
    `truncated` key, and `host_dispatch` rendered that straight into the UI as
    "5000 items" over 1000 rows. It also disagreed with `content_search`, whose
    `count` is the retained number -- one field name meaning opposite things in
    the same tool family.
    """
    from openai4s.tools.glob_files import _MAX_MATCHES

    dispatcher = _dispatcher(tmp_path)
    workspace = dispatcher._workspace()
    bulk = workspace / "bulk"
    bulk.mkdir(parents=True, exist_ok=True)
    for index in range(_MAX_MATCHES + 25):
        (bulk / f"f{index:05d}.dat").write_text("x", encoding="utf-8")

    result = dispatcher("glob", [{"pattern": "bulk/*.dat"}])
    assert len(result["matches"]) == _MAX_MATCHES
    assert result["count"] == _MAX_MATCHES
    assert result["total_count"] == _MAX_MATCHES + 25
    assert result["truncated"] is True

    # An untruncated glob says nothing about truncation.
    small = dispatcher("glob", [{"pattern": "bulk/f0000*.dat"}])
    assert small["count"] == small["total_count"] == len(small["matches"])
    assert "truncated" not in small


def test_the_workspace_is_resolved_once_per_identity_not_once_per_call(tmp_path):
    """`workspace()` did a `resolve()` and a `mkdir` on every call.

    `relative()` calls it once per candidate path, and glob/grep/list_dir call
    `relative()` once per file, so a scan over N files paid N resolves and N
    mkdirs on top of the scan. Measured at ~16us per call before this change,
    roughly half the total cost of `relative()` itself.

    Keyed on the frame/workspace providers rather than cached outright, because
    both are late-bound -- the CLI assigns its root frame after the dispatcher
    exists. The key changing is precisely when the directory must be recomputed,
    which is what the late-binding test above pins.
    """
    from openai4s.host.files import WorkspaceFileService

    frame = {"id": "frame-a"}
    made: list[str] = []
    service = WorkspaceFileService(
        data_dir=tmp_path / "data", frame_id=lambda: frame["id"]
    )

    real_mkdir = Path.mkdir

    def counting_mkdir(self, *args, **kwargs):
        # `parents=True` recurses into each missing parent, so count only the
        # workspace directory itself.
        if self.name.startswith("frame-"):
            made.append(str(self))
        return real_mkdir(self, *args, **kwargs)

    Path.mkdir = counting_mkdir  # type: ignore[method-assign]
    try:
        first = service.workspace()
        # However many syscalls creating it took, repeating the call adds none.
        after_create = len(made)
        for _ in range(50):
            assert service.workspace() == first
        assert len(made) == after_create, made[after_create:]

        # A new frame is a different workspace, and must be resolved again --
        # the memo is keyed, not unconditional.
        frame["id"] = "frame-b"
        second = service.workspace()
        assert second != first
        assert len(made) > after_create
    finally:
        Path.mkdir = real_mkdir  # type: ignore[method-assign]


def test_secret_denylist_matches_credential_directories_not_only_basenames():
    """A credential is in the directory as often as it is in the name.

    Before this, the denylist tested the basename alone, so every one of these
    was readable through `read_file` with no prompt: none of `credentials`,
    `known_hosts`, `authorized_keys`, `config` or `hosts.yml` is a
    secret-shaped name, and all five are secrets where they live.
    """
    from openai4s.host.files import is_secret_path

    for path in (
        ".aws/credentials",
        ".ssh/known_hosts",
        ".ssh/authorized_keys",
        ".ssh/config",
        ".kube/config",
        ".docker/config.json",
        ".gnupg/trustdb.gpg",
        ".config/gcloud/credentials.db",
        ".config/gh/hosts.yml",
        "home/user/.aws/CREDENTIALS",
        "backup/.ssh",
        ".git-credentials",
        "project/.npmrc",
        "project/.pypirc",
    ):
        assert is_secret_path(path), path

    # Still true of the names the basename tier always covered.
    assert is_secret_path(".env") and is_secret_path("cfg/.ENV")
    assert is_secret_path("deploy/prod.env") and is_secret_path("id_rsa")


def test_secret_denylist_does_not_widen_into_ordinary_science_paths():
    """The measured trade-off, pinned.

    Over 182,494 files across real project trees, directory awareness added
    exactly two denials, both `.npmrc`. These are the shapes that must keep
    reading -- in particular a `.config` directory that is not gcloud/gh, which
    a substring test over the joined path would have matched.
    """
    from openai4s.host.files import is_secret_path

    for path in (
        "data/results.csv",
        "notes.txt",
        "src/main.py",
        "config.json",
        "config/settings.yaml",
        "docs/config",
        ".config/nvim/init.lua",
        ".config/gcloud-migration/plan.md",
        "runs/gh/summary.tsv",
        "figures/ssh-latency.png",
        "",
    ):
        assert not is_secret_path(path), path


def test_unattended_tier_is_wider_than_the_interactive_denylist():
    """Two tiers, one table -- and the reason they cannot be one tier.

    `is_credential_path` is what an unattended approval must consult: no human
    sees the path, so a false positive costs a fallback to asking. The hard
    pre-gate cannot be that wide, because it has no fallback -- a denied
    interactive read is a refusal, and `config.json` is an ordinary filename
    (7 of the 8 paths this tier adds under `$HOME` were exactly that).
    """
    from openai4s.host.files import is_credential_path, is_secret_path

    for path in ("config.json", "credentials", "token.json", "known_hosts"):
        assert is_credential_path(path) and not is_secret_path(path), path

    # Wider, never narrower: everything the tools refuse, the Guardian refuses.
    for path in (".env", "id_rsa", ".aws/credentials", "project/.npmrc"):
        assert is_secret_path(path) and is_credential_path(path), path

    assert not is_credential_path("results.csv") and not is_credential_path("")


def test_a_symlink_cannot_walk_a_secret_past_the_raw_path_pre_gate(tmp_path):
    """The denylist is applied to what is opened, not to what was typed.

    `HostDispatcher` screens the raw argument, which a symlink inside the
    workspace does not contain. With the workspace at the run cwd -- what the
    CLI sets -- that turned the unsandboxed daemon into a read primitive for a
    file the kernel sandbox denies the cell directly.
    """
    from openai4s.host.files import WorkspaceFileService

    workspace = tmp_path / "run"
    (workspace / ".ssh").mkdir(parents=True)
    (workspace / ".ssh" / "id_rsa").write_text("PRIVATE KEY")
    (workspace / "notes.txt").symlink_to(workspace / ".ssh" / "id_rsa")
    service = WorkspaceFileService(
        data_dir=tmp_path / "data",
        frame_id=lambda: "frame-symlink",
        workspace=lambda: workspace,
    )

    with pytest.raises(ValueError, match="secret"):
        service.resolve("notes.txt", must_exist=True)
    with pytest.raises(ValueError, match="secret"):
        service.resolve(".ssh/id_rsa", must_exist=True)
    # An ordinary file in the same workspace is untouched.
    (workspace / "data.csv").write_text("a,b\n")
    assert service.resolve("data.csv", must_exist=True).name == "data.csv"


def test_a_workspace_inside_a_credential_directory_is_still_usable(tmp_path):
    """The boundary must not deny its own root.

    Paths are tested workspace-*relative*, so running from inside `.aws` does
    not make every file in the run unreadable -- the same carve-out the kernel
    sandbox makes in `_default_secret_read_denials`.
    """
    from openai4s.host.files import WorkspaceFileService

    workspace = tmp_path / ".aws"
    workspace.mkdir()
    (workspace / "notes.txt").write_text("hi")
    service = WorkspaceFileService(
        data_dir=tmp_path / "data",
        frame_id=lambda: "frame-inside",
        workspace=lambda: workspace,
    )

    assert service.resolve("notes.txt", must_exist=True).read_text() == "hi"


def test_read_file_hard_denies_a_credential_directory_without_a_prompt(tmp_path):
    """End to end through the dispatcher, not just the predicate."""
    dispatcher = _dispatcher(tmp_path)
    workspace = dispatcher._workspace()
    (workspace / ".aws").mkdir(parents=True, exist_ok=True)
    (workspace / ".aws" / "credentials").write_text(
        "[default]\naws_secret_access_key=x"
    )

    result = dispatcher("read_file", [{"path": ".aws/credentials"}])

    assert set(result.keys()) == {"error"}
    assert "secret" in result["error"].lower()
