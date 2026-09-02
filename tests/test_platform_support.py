"""Windows is unsupported, and unsupported has to mean refused.

The frozen platform matrix (docs/v02-decisions.md, 8.5) says macOS arm64
stable, Linux beta, **Windows unsupported and fails closed**. The code did the
first two and only warned about the third: a native Windows install printed
"Native Windows kernels are unsupported; run OpenAI4S under WSL2" during
onboarding and then went on to try to start a kernel.

That gap is the whole subject of this file. A program that warns and proceeds
has made a different promise from one that refuses -- the first leaves a
scientist to discover the problem from a half-working analysis, which is
exactly the failure a product built on trustworthy results cannot afford.

So the test that matters is not "does it raise" but **"does it raise before it
spawns"**. A refusal after `Popen` would satisfy a naive test and none of the
intent.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from openai4s.platform_support import (
    UnsupportedPlatform,
    is_supported,
    require_supported,
    support_status,
)

# --------------------------------------------------------------------------
# the declared matrix
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "platform,status",
    [
        ("darwin", "stable"),
        ("darwin21", "stable"),
        ("linux", "beta"),
        ("linux2", "beta"),
        ("win32", "unsupported"),
        ("cygwin", "unsupported"),
        ("freebsd13", "unsupported"),
    ],
)
def test_the_matrix_matches_the_frozen_decision(platform, status):
    assert support_status(platform) == status


def test_supported_means_macos_or_linux_and_nothing_else():
    assert is_supported("darwin") and is_supported("linux")
    assert not is_supported("win32")
    assert not is_supported("freebsd13")


def test_a_supported_platform_is_allowed_silently():
    require_supported("darwin")
    require_supported("linux")  # must not raise


# --------------------------------------------------------------------------
# refusal, and what it tells the user
# --------------------------------------------------------------------------


def test_windows_is_refused_rather_than_warned():
    with pytest.raises(UnsupportedPlatform):
        require_supported("win32")


def test_the_windows_message_names_the_way_out():
    """A refusal that does not say what to do instead is a dead end. WSL2
    reports as linux, so the advice lands the user in the supported set."""
    with pytest.raises(UnsupportedPlatform) as raised:
        require_supported("win32")
    message = str(raised.value)
    assert "WSL2" in message
    assert is_supported("linux"), "the advice must lead somewhere supported"


def test_the_windows_message_says_why_not_just_that_it_will_not():
    """ "Unsupported" without a reason reads as arbitrary; the real reasons are
    POSIX subprocesses and the absence of a Windows sandbox backend, and a user
    deciding whether to trust a workaround needs them."""
    with pytest.raises(UnsupportedPlatform) as raised:
        require_supported("win32")
    message = str(raised.value).lower()
    assert "sandbox" in message
    assert "subprocess" in message


def test_an_unknown_platform_is_refused_too_and_names_the_supported_set():
    with pytest.raises(UnsupportedPlatform) as raised:
        require_supported("freebsd13")
    message = str(raised.value)
    assert "freebsd13" in message
    assert "macOS" in message and "Linux" in message


# --------------------------------------------------------------------------
# the property that distinguishes "fails closed" from "warns"
# --------------------------------------------------------------------------


def test_the_kernel_refuses_before_it_spawns_a_subprocess(monkeypatch, tmp_path):
    """The load-bearing test. A refusal raised *after* Popen would pass a naive
    "does it raise" check while still having started an unsupported kernel."""
    import openai4s.platform_support as platform_support
    from openai4s.kernel.manager import Kernel

    monkeypatch.setattr(platform_support.sys, "platform", "win32")

    spawned: list = []

    def forbidden_popen(*args, **kwargs):
        spawned.append(args)
        raise AssertionError("a subprocess was started on an unsupported platform")

    monkeypatch.setattr(subprocess, "Popen", forbidden_popen)

    # Constructing a Kernel spawns it, so the refusal lands here -- earlier than
    # the guard's placement strictly promises, which is the safe direction.
    with pytest.raises(UnsupportedPlatform):
        Kernel(cwd=str(tmp_path))

    assert spawned == [], "nothing may be spawned before the platform is checked"


def test_a_supported_platform_still_reaches_the_spawn(monkeypatch, tmp_path):
    """The other half: the guard must not refuse a platform we do support, or
    it would be a very effective way to break every install."""
    import openai4s.platform_support as platform_support
    from openai4s.kernel.manager import Kernel

    monkeypatch.setattr(platform_support.sys, "platform", "linux")

    reached: list = []

    def fake_popen(*args, **kwargs):
        reached.append(args)
        raise RuntimeError("stop here; we only need to know we got this far")

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    with pytest.raises(RuntimeError, match="stop here"):
        Kernel(cwd=str(tmp_path))

    assert reached, "a supported platform must reach the spawn"


# --------------------------------------------------------------------------
# the Python version matrix: three files that each claim a different thing
# --------------------------------------------------------------------------

_ROOT = Path(__file__).resolve().parent.parent


def _classifier_versions() -> set[str]:
    text = (_ROOT / "pyproject.toml").read_text("utf-8")
    return set(re.findall(r'"Programming Language :: Python :: (\d+\.\d+)"', text))


def _requires_python_floor() -> tuple[int, int]:
    text = (_ROOT / "pyproject.toml").read_text("utf-8")
    match = re.search(r'requires-python\s*=\s*">=\s*(\d+)\.(\d+)"', text)
    assert match, "requires-python is not the '>=X.Y' form this reads"
    return int(match.group(1)), int(match.group(2))


def _ci_tested_versions() -> set[str]:
    text = (_ROOT / ".github" / "workflows" / "ci.yml").read_text("utf-8")
    match = re.search(r"python-version:\s*\[([^\]]*)\]", text)
    assert match, "the offline-test job's version matrix is not where this reads"
    return set(re.findall(r'"(\d+\.\d+)"', match.group(1)))


def _dmg_series() -> str:
    text = (_ROOT / "scripts" / "build_macos_dmg.sh").read_text("utf-8")
    match = re.search(r'^PYSERIES="(\d+\.\d+)"', text, re.M)
    assert match, "the DMG's Python series is not where this reads"
    return match.group(1)


def _linux_series() -> str:
    """The interpreter the Linux tarball embeds -- and, through it, the Windows
    zip, which wraps that same payload rather than building its own.

    A second shipped interpreter that no reconciliation read. `_dmg_series`
    covered the macOS image and nothing covered this one, so the Linux bundle's
    Python could drift away from the classifiers and the CI matrix in silence --
    on the two platforms where the deliverable *is* the interpreter.
    """
    text = (_ROOT / "scripts" / "build_linux_bundle.sh").read_text("utf-8")
    match = re.search(r'^PYSERIES="(\d+\.\d+)"', text, re.M)
    assert match, "the Linux bundle's Python series is not where this reads"
    return match.group(1)


def _dockerfile_series() -> str:
    """The interpreter the published container image runs.

    The image is a third shipped runtime, next to the .dmg and the Linux
    tarball. Reading the Dockerfile rather than restating 3.14 here is the
    mutation the release gate needs: change the FROM line to an unlisted
    series and this fails instead of the container silently leaving the
    tested matrix.
    """
    text = (_ROOT / "Dockerfile").read_text("utf-8")
    matches = re.findall(r"^FROM python:(\d+\.\d+)[^\n]*", text, re.M)
    assert matches, "Dockerfile FROM python:X.Y is not where this reads"
    series = set(matches)
    assert (
        len(series) == 1
    ), f"Dockerfile names more than one Python series: {sorted(series)}"
    return matches[0]


def test_every_shipped_interpreter_is_claimed_and_tested():
    """Not just the DMG's. Both bundles embed a CPython, and the previous
    version of this reconciliation read one of the two build scripts."""
    classifiers = _classifier_versions()
    tested = _ci_tested_versions()
    for label, series in (
        ("macOS .dmg", _dmg_series()),
        ("Linux tarball", _linux_series()),
    ):
        assert (
            series in classifiers
        ), f"the {label} ships CPython {series} and pyproject does not claim it"
        assert (
            series in tested
        ), f"the {label} ships CPython {series} and no CI job runs it"


def test_the_two_shipped_bundles_agree_on_their_interpreter():
    """They do not have to, but a divergence has to be deliberate.

    The Windows zip wraps the Linux payload, so these two series are what every
    non-PyPI user gets. Two different ones is a supportable choice and an
    unnoticed one is not."""
    assert _dmg_series() == _linux_series(), (
        f"the .dmg embeds CPython {_dmg_series()} and the Linux tarball "
        f"{_linux_series()}; if that is intended, say so here"
    )


def test_the_shipped_interpreter_is_claimed_and_tested():
    """The .dmg embedded Python 3.13. The classifiers stopped at 3.12 and the
    CI matrix tested 3.10 and 3.12.

    So the build that reaches the most end users -- a double-clickable app,
    versus `pip install` for everyone else -- ran on the one interpreter
    nothing in the repository ever exercised, and the package did not even
    claim to support it. Not a hypothetical gap: a 3.13-only failure would have
    shipped green, because no job could see it.

    Reading all three files rather than restating them is the point. A support
    matrix written down in prose is correct on the day it is written.
    """
    shipped = _dmg_series()
    assert shipped in _classifier_versions(), (
        f"the .dmg ships Python {shipped}, which pyproject's classifiers do not "
        "claim to support"
    )
    assert shipped in _ci_tested_versions(), (
        f"the .dmg ships Python {shipped}, which the CI offline-test matrix "
        "never runs"
    )


def test_the_container_interpreter_is_tested_and_gated():
    """The published image runs 3.14. Until this check, CI and the release
    gate stopped at 3.13, so a 3.14-only failure shipped as a green
    container smoke."""
    shipped = _dockerfile_series()
    assert shipped in _ci_tested_versions(), (
        f"the container ships Python {shipped}, which the CI offline-test "
        "matrix never runs"
    )
    from scripts import release_gates

    names = {gate.check_name for gate in release_gates.CHECK_SUITE_GATES}
    assert f"Offline tests (py{shipped})" in names, (
        f"the container ships Python {shipped}, which the release gate does "
        "not require at the candidate SHA"
    )


def test_every_tested_version_is_a_claimed_version():
    """The other direction, and the cheaper one.

    A CI job on a version the classifiers omit spends real minutes proving
    something the package tells installers it does not offer. That is not
    harmless -- it is the shape of a claim that drifted, and whichever side is
    wrong, the two disagreeing is the bug.

    No exemption for the container's series: an interpreter the published
    image runs is one the wheel supports, so it is claimed by a classifier
    like every other tested version. Exempting whatever the Dockerfile names
    would let a FROM bump to an unclaimed series pass this check silently.
    """
    tested, claimed = _ci_tested_versions(), _classifier_versions()
    extra = tested - claimed
    assert (
        not extra
    ), f"CI tests Python {sorted(extra)}, which the classifiers do not claim"
    assert _dockerfile_series() in claimed, (
        f"the container ships Python {_dockerfile_series()}, which the "
        "classifiers do not claim"
    )


def test_the_floor_is_tested_and_no_claim_sits_below_it():
    """`requires-python` is what pip enforces; the classifiers are what a human
    reads. They are separate strings and nothing made them agree."""
    floor = _requires_python_floor()
    floor_text = f"{floor[0]}.{floor[1]}"
    assert floor_text in _ci_tested_versions(), (
        f"requires-python admits {floor_text} but no CI job runs it -- the "
        "oldest supported interpreter is the one most likely to break"
    )
    below = sorted(
        version
        for version in _classifier_versions()
        if tuple(int(part) for part in version.split(".")) < floor
    )
    assert not below, (
        f"the classifiers claim Python {below}, which requires-python refuses "
        "to install on"
    )
