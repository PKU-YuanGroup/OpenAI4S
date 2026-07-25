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

import subprocess

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
