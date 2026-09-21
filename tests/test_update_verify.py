"""What has to be true about a payload before anything is stopped or replaced.

The interesting cases here are the ones a plausible-looking bad payload
passes four of five checks on. A wheel that carries every required path, names
itself openai4s and unpacks cleanly is still refused when its own
`openai4s/__init__.py` declares a different version, and a `SHA256SUMS` that
matches the wheel byte-for-byte is still refused for the *tarball* it forged.

The ordering rule gets its own section, because it is the property the whole
two-witness scheme rests on and it is enforced structurally rather than by a
comment: `digest_for` needs a `Witnesses`, only `agree()` can build one, and
`agree()` will not return until the artifact both sources describe has matched.
"""

from __future__ import annotations

import hashlib
import io
import stat
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest

from openai4s.update import UpdateRefusal, verify

VERSION = "0.4.0"
WHEEL = f"openai4s-{VERSION}-py3-none-any.whl"
SDIST = f"openai4s-{VERSION}.tar.gz"
BUNDLE = f"OpenAI4S-{VERSION}-linux-x86_64.tar.gz"

A = "a" * 64
B = "b" * 64
C = "c" * 64


# --------------------------------------------------------------------------- #
#  digest grammar
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "candidate",
    [
        "a" * 63,
        "a" * 65,
        "A" * 64,
        "a" * 63 + "A",
        " " + "a" * 64,
        "a" * 64 + "\n",
        "g" * 64,
        "",
        None,
        1234,
        b"a" * 64,
    ],
)
def test_a_digest_is_exactly_64_lowercase_hex_or_nothing(candidate):
    """Uppercase is refused rather than folded: a digest in a spelling this
    package did not produce came from somewhere it has not thought about."""
    assert verify.is_digest(candidate) is False
    with pytest.raises(UpdateRefusal) as caught:
        verify.normalize_digest(candidate)
    assert caught.value.code == "bad_digest"


def test_a_well_formed_digest_passes_through_unchanged():
    assert verify.is_digest(A) is True
    assert verify.normalize_digest(A) == A


@pytest.mark.parametrize(
    "candidate",
    ["1.2.3", "0.0.0", "10.20.30"],
)
def test_the_release_tag_grammar_is_three_integers(candidate):
    assert verify.is_version(candidate) is True


@pytest.mark.parametrize(
    "candidate",
    ["1.2", "1.2.3.4", "1.2.3rc1", "v1.2.3", "1.2.3-dev", "", None, 1.2],
)
def test_anything_outside_the_tag_grammar_is_not_a_version(candidate):
    assert verify.is_version(candidate) is False


@pytest.mark.parametrize("suffix", ["\n", "\r\n", "\n\n"])
def test_a_trailing_newline_does_not_make_a_digest_or_a_version(suffix):
    """Python's `$` also matches just before a trailing newline.

    `"a"*64 + "\\n"` is exactly what an unstripped `SHA256SUMS` line yields, and
    a version with a newline is worse than a cosmetic problem: `is_version` is
    the gate on a string that becomes a directory name under
    `<data_dir>/updates/` and a segment of a download URL.
    """
    assert verify.is_digest("a" * 64 + suffix) is False
    assert verify.is_version("0.4.0" + suffix) is False


def test_rehash_and_expect_digest_read_the_bytes_on_disk(tmp_path):
    payload = tmp_path / "payload.bin"
    payload.write_bytes(b"openai4s" * 1000)
    digest = hashlib.sha256(b"openai4s" * 1000).hexdigest()
    assert verify.rehash(payload, chunk=7) == digest
    assert verify.expect_digest(payload, digest) == digest

    payload.write_bytes(b"openai4s" * 1000 + b"!")
    with pytest.raises(UpdateRefusal) as caught:
        verify.expect_digest(payload, digest)
    assert caught.value.code == "digest_mismatch"


def test_expect_digest_refuses_a_malformed_expectation_before_reading(tmp_path):
    missing = tmp_path / "not-there.bin"
    with pytest.raises(UpdateRefusal) as caught:
        verify.expect_digest(missing, "A" * 64)
    assert caught.value.code == "bad_digest"


# --------------------------------------------------------------------------- #
#  the two witnesses
# --------------------------------------------------------------------------- #


def test_witnesses_cannot_be_constructed_outside_agree():
    """The ordering rule stops being a rule the moment a caller can hand the
    rest of the updater a Witnesses it assembled itself."""
    with pytest.raises(TypeError):
        verify.Witnesses(
            object(),
            version=VERSION,
            wheel=WHEEL,
            pypi={WHEEL: A},
            github={WHEEL: A},
            compared=(WHEEL,),
        )


def test_two_agreeing_witnesses_record_what_was_compared():
    witnesses = verify.agree(
        VERSION, {WHEEL: A, SDIST: B}, {WHEEL: A, BUNDLE: C}, wheel=WHEEL
    )
    assert witnesses.compared == (WHEEL,)
    assert witnesses.single_witness is False
    assert witnesses.trust["integrity"] == verify.TRUST_INTEGRITY
    assert witnesses.trust["authorship"] == "not established"


def test_a_manifest_that_forges_the_tarball_is_refused_although_the_wheel_agreed():
    """The case the scheme is built for. An attacker who can rewrite the GitHub
    release rewrites `SHA256SUMS` with it, so they keep the wheel's real digest
    (which pypi.org would otherwise contradict) and forge the artifact only the
    manifest describes... except the sdist is described by *both*, so the
    forgery lands on a name that is compared."""
    with pytest.raises(UpdateRefusal) as caught:
        verify.agree(
            VERSION,
            {WHEEL: A, SDIST: B},
            {WHEEL: A, SDIST: C},
            wheel=WHEEL,
        )
    assert caught.value.code == "witness_disagreement"
    assert SDIST in str(caught.value)
    assert WHEEL not in str(caught.value).split(SDIST)[1]


def test_every_shared_artifact_is_compared_not_only_the_wheel():
    """A disagreement on any shared name refuses the whole release. Agreeing on
    the wheel buys the other artifacts nothing."""
    with pytest.raises(UpdateRefusal) as caught:
        verify.agree(VERSION, {WHEEL: A, BUNDLE: B}, {WHEEL: A, BUNDLE: C}, wheel=WHEEL)
    assert caught.value.code == "witness_disagreement"


def test_the_manifest_is_honoured_only_after_the_wheel_agreed():
    """The ordering, proved in both directions.

    A bundle tarball's digest exists only in `SHA256SUMS`. Reaching it requires
    a `Witnesses`, and building one requires the wheel to have matched — so the
    'wheel agreed first' step is not a convention a caller can skip, it is the
    only code path to the manifest.
    """
    witnesses = verify.agree(VERSION, {WHEEL: A}, {WHEEL: A, BUNDLE: C}, wheel=WHEEL)
    assert verify.digest_for(witnesses, BUNDLE) == C
    assert verify.digest_for(witnesses, WHEEL) == A

    # The same manifest, when the wheel does *not* agree: no Witnesses exists,
    # so `digest_for` is unreachable and the bundle's digest is never read.
    with pytest.raises(UpdateRefusal) as caught:
        verify.agree(VERSION, {WHEEL: A}, {WHEEL: B, BUNDLE: C}, wheel=WHEEL)
    assert caught.value.code == "witness_disagreement"


def test_a_manifest_only_digest_is_one_witness_and_the_shape_says_so():
    """What the ordering rule does *not* buy, pinned so WP2 inherits the real
    limit rather than the remembered sentence.

    Somebody who can rewrite one genuine release's assets can replace the
    bundle and its `SHA256SUMS` line while leaving the wheel line honest. The
    wheel then agrees, `agree` returns, and `digest_for` hands back the forged
    bundle digest. That is not a hole this code can close without a signature;
    it is why `compared` exists and is the only honest answer to "was this
    particular artifact seen by two witnesses".
    """
    forged = "d" * 64
    witnesses = verify.agree(
        VERSION, {WHEEL: A}, {WHEEL: A, BUNDLE: forged}, wheel=WHEEL
    )
    assert verify.digest_for(witnesses, BUNDLE) == forged
    # The one fact that distinguishes it from the wheel's digest.
    assert WHEEL in witnesses.compared
    assert BUNDLE not in witnesses.compared
    assert BUNDLE not in witnesses.pypi


def test_digest_for_needs_a_witnesses_object():
    with pytest.raises(TypeError):
        verify.digest_for({"pypi": {WHEEL: A}}, WHEEL)  # type: ignore[arg-type]


def test_a_version_present_in_one_witness_and_absent_in_the_other():
    """Nothing in common is not agreement; it is nothing to compare."""
    with pytest.raises(UpdateRefusal) as caught:
        verify.agree(VERSION, {SDIST: B}, {WHEEL: A}, wheel=WHEEL)
    assert caught.value.code == "witness_missing"

    with pytest.raises(UpdateRefusal) as caught:
        verify.agree(VERSION, {WHEEL: A}, {BUNDLE: C}, wheel=WHEEL)
    assert caught.value.code == "witness_missing"

    # And an `allow_single_witness` read does not rescue that: a manifest that
    # exists but does not cover the wheel is a manifest we cannot corroborate.
    with pytest.raises(UpdateRefusal) as caught:
        verify.agree(
            VERSION, {WHEEL: A}, {BUNDLE: C}, wheel=WHEEL, allow_single_witness=True
        )
    assert caught.value.code == "witness_missing"


def test_a_single_witness_read_records_the_weaker_trust_line():
    """`check` must still report a new version when github.com is unreachable.
    It says so rather than claiming two witnesses."""
    witnesses = verify.agree(
        VERSION, {WHEEL: A, SDIST: B}, {}, wheel=WHEEL, allow_single_witness=True
    )
    assert witnesses.single_witness is True
    assert witnesses.compared == ()
    assert witnesses.trust["integrity"] == verify.TRUST_INTEGRITY_SINGLE
    assert verify.digest_for(witnesses, WHEEL) == A
    with pytest.raises(UpdateRefusal) as caught:
        verify.digest_for(witnesses, BUNDLE)
    assert caught.value.code == "witness_missing"


def test_apply_never_accepts_a_single_witness():
    with pytest.raises(UpdateRefusal) as caught:
        verify.agree(VERSION, {WHEEL: A}, {}, wheel=WHEEL)
    assert caught.value.code == "witness_missing"


def test_an_unverified_manifest_is_refused_for_a_name_only_it_describes():
    """Defence for a state `agree()` does not currently produce.

    Reached here through the module's own seal because there is no input that
    yields a `Witnesses` with a populated manifest and nothing compared. Left
    in place and covered deliberately: it is the guard that keeps the ordering
    rule true if `agree` ever grows a path that short-circuits the comparison.
    """
    degenerate = verify.Witnesses(
        verify._SEAL,
        version=VERSION,
        wheel=WHEEL,
        pypi={},
        github={BUNDLE: C},
        compared=(),
    )
    with pytest.raises(UpdateRefusal) as caught:
        verify.digest_for(degenerate, BUNDLE)
    assert caught.value.code == "unverified_manifest"


def test_a_malformed_digest_in_either_witness_refuses_before_comparison():
    """A comparison between two strings that are not both digests is a
    comparison that can be made to succeed."""
    with pytest.raises(UpdateRefusal) as caught:
        verify.agree(VERSION, {WHEEL: A.upper()}, {WHEEL: A}, wheel=WHEEL)
    assert caught.value.code == "bad_digest"

    with pytest.raises(UpdateRefusal) as caught:
        verify.agree(VERSION, {WHEEL: A}, {WHEEL: "nope"}, wheel=WHEEL)
    assert caught.value.code == "bad_digest"


def test_agree_refuses_a_version_outside_the_tag_grammar():
    with pytest.raises(UpdateRefusal) as caught:
        verify.agree("0.4", {WHEEL: A}, {WHEEL: A}, wheel=WHEEL)
    assert caught.value.code == "witness_missing"


def test_the_trust_line_never_claims_authorship():
    """Both witnesses publish from the same repository through Trusted
    Publishing OIDC, so they share a root of trust. Integrity is verified;
    authorship is not established, and the second half is the one that goes
    missing."""
    witnesses = verify.agree(VERSION, {WHEEL: A}, {WHEEL: A}, wheel=WHEEL)
    assert "not established" in witnesses.as_dict()["trust"]["authorship_detail"]
    assert "verified" not in verify.TRUST_AUTHORSHIP


# --------------------------------------------------------------------------- #
#  archive member validation
# --------------------------------------------------------------------------- #


def _zip_with(tmp_path: Path, name: str, *, external_attr: int = 0) -> Path:
    path = tmp_path / "archive.zip"
    with zipfile.ZipFile(path, "w") as archive:
        info = zipfile.ZipInfo(name)
        info.external_attr = external_attr
        archive.writestr(info, b"x")
    return path


@pytest.mark.parametrize(
    "name",
    [
        "/etc/passwd",
        "../outside.txt",
        "openai4s/../../outside.txt",
        "C:/Windows/system32.dll",
        "openai4s\\win.py",
        "",
    ],
)
def test_a_zip_member_that_could_write_outside_the_tree_is_refused(tmp_path, name):
    """Nothing is extracted: the decision is made from the declared members,
    before a single byte lands on disk."""
    path = _zip_with(tmp_path, name or "ok.txt")
    if not name:
        # An empty member name cannot be written by zipfile, so ask the
        # validator directly for the one case the writer will not produce.
        with pytest.raises(UpdateRefusal) as caught:
            verify._check_member_name("")
        assert caught.value.code == "archive_member_unsafe"
        return
    with pytest.raises(UpdateRefusal) as caught:
        verify.validate_zip(path)
    assert caught.value.code == "archive_member_unsafe"


def test_a_nul_in_a_member_name_is_refused():
    with pytest.raises(UpdateRefusal) as caught:
        verify._check_member_name("openai4s/\x00.py")
    assert caught.value.code == "archive_member_unsafe"


def test_a_setuid_zip_member_is_refused(tmp_path):
    path = _zip_with(
        tmp_path, "openai4s/tool", external_attr=(stat.S_IFREG | 0o4755) << 16
    )
    with pytest.raises(UpdateRefusal) as caught:
        verify.validate_zip(path)
    assert "setuid" in str(caught.value)


def test_a_symlink_entry_in_a_zip_is_refused(tmp_path):
    path = _zip_with(
        tmp_path, "openai4s/link", external_attr=(stat.S_IFLNK | 0o777) << 16
    )
    with pytest.raises(UpdateRefusal) as caught:
        verify.validate_zip(path)
    assert "symlink" in str(caught.value)


def test_a_zip_entry_that_is_neither_file_nor_directory_is_refused(tmp_path):
    path = _zip_with(
        tmp_path, "openai4s/fifo", external_attr=(stat.S_IFIFO | 0o644) << 16
    )
    with pytest.raises(UpdateRefusal) as caught:
        verify.validate_zip(path)
    assert "regular file" in str(caught.value)


def test_a_plain_zip_walks_clean(tmp_path):
    """The stdlib's own writer records `0o600 << 16` — permission bits with no
    S_IFMT — and reading that as "not a regular file" refused ordinary
    archives, i.e. refused to update from a perfectly good wheel."""
    path = tmp_path / "plain.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("openai4s/__init__.py", b"x")
        archive.writestr("openai4s/cli/main.py", b"y")
    assert verify.validate_zip(path) == ("openai4s/__init__.py", "openai4s/cli/main.py")


@pytest.mark.parametrize("mode", [0o600, 0o644, 0o755, 0])
def test_a_member_recording_no_entry_kind_is_not_refused_for_it(tmp_path, mode):
    """Permission bits with no S_IFMT are what `writestr`, hatchling and every
    zip written on Windows produce. "No kind recorded" is not "a device node"."""
    path = _zip_with(tmp_path, "openai4s/x.py", external_attr=mode << 16)
    assert verify.validate_zip(path) == ("openai4s/x.py",)


def test_an_oversized_zip_is_refused_from_its_declared_sizes(tmp_path, monkeypatch):
    """A compressed archive can describe far more than it carries, so the walk
    reads the declared sizes and stops before anything is written."""
    monkeypatch.setattr(verify, "MAX_UNPACKED_BYTES", 8)
    path = tmp_path / "big.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("a.bin", b"0" * 16)
    with pytest.raises(UpdateRefusal) as caught:
        verify.validate_zip(path)
    assert caught.value.code == "archive_too_large"


def _tar_with(tmp_path: Path, *members: tarfile.TarInfo) -> Path:
    path = tmp_path / "archive.tar"
    with tarfile.open(path, "w") as archive:
        for member in members:
            if member.isreg() and member.size:
                archive.addfile(member, io.BytesIO(b"x" * member.size))
            else:
                archive.addfile(member)
    return path


def test_an_absolute_tar_member_is_refused(tmp_path):
    member = tarfile.TarInfo("/etc/shadow")
    member.size = 1
    with pytest.raises(UpdateRefusal) as caught:
        verify.validate_tar(_tar_with(tmp_path, member))
    assert caught.value.code == "archive_member_unsafe"


def test_a_dotdot_tar_member_is_refused(tmp_path):
    member = tarfile.TarInfo("app/../../outside")
    member.size = 1
    with pytest.raises(UpdateRefusal) as caught:
        verify.validate_tar(_tar_with(tmp_path, member))
    assert caught.value.code == "archive_member_unsafe"


@pytest.mark.parametrize(
    "linkname", ["/etc/passwd", "../../elsewhere", "../../../../etc/hosts", "C:/win"]
)
def test_a_symlink_escaping_the_root_is_refused(tmp_path, linkname):
    member = tarfile.TarInfo("app/link")
    member.type = tarfile.SYMTYPE
    member.linkname = linkname
    with pytest.raises(UpdateRefusal) as caught:
        verify.validate_tar(_tar_with(tmp_path, member))
    assert caught.value.code == "archive_member_unsafe"


def test_a_symlink_one_level_up_from_a_subdirectory_stays_inside(tmp_path):
    """`app/link -> ../outside` resolves to `outside`, which is in the root.
    Refusing it would refuse the relocatable bundle's own layout."""
    member = tarfile.TarInfo("app/link")
    member.type = tarfile.SYMTYPE
    member.linkname = "../outside"
    assert verify.validate_tar(_tar_with(tmp_path, member)) == ("app/link",)


def test_a_hardlink_escaping_the_root_is_refused(tmp_path):
    member = tarfile.TarInfo("app/hard")
    member.type = tarfile.LNKTYPE
    member.linkname = "../../../etc/passwd"
    with pytest.raises(UpdateRefusal) as caught:
        verify.validate_tar(_tar_with(tmp_path, member))
    assert caught.value.code == "archive_member_unsafe"


def test_a_symlink_that_stays_inside_the_root_is_allowed(tmp_path):
    member = tarfile.TarInfo("app/bin/python")
    member.type = tarfile.SYMTYPE
    member.linkname = "../runtime/bin/python3"
    assert verify.validate_tar(_tar_with(tmp_path, member)) == ("app/bin/python",)


def test_an_empty_link_target_is_refused(tmp_path):
    member = tarfile.TarInfo("app/link")
    member.type = tarfile.SYMTYPE
    member.linkname = ""
    with pytest.raises(UpdateRefusal) as caught:
        verify.validate_tar(_tar_with(tmp_path, member))
    assert caught.value.code == "archive_member_unsafe"


@pytest.mark.parametrize("kind", [tarfile.CHRTYPE, tarfile.BLKTYPE, tarfile.FIFOTYPE])
def test_a_device_or_fifo_entry_is_refused(tmp_path, kind):
    member = tarfile.TarInfo("app/dev")
    member.type = kind
    with pytest.raises(UpdateRefusal) as caught:
        verify.validate_tar(_tar_with(tmp_path, member))
    assert "device or FIFO" in str(caught.value)


def test_a_setuid_tar_member_is_refused(tmp_path):
    member = tarfile.TarInfo("app/bin/tool")
    member.mode = 0o4755
    member.size = 1
    with pytest.raises(UpdateRefusal) as caught:
        verify.validate_tar(_tar_with(tmp_path, member))
    assert "setuid" in str(caught.value)


def test_an_oversized_tar_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(verify, "MAX_UNPACKED_BYTES", 4)
    member = tarfile.TarInfo("app/big.bin")
    member.size = 16
    with pytest.raises(UpdateRefusal) as caught:
        verify.validate_tar(_tar_with(tmp_path, member))
    assert caught.value.code == "archive_too_large"


# --------------------------------------------------------------------------- #
#  the wheel structure gate
# --------------------------------------------------------------------------- #

_METADATA = """Metadata-Version: 2.1
Name: openai4s
Version: {version}
Summary: a Code-as-Action independent agent system
"""


def _wheel(
    tmp_path: Path,
    *,
    version: str = VERSION,
    declared: str | None = None,
    metadata: str | None = None,
    drop: str | None = None,
    dist_info_version: str | None = None,
    extra_members: dict | None = None,
) -> Path:
    """A synthetic wheel that passes every check unless a case breaks one."""
    path = tmp_path / f"openai4s-{version}-py3-none-any.whl"
    dist_info = f"openai4s-{dist_info_version or version}.dist-info"
    body = metadata if metadata is not None else _METADATA.format(version=version)
    with zipfile.ZipFile(path, "w") as archive:
        for name in sorted(verify.WHEEL_REQUIRED):
            if name == drop:
                continue
            if name == "openai4s/__init__.py":
                shown = version if declared is None else declared
                archive.writestr(name, f'__version__ = "{shown}"\n')
            else:
                archive.writestr(name, b"placeholder\n")
        archive.writestr(f"{dist_info}/METADATA", body)
        archive.writestr(f"{dist_info}/WHEEL", "Wheel-Version: 1.0\n")
        for name, payload in (extra_members or {}).items():
            archive.writestr(name, payload)
    return path


def test_a_good_wheel_reports_what_it_is(tmp_path):
    report = verify.wheel_structure(_wheel(tmp_path), VERSION)
    assert report.version == VERSION
    assert report.dist_info == f"openai4s-{VERSION}.dist-info"
    assert report.required_present == len(verify.WHEEL_REQUIRED)
    assert report.members > len(verify.WHEEL_REQUIRED)


def test_a_wheel_missing_a_required_path_is_refused(tmp_path):
    path = _wheel(tmp_path, drop="openai4s/server/webui/dist/index.html")
    with pytest.raises(UpdateRefusal) as caught:
        verify.wheel_structure(path, VERSION)
    assert caught.value.code == "wheel_structure"
    assert "dist/index.html" in str(caught.value)


def test_a_wheel_whose_code_declares_another_version_is_refused(tmp_path):
    path = _wheel(tmp_path, declared="0.9.9")
    with pytest.raises(UpdateRefusal) as caught:
        verify.wheel_structure(path, VERSION)
    assert caught.value.code == "wheel_version_mismatch"
    assert "0.9.9" in str(caught.value)


def test_a_wheel_whose_dist_info_names_another_version_is_refused(tmp_path):
    path = _wheel(tmp_path, dist_info_version="0.9.9")
    with pytest.raises(UpdateRefusal) as caught:
        verify.wheel_structure(path, VERSION)
    assert caught.value.code == "wheel_structure"


def test_a_wheel_metadata_naming_another_project_is_refused(tmp_path):
    path = _wheel(
        tmp_path,
        metadata=f"Metadata-Version: 2.1\nName: openai4s-x\nVersion: {VERSION}\n",
    )
    with pytest.raises(UpdateRefusal) as caught:
        verify.wheel_structure(path, VERSION)
    assert caught.value.code == "wheel_structure"


def test_a_wheel_demanding_a_runtime_dependency_is_refused(tmp_path):
    """The core is zero-dependency by design, so a wheel that asks for one is
    either not ours or is a dependency-confusion payload — and `--no-deps` at
    install time would not save us from a wheel whose own code is the attack."""
    path = _wheel(
        tmp_path,
        metadata=(
            f"Metadata-Version: 2.1\nName: openai4s\nVersion: {VERSION}\n"
            "Requires-Dist: requests>=2\n"
        ),
    )
    with pytest.raises(UpdateRefusal) as caught:
        verify.wheel_structure(path, VERSION)
    assert caught.value.code == "wheel_dependencies"
    assert "requests" in str(caught.value)


def test_an_extra_marked_dependency_is_not_a_runtime_dependency(tmp_path):
    path = _wheel(
        tmp_path,
        metadata=(
            f"Metadata-Version: 2.1\nName: openai4s\nVersion: {VERSION}\n"
            'Requires-Dist: numpy>=1.24; extra == "science"\n'
            "Provides-Extra: science\n"
        ),
    )
    assert verify.wheel_structure(path, VERSION).version == VERSION


def test_a_wheel_with_an_escaping_member_is_refused_before_anything_else(tmp_path):
    path = _wheel(tmp_path, extra_members={"../evil.py": b"x"})
    with pytest.raises(UpdateRefusal) as caught:
        verify.wheel_structure(path, VERSION)
    assert caught.value.code == "archive_member_unsafe"


def test_the_gate_refuses_a_version_outside_the_tag_grammar(tmp_path):
    with pytest.raises(UpdateRefusal) as caught:
        verify.wheel_structure(_wheel(tmp_path), "0.4")
    assert caught.value.code == "wheel_version_mismatch"


def test_extract_wheel_revalidates_rather_than_trusting_its_caller(tmp_path):
    """This is the function that writes files, and a member check that lives at
    the call site is a member check somebody forgets at the next call site."""
    good = _wheel(tmp_path)
    target = verify.extract_wheel(good, tmp_path / "out")
    assert (
        (target / "openai4s" / "__init__.py")
        .read_text()
        .strip()
        .endswith(f'"{VERSION}"')
    )

    hostile_dir = tmp_path / "h"
    hostile_dir.mkdir()
    hostile = _wheel(hostile_dir, extra_members={"../evil.py": b"x"})
    with pytest.raises(UpdateRefusal):
        verify.extract_wheel(hostile, tmp_path / "out2")
    assert not (tmp_path / "out2").exists()


def test_the_required_path_set_matches_the_release_script(tmp_path):
    """Two writers of one truth: `scripts/` is not packaged, so an installed
    daemon cannot import the release gate's copy. The copy is pinned by this
    test rather than by hope."""
    import importlib.util

    script = Path(__file__).resolve().parent.parent / "scripts"
    script = script / "verify_release_artifacts.py"
    if not script.is_file():  # pragma: no cover - a wheel-only checkout
        pytest.skip("scripts/verify_release_artifacts.py is not in this tree")
    spec = importlib.util.spec_from_file_location("_vra_for_test", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert set(verify.WHEEL_REQUIRED) == set(module._WHEEL_REQUIRED)


# --------------------------------------------------------------------------- #
#  the execution probe
# --------------------------------------------------------------------------- #


class _Done:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _runner(*results):
    calls = []

    def run(argv, env, timeout):
        calls.append((list(argv), dict(env), timeout))
        outcome = results[min(len(calls) - 1, len(results) - 1)]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    run.calls = calls  # type: ignore[attr-defined]
    return run


def _report(version=VERSION, schema=32):
    import json

    return _Done(stdout=json.dumps({"version": version, "schema_version": schema}))


def test_the_probe_runs_the_new_code_and_then_doctor(tmp_path):
    run = _runner(_report(), _doctor_report())
    result = verify.probe_installation("/usr/bin/python3", VERSION, 32, runner=run)
    assert result == {
        "version": VERSION,
        "schema_version": 32,
        "interpreter": "/usr/bin/python3",
        "doctor_ok": True,
    }
    assert run.calls[0][0][:3] == ["/usr/bin/python3", "-s", "-c"]
    assert run.calls[1][0][:3] == ["/usr/bin/python3", "-s", "-c"]
    assert "doctor" in run.calls[1][0][3]
    # A throwaway data directory, never the running one.
    assert run.calls[0][1]["OPENAI4S_DATA_DIR"] != run.calls[0][1].get("HOME", "")


def test_the_probe_refuses_code_that_reports_another_version(tmp_path):
    run = _runner(_report(version="0.9.9"))
    with pytest.raises(UpdateRefusal) as caught:
        verify.probe_installation("/usr/bin/python3", VERSION, 32, runner=run)
    assert caught.value.code == "probe_failed"
    assert len(run.calls) == 1


def test_the_probe_refuses_code_that_reports_another_schema(tmp_path):
    run = _runner(_report(schema=31))
    with pytest.raises(UpdateRefusal) as caught:
        verify.probe_installation("/usr/bin/python3", VERSION, 32, runner=run)
    assert caught.value.code == "probe_failed"


def test_the_probe_refuses_an_import_that_did_not_exit_zero():
    run = _runner(_Done(returncode=1, stderr="ImportError: boom"))
    with pytest.raises(UpdateRefusal) as caught:
        verify.probe_installation("/usr/bin/python3", VERSION, 32, runner=run)
    assert caught.value.code == "probe_failed"
    assert "ImportError" in str(caught.value)


def test_the_probe_refuses_output_that_is_not_json():
    run = _runner(_Done(stdout="hello"))
    with pytest.raises(UpdateRefusal) as caught:
        verify.probe_installation("/usr/bin/python3", VERSION, 32, runner=run)
    assert caught.value.code == "probe_failed"


@pytest.mark.parametrize("stdout", ["[]", "null", "7", '"0.4.0"', "true", '[{"a":1}]'])
def test_the_probe_refuses_json_that_is_not_an_object(stdout):
    """Valid JSON that is not a mapping, which `.get` does not answer.

    This stdout is written by the payload we are deciding whether to install,
    so its shape is attacker-controlled by construction. An `AttributeError`
    escaping here would not be a refusal: every caller of this module catches
    `UpdateRefusal` and maps `code` to an exit status or a response body, so a
    different exception type out of the last gate before a commit is a crash
    with no decision in it.
    """
    run = _runner(_Done(stdout=stdout))
    with pytest.raises(UpdateRefusal) as caught:
        verify.probe_installation("/usr/bin/python3", VERSION, 32, runner=run)
    assert caught.value.code == "probe_failed"
    assert len(run.calls) == 1  # doctor is never reached


@pytest.mark.parametrize("schema", ["thirty-two", None, [], {"a": 1}, "32x"])
def test_the_probe_refuses_a_schema_version_that_is_not_a_number(schema):
    """`int()` on the same untrusted document. A `ValueError` here is the same
    defect as the one above wearing a different exception type."""
    import json as _json

    run = _runner(
        _Done(stdout=_json.dumps({"version": VERSION, "schema_version": schema}))
    )
    with pytest.raises(UpdateRefusal) as caught:
        verify.probe_installation("/usr/bin/python3", VERSION, 32, runner=run)
    assert caught.value.code == "probe_failed"
    assert len(run.calls) == 1


def test_only_an_update_refusal_ever_leaves_the_probe():
    """The class claim, asserted over every shape at once rather than per case.

    A gate that raises something the caller does not catch has not refused; it
    has crashed in the place a refusal was supposed to be recorded.
    """
    import json as _json

    hostile = [
        "",
        "   ",
        "hello",
        "[]",
        "null",
        "7",
        '"x"',
        "{}",
        _json.dumps({"version": VERSION}),
        _json.dumps({"version": None, "schema_version": 32}),
        _json.dumps({"version": VERSION, "schema_version": "x"}),
        _json.dumps({"version": VERSION, "schema_version": [32]}),
        _json.dumps([VERSION, 32]),
    ]
    for stdout in hostile:
        try:
            verify.probe_installation(
                "/usr/bin/python3", VERSION, 32, runner=_runner(_Done(stdout=stdout))
            )
        except UpdateRefusal as refusal:
            assert refusal.code in verify.REFUSAL_CODES
        except Exception as other:  # noqa: BLE001 - that is the assertion
            raise AssertionError(
                f"probe stdout {stdout!r} raised {type(other).__name__} "
                f"instead of an UpdateRefusal: {other}"
            ) from other
        else:
            raise AssertionError(f"probe stdout {stdout!r} was accepted")


def test_the_probe_refuses_a_doctor_that_does_not_accept_the_install():
    run = _runner(_report(), _Done(returncode=2, stdout="no model configured"))
    with pytest.raises(UpdateRefusal) as caught:
        verify.probe_installation("/usr/bin/python3", VERSION, 32, runner=run)
    assert caught.value.code == "probe_failed"
    assert "doctor" in str(caught.value)


def test_a_probe_that_hangs_is_a_timeout_not_a_failure():
    """An installer exiting 0 is not proof that what it installed runs, and a
    hang is a distinct outcome from a refusal."""
    run = _runner(subprocess.TimeoutExpired(cmd="python", timeout=1.0))
    with pytest.raises(UpdateRefusal) as caught:
        verify.probe_installation(
            "/usr/bin/python3", VERSION, 32, runner=run, timeout=1
        )
    assert caught.value.code == "probe_timeout"


def test_an_interpreter_that_cannot_start_is_a_refusal_not_a_traceback():
    run = _runner(OSError("No such file or directory"))
    with pytest.raises(UpdateRefusal) as caught:
        verify.probe_installation("/nope/python3", VERSION, 32, runner=run)
    assert caught.value.code == "probe_failed"


def test_the_probe_child_gets_a_pinned_posture_and_a_throwaway_data_dir(tmp_path):
    env = verify.probe_environment(tmp_path / "scratch")
    assert env["OPENAI4S_DATA_DIR"] == str(tmp_path / "scratch")
    assert env["OPENAI4S_ALLOW_NETWORK"] == "0"
    assert env["OPENAI4S_UNATTENDED_APPROVAL"] == "deny"
    assert env["OPENAI4S_SKIP_DOTENV"] == "1"
    assert env["OPENAI4S_SECRET_STORE"] == "plaintext"
    assert "PYTHONPATH" not in env


def test_the_probe_child_inherits_an_allowlist_and_nothing_else(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI4S_LLM_API_KEY", "sk-real")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "real")
    monkeypatch.setenv("SOME_RANDOM_HOST_VAR", "x")
    env = verify.probe_environment(tmp_path)
    assert "OPENAI4S_LLM_API_KEY" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert "SOME_RANDOM_HOST_VAR" not in env


def test_a_credential_shaped_name_is_dropped_even_from_the_allowlist(
    monkeypatch, tmp_path
):
    """The allowlist has no credential-shaped member today. The second filter
    exists so that adding one does not silently hand a daemon subprocess the
    operator's provider key, and this is the only way to exercise it."""
    monkeypatch.setattr(verify, "_PROBE_ENV_INHERIT", ("PATH", "OPENAI4S_LLM_API_KEY"))
    monkeypatch.setenv("OPENAI4S_LLM_API_KEY", "sk-real")
    env = verify.probe_environment(tmp_path)
    assert "OPENAI4S_LLM_API_KEY" not in env
    assert env["PATH"]


def test_a_pythonpath_is_set_after_the_allowlist_not_inherited(monkeypatch, tmp_path):
    """The kernel's allowlist forbids PYTHONPATH because a *host* value is a
    code-injection channel. This one is a directory this process just made,
    which is a different fact about the same variable name."""
    monkeypatch.setenv("PYTHONPATH", "/attacker/site-packages")
    env = verify.probe_environment(tmp_path, pythonpath=str(tmp_path / "unpacked"))
    assert env["PYTHONPATH"] == str(tmp_path / "unpacked")


def test_every_refusal_code_this_module_raises_is_declared():
    """The tuple is the contract the CLI and the routes map; a code outside it
    is a bug here, not an input a caller has to handle."""
    import ast

    source = Path(verify.__file__).read_text("utf-8")
    raised = {
        node.args[0].value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "UpdateRefusal"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    }
    assert raised
    assert raised <= set(verify.REFUSAL_CODES)


def _link(name, target, kind=tarfile.SYMTYPE):
    member = tarfile.TarInfo(name)
    member.type = kind
    member.linkname = target
    return member


@pytest.mark.parametrize("target", ["../outside", "dir/../../outside"])
def test_tar_hardlink_targets_are_relative_to_the_archive_root(tmp_path, target):
    path = _tar_with(tmp_path, _link("deep/inside/link", target, tarfile.LNKTYPE))
    with pytest.raises(UpdateRefusal) as caught:
        verify.validate_tar(path)
    assert caught.value.code == "archive_member_unsafe"


def test_tar_symlink_resolution_checks_the_whole_chain(tmp_path):
    path = _tar_with(
        tmp_path,
        _link("dir/up", ".."),
        _link("escape", "dir/up/../outside"),
    )
    with pytest.raises(UpdateRefusal) as caught:
        verify.validate_tar(path)
    assert caught.value.code == "archive_member_unsafe"


def test_safe_tar_link_chains_and_root_relative_hardlinks_remain_supported(tmp_path):
    path = _tar_with(
        tmp_path,
        tarfile.TarInfo("runtime/python3.12"),
        _link("runtime/python3", "python3.12"),
        _link("runtime/python", "python3"),
        _link("bin/python", "runtime/python3.12", tarfile.LNKTYPE),
    )
    assert len(verify.validate_tar(path)) == 4


@pytest.mark.parametrize(
    "members",
    [
        [_link("alias", "inside"), tarfile.TarInfo("alias/file")],
        [_link("loop-a", "loop-b"), _link("loop-b", "loop-a")],
        [tarfile.TarInfo("same"), _link("same", "inside")],
        [_link("inside/link", "..\\outside")],
    ],
)
def test_ambiguous_tar_link_layouts_are_refused(tmp_path, members):
    with pytest.raises(UpdateRefusal) as caught:
        verify.validate_tar(_tar_with(tmp_path, *members))
    assert caught.value.code == "archive_member_unsafe"


def test_wheel_extraction_refuses_a_destination_with_existing_links(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    victim = tmp_path / "victim"
    victim.mkdir()
    (target / "openai4s").symlink_to(victim, target_is_directory=True)
    with pytest.raises(UpdateRefusal) as caught:
        verify.extract_wheel(_wheel(tmp_path), target)
    assert caught.value.code == "archive_member_unsafe"
    assert list(victim.iterdir()) == []


@pytest.mark.parametrize(
    "version_header", ["", "Version: 9.9.9\n", f"Version: {VERSION}\nVersion: 9.9.9\n"]
)
def test_wheel_metadata_version_must_match_the_requested_release(
    tmp_path, version_header
):
    path = _wheel(
        tmp_path, metadata=f"Metadata-Version: 2.1\nName: openai4s\n{version_header}"
    )
    with pytest.raises(UpdateRefusal) as caught:
        verify.wheel_structure(path, VERSION)
    assert caught.value.code == "wheel_version_mismatch"


@pytest.mark.parametrize(
    "marker",
    [
        'extra == "science" or python_version >= "3.10"',
        'extra == ""',
        'os_name == "extra == science"',
    ],
)
def test_a_mention_of_extra_does_not_make_a_runtime_dependency_optional(
    tmp_path, marker
):
    path = _wheel(
        tmp_path,
        metadata=(
            f"Metadata-Version: 2.1\nName: openai4s\nVersion: {VERSION}\n"
            f"Requires-Dist: unwanted; {marker}\n"
        ),
    )
    with pytest.raises(UpdateRefusal) as caught:
        verify.wheel_structure(path, VERSION)
    assert caught.value.code == "wheel_dependencies"


def _doctor_report(status="ok", checks=None):
    import json

    checks = checks or [
        {"name": "data", "status": "ok"},
        {"name": "connectors", "status": status},
    ]
    return _Done(
        returncode={"ok": 0, "warn": 1, "fail": 2}[status],
        stdout=json.dumps({"status": status, "checks": checks}),
    )


@pytest.mark.parametrize("schema", [float("inf"), 32.5, "32", True])
def test_probe_schema_requires_an_actual_json_integer(schema):
    with pytest.raises(UpdateRefusal) as caught:
        verify.probe_installation(
            "unused",
            VERSION,
            32,
            runner=_runner(_report(schema=schema), _doctor_report()),
        )
    assert caught.value.code == "probe_failed"


def test_probe_accepts_the_expected_offline_doctor_warning():
    result = verify.probe_installation(
        "unused", VERSION, 32, runner=_runner(_report(), _doctor_report("warn"))
    )
    assert result["doctor_ok"] is True


@pytest.mark.parametrize("stdout", ["", "[]", "{}", '{"status":"ok","checks":[]}'])
def test_probe_requires_a_real_doctor_report_even_after_exit_zero(stdout):
    with pytest.raises(UpdateRefusal) as caught:
        verify.probe_installation(
            "unused", VERSION, 32, runner=_runner(_report(), _Done(stdout=stdout))
        )
    assert caught.value.code == "probe_failed"


def test_probe_runs_a_real_child_on_every_supported_python(tmp_path, monkeypatch):
    import json
    import sys

    staged = tmp_path / "staged"
    package = staged / "openai4s"
    (package / "storage").mkdir(parents=True)
    (package / "__init__.py").write_text(f'__version__ = "{VERSION}"\n')
    (package / "storage" / "__init__.py").write_text("")
    (package / "storage" / "migrations.py").write_text("SCHEMA_VERSION = 32\n")
    report = _doctor_report("warn").stdout
    (package / "__main__.py").write_text(
        "import os, sys\n"
        "assert sys.argv[1:] == ['doctor', '--json']\n"
        "assert os.environ['OPENAI4S_ALLOW_NETWORK'] == '0'\n"
        f"print({report!r})\n"
        "raise SystemExit(1)\n"
    )
    caller = tmp_path / "caller"
    caller.mkdir()
    (caller / "openai4s.py").write_text(
        "raise AssertionError('imported the working directory')\n"
    )
    monkeypatch.chdir(caller)
    result = verify.probe_installation(
        sys.executable, VERSION, 32, pythonpath=str(staged)
    )
    assert result["version"] == VERSION
    assert result["doctor_ok"] is True


@pytest.mark.parametrize(
    "marker",
    [
        'python_version >= "3.11" and extra == "singlecell"',
        'extra == "science" or extra == "singlecell"',
        '"science" == extra',
    ],
)
def test_compound_extra_only_dependencies_remain_supported(tmp_path, marker):
    path = _wheel(
        tmp_path,
        metadata=(
            f"Metadata-Version: 2.1\nName: openai4s\nVersion: {VERSION}\n"
            f"Requires-Dist: optional; {marker}\n"
        ),
    )
    assert verify.wheel_structure(path, VERSION).version == VERSION


def test_probe_refuses_an_installed_copy_outside_the_staged_payload(tmp_path):
    import json

    result = _Done(
        stdout=json.dumps(
            {
                "version": VERSION,
                "schema_version": 32,
                "file": str(tmp_path / "installed" / "openai4s" / "__init__.py"),
            }
        )
    )
    run = _runner(result, _doctor_report())
    with pytest.raises(UpdateRefusal) as caught:
        verify.probe_installation(
            "unused", VERSION, 32, pythonpath=str(tmp_path / "staged"), runner=run
        )
    assert caught.value.code == "probe_failed"
    assert len(run.calls) == 1


def test_probe_does_not_accept_a_scratch_store_failure_as_an_offline_warning():
    checks = [
        {"name": "data", "status": "ok"},
        {
            "name": "connectors",
            "status": "warn",
            "facts": {"connector_store_error": "schema initialization failed"},
        },
    ]
    with pytest.raises(UpdateRefusal) as caught:
        verify.probe_installation(
            "unused",
            VERSION,
            32,
            runner=_runner(_report(), _doctor_report("warn", checks)),
        )
    assert caught.value.code == "probe_failed"


def test_probe_refuses_undecodable_child_output():
    error = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
    with pytest.raises(UpdateRefusal) as caught:
        verify.probe_installation("unused", VERSION, 32, runner=_runner(error))
    assert caught.value.code == "probe_failed"
