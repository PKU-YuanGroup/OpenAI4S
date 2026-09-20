import re
import subprocess
from contextlib import contextmanager

import pytest

from scripts import check_directory_readmes, update_contributors


def test_public_recognition_is_appended_after_commit_contributors():
    # Spelled out rather than splatting RECOGNIZED_CONTRIBUTORS: an expectation
    # re-derived from the constant under test passes just as happily when that
    # constant is empty, which is the one outcome this has to catch.
    merged = update_contributors.include_recognized_contributors(
        [{"login": "MostCommits", "type": "User", "contributions": 10}]
    )

    assert [person["login"] for person in merged] == [
        "MostCommits",
        "EQSTLab",
        "difficulttopickaname",
        "ChampionZhong",
        "Pandasama2025",
    ]


def test_a_recognized_login_the_api_already_lists_is_not_duplicated():
    commit_people = [
        {"login": "MostCommits", "type": "User", "contributions": 10},
        {"login": "eqstlab", "type": "User", "contributions": 1},
    ]

    merged = update_contributors.include_recognized_contributors(commit_people)

    # The other recognized logins are still appended: the claim here is only
    # that the login the API already returned is not repeated.
    assert [person["login"] for person in merged] == [
        "MostCommits",
        "eqstlab",
        "difficulttopickaname",
        "ChampionZhong",
        "Pandasama2025",
    ]


def test_a_recognized_login_that_is_excluded_is_still_refused(monkeypatch):
    excluded = next(iter(update_contributors.EXCLUDE))
    monkeypatch.setattr(update_contributors, "RECOGNIZED_CONTRIBUTORS", (excluded,))

    merged = update_contributors.include_recognized_contributors(
        [{"login": "MostCommits", "type": "User", "contributions": 10}]
    )

    assert [person["login"] for person in merged] == ["MostCommits"]


def test_empty_api_result_still_fails_before_recognition_is_added(monkeypatch):
    monkeypatch.setattr(update_contributors, "read_documents", dict)
    monkeypatch.setattr(update_contributors, "_token", lambda: None)
    monkeypatch.setattr(update_contributors, "fetch_contributors", lambda _token: [])

    def unexpected_write(_people, _token):
        raise AssertionError(
            "an empty API result must not rewrite the contributor wall"
        )

    monkeypatch.setattr(update_contributors, "write_avatars", unexpected_write)

    assert update_contributors.main() == 1


def test_avatar_refresh_failure_keeps_current_png_and_prunes_departed_one(
    tmp_path, monkeypatch
):
    avatar_dir = tmp_path / "contributors"
    avatar_dir.mkdir()
    current = avatar_dir / "EQSTLab.png"
    current.write_bytes(b"existing-avatar")
    departed = avatar_dir / "Departed.png"
    departed.write_bytes(b"old-avatar")
    legacy_svg = avatar_dir / "EQSTLab.svg"
    legacy_svg.write_text("<svg/>", encoding="utf-8")

    monkeypatch.setattr(update_contributors, "AVATAR_DIR", str(avatar_dir))

    def fail_download(_url, _token):
        raise OSError("temporary avatar failure")

    monkeypatch.setattr(update_contributors, "_get", fail_download)

    have_png, written, surviving = update_contributors.write_avatars(
        [{"login": "EQSTLab"}], None
    )

    assert have_png == {"EQSTLab": "EQSTLab.png"}
    assert written == 0  # nothing was refreshed; only the count says so
    assert surviving == ["EQSTLab.png"]
    assert current.read_bytes() == b"existing-avatar"
    assert not departed.exists()
    assert not legacy_svg.exists()


def test_a_login_whose_casing_drifted_links_the_file_it_actually_has(
    tmp_path, monkeypatch
):
    """The committed file wins over the login's spelling, and render() follows.

    `os.path.isfile` answers case-insensitively on a case-preserving
    filesystem, so keying "do I have a PNG" off it while pruning on an exact
    `os.listdir` match deleted the committed avatar and still emitted a local
    `<img src>` for it -- a dead image on the front page of both READMEs. The
    src is therefore read back from `os.listdir`: it names the file that
    survived the prune, never the one the login would imply.
    """
    avatar_dir = tmp_path / "contributors"
    avatar_dir.mkdir()
    committed = avatar_dir / "eqstlab.png"
    committed.write_bytes(b"existing-avatar")

    monkeypatch.setattr(update_contributors, "AVATAR_DIR", str(avatar_dir))

    def fail_download(_url, _token):
        raise OSError("temporary avatar failure")

    monkeypatch.setattr(update_contributors, "_get", fail_download)

    people = [{"login": "EQSTLab"}, {"login": "NoFile"}]
    have_png, _written, surviving = update_contributors.write_avatars(people, None)

    assert committed.read_bytes() == b"existing-avatar"
    assert have_png == {"EQSTLab": "eqstlab.png"}
    assert surviving == ["eqstlab.png"]
    rendered = update_contributors.render(people, have_png)
    assert f'src="{avatar_dir.as_posix()}/eqstlab.png"' in rendered
    assert 'src="https://github.com/NoFile.png"' in rendered


def test_the_refresh_writes_the_committed_spelling_of_an_avatar():
    # Filesystem-independent, and the only assertion that is: a case-insensitive
    # filesystem writes through to the committed file whatever name it is given,
    # so the refresh scenarios below cannot see a case-variant twin at all.
    assert update_contributors._target_name("Existing", {"existing.png"}) == (
        "existing.png"
    )
    assert update_contributors._target_name("Existing", {"Existing.png"}) == (
        "Existing.png"
    )
    assert update_contributors._target_name("NewPerson", {"existing.png"}) == (
        "NewPerson.png"
    )


def test_the_unauthenticated_avatar_fallback_never_carries_the_token(
    tmp_path, monkeypatch
):
    """`github.com/<login>.png` 302s cross-host, and urllib forwards headers.

    Only `content-length`/`content-type` are dropped across a redirect, so a
    token attached here reaches avatars.githubusercontent.com, which never
    asked for it. A recognized contributor has no `avatar_url`, which is what
    made this previously-dead branch live.
    """
    monkeypatch.setattr(update_contributors, "AVATAR_DIR", str(tmp_path))
    monkeypatch.setattr(update_contributors, "_circular_png", lambda raw: raw)
    seen: list[tuple[str, str | None]] = []

    def record(url, token):
        seen.append((url, token))
        return b"png"

    monkeypatch.setattr(update_contributors, "_get", record)

    update_contributors.write_avatars(
        [
            {"login": "FromApi", "avatar_url": "https://avatars.example/u/1"},
            {"login": "Recognized"},
        ],
        "secret-token",
    )

    assert seen[0][1] == "secret-token"
    assert seen[1] == ("https://github.com/Recognized.png?s=256", None)


def test_documents_are_all_staged_before_any_is_replaced(tmp_path):
    first = tmp_path / "README.md"
    first.write_text("old\n", encoding="utf-8")
    unwritable = tmp_path / "missing" / "README_zh.md"

    with pytest.raises(OSError):
        update_contributors._write_texts(
            {str(first): "new\n", str(unwritable): "new\n"}
        )

    assert first.read_text(encoding="utf-8") == "old\n"
    assert [path.name for path in tmp_path.iterdir()] == ["README.md"]


def test_a_staged_file_a_killed_run_left_behind_is_swept(tmp_path):
    document = tmp_path / "README.md"
    document.write_text("old\n", encoding="utf-8")
    leftover = tmp_path / f"{update_contributors.TMP_PREFIX}killed"
    leftover.write_text("half a document\n", encoding="utf-8")

    update_contributors._write_texts({str(document): "new\n"})

    # Nothing but the document: the avatar directory's gate fails on any file
    # its READMEs do not list, and the prune only removes images.
    assert [path.name for path in tmp_path.iterdir()] == ["README.md"]
    assert document.read_text(encoding="utf-8") == "new\n"


def test_a_replaced_document_keeps_its_permissions(tmp_path):
    document = tmp_path / "README.md"
    document.write_text("old\n", encoding="utf-8")
    document.chmod(0o644)
    mode = document.stat().st_mode

    update_contributors._write_texts({str(document): "new\n"})

    assert document.read_text(encoding="utf-8") == "new\n"
    assert document.stat().st_mode == mode


def _stage(root):
    # The directory gate reads Git's file list, which keeps a deleted avatar
    # until the deletion is staged -- exactly as a real refresh has to be.
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)


@pytest.fixture
def contributor_checkout(tmp_path, monkeypatch):
    """A real Git file inventory with offline contributor/PNG boundaries."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    monkeypatch.chdir(tmp_path)
    avatar_dir = tmp_path / ".github" / "contributors"
    avatar_dir.mkdir(parents=True)
    for name in ("README.md", "README_zh.md"):
        (tmp_path / name).write_text(
            "# Wall\n\n<!-- CONTRIBUTORS:START -->\n<!-- CONTRIBUTORS:END -->\n",
            encoding="utf-8",
        )
        (avatar_dir / name).write_text(
            "# Avatars\n\nKeep this introduction.\n\n"
            "<!-- AVATAR-FILES:START -->\n"
            "| File | Description |\n| --- | --- |\n"
            "| `Departed.png` | Old entry. |\n"
            "<!-- AVATAR-FILES:END -->\n\nKeep this footer.\n",
            encoding="utf-8",
        )
    for name in ("CONTENTS.md", "CONTENTS_zh.md"):
        (tmp_path / ".github" / name).write_text(
            "# Files\n\n`contributors/`\n", encoding="utf-8"
        )
    monkeypatch.setattr(check_directory_readmes, "ROOT", tmp_path)
    monkeypatch.setattr(update_contributors, "_token", lambda: None)
    monkeypatch.setattr(update_contributors, "RECOGNIZED_CONTRIBUTORS", ())
    monkeypatch.setattr(update_contributors, "_circular_png", lambda raw: raw)
    return avatar_dir


@pytest.mark.parametrize(
    "existing,logins,failed,expected",
    [
        ([], ["NewPerson"], [], ["NewPerson.png"]),
        (["Departed.png", "Departed.svg"], ["NewPerson"], [], ["NewPerson.png"]),
        # The staged file a killed run left behind goes with the departed:
        # the gate below fails on any file the inventories do not list.
        (
            [f"{update_contributors.TMP_PREFIX}killed", "Departed.png"],
            ["NewPerson"],
            [],
            ["NewPerson.png"],
        ),
        (
            ["Existing.png"],
            ["NewPerson", "Existing"],
            ["Existing"],
            ["Existing.png", "NewPerson.png"],
        ),
        (
            ["existing.png"],
            ["NewPerson", "Existing"],
            ["Existing"],
            ["NewPerson.png", "existing.png"],
        ),
        # A successful refresh keeps the committed spelling. On a
        # case-insensitive filesystem this case passes either way -- only
        # `test_the_refresh_writes_the_committed_spelling_of_an_avatar` pins
        # the choice that a case-sensitive filesystem would act on.
        (["existing.png"], ["Existing"], [], ["existing.png"]),
        ([], ["Unavailable"], ["Unavailable"], []),
        (
            ["Existing.png"],
            ["Existing", "Unavailable"],
            ["Unavailable"],
            ["Existing.png"],
        ),
    ],
)
def test_refresh_keeps_bilingual_inventory_and_gate_in_sync(
    contributor_checkout, monkeypatch, existing, logins, failed, expected
):
    avatar_dir = contributor_checkout
    root = avatar_dir.parents[1]
    for name in existing:
        (avatar_dir / name).write_bytes(b"previous-avatar")
    _stage(root)
    monkeypatch.setattr(
        update_contributors,
        "fetch_contributors",
        lambda _token: [{"login": login} for login in logins],
    )

    def download(url, _token):
        if any(f"/{login}.png?" in url for login in failed):
            raise OSError("temporary avatar failure")
        return b"synthetic-avatar"

    monkeypatch.setattr(update_contributors, "_get", download)
    assert update_contributors.main() == 0
    assert sorted(path.name for path in avatar_dir.glob("*.png")) == expected
    _stage(root)
    assert check_directory_readmes.main() == 0
    refreshed = {f"{login}.png".casefold() for login in logins if login not in failed}
    for name in expected:
        assert (avatar_dir / name).read_bytes() == (
            b"synthetic-avatar" if name.casefold() in refreshed else b"previous-avatar"
        )
    inventories = []
    for name in ("README.md", "README_zh.md"):
        text = (avatar_dir / name).read_text(encoding="utf-8")
        assert re.findall(r"^\| `([^`]+\.png)` \|", text, re.MULTILINE) == expected
        assert "Keep this introduction.\n\n<!-- AVATAR-FILES:START -->" in text
        assert "<!-- AVATAR-FILES:END -->\n\nKeep this footer.\n" in text
        inventories.append(text)
    assert "| File | Purpose |" in inventories[0]
    assert "| 文件 | 职责 |" in inventories[1]

    assert update_contributors.main() == 0
    assert inventories == [
        (avatar_dir / name).read_text(encoding="utf-8")
        for name in ("README.md", "README_zh.md")
    ]


@pytest.mark.parametrize(
    "invalid_block",
    [
        None,  # the document is missing altogether
        "no markers",
        "<!-- AVATAR-FILES:START -->",
        "<!-- AVATAR-FILES:END --><!-- AVATAR-FILES:START -->",
        "<!-- AVATAR-FILES:START -->"
        "<!-- AVATAR-FILES:START --><!-- AVATAR-FILES:END -->",
    ],
)
@pytest.mark.parametrize("filename", ["README.md", "README_zh.md"])
def test_an_unusable_inventory_stops_the_refresh_before_any_write(
    contributor_checkout, monkeypatch, capsys, invalid_block, filename
):
    """No avatar, wall or inventory may change when one inventory is unusable.

    Checking the markers only after the PNGs and the wall were rewritten left
    exactly the half-updated tree the directory gate rejects.
    """
    avatar_dir = contributor_checkout
    root = avatar_dir.parents[1]
    (avatar_dir / "Departed.png").write_bytes(b"previous-avatar")
    document = avatar_dir / filename
    if invalid_block is None:
        document.unlink()
    else:
        document.write_text(invalid_block, encoding="utf-8")

    def unexpected_fetch(*_args):
        raise AssertionError("invalid inventories must fail before auth or network")

    monkeypatch.setattr(update_contributors, "_token", unexpected_fetch)
    monkeypatch.setattr(update_contributors, "fetch_contributors", unexpected_fetch)

    def snapshot():
        return {
            path.relative_to(root): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file() and ".git" not in path.relative_to(root).parts
        }

    before = snapshot()
    assert update_contributors.main() == 1
    assert snapshot() == before
    assert filename in capsys.readouterr().err


@pytest.mark.parametrize("failure", [OSError, KeyboardInterrupt])
def test_main_stages_all_four_readmes_before_replacing_any(
    contributor_checkout, monkeypatch, failure
):
    avatar_dir = contributor_checkout
    root = avatar_dir.parents[1]
    (avatar_dir / "Existing.png").write_bytes(b"previous-avatar")
    monkeypatch.setattr(
        update_contributors,
        "fetch_contributors",
        lambda _token: [{"login": "Existing"}],
    )
    monkeypatch.setattr(
        update_contributors, "_get", lambda _url, _token: b"previous-avatar"
    )
    originals = {
        path: path.read_bytes()
        for directory in (root, avatar_dir)
        for path in directory.glob("README*.md")
    }
    original_paths = set(root.rglob("*"))
    fdopen = update_contributors.os.fdopen
    writes = 0

    @contextmanager
    def fail_last_staged_write(fd, *args, **kwargs):
        nonlocal writes
        with fdopen(fd, *args, **kwargs) as stream:
            writes += 1
            if writes == 4:
                stream.write("partial staging output")
                raise failure("interrupted while staging the last document")
            yield stream

    monkeypatch.setattr(update_contributors.os, "fdopen", fail_last_staged_write)

    with pytest.raises(failure):
        update_contributors.main()

    assert writes == 4
    assert {path: path.read_bytes() for path in originals} == originals
    assert set(root.rglob("*")) == original_paths


def test_a_wall_with_a_second_marker_pair_stops_the_refresh_too(
    contributor_checkout, monkeypatch, capsys
):
    """One skipped wall beside one rewritten wall is two answers, not one.

    Splicing "the" marker pair needs there to be exactly one, and a root
    README that carries two used to be skipped while its sibling updated --
    with the run still reporting success.
    """
    avatar_dir = contributor_checkout
    root = avatar_dir.parents[1]
    (root / "README.md").write_text(
        "# Wall\n\n<!-- CONTRIBUTORS:START -->\n<!-- CONTRIBUTORS:END -->\n\n"
        "<!-- CONTRIBUTORS:START -->\n<!-- CONTRIBUTORS:END -->\n",
        encoding="utf-8",
    )

    def unexpected_fetch(*_args):
        raise AssertionError("an unusable wall must fail before auth or network")

    monkeypatch.setattr(update_contributors, "_token", unexpected_fetch)
    monkeypatch.setattr(update_contributors, "fetch_contributors", unexpected_fetch)
    before = {
        path: path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(root).parts
    }

    assert update_contributors.main() == 1

    assert {path: path.read_bytes() for path in before} == before
    assert "README.md" in capsys.readouterr().err


def test_a_leftover_staged_file_goes_even_when_no_document_changes(
    contributor_checkout, monkeypatch
):
    """The refresh that finds nothing to write still has to clear the tree.

    The writer only sweeps a directory it is about to write, and a second run
    over an already-current inventory writes nothing at all -- so the prune is
    what keeps a killed run from leaving the directory gate red for good.
    """
    avatar_dir = contributor_checkout
    root = avatar_dir.parents[1]
    monkeypatch.setattr(
        update_contributors,
        "fetch_contributors",
        lambda _token: [{"login": "NewPerson"}],
    )
    monkeypatch.setattr(
        update_contributors, "_get", lambda _url, _token: b"synthetic-avatar"
    )
    assert update_contributors.main() == 0
    leftover = avatar_dir / f"{update_contributors.TMP_PREFIX}killed"
    leftover.write_text("half a document\n", encoding="utf-8")

    assert update_contributors.main() == 0

    assert not leftover.exists()
    _stage(root)
    assert check_directory_readmes.main() == 0
