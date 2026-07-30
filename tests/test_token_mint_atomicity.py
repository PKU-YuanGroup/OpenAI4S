"""Whether two daemons starting together agree on the access token.

`load_or_mint` read the file, saw nothing, minted a candidate, and finished
with an *unconditional* `os.replace`. Nothing in that sequence can pick a
winner: `os.replace` never fails on a name already taken, so every racer
overwrote the file, and the re-read that was supposed to adopt the winner's
value sat outside any exclusion at all. Twelve processes starting together
produced four to nine different tokens, with only the last write on disk — a
daemon happily authorising cookies against a value `openai4s status`, the CLI,
and the browser-open URL could no longer read.

That is a real first-run shape rather than a thought experiment: the macOS
`.app`, a shell `openai4s serve`, and any supervisor that restarts a crashed
daemon can all reach the mint within the same millisecond, and the token is
what stands between a local account and a daemon that executes code.

The test that decides this is the multiprocess one. Threads would not do: the
whole gap is between a `read` and a `link`, both syscalls, and the GIL hands
the interpreter to another thread for the duration of each — a threaded
version of the same race interleaves *less* like the real one, so passing it
would prove nothing about two processes.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import stat
from pathlib import Path

import pytest

from openai4s.security import permissions
from openai4s.server import local_auth

#: Enough concurrency and enough repeats that the pre-fix code loses every
#: time. Measured against the unpatched module: 6/6 rounds disagreed at four
#: workers, 15/15 at twelve. Eight and three is chosen for margin on a loaded
#: CI box, not because the race is delicate.
_WORKERS = 8
_ROUNDS = 3


def _mint_under_barrier(data_dir: str, barrier, results) -> None:
    """Child entry point. Module level so `spawn` can pickle it by name."""
    from openai4s.server import local_auth as child_auth

    try:
        barrier.wait(timeout=120)
        results.put(child_auth.load_or_mint(data_dir))
    except BaseException as exc:  # noqa: BLE001 - reported, not swallowed
        results.put(f"error: {type(exc).__name__}: {exc}")


def _race_to_mint(data_dir: Path, workers: int) -> list[str]:
    """Start `workers` real processes and release them all at one barrier."""
    # spawn, not fork: fork would inherit this process's imported modules and
    # any pytest state with them, and it is not what a second `openai4s serve`
    # looks like.
    ctx = mp.get_context("spawn")
    barrier = ctx.Barrier(workers)
    results = ctx.Queue()
    processes = [
        ctx.Process(
            target=_mint_under_barrier,
            args=(str(data_dir), barrier, results),
            daemon=True,
        )
        for _ in range(workers)
    ]
    for process in processes:
        process.start()
    try:
        # Drain before joining: a child that has put a value onto the queue is
        # not finished until its feeder thread has flushed it, so joining first
        # can deadlock on a full pipe.
        minted = [results.get(timeout=180) for _ in range(workers)]
    finally:
        for process in processes:
            process.join(timeout=60)
            if process.is_alive():  # pragma: no cover - a hung child
                process.kill()
                process.join(timeout=30)
    return minted


def test_concurrent_daemons_mint_exactly_one_token(tmp_path):
    """The defect, at the only concurrency that can show it.

    Every process must come back with the same string, and that string must be
    the one on disk — a process holding a token the file does not contain is a
    daemon authorising a credential nobody else can present.
    """
    for round_index in range(_ROUNDS):
        data_dir = tmp_path / f"round-{round_index}"
        minted = _race_to_mint(data_dir, _WORKERS)

        failures = [value for value in minted if value.startswith("error: ")]
        assert not failures, failures

        distinct = set(minted)
        assert len(distinct) == 1, (
            f"{len(distinct)} different tokens from {_WORKERS} minters: "
            "every racer overwrote the file and kept its own value"
        )
        on_disk = local_auth.read_token(data_dir)
        assert (
            on_disk == minted[0]
        ), "the surviving daemons hold a token that is not the one on disk"

        path = local_auth.token_path(data_dir)
        assert permissions.is_owner_only(path), oct(path.stat().st_mode)
        if os.name == "posix":
            assert stat.S_IMODE(path.stat().st_mode) == 0o600

        # A publish that leaves its staging file behind leaks the same secret
        # under a second name.
        assert not list(data_dir.glob(".*tmp*"))


def test_a_minter_that_loses_returns_the_token_on_disk(tmp_path, monkeypatch):
    """The loser branch, made deterministic.

    The fast-path read is blinded once, which is exactly the state a racer is
    in: it believes there is no token, mints one, and then finds the name
    taken. It must come back with the winner's value. Returning its own
    candidate is the defect in miniature — the pre-fix code did precisely
    that, and clobbered the winner's file on the way.
    """
    winner = local_auth.load_or_mint(tmp_path)
    real_read = local_auth.read_token
    seen = {"reads": 0}

    def blind_the_first_read(data_dir):
        seen["reads"] += 1
        return None if seen["reads"] == 1 else real_read(data_dir)

    monkeypatch.setattr(local_auth, "read_token", blind_the_first_read)

    assert local_auth.load_or_mint(tmp_path) == winner
    assert real_read(tmp_path) == winner


def test_the_mint_fsyncs_the_file_and_its_directory(tmp_path, monkeypatch):
    """A published name that is not directory-fsynced can vanish on a crash.

    The bytes would survive and the name would not, so the next boot mints a
    second token and every cookie already issued stops working — the failure
    persisting the token was meant to end.
    """
    real_fsync = os.fsync
    synced_kinds: list[int] = []

    def recording_fsync(fd):
        try:
            synced_kinds.append(stat.S_IFMT(os.fstat(fd).st_mode))
        except OSError:  # pragma: no cover - fstat on a live fd
            pass
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", recording_fsync)
    assert local_auth.load_or_mint(tmp_path)

    assert stat.S_IFREG in synced_kinds, "the token's own bytes were not fsynced"
    assert stat.S_IFDIR in synced_kinds, "the directory entry was not fsynced"


def test_an_empty_token_file_is_refused_rather_than_overwritten(tmp_path):
    """The one case the old unconditional overwrite used to paper over.

    Minting can no longer claim a name that already exists, so an empty file is
    reported instead of replaced. That is the honest answer: overwriting it is
    the very operation that loses the race, and a daemon silently minting over
    a file another process may be holding is how the tokens diverged in the
    first place. The file is left alone so the operator decides.
    """
    path = local_auth.token_path(tmp_path)
    path.write_text("", encoding="utf-8")

    with pytest.raises(RuntimeError) as raised:
        local_auth.load_or_mint(tmp_path)

    assert str(path) in str(raised.value)
    assert path.read_text(encoding="utf-8") == ""
    assert not list(tmp_path.glob(".*tmp*"))
