from scripts import update_contributors


def test_public_recognition_is_appended_once_after_commit_contributors():
    commit_people = [
        {"login": "MostCommits", "type": "User", "contributions": 10},
        {"login": "eqstlab", "type": "User", "contributions": 1},
    ]

    merged = update_contributors.include_recognized_contributors(commit_people)

    assert [person["login"] for person in merged] == ["MostCommits", "eqstlab"]

    without_recognized = update_contributors.include_recognized_contributors(
        commit_people[:1]
    )
    assert [person["login"] for person in without_recognized] == [
        "MostCommits",
        *update_contributors.RECOGNIZED_CONTRIBUTORS,
    ]


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

    have_png = update_contributors.write_avatars([{"login": "EQSTLab"}], None)

    assert have_png == {"EQSTLab"}
    assert current.read_bytes() == b"existing-avatar"
    assert not departed.exists()
    assert not legacy_svg.exists()
