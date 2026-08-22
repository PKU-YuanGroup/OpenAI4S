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
        ".azure/accessTokens.json",
        ".config/gcloud/credentials.db",
        ".config/gh/hosts.yml",
        ".Kube/config",
        ".ſſh/known_hosts",
        ".conﬁg/gh/hosts.yml",
        "home/user/.aws/CREDENTIALS",
        r"HOME\USER\.AWS\credentials",
        "backup/.ssh",
        ".git-credentials",
        "project/.npmrc",
        "project/.pypirc",
        "certificates/client.p12",
        "certificates/client.PFX",
        "keys/id_dsa",
        "keys/ID_ECDSA",
        "auth/.htpasswd",
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


def test_unicode_filesystem_case_equivalents_are_denied_end_to_end(tmp_path):
    dispatcher = _dispatcher(tmp_path)
    workspace = dispatcher._workspace()
    (workspace / "Vault").mkdir()
    (workspace / "Vault" / "config").write_text("SECRET")
    (workspace / ".Kube").symlink_to(workspace / "Vault", target_is_directory=True)
    # Path.resolve preserves the spelling supplied by the caller even when
    # APFS maps it to the same inode as `Vault`.
    (workspace / "notes.txt").symlink_to(workspace / "vault" / "config")

    # `notes.txt` contains no credential-shaped segment. Only the complete
    # inventory's case-folded `.Kube -> vault` reverse alias can classify it.
    with pytest.raises(ValueError, match="secret"):
        dispatcher("read_file", [{"path": "notes.txt"}])
    with pytest.raises(ValueError, match="secret"):
        dispatcher._files.resolve("notes.txt", must_exist=True)


def test_unattended_tier_is_wider_than_the_interactive_denylist():
    """Two tiers, one table -- and the reason they cannot be one tier.

    `is_credential_path` is what automatic approval must consult. A match
    becomes an audited ask, so an attached human can review a false positive;
    without a channel it is denied. The hard pre-gate cannot be that wide,
    because it has no review path -- `config.json` is an ordinary filename (7
    of the 8 paths this tier adds under `$HOME` were exactly that).
    """
    from openai4s.host.files import is_credential_path, is_secret_path

    for path in ("config.json", "credentials", "token.json", "known_hosts"):
        assert is_credential_path(path) and not is_secret_path(path), path

    # Wider, never narrower: everything the tools refuse, auto approval refuses.
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
    dispatcher = _dispatcher(tmp_path)
    workspace = dispatcher._workspace()
    (workspace / "vault").mkdir(parents=True)
    secret = "PRIVATE HOST INVENTORY"
    (workspace / "vault" / "known_hosts").write_text(secret)
    # Resolving `.ssh` erases the credential-bearing segment and leaves only
    # `vault/known_hosts`. The service must inspect each stable symlink hop as
    # well as the final canonical path.
    # Mixed case pins the same case-insensitive policy on case-sensitive Linux.
    (workspace / ".SSH").symlink_to(workspace / "vault", target_is_directory=True)
    (workspace / "via-chain.txt").symlink_to(workspace / ".SSH" / "known_hosts")
    # A second alias skips the `.ssh` name entirely. The canonical root of the
    # separately named `.ssh` link must still classify the same underlying dir.
    (workspace / "notes.txt").symlink_to(workspace / "vault" / "known_hosts")

    # The raw argument is innocuous, so only the resolved-path check can stop
    # this. Exercise the real dispatcher/tool/service chain rather than calling
    # WorkspaceFileService.resolve() directly.
    for alias in ("via-chain.txt", "notes.txt"):
        with pytest.raises(ValueError, match="secret") as denied:
            dispatcher("read_file", [{"path": alias}])
        assert secret not in str(denied.value)

    # An ordinary file in the same workspace is untouched.
    (workspace / "data.csv").write_text("a,b\n")
    result = dispatcher("read_file", [{"path": "data.csv"}])
    assert result["content"] == "a,b"


def test_symlink_chain_cannot_leave_workspace_and_reenter_through_secret_alias(
    tmp_path,
):
    dispatcher = _dispatcher(tmp_path)
    workspace = dispatcher._workspace()
    outside = tmp_path / "outside-aliases"
    outside.mkdir()
    (workspace / "vault").mkdir()
    secret = "ESCAPE_REENTER_ALIAS_VALUE"
    (workspace / "vault" / "known_hosts").write_text(secret)
    (outside / ".ssh").symlink_to(workspace / "vault", target_is_directory=True)
    (outside / "back").symlink_to(outside / ".ssh", target_is_directory=True)
    (workspace / "bridge").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="path escapes the workspace") as denied:
        dispatcher("read_file", [{"path": "bridge/back/known_hosts"}])
    assert secret not in str(denied.value)


def test_alias_inspection_does_not_treat_scandir_failure_as_no_alias(
    tmp_path, monkeypatch
):
    from openai4s.host import files as files_mod

    dispatcher = _dispatcher(tmp_path)
    workspace = dispatcher._workspace()
    (workspace / "vault").mkdir()
    secret = "PRIVATE FALLBACK INVENTORY"
    (workspace / "vault" / "known_hosts").write_text(secret)
    (workspace / ".SSH").symlink_to(workspace / "vault", target_is_directory=True)
    (workspace / "notes.txt").symlink_to(workspace / "vault" / "known_hosts")
    real_scandir = files_mod.os.scandir

    def unreadable(directory):
        if Path(directory) == workspace:
            raise PermissionError("directory is traverse-only")
        return real_scandir(directory)

    monkeypatch.setattr(files_mod.os, "scandir", unreadable)

    with pytest.raises(ValueError, match="secret") as denied:
        dispatcher("read_file", [{"path": "notes.txt"}])
    assert secret not in str(denied.value)


def test_nested_sibling_secret_alias_marks_the_canonical_tree(tmp_path):
    dispatcher = _dispatcher(tmp_path)
    workspace = dispatcher._workspace()
    (workspace / "vault").mkdir()
    secret = "PRIVATE_NESTED_SIBLING_ALIAS_VALUE"
    (workspace / "vault" / "known_hosts").write_text(secret)
    (workspace / "nested").mkdir()
    (workspace / "nested" / ".ssh").symlink_to(
        workspace / "vault", target_is_directory=True
    )
    (workspace / "notes.txt").symlink_to(workspace / "vault" / "known_hosts")

    with pytest.raises(ValueError, match="secret") as denied:
        dispatcher("read_file", [{"path": "notes.txt"}])
    assert secret not in str(denied.value)
    searched = dispatcher("grep", [{"pattern": "PRIVATE_NESTED_SIBLING", "path": "."}])
    assert searched["matches"] == []
    assert secret not in repr(searched)


def test_secret_basename_alias_marks_the_same_canonical_file(tmp_path):
    dispatcher = _dispatcher(tmp_path)
    workspace = dispatcher._workspace()
    (workspace / "vault").mkdir()
    secret = "PRIVATE_BASENAME_ALIAS"
    target = workspace / "vault" / "token-data"
    target.write_text(secret)
    (workspace / ".env").symlink_to(target)
    (workspace / "notes.txt").symlink_to(target)

    with pytest.raises(ValueError, match="secret") as denied:
        dispatcher("read_file", [{"path": "notes.txt"}])
    assert secret not in str(denied.value)


def test_secret_root_descendant_hardlink_is_denied_by_inode_identity(tmp_path):
    dispatcher = _dispatcher(tmp_path)
    workspace = dispatcher._workspace()
    secret = "PRIVATE_HARDLINK_VALUE"
    (workspace / "vault").mkdir()
    credential = workspace / "vault" / "known_hosts"
    credential.write_text(secret)
    (workspace / ".ssh").symlink_to(workspace / "vault", target_is_directory=True)
    (workspace / "notes.txt").hardlink_to(credential)

    with pytest.raises(ValueError, match="secret") as denied:
        dispatcher("read_file", [{"path": "notes.txt"}])
    assert secret not in str(denied.value)


def test_external_secret_hardlink_is_denied_but_single_link_reads_still_work(
    tmp_path,
):
    """An inode alias can cross the workspace boundary without a symlink."""

    dispatcher = _dispatcher(tmp_path)
    workspace = dispatcher._workspace()
    outside = tmp_path / "outside" / ".ssh"
    outside.mkdir(parents=True)
    secret = "EXTERNAL_PRIVATE_KEY_MATERIAL"
    credential = outside / "id_rsa"
    credential.write_text(secret)
    alias = workspace / "notes.txt"
    try:
        alias.hardlink_to(credential)
    except OSError as error:
        pytest.skip(f"filesystem does not support hardlinks: {error}")

    with pytest.raises(ValueError, match="multiply-linked") as denied:
        dispatcher("read_file", [{"path": "notes.txt"}])
    assert secret not in str(denied.value)
    assert dispatcher._files.resolved_credential_checker()(alias) is True

    ordinary = workspace / "ordinary.txt"
    ordinary.write_text("ordinary single-link content")
    read = dispatcher("read_file", [{"path": "ordinary.txt"}])
    assert read["content"] == "ordinary single-link content"
    assert dispatcher._files.resolved_credential_checker()(ordinary) is False

    searched = dispatcher("grep", [{"pattern": "EXTERNAL_PRIVATE", "path": "."}])
    assert searched["matches"] == []
    assert secret not in repr(searched)
    assert dispatcher("glob", [{"pattern": "*.txt"}])["matches"] == ["ordinary.txt"]
    listed = dispatcher("list_dir", [{"path": "."}])
    assert [entry["name"] for entry in listed["entries"]] == ["ordinary.txt"]


def test_verified_read_rejects_final_symlink_swapped_after_parent_acquisition(
    tmp_path,
):
    dispatcher = _dispatcher(tmp_path)
    workspace = dispatcher._workspace()
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("TOCTOU_SECRET")
    candidate = workspace / "candidate.txt"
    candidate.write_text("safe")

    with dispatcher._files.secure_parent("candidate.txt") as parent:
        candidate.unlink()
        candidate.symlink_to(outside)
        with pytest.raises(ValueError, match="symlink traversal") as denied:
            parent.open_verified_read()
    assert "TOCTOU_SECRET" not in str(denied.value)


def test_write_and_edit_keep_using_the_acquired_parent_after_path_swap(
    tmp_path, monkeypatch
):
    """A cell-side directory swap cannot redirect publication out of bounds."""

    dispatcher = _dispatcher(tmp_path)
    workspace = dispatcher._workspace()
    write_live = workspace / "write-live"
    edit_live = workspace / "edit-live"
    write_live.mkdir()
    edit_live.mkdir()
    (edit_live / "value.txt").write_text("before")
    write_detached = workspace / "write-detached"
    edit_detached = workspace / "edit-detached"
    write_outside = tmp_path / "write-outside"
    edit_outside = tmp_path / "edit-outside"
    write_outside.mkdir()
    edit_outside.mkdir()
    (edit_outside / "value.txt").write_text("outside sentinel")

    real_secure_parent = dispatcher._files.secure_parent
    swaps = {
        "write-live/result.txt": (write_live, write_detached, write_outside),
        "edit-live/value.txt": (edit_live, edit_detached, edit_outside),
    }

    def acquire_then_swap(relative, *, create_parents=False):
        parent = real_secure_parent(relative, create_parents=create_parents)
        live, detached, outside = swaps[str(relative)]
        live.rename(detached)
        live.symlink_to(outside, target_is_directory=True)
        return parent

    monkeypatch.setattr(dispatcher._files, "secure_parent", acquire_then_swap)

    written = dispatcher(
        "write_file", [{"path": "write-live/result.txt", "content": "inside"}]
    )
    edited = dispatcher(
        "edit_file",
        [
            {
                "path": "edit-live/value.txt",
                "old_string": "before",
                "new_string": "after",
            }
        ],
    )

    assert written["path"] == "write-live/result.txt"
    assert edited == {"path": "edit-live/value.txt", "replaced": 1}
    assert (write_detached / "result.txt").read_text() == "inside"
    assert not (write_outside / "result.txt").exists()
    assert (edit_detached / "value.txt").read_text() == "after"
    assert (edit_outside / "value.txt").read_text() == "outside sentinel"


def test_dangling_exact_alias_blocks_casefold_equivalent_future_write(tmp_path):
    dispatcher = _dispatcher(tmp_path)
    workspace = dispatcher._workspace()
    (workspace / ".env").symlink_to(workspace / "Token")

    with pytest.raises(ValueError, match="secret"):
        dispatcher("write_file", [{"path": "token", "content": "PRIVATE"}])

    assert not (workspace / "token").exists()
    assert not (workspace / "Token").exists()


def test_dangling_root_alias_blocks_unicode_equivalent_future_write(tmp_path):
    dispatcher = _dispatcher(tmp_path)
    workspace = dispatcher._workspace()
    composed = workspace / "Caf\u00e9"
    decomposed = "Cafe\u0301"
    (workspace / ".ssh").symlink_to(composed, target_is_directory=True)

    with pytest.raises(ValueError, match="secret"):
        dispatcher(
            "write_file",
            [{"path": f"{decomposed}/future.txt", "content": "PRIVATE"}],
        )

    assert not (workspace / decomposed).exists()
    assert not composed.exists()


@pytest.mark.parametrize("vault_exists", [False, True])
def test_secret_sequence_alias_blocks_paths_before_write_creates_them(
    tmp_path, vault_exists
):
    dispatcher = _dispatcher(tmp_path)
    workspace = dispatcher._workspace()
    vault = workspace / "Vault"
    if vault_exists:
        vault.mkdir()
    (workspace / ".config").symlink_to(vault, target_is_directory=True)

    target = workspace / "vault" / "GCLOUD" / "future-token.txt"
    with pytest.raises(ValueError, match="secret"):
        dispatcher(
            "write_file",
            [{"path": "vault/GCLOUD/future-token.txt", "content": "PRIVATE"}],
        )

    assert not target.exists()
    assert not target.parent.exists()
    assert not (vault / "gcloud" / "future-token.txt").exists()


def test_alias_inventory_is_bounded_and_truncation_fails_closed(tmp_path, monkeypatch):
    from openai4s.host import files as files_mod

    workspace = tmp_path / "bounded-alias-scan"
    workspace.mkdir()
    yielded = 0

    class Entry:
        def __init__(self, name):
            self.name = name

        def is_dir(self, *, follow_symlinks=True):
            return False

    class Scan:
        def __enter__(self):
            def entries():
                nonlocal yielded
                for index in range(50_000):
                    yielded += 1
                    yield Entry(f"ordinary-{index}.dat")

            return entries()

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(files_mod, "MAX_SCAN_ENTRIES", 4096)
    monkeypatch.setattr(files_mod.os, "scandir", lambda _directory: Scan())
    snapshot = files_mod._SecretAliasSnapshot(workspace)

    with pytest.raises(ValueError, match="budget"):
        snapshot.contains(workspace / "results.csv")
    assert yielded == 4097
    assert snapshot._secret_roots == ()


@pytest.mark.parametrize("absolute_target", [False, True])
def test_symlink_parent_steps_do_not_erase_secret_directory_hops(
    tmp_path, absolute_target
):
    dispatcher = _dispatcher(tmp_path)
    workspace = dispatcher._workspace()
    (workspace / "vault" / "nested").mkdir(parents=True)
    (workspace / "vault" / "public").mkdir()
    secret = "PRIVATE INVENTORY"
    (workspace / "vault" / "public" / "inventory").write_text(secret)
    (workspace / ".ssh").symlink_to(
        workspace / "vault" / "nested", target_is_directory=True
    )
    link_target = Path(".ssh/../public/inventory")
    if absolute_target:
        link_target = workspace / link_target
    (workspace / "alias.txt").symlink_to(link_target)

    # POSIX applies `..` after expanding `.ssh` to `vault/nested`. Normalizing
    # the spelling first erases the credential hop and reaches the private file.
    with pytest.raises(ValueError, match="secret") as denied:
        dispatcher("read_file", [{"path": "alias.txt"}])
    assert secret not in str(denied.value)


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

    cfg = Config(
        data_dir=tmp_path / "dispatcher-data",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    dispatcher = HostDispatcher(
        cfg=cfg,
        frame_id="frame-inside-credential-root",
        workspace=workspace,
    )
    absolute = dispatcher("read_file", [{"path": str(workspace / "notes.txt")}])
    assert absolute["content"] == "hi"


def test_collection_tools_filter_files_reached_by_a_secret_directory_alias(tmp_path):
    dispatcher = _dispatcher(tmp_path)
    workspace = dispatcher._workspace()
    (workspace / "vault").mkdir()
    secret = "PRIVATE_GREP_SECRET_VALUE"
    (workspace / "vault" / "known_hosts").write_text(secret)
    (workspace / ".ssh").symlink_to(workspace / "vault", target_is_directory=True)
    (workspace / "notes.txt").symlink_to(workspace / "vault" / "known_hosts")
    (workspace / "public.txt").write_text("PUBLIC_CONTROL")

    grep_secret = dispatcher("grep", [{"pattern": "PRIVATE_GREP", "path": "."}])
    assert grep_secret["matches"] == []
    assert secret not in repr(grep_secret)
    grep_public = dispatcher("grep", [{"pattern": "PUBLIC_CONTROL", "path": "."}])
    assert [match["file"] for match in grep_public["matches"]] == ["public.txt"]

    globbed = dispatcher("glob", [{"pattern": "**/*"}])
    assert globbed["matches"] == ["public.txt"]
    listed = dispatcher("list_dir", [{"path": "."}])
    assert [entry["name"] for entry in listed["entries"]] == ["public.txt"]


def test_content_search_rechecks_a_candidate_immediately_before_open(
    tmp_path, monkeypatch
):
    dispatcher = _dispatcher(tmp_path)
    workspace = dispatcher._workspace()
    (workspace / ".ssh").mkdir()
    secret = "PRIVATE_SECOND_CHECK_VALUE"
    credential = workspace / ".ssh" / "known_hosts"
    credential.write_text(secret)
    candidate = workspace / "candidate.txt"
    candidate.write_text("ordinary")
    real_factory = dispatcher._files.verified_read_opener
    swapped = False
    opens = 0

    def opener_factory():
        real_open = real_factory()

        def open_candidate(path):
            nonlocal swapped, opens
            opens += 1
            opened = real_open(Path(path))
            if Path(path) == candidate and not swapped:
                candidate.unlink()
                candidate.symlink_to(credential)
                swapped = True
            return opened

        return open_candidate

    monkeypatch.setattr(dispatcher._files, "verified_read_opener", opener_factory)

    searched = dispatcher(
        "grep", [{"pattern": "PRIVATE_SECOND_CHECK", "include": "candidate.txt"}]
    )
    assert swapped is True and opens >= 2
    assert searched["matches"] == []
    assert secret not in repr(searched)


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
