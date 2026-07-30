"""What the boundary hands back is as load-bearing as what it denies.

Three findings, one theme — a check that answered an easier question than the
one it was standing in for:

  * ``runtime_read_paths`` allowed the helper package's *parent*, which in a
    source or editable install is the repository root. The home denial was then
    reopened over everything beneath it, so a provider shim — which by design
    also has the network — could read an untracked ``.env``, ``.git``, and any
    unrelated source or data sitting there;
  * ``available()`` asked whether the backend binary was installed. On a Linux
    host with unprivileged user namespaces or mounts disabled, ``bwrap`` is
    installed and confines nothing, so the enforce gate proceeded and the real
    invocation died before the helper started — reported as an indeterminate
    remote operation rather than a host that cannot sandbox;
  * ``doctor`` had its own opinion and contradicted the runtime's, reporting
    unconditionally that no OS boundary existed and advising the user to weaken
    ``enforce`` to ``auto``;
  * and the profile itself, in the same shape. It denied ``file-read-data``
    under ``$HOME``, which is not all of a file's contents on macOS: extended
    attributes are a separate Seatbelt class, ``com.apple.ResourceFork`` holds
    the resource fork, and ``com.apple.metadata:kMDItemWhereFroms`` holds the
    whole document base64-encoded for anything saved from a ``data:`` URL. The
    same bytes were refused through ``open()`` and served through
    ``getxattr()``. The helper's own in-sandbox probe could not have caught it:
    ``listdir`` is a data read, so the invariant it verifies from inside held
    the entire time.

The macOS tests below establish a real boundary with ``sandbox-exec`` and try
to read real files through it. A profile-string assertion would have passed
against the broken code — the string was right, and it allowed the repository.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import json
import subprocess
import sys
import tempfile
import types
from pathlib import Path

import pytest

from openai4s.security import byoc_confinement as bc

_REPO = Path(bc.__file__).resolve().parent.parent.parent

macos_only = pytest.mark.skipif(
    sys.platform != "darwin", reason="Seatbelt is the macOS backend"
)

_READ_PROBE = r"""
import json, os, sys
out = {}


def t(key, fn):
    try:
        out[key] = fn() or "OK"
    except PermissionError:
        out[key] = "DENIED"
    except FileNotFoundError:
        out[key] = "ABSENT"
    except Exception as exc:
        out[key] = "ERR:" + type(exc).__name__


repo = sys.argv[1]
t("repo_list", lambda: os.listdir(repo) and "READABLE")
t("repo_file", lambda: open(os.path.join(repo, "pyproject.toml"), "rb").read() and "READABLE")
# argv[3] rather than repo/.git: in a linked worktree `.git` is a regular file
# pointing elsewhere, and listing it raises NotADirectoryError whether or not
# the boundary holds — a verdict about the path's type, not about the sandbox.
# The host resolves the pointer and names the directory that must stay unread.
t("git_dir", lambda: os.listdir(sys.argv[3]) and "READABLE")
t("helper_pkg", lambda: os.listdir(sys.argv[2]) and "READABLE")
print(json.dumps(out))
"""


def _git_metadata_dir(repo: Path) -> Path:
    """The directory this checkout's git metadata actually lives in.

    In a clone that is ``<repo>/.git``. In a linked worktree — ``git worktree
    add``, which is how an agent session checks this tree out — ``.git`` is a
    regular file holding ``gitdir: <path>`` and the metadata sits under the main
    repository's ``.git/worktrees/<name>``. Resolved here, outside the boundary,
    because a probe handed the pointer file asks a question about file types and
    gets an answer that looks like a denial without being one.
    """
    dot_git = repo / ".git"
    if dot_git.is_file():
        pointer = dot_git.read_text("utf-8").strip()
        if pointer.startswith("gitdir:"):
            target = Path(pointer.split(":", 1)[1].strip())
            return target if target.is_absolute() else (repo / target).resolve()
    return dot_git


#: Reports the raw errno for each read class, so the data/metadata split the
#: profile deliberately makes is measured rather than described.
_CLASS_PROBE = r"""
import ctypes, ctypes.util, errno, json, os, sys

# macOS CPython exposes no os.getxattr, which is precisely why this channel
# went unnoticed: a stdlib-only probe cannot see it.
_libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)


def _xattr_value(path, name):
    buf = ctypes.create_string_buffer(65536)
    size = _libc.getxattr(
        path.encode(), name.encode(), buf, ctypes.c_size_t(len(buf)),
        ctypes.c_uint32(0), ctypes.c_int(0),
    )
    if size < 0:
        raise OSError(ctypes.get_errno(), "getxattr")
    return buf.raw[:size]


def _xattr_names(path):
    buf = ctypes.create_string_buffer(65536)
    size = _libc.listxattr(
        path.encode(), buf, ctypes.c_size_t(len(buf)), ctypes.c_int(0)
    )
    if size < 0:
        raise OSError(ctypes.get_errno(), "listxattr")
    return buf.raw[:size]


def outcome(fn):
    try:
        fn()
        return "ALLOWED"
    except OSError as exc:
        return errno.errorcode.get(exc.errno, str(exc.errno))


target_file, target_dir, xattr_name = sys.argv[1], sys.argv[2], sys.argv[3]
print(json.dumps({
    "stat_file": outcome(lambda: os.stat(target_file)),
    "stat_dir": outcome(lambda: os.stat(target_dir)),
    "read_file": outcome(lambda: open(target_file, "rb").read(1)),
    "list_dir": outcome(lambda: os.listdir(target_dir)),
    "list_file": outcome(lambda: os.listdir(target_file)),
    "xattr_value": outcome(lambda: _xattr_value(target_file, xattr_name)),
    "xattr_names": outcome(lambda: _xattr_names(target_file)),
}))
"""

#: An xattr macOS itself uses to carry a file's own bytes: anything saved from
#: a `data:` URL gets the whole document base64-encoded in here.
_WHERE_FROMS = "com.apple.metadata:kMDItemWhereFroms"


def _set_xattr(path: Path, name: str, value: bytes) -> None:
    libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    if (
        libc.setxattr(
            str(path).encode(),
            name.encode(),
            value,
            ctypes.c_size_t(len(value)),
            ctypes.c_uint32(0),
            ctypes.c_int(0),
        )
        < 0
    ):
        raise OSError(ctypes.get_errno(), f"setxattr {name}")


def _probe(argv: list[str]) -> dict:
    """Run a probe -- confined or not -- and return the JSON report it printed.

    One place that builds the argv and parses the answer, so a second copy
    cannot drift away from the first and start asking a different question.
    """
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _read_probe_argv(
    repo: Path, git_dir: Path, profile: str | None = None
) -> list[str]:
    """`_READ_PROBE`'s argv, optionally wrapped in a Seatbelt profile.

    Unconfined it is the control: the same probe against the same paths with
    nothing denying it must report READABLE, or a DENIED from the confined run
    would be proving something about the probe rather than about the boundary.
    """
    inner = [
        sys.executable,
        "-I",
        "-c",
        _READ_PROBE,
        str(repo),
        bc.helper_package_dir(),
        str(git_dir),
    ]
    if profile is None:
        return inner
    return ["sandbox-exec", "-p", profile, *inner]


def _write_git(repo: Path, shape: str) -> Path:
    """Build one of the two real shapes of `.git`, and return the metadata dir.

    A linked worktree's `.git` is a regular file holding `gitdir: <path>`; every
    ordinary checkout's is a directory. Both are produced by git itself, so a
    boundary that only refuses one of them refuses nothing in half the installs.
    The pointer target is a real directory *inside the same tree*, because that
    is what git does and what `_git_metadata_dir` has to resolve to something
    the boundary then covers.
    """
    git = repo / ".git"
    if shape == "directory":
        git.mkdir()
        (git / "config").write_text("[core]\n\tbare = false\n", encoding="utf-8")
        return git
    if shape == "file":
        real = repo.parent / "main-repo" / ".git" / "worktrees" / repo.name
        real.mkdir(parents=True)
        (real / "gitdir").write_text(f"{repo}/.git\n", encoding="utf-8")
        git.write_text(f"gitdir: {real}\n", encoding="utf-8")
        return real
    raise ValueError(f"unknown .git shape: {shape!r}")  # pragma: no cover


@pytest.fixture(autouse=True)
def _fresh_self_test():
    bc.reset_self_test_cache()
    yield
    bc.reset_self_test_cache()


# --------------------------------------------------------------------------
# the repository is not the helper's to read
# --------------------------------------------------------------------------


def test_the_allowance_names_the_package_not_the_tree_it_sits_in():
    allowed = bc.runtime_read_paths()
    assert str(_REPO) not in allowed, (
        "the repository root was allow-listed back over the home denial, so "
        "an untracked .env and .git were readable from inside the boundary"
    )
    assert bc.helper_package_dir() in allowed
    assert bc.helper_package_dir().endswith("openai4s_compute_provider")


@macos_only
def test_a_confined_process_cannot_read_the_repository(tmp_path):
    """The leak, tried for real, in the install this test is running from."""
    if not str(_REPO).startswith(str(Path.home())):
        pytest.skip("this install is not under the user's home")
    git_dir = _git_metadata_dir(_REPO)
    if not str(git_dir).startswith(str(Path.home())):
        # A worktree whose main repository lives outside the home directory: the
        # denial under test does not cover that path, so a read there would be
        # expected rather than a leak.
        pytest.skip("this checkout's git metadata is not under the user's home")
    stage = tmp_path / "stage"
    stage.mkdir()
    result = _probe(_read_probe_argv(_REPO, git_dir, bc.build_profile(stage)))

    assert result["repo_list"] == "DENIED", result
    assert result["repo_file"] == "DENIED", result
    assert result["git_dir"] in ("DENIED", "ABSENT"), result
    # ...and the one thing it does need is still there.
    assert result["helper_pkg"] == "READABLE", result


@macos_only
@pytest.mark.parametrize("git_shape", ("directory", "file"))
def test_neither_shape_of_git_metadata_survives_the_boundary(tmp_path, git_shape):
    """Both shapes, on every machine, rather than whichever one is on this one.

    The test above can only exercise the layout the developer running it happens
    to have — a clone or a linked worktree, never both — so for most of this
    suite's life one of the two was never checked at all, and the worktree shape
    is the one that turned out to be broken.

    `build_profile(home=...)` is what makes this hermetic: the denial covers a
    synthetic tree under tmp_path instead of the real `$HOME`, so the
    interpreter and the helper package stay readable and the only variable is
    the shape of `.git`.
    """
    home = tmp_path / "home"
    repo = home / "repo"
    repo.mkdir(parents=True)
    (repo / "pyproject.toml").write_text(
        "[project]\nname = 'decoy'\n", encoding="utf-8"
    )
    git_dir = _write_git(repo, git_shape)
    assert _git_metadata_dir(repo) == git_dir, "the host-side resolver disagrees"
    stage = tmp_path / "stage"
    stage.mkdir()

    result = _probe(_read_probe_argv(repo, git_dir, bc.build_profile(stage, home=home)))

    assert result["repo_list"] == "DENIED", result
    assert result["repo_file"] == "DENIED", result
    # Exactly DENIED, not "DENIED or ABSENT": this metadata directory was
    # created two lines up, so ABSENT here would mean the probe looked
    # somewhere else.
    assert result["git_dir"] == "DENIED", result
    assert result["helper_pkg"] == "READABLE", result


@pytest.mark.parametrize("git_shape", ("directory", "file"))
def test_the_probe_reads_both_shapes_of_git_when_nothing_denies_it(tmp_path, git_shape):
    """The unconfined control, and the check that would have caught the bug.

    A probe that errors before reading anything reports the same thing whether
    the boundary holds or leaks — which is exactly how listing a worktree's
    file-shaped `.git` used to behave, and nothing asserted that the probe had
    to actually succeed at something first. Runs on every platform, because it
    needs no sandbox: with nothing denying it, both shapes must come back
    READABLE.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(
        "[project]\nname = 'decoy'\n", encoding="utf-8"
    )
    git_dir = _write_git(repo, git_shape)

    result = _probe(_read_probe_argv(repo, git_dir))

    assert result["git_dir"] == "READABLE", result
    assert result["repo_file"] == "READABLE", result


@macos_only
def test_metadata_is_readable_under_home_on_purpose_and_contents_are_not(tmp_path):
    """Every read class under the denied home, pinned as a fact rather than a comment.

    This test used to assert `stat_file == "ALLOWED"`, and that was not an
    oversight being corrected here — it deliberately pinned the trade-off the
    profile had made. `build_profile` denied `file-read-data`, because denying
    the whole read class also denies the metadata reads `execvp` and dyld perform
    on the interpreter, and `sandbox-exec` then dies before the helper starts.
    `stat()` therefore answered under a denied home, by design.

    The symptom was real and the conclusion was not: the loader needs metadata on
    the *specific components it walks*, and `traversal_metadata_paths` enumerates
    them, so the denial is now `file-read*` and `stat()` is refused with
    everything else. The home here is synthetic and holds no interpreter, so no
    component of it is on any traversal route and the denial applies whole —
    which is exactly the shape a credential path has.

    Extended attributes were the class that was missed once already: they carry
    contents, not metadata, whatever their name suggests. `getxattr` stays
    refused, now by the whole-class denial rather than by a dedicated
    `file-read-xattr` line, which measured as a no-op beside it and was dropped.
    Attribute *names* are the residual and are still tolerated either way — see
    the note in `build_profile` for why closing them is not available without
    breaking the loader.

    All four classes are pinned together so they cannot drift apart silently.
    """
    home = tmp_path / "home"
    home.mkdir()
    secret = home / "credentials.txt"
    body = b"sk-not-a-real-key-" + b"x" * 512 + b"\n"
    secret.write_bytes(body)
    # The same bytes on the side channel, the way macOS itself stores them.
    _set_xattr(secret, _WHERE_FROMS, body)
    listing = home / "subdir"
    listing.mkdir()
    (listing / "entry-name-worth-hiding").write_text("x", encoding="utf-8")
    stage = tmp_path / "stage"
    stage.mkdir()

    result = _probe(
        [
            "sandbox-exec",
            "-p",
            bc.build_profile(stage, home=home),
            sys.executable,
            "-I",
            "-c",
            _CLASS_PROBE,
            str(secret),
            str(listing),
            _WHERE_FROMS,
        ]
    )

    # Metadata: refused, and this is the assertion that changed. Under
    # `file-read-data` a shim could walk a list of guesses and collect the size,
    # mtime, mode and owner of `~/.ssh/id_ed25519` over the network it is allowed
    # by design. If this starts failing, the denial has been narrowed back — read
    # the comment in `build_profile` before "fixing" it.
    assert result["stat_file"] == "EPERM", result
    assert result["stat_dir"] == "EPERM", result
    # Contents and directory entries: refused. These are the boundary, and
    # `list_dir` is the one that keeps file *names* from being enumerated,
    # which matters far more than sizes for a helper that also has the network.
    assert result["read_file"] == "EPERM", result
    assert result["list_dir"] == "EPERM", result
    # An extended attribute is a second place a file's own bytes live. Denying
    # `file-read-data` alone left `getxattr` open, so the same content was
    # refused through open() and served through here.
    assert result["xattr_value"] == "EPERM", result
    # Attribute *names* are still readable, deliberately: closing them needs
    # `file-read-metadata`, which is the denial that breaks execvp and dyld.
    # Names are not contents. Tolerant of either answer so a future tightening
    # is not a failure.
    assert result["xattr_names"] in ("ALLOWED", "EPERM"), result
    # Listing a non-directory: never allowed, and never informative. Measured
    # identical under `deny file-read*`, where `stat()` is already EPERM, so the
    # errno says nothing about the policy — which is why a probe must never be
    # allowed to stop there.
    assert result["list_file"] in ("ENOTDIR", "EPERM"), result


@macos_only
def test_the_helper_still_loads_its_own_package_under_the_boundary(tmp_path):
    """The allowance was there for a reason; the reason has to still work.

    Invoked with no arguments the entrypoint imports its package and *then*
    fails to unpack argv — so a ValueError proves the import got through, and
    an ImportError would prove the tightening broke the helper.
    """
    stage = tmp_path / "stage"
    stage.mkdir()
    helper_main = str(Path(bc.helper_package_dir()) / "__main__.py")
    argv = bc.wrap([sys.executable, "-I", helper_main], stage)
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=120)

    assert "ModuleNotFoundError" not in proc.stderr, proc.stderr
    assert "ImportError" not in proc.stderr, proc.stderr
    assert (
        "ValueError" in proc.stderr
    ), f"the entrypoint did not get past its own import: {proc.stderr}"


# --------------------------------------------------------------------------
# the home denial covers metadata, and the loader still gets through
# --------------------------------------------------------------------------

_METADATA_PROBE = r"""
import ctypes, ctypes.util, errno, json, os

out = {}
_libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)


def t(key, fn):
    try:
        out[key] = fn() or "OK"
    except OSError as exc:
        out[key] = errno.errorcode.get(exc.errno, str(exc.errno))


def listxattr(path):
    # os.listxattr is Linux-only; this test's whole subject is macOS.
    n = _libc.listxattr(path.encode(), None, ctypes.c_size_t(0), 0)
    if n < 0:
        raise OSError(ctypes.get_errno(), "listxattr")
    return "ALLOWED"


home = os.path.expanduser("~")
probe = os.path.join(home, ".openai4s-confinement-probe")
t("list_home", lambda: os.listdir(home) and "ALLOWED")
t("stat_file", lambda: os.stat(probe) and "ALLOWED")
t("read_file", lambda: open(probe, "rb").read() and "ALLOWED")
t("xattr_file", lambda: listxattr(probe))
t("stat_absent", lambda: os.stat(probe + "-not-here") and "ALLOWED")
t("import_ssl", lambda: __import__("ssl") and "OK")
print("PROBE" + json.dumps(out))
"""


@macos_only
def test_metadata_under_home_is_denied_and_only_existence_survives(tmp_path):
    """The home denial is `file-read*`, and this pins what that does and does not buy.

    It replaces a test that asserted ``stat_file == "ALLOWED"``. That was not an
    oversight being corrected — it deliberately pinned the trade-off the profile
    had made: the denial was `file-read-data`, because denying the whole read
    class also denies the metadata reads `execvp` and dyld perform on the
    interpreter, and `sandbox-exec` then dies before the helper starts. The
    symptom was real; the conclusion that `file-read*` was therefore unusable was
    not. Naming the loader's own path components as `file-read-metadata`
    literals buys the whole class, so `stat()` on a guessed path under $HOME no
    longer hands a networked provider shim the size, mtime, mode and owner of
    `~/.ssh/id_ed25519`.

    Three things are asserted rather than one, because the interesting part of
    this boundary is where it stops:

      * contents and metadata are both denied — the change itself;
      * *existence* is not, and cannot be: Seatbelt answers a denied path EPERM
        and a missing one ENOENT. A test that skipped this would let the docstring
        claim be read as total;
      * TLS still works, since the helper's entire job is calling a provider API.
    """
    probe_file = Path.home() / ".openai4s-confinement-probe"
    if probe_file.exists():
        pytest.skip("a real file is already at the probe path")
    probe_file.write_text("not a credential, but shaped like one\n", encoding="utf-8")
    try:
        stage = tmp_path / "stage"
        stage.mkdir()
        argv = [
            "sandbox-exec",
            "-p",
            bc.build_profile(stage),
            sys.executable,
            "-I",
            "-c",
            _METADATA_PROBE,
        ]
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=120)
    finally:
        probe_file.unlink()

    assert proc.returncode == 0, proc.stderr
    result = json.loads(
        [ln for ln in proc.stdout.splitlines() if ln.startswith("PROBE")][0][5:]
    )

    assert result["list_home"] == "EPERM", result
    assert result["read_file"] == "EPERM", result
    assert result["stat_file"] == "EPERM", (
        "a stat() on a guessed path under $HOME still answered, so a provider "
        "shim with the network could fingerprint the user's home",
        result,
    )
    assert result["xattr_file"] == "EPERM", result
    # Not a wish — a limit. ENOENT and EPERM are distinguishable, so existence
    # leaks whatever the profile says. Pinned so the claim stays honest.
    assert result["stat_absent"] == "ENOENT", result
    assert result["import_ssl"] == "OK", result


def test_the_walk_names_the_symlink_hops_a_resolved_prefix_cannot(tmp_path):
    """The load-bearing entries are the *unresolved* hops, which is the whole bug.

    `runtime_read_paths` reports `sys.base_prefix` and `_canonical` realpaths
    everything, so a uv-managed venv's alias directory — the one the loader
    actually traverses — appears nowhere in the profile. Allowing `file-read*`
    on the resolved prefix is not enough; verified by execution before this was
    written, and reproduced in miniature here.
    """
    home = tmp_path / "home"
    real = home / "pythons" / "cpython-3.13.13" / "bin"
    real.mkdir(parents=True)
    (real / "python3.13").write_text("#!/bin/sh\n", encoding="utf-8")
    alias = home / "pythons" / "cpython-3.13"
    alias.symlink_to(real.parent)  # the version alias, as uv lays it out
    venv_bin = home / "project" / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").symlink_to(alias / "bin" / "python3.13")

    found = bc.traversal_metadata_paths(
        (str(real.parent),), home=home, executable=str(venv_bin / "python")
    )

    assert str(alias) in found, (
        "the alias directory the loader walks is missing, so execvp's metadata "
        "read on it is denied and the helper never starts"
    )
    assert str(alias / "bin" / "python3.13") in found, found
    assert str(home / "project") in found, "an ancestor of the venv is missing"
    assert str(home) in found
    # A component outside the route is not named, or the deny means nothing.
    assert str(home / "Documents") not in found


def test_the_walk_covers_every_read_root_not_just_the_interpreter(tmp_path):
    """A system interpreter lives outside $HOME; the helper's package does not.

    Seeding the walk from `sys.executable` alone would leave the components
    leading to the helper's own package unnamed on exactly that layout, and the
    failure is the helper not starting on a user's machine.
    """
    home = tmp_path / "home"
    pkg = home / "src" / "checkout" / "openai4s_compute_provider"
    pkg.mkdir(parents=True)

    found = bc.traversal_metadata_paths(
        (str(pkg),), home=home, executable="/usr/bin/python3"
    )

    assert str(home / "src") in found, found
    assert str(home / "src" / "checkout") in found, found


def test_the_walk_terminates_on_a_symlink_loop(tmp_path):
    """An incomplete enumeration fails a user's helper; a hanging one fails the
    daemon building the profile. Bounded, and asserted rather than assumed."""
    home = tmp_path / "home"
    home.mkdir()
    (home / "a").symlink_to(home / "b")
    (home / "b").symlink_to(home / "a")

    found = bc.traversal_metadata_paths(
        (str(home),), home=home, executable=str(home / "a" / "bin" / "python")
    )
    assert str(home) in found


@macos_only
def test_the_profile_denies_the_read_class_over_the_home(tmp_path):
    """The string, as a guard on the one line whose subtype is the whole point."""
    stage = tmp_path / "stage"
    stage.mkdir()
    profile = bc.build_profile(stage, home="/Users/someone")

    assert '(deny file-read* (subpath "/Users/someone"))' in profile
    assert "(deny file-read-data" not in profile, (
        "the home denial narrowed back to contents-only, so stat() on a guessed "
        "path under $HOME answers again"
    )


def test_the_metadata_allowance_is_literal_not_subpath(tmp_path):
    """`subpath` on $HOME's components would hand back everything beneath them,
    which is the disclosure the deny exists to end."""
    home = tmp_path / "home"
    (home / "project" / ".venv" / "bin").mkdir(parents=True)
    stage = tmp_path / "stage"
    stage.mkdir()

    profile = bc.build_profile(
        stage, home=home, read_paths=(str(home / "project" / ".venv"),)
    )
    block = profile.split("(allow file-read-metadata", 1)[1].split("(allow file-read*")[
        0
    ]
    assert "(literal " in block
    assert "(subpath " not in block, block


def test_the_self_test_probe_execs_the_interpreter_not_a_shell(monkeypatch):
    """`/bin/sh` lives outside $HOME and starts under any profile this module can
    emit, so a shell probe passed on hosts where the interpreter could not start —
    which is the single failure mode of the metadata enumeration. The self-test
    has to exercise what the helper is actually launched with."""
    monkeypatch.setattr(bc.sys, "platform", "darwin")
    argv = bc._probe_argv("/usr/bin/sandbox-exec", "/tmp/stage", str(Path.home()))
    assert sys.executable in argv, argv
    assert "/bin/sh" not in argv, argv


def test_a_profile_that_blocks_the_interpreter_is_named_as_such(monkeypatch):
    """Not "no boundary" — that sends someone to look at sandbox-exec when the
    fault is a path this host's layout needed and the walk did not produce."""

    def blocked(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv,
            71,
            b"",
            b"sandbox-exec: execvp() of '/x/python' failed: Operation not permitted",
        )

    monkeypatch.setattr(bc.sys, "platform", "darwin")
    monkeypatch.setattr(bc.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(bc.subprocess, "run", blocked)

    ok, reason = bc.available()
    assert ok is False
    assert "denies the interpreter itself" in reason, reason
    assert "every path component the loader walks" in reason, reason


def test_the_entrypoint_does_not_put_its_parent_on_the_path():
    source = (Path(bc.helper_package_dir()) / "__main__.py").read_text("utf-8")
    assert "sys.path.insert(0, os.path.dirname(_here))" not in source, (
        "putting the parent on sys.path is what forced the repository root to "
        "be readable: a package import lists the directory it searches"
    )


def test_the_helper_package_still_imports_the_new_way():
    """Unconfined, so a failure here is about the loader and nothing else."""
    helper_main = str(Path(bc.helper_package_dir()) / "__main__.py")
    proc = subprocess.run(
        [sys.executable, "-I", helper_main], capture_output=True, text=True, timeout=60
    )
    assert "ModuleNotFoundError" not in proc.stderr, proc.stderr
    assert "ValueError" in proc.stderr, proc.stderr


# --------------------------------------------------------------------------
# installed is not working
# --------------------------------------------------------------------------


def test_available_runs_a_boundary_self_test_rather_than_a_which(monkeypatch):
    """The bwrap reproduction: the binary is there and confines nothing."""
    calls: list[list[str]] = []

    def failing_probe(argv, **kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(
            argv, 1, b"", b"bwrap: setting up uid map: Permission denied"
        )

    monkeypatch.setattr(bc.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(bc.subprocess, "run", failing_probe)

    ok, reason = bc.available()

    assert calls, "available() did not establish a boundary before answering"
    assert ok is False
    assert "did not establish a filesystem boundary" in reason
    assert "Permission denied" in reason


def test_a_self_test_verdict_is_cached_but_not_across_a_change(monkeypatch):
    runs: list[int] = []

    def probe(argv, **kwargs):
        runs.append(1)
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    # `st_dev` as well as `st_mtime_ns`: the cache key reads the mtime, but on
    # Linux the probe itself reads the home directory's device id to compare
    # against the one inside the sandbox. A stub carrying only the mtime made
    # `_probe_argv` raise `AttributeError`, `self_test` turn that into a failed
    # verdict, and this test fail for a reason that has nothing to do with
    # caching — on Linux only, which is why it was invisible on a Mac.
    def fake_stat(mtime):
        return lambda *_a, **_k: types.SimpleNamespace(st_mtime_ns=mtime, st_dev=4242)

    monkeypatch.setattr(bc.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(bc.subprocess, "run", probe)
    monkeypatch.setattr(bc.os, "stat", fake_stat(1))

    assert bc.available()[0] is True
    assert bc.available()[0] is True
    assert len(runs) == 1, "the self-test is meant to be cached"

    # The backend binary changed underneath us: the verdict must not survive it.
    monkeypatch.setattr(bc.os, "stat", fake_stat(2))
    assert bc.available()[0] is True
    assert len(runs) == 2, "a changed backend must invalidate the cached verdict"


def test_a_self_test_that_hangs_is_a_failure_not_a_wait(monkeypatch):
    def hangs(argv, **kwargs):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=bc.SELF_TEST_TIMEOUT_S)

    monkeypatch.setattr(bc.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(bc.subprocess, "run", hangs)

    ok, reason = bc.available()
    assert ok is False
    assert "self-test" in reason


@macos_only
def test_the_real_backend_passes_its_own_self_test():
    """The control for all of the above, on a host that can actually confine."""
    ok, reason = bc.self_test(force=True)
    assert ok is True, reason
    assert "Seatbelt" in reason


# --------------------------------------------------------------------------
# doctor and the runtime answer from the same place
# --------------------------------------------------------------------------


def test_doctor_reports_the_boundary_that_is_actually_implemented(
    tmp_path, monkeypatch
):
    from openai4s import doctor as doctor_mod

    skills = tmp_path / "skills"
    (skills / "remote-compute-fake").mkdir(parents=True)
    (skills / "remote-compute-fake" / "provider.py").write_text("x", encoding="utf-8")
    (skills / "remote-compute-fake" / "provider.json").write_text(
        '{"id": "fake"}', encoding="utf-8"
    )
    cfg = types.SimpleNamespace(skills_dir=skills, data_dir=tmp_path)

    monkeypatch.setenv("OPENAI4S_COMPUTE_CONFINEMENT", "enforce")
    monkeypatch.setattr(bc, "available", lambda: (True, "macOS Seatbelt"))

    check = doctor_mod._remote(cfg)

    assert check.status != doctor_mod.FAIL, (
        "doctor claimed no OS boundary exists on a host where one is applied, "
        "and told the user to weaken enforce to auto"
    )
    assert check.facts["confinement_state"] == "active"


def test_doctor_still_fails_closed_when_enforce_cannot_be_satisfied(
    tmp_path, monkeypatch
):
    from openai4s import doctor as doctor_mod

    skills = tmp_path / "skills"
    (skills / "remote-compute-fake").mkdir(parents=True)
    (skills / "remote-compute-fake" / "provider.py").write_text("x", encoding="utf-8")
    (skills / "remote-compute-fake" / "provider.json").write_text(
        '{"id": "fake"}', encoding="utf-8"
    )
    cfg = types.SimpleNamespace(skills_dir=skills, data_dir=tmp_path)

    monkeypatch.setenv("OPENAI4S_COMPUTE_CONFINEMENT", "enforce")
    monkeypatch.setattr(bc, "available", lambda: (False, "bwrap is not on PATH"))

    check = doctor_mod._remote(cfg)
    assert check.status == doctor_mod.FAIL
    assert "bwrap is not on PATH" in check.detail


def test_an_invalid_mode_fails_for_an_ssh_only_host_not_just_byoc(
    tmp_path, monkeypatch
):
    """`ComputeManager.__init__` parses the confinement mode unconditionally, so
    an invalid value makes construction raise even with no BYOC provider — an
    ssh-only host used to slip through to a "fixable" WARN while the manager
    would refuse to start."""
    from openai4s import doctor as doctor_mod
    from openai4s.compute.manager import ComputeManager

    skills = tmp_path / "skills"
    (skills / "remote-compute-ssh").mkdir(parents=True)  # a family, not a provider
    cfg = types.SimpleNamespace(data_dir=tmp_path, skills_dir=skills, db_path=None)

    monkeypatch.setenv("OPENAI4S_COMPUTE_CONFINEMENT", "enfore")  # typo, invalid

    # The runtime this doctor speaks for: construction is a hard failure.
    with pytest.raises(Exception):
        ComputeManager(cfg)

    check = doctor_mod._remote(cfg)
    assert check.status == doctor_mod.FAIL, (
        "doctor said an invalid confinement mode was a fixable warning while the "
        "manager it speaks for refuses to construct"
    )
    assert "ssh" in check.detail.lower()


def test_an_invalid_mode_with_no_remote_at_all_is_only_a_warning(tmp_path, monkeypatch):
    """Nothing remote is configured, so a bad mode cannot stop any real op — it
    is worth flagging but not a hard failure."""
    from openai4s import doctor as doctor_mod

    skills = tmp_path / "skills"
    skills.mkdir()
    cfg = types.SimpleNamespace(data_dir=tmp_path, skills_dir=skills, db_path=None)

    monkeypatch.setenv("OPENAI4S_COMPUTE_CONFINEMENT", "enfore")
    check = doctor_mod._remote(cfg)
    assert check.status == doctor_mod.WARN


def test_the_manager_and_doctor_describe_the_same_posture(tmp_path, monkeypatch):
    from openai4s.compute.manager import ComputeManager

    monkeypatch.setenv("OPENAI4S_COMPUTE_CONFINEMENT", "auto")
    (tmp_path / "skills").mkdir()
    cfg = types.SimpleNamespace(
        data_dir=tmp_path, skills_dir=tmp_path / "skills", db_path=None
    )
    status = ComputeManager(cfg).confinement_status()
    posture = bc.posture("auto")

    assert status["state"] == posture["state"]
    assert status["enforced"] == posture["enforced"]
    assert status["network_isolated"] == posture["network_isolated"]


def test_the_linux_self_test_uses_mount_identity_not_emptiness(monkeypatch):
    """The interpreter's runtime paths are bound back over the home tmpfs, and
    on a user install they live under $HOME — so the tmpfs is legitimately
    non-empty. An emptiness check would report a working boundary as broken;
    the device-id comparison does not."""
    monkeypatch.setattr(bc.sys, "platform", "linux")
    argv = bc._probe_argv("/usr/bin/bwrap", "/tmp/stage", "/home/alice")
    script = " ".join(argv)
    assert (
        "stat -c %d" in script
    ), "the Linux self-test must compare mount identity, not list the home"
    assert "ls -A" not in script, "the emptiness check misfires on a user install"
