"""Find OpenMHS for OpenAI4S, so nobody has to set ``PYTHONPATH`` by hand.

OpenMHS (https://mhs.openai4s.org) ships two source packages OpenAI4S uses: the
SDK ``mhs`` (``MHS/sdk/src``) and the reference host ``openai4s_mhs``
(``integration/openai4s``), plus the driver packages of the device market
(``MHS/drivers``).  This module looks for an OpenMHS checkout, in order:

1. ``$OPENAI4S_OPENMHS`` (an explicit checkout; ``off`` disables discovery);
2. the ``third_party/OpenMHS`` git submodule of an OpenAI4S source checkout
   (``git clone --recursive`` or ``git submodule update --init``);
3. ``<data dir>/openmhs``, where :func:`fetch` puts a pinned copy for
   installations without the submodule (``openai4s lab setup``).

:func:`bootstrap` puts the checkout's two source directories on ``sys.path``
and on ``PYTHONPATH`` (so connector and endpoint child processes see them too).
It never imports OpenMHS, never touches the network and is a no-op when the
packages are already importable from elsewhere (an explicit ``PYTHONPATH`` or
an installed distribution wins).
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

#: The OpenMHS release this OpenAI4S version is tested with (the submodule's tag).
PINNED_REF = "v0.9.0.dev0"
REPO_URL = "https://github.com/PKU-YuanGroup/OpenMHS"
SUBMODULE = Path("third_party") / "OpenMHS"
_SDK = Path("MHS") / "sdk" / "src"
_HOST = Path("integration") / "openai4s"


def _data_base() -> Path:
    data = os.environ.get("OPENAI4S_DATA_DIR", "").strip()
    return Path(data).expanduser() if data else Path.home() / ".openai4s"


def repo_root() -> Path:
    """The directory holding the ``openai4s`` package (an OpenAI4S checkout root in a source tree)."""
    return Path(__file__).resolve().parents[2]


def is_checkout(path: Path) -> bool:
    return (path / _SDK / "mhs" / "__init__.py").is_file() and (path / _HOST / "openai4s_mhs" / "__init__.py").is_file()


def disabled() -> bool:
    """``OPENAI4S_OPENMHS=off`` turns discovery off (the core then runs exactly as without OpenMHS)."""
    return os.environ.get("OPENAI4S_OPENMHS", "").strip().lower() in ("off", "0", "none", "false")


def candidates() -> list[tuple[str, Path]]:
    found: list[tuple[str, Path]] = []
    if disabled():
        return found
    explicit = os.environ.get("OPENAI4S_OPENMHS", "").strip()
    if explicit:
        found.append(("OPENAI4S_OPENMHS", Path(explicit).expanduser()))
    found.append(("submodule", repo_root() / SUBMODULE))
    found.append(("downloaded", _data_base() / "openmhs"))
    return found


def find_checkout() -> tuple[str, Path] | None:
    for source, path in candidates():
        if is_checkout(path):
            return source, path.resolve()
    return None


def source_paths(checkout: Path) -> list[Path]:
    return [checkout / _SDK, checkout / _HOST]


def _importable(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def lab_settings_path() -> Path:
    data = os.environ.get("OPENAI4S_MHS_LAB_DIR", "").strip()
    base = Path(data).expanduser() if data else _data_base() / "mhs-lab"
    return base / "mhs.json"


def bootstrap() -> Path | None:
    """Make an OpenMHS checkout importable for this process and its children; return it (or ``None``).

    Also points ``MHS_CONFIG`` at the lab's settings when the operator has set
    up the lab and has no settings document of their own, so the native
    ``mhs_*`` tools reach the lab without an environment variable.
    """
    found = None
    if disabled():
        return None
    if not (_importable("mhs") and _importable("openai4s_mhs")):
        located = find_checkout()
        if located is not None:
            found = located[1]
            paths = [str(p) for p in source_paths(found)]
            # Appended, not prepended: ``integration/openai4s`` also holds a ``tests``
            # package, which must not shadow the host application's own.
            for entry in paths:
                if entry not in sys.path:
                    sys.path.append(entry)
            current = [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]
            os.environ["PYTHONPATH"] = os.pathsep.join(paths + [p for p in current if p not in paths])
            importlib.invalidate_caches()
    if not os.environ.get("MHS_CONFIG"):
        own = _data_base() / "mhs.json"
        lab = lab_settings_path()
        if not own.exists() and lab.exists():
            os.environ["MHS_CONFIG"] = str(lab)
    return found


def _git() -> str | None:
    return shutil.which("git")


def fetch(ref: str = PINNED_REF, dest: Path | None = None, *, quiet: bool = False) -> Path:
    """Download the pinned OpenMHS release into ``<data dir>/openmhs`` (git, else a release tarball)."""
    target = Path(dest) if dest is not None else _data_base() / "openmhs"
    if is_checkout(target):
        return target
    if target.exists() and any(target.iterdir()):
        raise RuntimeError(f"{target} exists but is not an OpenMHS checkout; move it away and retry")
    target.parent.mkdir(parents=True, exist_ok=True)
    say = (lambda *_a: None) if quiet else (lambda msg: print(msg, file=sys.stderr, flush=True))
    git = _git()
    if git:
        say(f"· downloading OpenMHS {ref} (about 35 MB) into {target} …")
        result = subprocess.run(  # noqa: S603 - fixed public repository, pinned ref
            [git, "clone", "--quiet", "--depth", "1", "--branch", ref, REPO_URL + ".git", str(target)],
            capture_output=True, text=True, check=False,
        )
        if result.returncode == 0 and is_checkout(target):
            return target
        shutil.rmtree(target, ignore_errors=True)
        say(f"  git clone failed ({result.stderr.strip()[:200]}); trying the release tarball")
    url = f"{REPO_URL}/archive/refs/tags/{ref}.tar.gz"
    say(f"· downloading {url} …")
    with tempfile.TemporaryDirectory(dir=target.parent) as tmp:
        archive = Path(tmp) / "openmhs.tar.gz"
        with urllib.request.urlopen(url, timeout=120) as response, open(archive, "wb") as out:  # noqa: S310
            shutil.copyfileobj(response, out)
        with tarfile.open(archive) as tar:
            for member in tar.getmembers():
                name = Path(member.name)
                if name.is_absolute() or ".." in name.parts:
                    raise RuntimeError(f"unsafe path in the OpenMHS archive: {member.name}")
            if sys.version_info >= (3, 12):
                tar.extractall(tmp, filter="data")  # noqa: S202 - paths checked above
            else:
                tar.extractall(tmp)  # noqa: S202 - paths checked above
        roots = [p for p in Path(tmp).iterdir() if p.is_dir() and is_checkout(p)]
        if not roots:
            raise RuntimeError(f"the archive at {url} does not contain an OpenMHS checkout")
        roots[0].rename(target)
    return target


def ensure(*, download: bool = True, quiet: bool = False) -> Path:
    """Find (or, if allowed, download) OpenMHS and bootstrap it; raise with a fix when impossible."""
    located = find_checkout()
    if located is None:
        if not download:
            raise RuntimeError(
                "OpenMHS is not available. In an OpenAI4S source checkout run "
                "`git submodule update --init third_party/OpenMHS`; otherwise run `openai4s lab setup`."
            )
        fetch(quiet=quiet)
    checkout = bootstrap() or (find_checkout() or ("", None))[1]
    if not (_importable("mhs") and _importable("openai4s_mhs")):
        raise RuntimeError("OpenMHS was found but its packages are not importable; see `openai4s lab setup`")
    return Path(checkout) if checkout else Path(".")


def describe() -> dict[str, Any]:
    located = find_checkout()
    info: dict[str, Any] = {
        "pinned_ref": PINNED_REF,
        "checkout": str(located[1]) if located else None,
        "source": located[0] if located else None,
        "importable": _importable("mhs") and _importable("openai4s_mhs"),
        "searched": [str(p) for _s, p in candidates()],
    }
    if info["importable"]:
        try:
            import mhs  # type: ignore

            info["sdk_version"] = getattr(mhs, "__version__", None)
            info["sdk_path"] = str(Path(mhs.__file__).resolve().parent)
        except Exception as exc:  # noqa: BLE001 - reported, never hidden
            info["error"] = f"{type(exc).__name__}: {exc}"
    return info


__all__ = ["PINNED_REF", "REPO_URL", "bootstrap", "describe", "ensure", "fetch", "find_checkout", "is_checkout"]
