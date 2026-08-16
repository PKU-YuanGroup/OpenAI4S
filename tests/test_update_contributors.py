from scripts import update_contributors


def test_public_recognition_is_appended_after_commit_contributors():
    # Spelled out rather than splatting RECOGNIZED_CONTRIBUTORS: an expectation
    # re-derived from the constant under test passes just as happily when that
    # constant is empty, which is the one outcome this has to catch.
    merged = update_contributors.include_recognized_contributors(
        [{"login": "MostCommits", "type": "User", "contributions": 10}]
    )

    assert [person["login"] for person in merged] == ["MostCommits", "EQSTLab"]


def test_a_recognized_login_the_api_already_lists_is_not_duplicated():
    commit_people = [
        {"login": "MostCommits", "type": "User", "contributions": 10},
        {"login": "eqstlab", "type": "User", "contributions": 1},
    ]

    merged = update_contributors.include_recognized_contributors(commit_people)

    assert [person["login"] for person in merged] == ["MostCommits", "eqstlab"]


def test_a_recognized_login_that_is_excluded_is_still_refused(monkeypatch):
    excluded = next(iter(update_contributors.EXCLUDE))
    monkeypatch.setattr(update_contributors, "RECOGNIZED_CONTRIBUTORS", (excluded,))

    merged = update_contributors.include_recognized_contributors(
        [{"login": "MostCommits", "type": "User", "contributions": 10}]
    )

    assert [person["login"] for person in merged] == ["MostCommits"]


def test_empty_api_result_still_fails_before_recognition_is_added(monkeypatch):
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

    have_png, written = update_contributors.write_avatars([{"login": "EQSTLab"}], None)

    assert have_png == {"EQSTLab"}
    assert written == 0  # nothing was refreshed; only the count says so
    assert current.read_bytes() == b"existing-avatar"
    assert not departed.exists()
    assert not legacy_svg.exists()


def test_a_login_whose_casing_drifted_keeps_its_file_and_links_remotely(
    tmp_path, monkeypatch
):
    """The committed file wins over the login's spelling, and render() follows.

    `os.path.isfile` answers case-insensitively on a case-preserving
    filesystem, so keying "do I have a PNG" off it while pruning on an exact
    `os.listdir` match deleted the committed avatar and still emitted a local
    `<img src>` for it -- a dead image on the front page of both READMEs.
    """
    avatar_dir = tmp_path / "contributors"
    avatar_dir.mkdir()
    committed = avatar_dir / "eqstlab.png"
    committed.write_bytes(b"existing-avatar")

    monkeypatch.setattr(update_contributors, "AVATAR_DIR", str(avatar_dir))

    def fail_download(_url, _token):
        raise OSError("temporary avatar failure")

    monkeypatch.setattr(update_contributors, "_get", fail_download)

    people = [{"login": "EQSTLab"}]
    have_png, _written = update_contributors.write_avatars(people, None)

    assert committed.read_bytes() == b"existing-avatar"
    assert have_png == set()
    assert 'src="https://github.com/EQSTLab.png"' in update_contributors.render(
        people, have_png
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
