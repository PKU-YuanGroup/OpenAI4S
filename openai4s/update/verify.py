"""What has to be true about a payload before anything is stopped or replaced.

Every check here runs **before** the daemon is quiesced, which is the whole
point: a bad build must never cost a restart. The order is download → two
witnesses agree → digest re-hashed at stage → archive structure → wheel
structure → the new code runs out of process. Only then does §3's transaction
begin.

Three things in this module are load-bearing and easy to weaken by accident.

**Two witnesses, and the ordering between them.** PyPI publishes a per-version
document whose `digests.sha256` is immutable for that version; the GitHub
release publishes a `SHA256SUMS` manifest that is an ordinary mutable release
asset. Taking a bundle tarball's digest from `SHA256SUMS` alone verifies
nothing against an attacker who can rewrite the release, because they would
rewrite the manifest too. So the manifest is honoured **only after** the one
artifact both sources describe — the wheel — has already agreed byte-for-byte.
That ordering is enforced structurally: `digest_for` needs a `Witnesses`, and
only `agree()` can build one. A caller cannot skip the step it does not know
about.

What this buys, stated exactly, because the precise version is narrower than
the sentence people remember. For **the wheel and the sdist — every artifact
both witnesses describe** — it defeats a compromised GitHub release alone and a
compromised PyPI account alone: the other witness still holds the true digest
and `agree` refuses. For **an artifact only `SHA256SUMS` describes** — the
Linux bundle, the Windows zip, the DMG — it defeats a *wholesale-forged release
page*, and nothing finer: somebody who can rewrite one genuine release's assets
can replace the bundle and its manifest line while leaving the wheel line
honest, and `digest_for` will then hand that digest back. The ordering rule detects
whole-manifest substitutions; it does not add a second digest witness for a
manifest-only artifact. Closing the rest needs a signature over the manifest,
which this change does not claim to have. WP2 consumes `digest_for` for exactly those artifacts, so it inherits
this limit and not a stronger one.

It does **not** defeat a compromise of this repository in any case, because
PyPI publishes through Trusted Publishing OIDC from the same repository — the
two witnesses share a root of trust. Nothing in this release is signed with a
key a client holds. So the word "verified" never appears here unqualified:
integrity is verified against two independent witnesses, and authorship is
*not established*.

**Digest grammar before digest comparison.** Exactly 64 lowercase hex or
refuse, which is the bar `scripts/windows/bootstrap.sh` already sets before it
unpacks anything. A comparison between two strings that are not both digests is
a comparison that can be made to succeed.

**The wheel structure gate re-enforces the release gate at install time.** In
particular: no non-extra `Requires-Dist`. `pyproject.toml` declares
`dependencies = []`, so a wheel that asks for a runtime dependency is either
not ours or is a dependency-confusion payload, and `--no-deps` at install time
would not save us from a wheel whose own `openai4s/__init__.py` is the attack.
"""

from __future__ import annotations

import ast
import email.parser
import hashlib
import os
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Mapping, Sequence

from openai4s.update import UpdateError, UpdateRefusal

#: Every code a refusal from this package may carry. The CLI maps these to exit
#: statuses and the HTTP routes map them to response bodies, so the tuple is the
#: contract and the prose is not. A code outside it is a bug here.
REFUSAL_CODES: tuple[str, ...] = (
    "bad_digest",
    "digest_mismatch",
    "witness_disagreement",
    "witness_missing",
    "unverified_manifest",
    "archive_member_unsafe",
    "archive_too_large",
    "wheel_structure",
    "wheel_version_mismatch",
    "wheel_dependencies",
    "probe_failed",
    "probe_timeout",
)

#: Exactly 64 lowercase hex. Uppercase is refused rather than folded: a digest
#: this package did not produce in the spelling it expects is a digest from
#: somewhere it has not thought about.
#:
#: ``\Z``, not ``$``. Python's ``$`` also matches immediately before a trailing
#: newline, so ``"a"*64 + "\n"`` — precisely what an unstripped `SHA256SUMS`
#: line yields — read as a well-formed digest, and `normalize_digest` handed
#: the newline back to a caller that then compared it, used it as a dict key,
#: or built a path from it. The same holds for the version below, and that one
#: is worse: `is_version` is the gate on a string that becomes a directory name
#: under ``<data_dir>/updates/`` and a segment of a download URL.
_SHA256_RE = re.compile(r"^[0-9a-f]{64}\Z")

_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+\Z")

#: Paths every openai4s wheel must carry. This is a **copy** of
#: `scripts/verify_release_artifacts._WHEEL_REQUIRED`, because `scripts/` is not
#: packaged and an installed daemon cannot import it. Two writers of one truth,
#: so `tests/test_update_verify.py` asserts the two sets are equal whenever the
#: script is on disk — the copy is pinned by a test rather than by hope.
WHEEL_REQUIRED: frozenset[str] = frozenset(
    {
        "openai4s/__init__.py",
        "openai4s/cli/main.py",
        "openai4s/kernel/r_worker.R",
        "openai4s/compute/templates/run.sh.tmpl",
        "openai4s/compute/templates/wrapper.sh.tmpl",
        "openai4s/server/webui/index.html",
        "openai4s/server/webui/theme-bootstrap.js",
        "openai4s/server/webui/app.js",
        "openai4s/server/webui/style.css",
        "openai4s/server/webui/vendor/3Dmol-min.js",
        "openai4s/server/webui/dist/index.html",
        "openai4s_compute_provider/__init__.py",
        "openai4s_worker_runtime/__init__.py",
        "envs/python.yml",
        "envs/phylo.yml",
        "envs/r.yml",
        "envs/struct.yml",
        "skills/example_stats/SKILL.md",
        "skills/example_stats/kernel.py",
        "skills/bioskills/COLLECTION.json",
        "skills/bioskills/LICENSE",
        "skills/bioskills/MANIFEST.json",
        "skills/bioskills/README.md",
        "skills/bioskills/README_zh.md",
        "skills/bioskills/bio-structural-biology-structure-validation/SKILL.md",
        "skills/remote-compute-nvidia/provider.json",
        "skills/remote-compute-nvidia/provider.py",
    }
)

#: Byte ceilings, stated rather than inherited. A ceiling that exists only as a
#: default somewhere else is a ceiling nobody can find when it fires.
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_WHEEL_BYTES = 64 * 1024 * 1024
MAX_BUNDLE_BYTES = 512 * 1024 * 1024
#: Total *uncompressed* bytes an archive may claim. A compressed archive can
#: describe far more than it carries, and the member walk below reads the
#: declared sizes before anything is written.
MAX_UNPACKED_BYTES = 2 * 1024 * 1024 * 1024

#: The one-line trust statement every surface prints. Both halves are true and
#: the second is the one that usually goes missing.
TRUST_INTEGRITY = (
    "integrity: verified against two independent witnesses (pypi.org, github.com)"
)
TRUST_INTEGRITY_SINGLE = (
    "integrity: one witness only; the second (github.com SHA256SUMS) was not readable"
)
TRUST_AUTHORSHIP = "authorship: not established (no release signature a client holds)"


# --------------------------------------------------------------------------- #
#  digests
# --------------------------------------------------------------------------- #


def is_digest(text: object) -> bool:
    """Whether ``text`` is exactly 64 lowercase hex characters."""
    return isinstance(text, str) and bool(_SHA256_RE.match(text))


def normalize_digest(text: object, *, what: str = "digest") -> str:
    """Return ``text`` as a digest, or refuse. Never folds case, never strips."""
    if not is_digest(text):
        raise UpdateRefusal(
            "bad_digest",
            f"{what} is not 64 lowercase hex characters: {text!r}",
        )
    assert isinstance(text, str)
    return text


def is_version(text: object) -> bool:
    """Whether ``text`` is a bounded ASCII ``X.Y.Z`` release version."""
    # Bound both integer conversion and downstream URL/path construction.
    return isinstance(text, str) and len(text) <= 128 and bool(_VERSION_RE.match(text))


def rehash(path: "os.PathLike[str] | str", *, chunk: int = 1024 * 1024) -> str:
    """The sha256 of a file on disk, streamed.

    Called three times for one payload — at stream, at stage, and immediately
    before the commit consumes it — because the same-uid window between "we
    hashed it" and "we installed it" is not closed by hashing once. It is a
    cheap read; the thing it protects is not cheap to undo.
    """
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def expect_digest(
    path: "os.PathLike[str] | str", expected: str, *, what: str = "payload"
) -> str:
    """Re-hash ``path`` and refuse unless it is ``expected``."""
    want = normalize_digest(expected, what=f"{what} expected digest")
    found = rehash(path)
    if found != want:
        raise UpdateRefusal(
            "digest_mismatch",
            f"{what} at {os.fspath(path)} hashes to {found}, expected {want}",
        )
    return found


# --------------------------------------------------------------------------- #
#  the two witnesses
# --------------------------------------------------------------------------- #

_SEAL = object()


class Witnesses:
    """Two digest maps that have been compared. Build with `agree`.

    Construction is sealed for the same reason `telemetry.wire.SealedPayload`
    is: the ordering rule this class exists to enforce — a `SHA256SUMS` digest
    is honoured only after the wheel already agreed — stops being a rule the
    moment a caller can hand the rest of the updater a `Witnesses` it assembled
    itself.
    """

    __slots__ = ("version", "wheel", "pypi", "github", "compared", "single_witness")

    def __init__(
        self,
        token: object,
        *,
        version: str,
        wheel: str,
        pypi: Mapping[str, str],
        github: Mapping[str, str],
        compared: tuple[str, ...],
        single_witness: bool = False,
    ) -> None:
        if token is not _SEAL:
            raise TypeError(
                "Witnesses is built by openai4s.update.verify.agree(); "
                "constructing one elsewhere would let a SHA256SUMS digest be "
                "honoured before the wheel digest agreed"
            )
        self.version = version
        self.wheel = wheel
        self.pypi = dict(pypi)
        self.github = dict(github)
        self.compared = compared
        self.single_witness = single_witness

    @property
    def trust(self) -> dict[str, str]:
        return {
            "integrity": (
                TRUST_INTEGRITY_SINGLE if self.single_witness else TRUST_INTEGRITY
            ),
            "authorship": "not established",
            "authorship_detail": TRUST_AUTHORSHIP,
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "wheel": self.wheel,
            "pypi": dict(self.pypi),
            "github": dict(self.github),
            "compared": list(self.compared),
            "single_witness": self.single_witness,
            "trust": self.trust,
        }


def agree(
    version: str,
    pypi: Mapping[str, str],
    github: Mapping[str, str],
    *,
    wheel: str,
    allow_single_witness: bool = False,
) -> Witnesses:
    """Compare the two digest maps and refuse on any disagreement.

    Every artifact named in **both** documents must carry a byte-equal digest.
    The wheel must be one of them, because it is the only artifact PyPI and the
    GitHub release both describe, and it is therefore the only thing that can
    catch a wholesale-forged release page.

    ``allow_single_witness`` exists for `check`, which is a read and must still
    report a new version when github.com is unreachable — it records the weaker
    trust line rather than pretending. `apply` never passes it: there, a missing
    witness is `witness_missing` and nothing is installed.
    """
    if not is_version(version):
        raise UpdateRefusal("witness_missing", f"not a release version: {version!r}")
    if not wheel:
        raise UpdateRefusal(
            "witness_missing", f"no wheel was named for version {version}"
        )

    pypi_digests = {
        name: normalize_digest(value, what=f"pypi digest for {name!r}")
        for name, value in pypi.items()
    }
    github_digests = {
        name: normalize_digest(value, what=f"SHA256SUMS digest for {name!r}")
        for name, value in github.items()
    }

    if wheel not in pypi_digests:
        raise UpdateRefusal(
            "witness_missing",
            f"pypi.org does not describe {wheel!r} for version {version}",
        )
    if wheel not in github_digests:
        if not allow_single_witness or github_digests:
            raise UpdateRefusal(
                "witness_missing",
                f"the release SHA256SUMS for v{version} does not cover {wheel!r}, "
                "so the two witnesses have nothing in common to compare",
            )
        return Witnesses(
            _SEAL,
            version=version,
            wheel=wheel,
            pypi=pypi_digests,
            github={},
            compared=(),
            single_witness=True,
        )

    shared = sorted(set(pypi_digests) & set(github_digests))
    disagreed = [name for name in shared if pypi_digests[name] != github_digests[name]]
    if disagreed:
        detail = "; ".join(
            f"{name}: pypi={pypi_digests[name]} github={github_digests[name]}"
            for name in disagreed
        )
        raise UpdateRefusal(
            "witness_disagreement",
            "the two witnesses disagree and nothing will be installed — " f"{detail}",
        )
    return Witnesses(
        _SEAL,
        version=version,
        wheel=wheel,
        pypi=pypi_digests,
        github=github_digests,
        compared=tuple(shared),
        single_witness=False,
    )


def digest_for(witnesses: Witnesses, name: str) -> str:
    """The agreed digest for one artifact.

    A name PyPI describes answers from PyPI. A name only `SHA256SUMS` describes
    — a bundle tarball, the Windows zip, the DMG — answers from the manifest,
    **and only because** getting here at all required `agree()` to have matched
    the wheel first. That is the ordering rule, expressed as the only code path
    that can reach the manifest.

    Read the module docstring for what that is and is not worth: for a
    manifest-only artifact the rule raises the cost of a forgery to *one*
    compromise of the release, rather than reducing it to zero. It is not a
    signature, and a caller must not describe a digest it got from here as
    agreed by two witnesses unless ``name`` is in `Witnesses.compared`.
    """
    if not isinstance(witnesses, Witnesses):  # pragma: no cover - type guard
        raise TypeError("digest_for needs a Witnesses built by agree()")
    if name in witnesses.pypi:
        return witnesses.pypi[name]
    if name in witnesses.github:
        if witnesses.single_witness or not witnesses.compared:
            raise UpdateRefusal(
                "unverified_manifest",
                f"{name!r} is described only by SHA256SUMS, and no artifact has "
                "been agreed between the two witnesses, so the manifest is not "
                "trusted for anything",
            )
        return witnesses.github[name]
    raise UpdateRefusal("witness_missing", f"neither witness describes {name!r}")


# --------------------------------------------------------------------------- #
#  archive structure
# --------------------------------------------------------------------------- #


def _refuse_member(name: str, why: str) -> "UpdateRefusal":
    return UpdateRefusal("archive_member_unsafe", f"archive member {name!r}: {why}")


def _check_member_name(name: str) -> PurePosixPath:
    if not name or name in (".", "./"):
        raise _refuse_member(name, "empty name")
    if "\\" in name:
        raise _refuse_member(name, "backslash in a member name")
    if "\x00" in name:
        raise _refuse_member(name, "NUL in a member name")
    if name.startswith("/"):
        raise _refuse_member(name, "absolute path")
    if re.match(r"^[A-Za-z]:", name):
        raise _refuse_member(name, "drive-letter absolute path")
    pure = PurePosixPath(name)
    if not pure.parts:
        raise _refuse_member(name, "empty normalized path")
    if pure.is_absolute():
        raise _refuse_member(name, "absolute path")
    if any(part == ".." for part in pure.parts):
        raise _refuse_member(name, "a '..' component")
    return pure


def _check_mode(name: str, mode: int) -> None:
    if mode & (stat.S_ISUID | stat.S_ISGID):
        raise _refuse_member(name, "the setuid or setgid bit")


def _check_link_target(name: str, target: str, *, hardlink: bool = False) -> None:
    if "\\" in target or "\x00" in target:
        raise _refuse_member(name, "a backslash or NUL in the link target")
    if not target:
        raise _refuse_member(name, "an empty link target")
    if target.startswith("/") or re.match(r"^[A-Za-z]:", target):
        raise _refuse_member(name, f"an absolute link target {target!r}")
    # tar hardlinks name a member from the archive root, unlike symlinks.
    resolved = PurePosixPath(".") if hardlink else PurePosixPath(name).parent
    for part in PurePosixPath(target).parts:
        if part == "..":
            if resolved == PurePosixPath("."):
                raise _refuse_member(name, f"a link escaping the root: {target!r}")
            resolved = resolved.parent
        elif part not in (".",):
            resolved = resolved / part
    if any(part == ".." for part in resolved.parts):
        raise _refuse_member(name, f"a link escaping the root: {target!r}")


def validate_zip(path: "os.PathLike[str] | str") -> tuple[str, ...]:
    """Walk a zip's directory and refuse anything that could write outside it.

    Nothing is extracted here. The whole point is that the decision is made
    from the *declared* members, before a single byte lands on disk.
    """
    names: list[str] = []
    total = 0
    with zipfile.ZipFile(os.fspath(path)) as archive:
        for info in archive.infolist():
            _check_member_name(info.filename)
            mode = (info.external_attr >> 16) & 0xFFFF
            if mode:
                _check_mode(info.filename, mode)
                # The entry kind is checked only when the entry *records* one.
                # A zip may carry permission bits with no S_IFMT at all --
                # `zipfile.ZipFile.writestr(str, ...)` stores `0o600 << 16`,
                # hatchling stores `0o644 << 16`, and a zip written on Windows
                # stores none of it -- and reading "no type recorded" as "not a
                # regular file" refused ordinary archives, including any wheel
                # with one such member. That is a refusal of a legitimate
                # payload, which for an updater means it declines to update.
                # Nothing is lost: a declared symlink or device node is still
                # refused, and `zipfile` never creates either on extraction
                # whatever the bits say.
                if stat.S_IFMT(mode):
                    if stat.S_ISLNK(mode):
                        raise _refuse_member(info.filename, "a symlink entry")
                    if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                        raise _refuse_member(info.filename, "not a regular file")
            total += int(info.file_size)
            if total > MAX_UNPACKED_BYTES:
                raise UpdateRefusal(
                    "archive_too_large",
                    f"{os.fspath(path)} declares more than "
                    f"{MAX_UNPACKED_BYTES} uncompressed bytes",
                )
            names.append(info.filename)
    return tuple(names)


def _validate_tar_links(members: Mapping[str, tarfile.TarInfo]) -> None:
    """Resolve links together, including aliases followed by '..'.

    This is checked against the complete namespace, so archive order cannot
    turn a previously checked directory into a link. Extraction must use an
    empty staging directory; pre-existing filesystem links are not described
    by an archive's member table.
    """
    links = {
        name: member
        for name, member in members.items()
        if member.issym() or member.islnk()
    }
    for name, member in members.items():
        if any(str(parent) in links for parent in PurePosixPath(name).parents):
            raise _refuse_member(name, "a member nested underneath a link")
        if name not in links:
            continue
        resolved = [] if member.islnk() else list(PurePosixPath(name).parent.parts)
        pending = list(reversed(PurePosixPath(member.linkname).parts))
        hops = 0
        while pending:
            part = pending.pop()
            if part == "..":
                if not resolved:
                    raise _refuse_member(name, "a link chain escaping the root")
                resolved.pop()
                continue
            if part == ".":
                continue
            resolved.append(part)
            linked = links.get("/".join(resolved))
            if linked is None:
                continue
            hops += 1
            if hops > 40:
                raise _refuse_member(name, "a cyclic or excessive link chain")
            if linked.islnk():
                resolved.clear()
            else:
                resolved.pop()
            pending.extend(reversed(PurePosixPath(linked.linkname).parts))


def validate_tar(path: "os.PathLike[str] | str") -> tuple[str, ...]:
    """Validate a tarball for extraction into an empty staging directory."""
    names: list[str] = []
    members: dict[str, tarfile.TarInfo] = {}
    total = 0
    with tarfile.open(os.fspath(path), "r:*") as archive:
        for member in archive:
            canonical = str(_check_member_name(member.name))
            if canonical in members:
                raise _refuse_member(member.name, "a duplicate member path")
            members[canonical] = member
            _check_mode(member.name, int(member.mode or 0))
            if member.ischr() or member.isblk() or member.isfifo():
                raise _refuse_member(member.name, "a device or FIFO entry")
            if member.issym() or member.islnk():
                _check_link_target(
                    member.name, member.linkname, hardlink=member.islnk()
                )
            elif not (member.isfile() or member.isdir()):
                raise _refuse_member(member.name, "not a regular file")
            total += int(member.size or 0)
            if total > MAX_UNPACKED_BYTES:
                raise UpdateRefusal(
                    "archive_too_large",
                    f"{os.fspath(path)} declares more than "
                    f"{MAX_UNPACKED_BYTES} uncompressed bytes",
                )
            names.append(member.name)
    _validate_tar_links(members)
    return tuple(names)


# --------------------------------------------------------------------------- #
#  the wheel structure gate
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class WheelReport:
    """What a wheel turned out to be. Returned only when every check passed."""

    path: Path
    version: str
    dist_info: str
    members: int
    required_present: int


def _wheel_declared_version(source: str) -> str | None:
    match = re.search(
        r"""^__version__\s*=\s*["']([^"']+)["']\s*$""", source, re.MULTILINE
    )
    return match.group(1) if match else None


def _requires_extra(marker: str) -> bool:
    """Prove that a marker is false without a selected extra.

    Unknown comparisons are conservatively possibly true. Only equality with
    a nonempty extra proves a false branch; boolean and/or preserve that proof.
    Unsupported marker syntax is refused rather than guessed.
    """
    try:
        expression = ast.parse(marker.strip(), mode="eval").body
    except (SyntaxError, ValueError, RecursionError):
        return False

    def may_apply(node: ast.AST) -> bool:
        if isinstance(node, ast.BoolOp):
            values = [may_apply(value) for value in node.values]
            return all(values) if isinstance(node.op, ast.And) else any(values)
        if (
            isinstance(node, ast.Compare)
            and len(node.ops) == 1
            and isinstance(node.ops[0], ast.Eq)
        ):
            left, right = node.left, node.comparators[0]
            if isinstance(right, ast.Name):
                left, right = right, left
            if (
                isinstance(left, ast.Name)
                and left.id == "extra"
                and isinstance(right, ast.Constant)
            ):
                if isinstance(right.value, str) and right.value:
                    return False
        return True

    try:
        return not may_apply(expression)
    except RecursionError:
        return False


def _non_extra_requirements(metadata: str) -> list[str]:
    """`Requires-Dist` lines not proven conditional on a selected extra.

    `pyproject.toml` declares `dependencies = []`, so there must be none. This
    re-enforces at install time what the release gate enforces at release time,
    which is a real dependency-confusion control on a zero-dependency package:
    `--no-deps` stops pip resolving them, it does not stop the wheel *being*
    something else.
    """
    parsed = email.parser.Parser().parsestr(metadata)
    hard: list[str] = []
    for raw in parsed.get_all("Requires-Dist") or []:
        requirement = str(raw).strip()
        if not requirement:
            continue
        _, separator, marker = requirement.partition(";")
        if separator and _requires_extra(marker):
            continue
        hard.append(requirement)
    return hard


def wheel_structure(
    path: "os.PathLike[str] | str",
    version: str,
    *,
    required: Iterable[str] | None = None,
) -> WheelReport:
    """Refuse a wheel that is not the openai4s wheel for ``version``.

    Five questions, each of which a plausible-looking bad wheel passes four of:
    can any member write outside the tree; does it carry every path a release
    must carry; does its own `openai4s/__init__.py` declare this version; is its
    METADATA `Name: openai4s`; and does it demand a runtime dependency.
    """
    wheel_path = Path(os.fspath(path))
    if not is_version(version):
        raise UpdateRefusal(
            "wheel_version_mismatch", f"not a release version: {version!r}"
        )
    names = validate_zip(wheel_path)
    present = set(names)
    want = frozenset(required) if required is not None else WHEEL_REQUIRED
    missing = sorted(want - present)
    if missing:
        raise UpdateRefusal(
            "wheel_structure",
            f"{wheel_path.name} is missing {len(missing)} required path(s): "
            + ", ".join(missing[:5])
            + ("…" if len(missing) > 5 else ""),
        )

    dist_info = f"openai4s-{version}.dist-info"
    metadata_name = f"{dist_info}/METADATA"
    if metadata_name not in present:
        raise UpdateRefusal(
            "wheel_structure",
            f"{wheel_path.name} carries no {metadata_name}; its dist-info does "
            f"not name version {version}",
        )

    with zipfile.ZipFile(wheel_path) as archive:
        init_source = archive.read("openai4s/__init__.py").decode("utf-8", "replace")
        metadata = archive.read(metadata_name).decode("utf-8", "replace")

    declared = _wheel_declared_version(init_source)
    if declared != version:
        raise UpdateRefusal(
            "wheel_version_mismatch",
            f"{wheel_path.name} says it is version {version} but its "
            f"openai4s/__init__.py declares {declared!r}",
        )

    parsed = email.parser.Parser().parsestr(metadata)
    if (parsed.get("Name") or "").strip().lower() != "openai4s":
        raise UpdateRefusal(
            "wheel_structure",
            f"{wheel_path.name} METADATA names {parsed.get('Name')!r}, not openai4s",
        )
    versions = parsed.get_all("Version") or []
    if len(versions) != 1 or versions[0].strip() != version:
        raise UpdateRefusal(
            "wheel_version_mismatch",
            f"{wheel_path.name} METADATA must declare exactly Version: {version}",
        )
    hard = _non_extra_requirements(metadata)
    if hard:
        raise UpdateRefusal(
            "wheel_dependencies",
            f"{wheel_path.name} declares {len(hard)} non-extra Requires-Dist "
            f"({', '.join(hard[:3])}); the core is zero-dependency by design, so "
            "this wheel is not the one this release gate produced",
        )
    return WheelReport(
        path=wheel_path,
        version=version,
        dist_info=dist_info,
        members=len(names),
        required_present=len(want),
    )


def extract_wheel(
    path: "os.PathLike[str] | str", destination: "os.PathLike[str] | str"
) -> Path:
    """Unpack a **already validated** wheel into ``destination``.

    `validate_zip` is re-run here rather than trusted from the caller: this is
    the function that writes files, and a member check that lives at the call
    site is a member check somebody will forget at the next call site.
    """
    wheel_path = Path(os.fspath(path))
    target = Path(os.fspath(destination))
    validate_zip(wheel_path)
    if target.is_symlink() or (
        target.exists() and (not target.is_dir() or any(target.iterdir()))
    ):
        raise UpdateRefusal(
            "archive_member_unsafe",
            "wheel extraction requires an empty, non-symlink staging directory",
        )
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(wheel_path) as archive:
        archive.extractall(target)
    return target


# --------------------------------------------------------------------------- #
#  the execution probe
# --------------------------------------------------------------------------- #
#
# `openai4s/kernel/env_generations.py` learned this the expensive way: an
# installer exiting 0 is not proof that what it installed runs. So the new code
# is executed, out of process, against a throwaway data directory, before the
# running one is replaced.

#: Host variables the probe child may inherit. Everything else is dropped, and
#: every name still standing is passed through the kernel's own
#: credential-shaped-name test — a daemon subprocess is not sandboxed, and
#: would otherwise inherit every provider key in the environment.
_PROBE_ENV_INHERIT = (
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TMPDIR",
    "TEMP",
    "TMP",
    "SYSTEMROOT",
    "USERPROFILE",
)

#: The posture the probe child runs under, pinned rather than inherited. It is
#: a fresh install being asked one question; it must not dial anything, must not
#: open the operator's keychain, and must not read their .env.
_PROBE_ENV_PINNED = {
    "OPENAI4S_SKIP_DOTENV": "1",
    "OPENAI4S_ALLOW_NETWORK": "0",
    "OPENAI4S_UNATTENDED_APPROVAL": "deny",
    "OPENAI4S_SECRET_STORE": "plaintext",
    "OPENAI4S_NOTEBOOK_REPL": "0",
    "OPENAI4S_NO_OPEN": "1",
    "PYTHONNOUSERSITE": "1",
    "PYTHONDONTWRITEBYTECODE": "1",
    # Exercise the model-config path without copying a credential or dialling.
    # A blank scratch Store otherwise makes every valid install fail doctor.
    "OPENAI4S_LLM_PROVIDER": "openai_responses",
    "OPENAI4S_LLM_BASE_URL": "http://127.0.0.1:1/v1",
    "OPENAI4S_LLM_MODEL": "update-probe",
}

# -P is unavailable on Python 3.10. With -c, index zero is the implicit
# working directory; remove it before importing any payload or runpy code.
_PROBE_BOOTSTRAP = "import sys; sys.path.pop(0)\n"
_DOCTOR_SCRIPT = (
    _PROBE_BOOTSTRAP
    + "import runpy\n"
    + "sys.argv = ['openai4s', 'doctor', '--json']\n"
    + "runpy.run_module('openai4s', run_name='__main__')\n"
)

_PROBE_SCRIPT = (
    _PROBE_BOOTSTRAP + "import json, openai4s\n"
    "from openai4s.storage import migrations\n"
    "print(json.dumps({'version': openai4s.__version__, "
    "'schema_version': migrations.SCHEMA_VERSION, "
    "'file': openai4s.__file__}))\n"
)

#: A completed child, as this module needs it. `subprocess.CompletedProcess`
#: satisfies it; so does a test double, which is why the seam is a callable
#: rather than a monkeypatch of `subprocess`.
ProbeRunner = Callable[[Sequence[str], Mapping[str, str], float], Any]


def probe_environment(
    data_dir: "os.PathLike[str] | str", *, pythonpath: str = ""
) -> dict[str, str]:
    """The child environment for a probe: an allowlist, then a deny check."""
    from openai4s.kernel.environment import name_can_carry_a_secret

    env: dict[str, str] = {}
    for name in _PROBE_ENV_INHERIT:
        value = os.environ.get(name)
        if value is None or name_can_carry_a_secret(name):
            continue
        env[name] = value
    env.setdefault("PATH", os.defpath)
    env.update(_PROBE_ENV_PINNED)
    env["OPENAI4S_DATA_DIR"] = os.fspath(data_dir)
    if pythonpath:
        # Deliberately set *after* the allowlist rather than inherited: the
        # kernel's allowlist forbids PYTHONPATH precisely because a host value
        # is a code-injection channel. This one is a path this process just
        # created, which is a different fact about the same variable name.
        env["PYTHONPATH"] = pythonpath
    return env


def _default_runner(
    argv: Sequence[str], env: Mapping[str, str], timeout: float
) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(  # noqa: S603 - fixed argv, no shell, scrubbed env
        list(argv),
        env=dict(env),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        stdin=subprocess.DEVNULL,
        cwd=Path(env["OPENAI4S_DATA_DIR"]).parent,
    )


def _tail(text: object, limit: int = 400) -> str:
    body = str(text or "").strip()
    return body[-limit:] if len(body) > limit else body


def probe_installation(
    interpreter: "os.PathLike[str] | str",
    version: str,
    schema_version: int,
    *,
    pythonpath: str = "",
    timeout: float = 60.0,
    runner: ProbeRunner | None = None,
) -> dict[str, Any]:
    """Run the new code out of process and require it to be what it claims.

    Two children against a throwaway ``OPENAI4S_DATA_DIR``:

    1. import it and print its `__version__` and `SCHEMA_VERSION`;
    2. `openai4s doctor --json`, which must return a valid ok/warn report.
       Offline connectors and missing optional runtimes are expected warnings;
       any failed check still refuses the installation.

    Neither child is given ``-I`` or ``-E``, and that is deliberate rather than
    an oversight: both imply ignoring ``PYTHON*``, which would discard the
    ``PYTHONPATH`` that makes the venv probe possible at all — the wheel is
    extracted and run *without being installed*. The isolation those flags would
    have given is achieved by the environment instead, which `probe_environment`
    builds from an allowlist. A fixed bootstrap removes the implicit working
    directory on every supported Python (including 3.10, which has no ``-P``),
    and ``-s`` excludes the user site. The child runs in the scratch directory.
    """
    run = runner or _default_runner
    python = os.fspath(interpreter)
    with tempfile.TemporaryDirectory(prefix="openai4s-update-probe-") as scratch:
        env = probe_environment(Path(scratch) / "data", pythonpath=pythonpath)
        import_argv = [python, "-s", "-c", _PROBE_SCRIPT]
        try:
            imported = run(import_argv, env, timeout)
        except subprocess.TimeoutExpired:
            raise UpdateRefusal(
                "probe_timeout",
                f"the new code did not import within {timeout:g}s",
            ) from None
        except (OSError, UnicodeError) as error:
            raise UpdateRefusal(
                "probe_failed", f"could not run {python}: {error}"
            ) from error
        if int(getattr(imported, "returncode", 1)) != 0:
            raise UpdateRefusal(
                "probe_failed",
                "the new code did not import: "
                + _tail(getattr(imported, "stderr", "")),
            )
        import json as _json

        try:
            reported = _json.loads(str(getattr(imported, "stdout", "")).strip())
        except (ValueError, RecursionError) as error:
            raise UpdateRefusal(
                "probe_failed",
                "the import probe printed something that is not JSON: "
                + _tail(getattr(imported, "stdout", "")),
            ) from error
        # A JSON *object*, checked rather than assumed. This stdout is produced
        # by the payload we are deciding whether to install, so its shape is
        # attacker-controlled by construction, and `[]`, `null`, `7` and `"x"`
        # are all valid JSON that `.get` does not answer. Letting an
        # `AttributeError` out of the last gate before a commit turns a refusal
        # into an unhandled traceback: every caller of this module catches
        # `UpdateRefusal` and maps `code` to an exit status or a response body,
        # so a different exception type here is a crash rather than a decision.
        if not isinstance(reported, dict):
            raise UpdateRefusal(
                "probe_failed",
                "the import probe printed JSON that is not an object "
                f"({type(reported).__name__}): "
                + _tail(getattr(imported, "stdout", "")),
            )
        if reported.get("version") != version:
            raise UpdateRefusal(
                "probe_failed",
                f"the new code reports version {reported.get('version')!r}, "
                f"expected {version}",
            )
        # Coercion would accept fractional versions and booleans, and can
        # raise OverflowError for JSON's nonstandard Infinity value.
        raw_schema = reported.get("schema_version")
        if type(raw_schema) is not int or raw_schema != schema_version:
            raise UpdateRefusal(
                "probe_failed",
                f"the new code reports schema version "
                f"{raw_schema!r}, expected {schema_version}",
            )

        if pythonpath:
            loaded_file = reported.get("file")
            expected_files = {
                (Path(part) / "openai4s" / "__init__.py").resolve()
                for part in pythonpath.split(os.pathsep)
                if part
            }
            if (
                not isinstance(loaded_file, str)
                or Path(loaded_file).resolve() not in expected_files
            ):
                raise UpdateRefusal(
                    "probe_failed",
                    "the probe imported openai4s outside the staged payload",
                )
        reported_schema = raw_schema
        doctor_argv = [python, "-s", "-c", _DOCTOR_SCRIPT]
        try:
            doctor = run(doctor_argv, env, timeout)
        except subprocess.TimeoutExpired:
            raise UpdateRefusal(
                "probe_timeout",
                f"`openai4s doctor --json` did not finish within {timeout:g}s",
            ) from None
        except (OSError, UnicodeError) as error:
            raise UpdateRefusal(
                "probe_failed", f"could not run {python}: {error}"
            ) from error
        doctor_code = int(getattr(doctor, "returncode", -1))
        if doctor_code not in (0, 1):
            raise UpdateRefusal(
                "probe_failed",
                "`openai4s doctor --json` refused the new installation: "
                + _tail(getattr(doctor, "stderr", "") or getattr(doctor, "stdout", "")),
            )
        try:
            diagnosis = _json.loads(str(getattr(doctor, "stdout", "")))
        except (ValueError, RecursionError):
            diagnosis = None
        checks = diagnosis.get("checks") if isinstance(diagnosis, dict) else None
        valid_checks = (
            isinstance(checks, list)
            and bool(checks)
            and all(
                isinstance(item, dict) and item.get("status") in ("ok", "warn")
                for item in checks
            )
            and any(
                item.get("name") == "data" and item.get("status") == "ok"
                for item in checks
            )
            and not any(
                isinstance(item.get("facts"), dict)
                and "connector_store_error" in item["facts"]
                for item in checks
            )
        )
        expected_status = "warn" if doctor_code == 1 else "ok"
        if not valid_checks or diagnosis.get("status") != expected_status:
            raise UpdateRefusal(
                "probe_failed",
                "doctor did not return a valid successful diagnostic report",
            )
    return {
        "version": reported.get("version"),
        "schema_version": reported_schema,
        "interpreter": python,
        "doctor_ok": True,
    }


def running_interpreter() -> str:
    """`sys.executable`, in one place so a test can see which one was used."""
    return sys.executable


__all__ = [
    "MAX_BUNDLE_BYTES",
    "MAX_JSON_BYTES",
    "MAX_MANIFEST_BYTES",
    "MAX_UNPACKED_BYTES",
    "MAX_WHEEL_BYTES",
    "REFUSAL_CODES",
    "TRUST_AUTHORSHIP",
    "TRUST_INTEGRITY",
    "TRUST_INTEGRITY_SINGLE",
    "WHEEL_REQUIRED",
    "UpdateError",
    "UpdateRefusal",
    "WheelReport",
    "Witnesses",
    "agree",
    "digest_for",
    "expect_digest",
    "extract_wheel",
    "is_digest",
    "is_version",
    "normalize_digest",
    "probe_environment",
    "probe_installation",
    "rehash",
    "running_interpreter",
    "validate_tar",
    "validate_zip",
    "wheel_structure",
]
