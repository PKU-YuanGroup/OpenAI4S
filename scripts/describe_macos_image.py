#!/usr/bin/env python3
"""Record what signed a disk image, and what is inside it.

    python3 scripts/describe_macos_image.py dist/OpenAI4S-0.2.0.dmg

Writes two sidecars beside the image:

* ``<name>.dmg.codesign.json`` — the signature's authority chain, read from
  ``codesign``. The job that stages a release runs on Linux and has no
  ``codesign``, so the evidence has to be produced here and travel with the
  image. Without it the release gate had nothing to read and fell back to "the
  ``OPENAI4S_MACOS_SIGNING_IDENTITY`` variable is set" — which an ad-hoc image
  satisfies just as well as a Developer ID one, and ad-hoc is the only kind the
  build script produces unless an identity is supplied.
* ``<name>.dmg.components.json`` — the packages actually embedded in the
  application's runtime. The SBOM previously described the interpreter of the
  machine assembling the release, which is neither the wheel nor the image.

Neither file makes a claim this script did not observe. An unsigned image gets
a receipt saying so; a runtime whose distributions cannot be read gets an empty
list and a reason.
"""
from __future__ import annotations

import hashlib
import json
import plistlib
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

#: What a real Apple distribution signature says. Kept in step with
#: ``release_pipeline.DEVELOPER_ID_AUTHORITY``.
DEVELOPER_ID_AUTHORITY = "Developer ID Application"


def _run(argv: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


@contextmanager
def _mounted(dmg: Path):
    """Attach the image read-only and give back the .app inside it."""
    with tempfile.TemporaryDirectory(prefix="openai4s-describe-") as point:
        attached = _run(
            [
                "hdiutil",
                "attach",
                str(dmg),
                "-nobrowse",
                "-readonly",
                "-mountpoint",
                point,
            ]
        )
        if attached.returncode != 0:
            raise RuntimeError(f"could not attach {dmg.name}: {attached.stderr[:400]}")
        try:
            apps = sorted(Path(point).glob("*.app"))
            if not apps:
                raise RuntimeError(f"{dmg.name} contains no .app bundle")
            yield apps[0]
        finally:
            _run(["hdiutil", "detach", point, "-force"])


def describe_signature(app: Path) -> dict:
    display = _run(["codesign", "--display", "--verbose=4", str(app)])
    text = display.stderr or ""
    authorities = [
        line.split("=", 1)[1].strip()
        for line in text.splitlines()
        if line.startswith("Authority=")
    ]
    verified = _run(["codesign", "--verify", "--deep", "--strict", str(app)])
    adhoc = "Signature=adhoc" in text or (not authorities and verified.returncode == 0)
    return {
        "bundle": app.name,
        "authorities": authorities,
        "adhoc": adhoc,
        "developer_id": any(DEVELOPER_ID_AUTHORITY in a for a in authorities),
        "verify_returncode": verified.returncode,
        "verify_detail": (verified.stderr or "").strip()[:400],
        "note": (
            "read from codesign on the machine that built the image; this file "
            "is the only evidence a Linux staging job can consult"
        ),
    }


def describe_components(app: Path) -> dict:
    """Every distribution installed in the image's embedded runtime."""
    runtime = app / "Contents" / "Resources" / "runtime"
    roots = sorted(runtime.glob("lib/python*/site-packages"))
    packages: list[dict[str, str]] = []
    if not roots:
        return {
            "packages": [],
            "unavailable": f"no site-packages under {runtime}",
        }
    seen: set[tuple[str, str]] = set()
    for root in roots:
        for meta in sorted(root.glob("*.dist-info/METADATA")):
            name = version = ""
            try:
                for line in meta.read_text("utf-8", "replace").splitlines():
                    if line.startswith("Name: ") and not name:
                        name = line[6:].strip()
                    elif line.startswith("Version: ") and not version:
                        version = line[9:].strip()
                    elif not line.strip():
                        break
            except OSError:
                continue
            if name and (name, version) not in seen:
                seen.add((name, version))
                packages.append({"name": name, "version": version or "unknown"})
        for meta in sorted(root.glob("*.egg-info/PKG-INFO")):
            try:
                text = meta.read_text("utf-8", "replace")
            except OSError:
                continue
            name = version = ""
            for line in text.splitlines():
                if line.startswith("Name: ") and not name:
                    name = line[6:].strip()
                elif line.startswith("Version: ") and not version:
                    version = line[9:].strip()
            if name and (name, version) not in seen:
                seen.add((name, version))
                packages.append({"name": name, "version": version or "unknown"})
    return {"packages": sorted(packages, key=lambda p: p["name"].lower())}


def _bundle_version(app: Path) -> str:
    try:
        plist = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    except (OSError, ValueError):
        return ""
    return str(plist.get("CFBundleShortVersionString") or "")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def describe(dmg: Path) -> tuple[dict, dict]:
    with _mounted(dmg) as app:
        signature = describe_signature(app)
        components = describe_components(app)
        version = _bundle_version(app)
    image_digest = _sha256(dmg)
    signature["image"] = dmg.name
    # Bind the receipt to the exact image it describes. Without a digest a
    # stale or copied receipt could be paired with a different, unsigned DMG on
    # a staging host and pass the signing gate. The gate re-hashes the image and
    # requires this to match.
    signature["image_sha256"] = image_digest
    components["image"] = dmg.name
    # The component inventory needs the same binding: a `.components.json` left
    # by an earlier rebuild with the same filename must not describe a different
    # image's packages in the SBOM. The pipeline re-hashes the image and requires
    # this to match before consuming the list.
    components["image_sha256"] = image_digest
    components["bundle_version"] = version
    return signature, components


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: describe_macos_image.py <image.dmg> [...]", file=sys.stderr)
        return 2
    for target in argv:
        dmg = Path(target)
        if not dmg.is_file():
            print(f"no such image: {dmg}", file=sys.stderr)
            return 1
        signature, components = describe(dmg)
        dmg.with_name(dmg.name + ".codesign.json").write_text(
            json.dumps(signature, indent=2, sort_keys=True), "utf-8"
        )
        dmg.with_name(dmg.name + ".components.json").write_text(
            json.dumps(components, indent=2, sort_keys=True), "utf-8"
        )
        kind = (
            DEVELOPER_ID_AUTHORITY
            if signature["developer_id"]
            else ("ad-hoc" if signature["adhoc"] else "unsigned")
        )
        print(
            f"{dmg.name}: {kind}; "
            f"{len(components.get('packages') or [])} embedded package(s)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
