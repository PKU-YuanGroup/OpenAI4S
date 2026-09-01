"""The release pipeline, tested without cutting a release.

That is the whole reason it is a script. A step embedded in a workflow that
triggers on a release event can only be exercised by the event it is supposed
to protect, so the first time anyone learns it is wrong is on a real version.

What these pin, in order of how much they would cost to get wrong:

  * the GitHub flip is the *last* cross-channel step and refuses to run until
    PyPI actually has the version — a public release with no matching package
    is the half-published state this pipeline exists to prevent;
  * a staging run does not rebuild: GitHub and PyPI must receive the same bytes;
  * a disk image is signed on evidence, never on a configured variable;
  * the read-back compares content, not filenames;
  * SHA256SUMS covers everything and is itself uploaded;
  * the SBOM describes the wheel and the image, not the machine that staged it;
  * provenance names the repository this source actually lives in;
  * a failure stops the pipeline — the steps after it must not run.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.release_pipeline import (  # noqa: E402
    EXTERNAL,
    STAGING_SKIPPED,
    STEPS,
    Pipeline,
    ReleaseError,
    build_provenance,
    build_sbom,
    canonical_source_uri,
    read_signature,
    sha256_file,
    wheel_components,
)

ROOT = Path(__file__).resolve().parents[1]


def _completed(returncode=0, stdout=b"", stderr=b""):
    return subprocess.CompletedProcess(["fake"], returncode, stdout, stderr)


#: The SHA the fake runner reports for `git rev-parse HEAD`.
FAKE_HEAD = "a" * 40


def _git_aware(extra=None):
    """A runner that answers `git rev-parse HEAD`, as the real one does.

    `step_test` binds the quality receipt to the commit being released, so a
    runner that answers every command with empty stdout leaves the pipeline
    unable to say what it is releasing -- which the receipt check correctly
    refuses.
    """

    def runner(argv, cwd=None):
        parts = [str(a) for a in argv]
        if extra is not None:
            extra(" ".join(parts))
        if parts[:3] == ["git", "rev-parse", "HEAD"]:
            return _completed(0, FAKE_HEAD.encode())
        return _completed()

    return runner


def _write_receipt(directory, *, source_sha=FAKE_HEAD, failing=(), **overrides):
    """The document the quality job uploads beside the distributions.

    Built from the canonical manifest in `scripts/release_gates.py` rather than
    hand-listed. It used to be two rows -- `pytest` with the argv `["pytest"]`,
    `mypy` with `["mypy"]` -- and the consumer accepted it, so this fixture was
    itself a demonstration that the receipt proved nothing. Deriving it from the
    manifest means a gate added later cannot leave this describing the old set.

    `failing` names gates whose exit code should be non-zero, which is how the
    refusal tests stay refusal tests.
    """
    from scripts import release_gates

    document = release_gates.build_receipt(
        source_sha,
        [
            {
                "name": gate.name,
                "command": list(gate.command),
                "returncode": 1 if gate.name in failing else 0,
            }
            for gate in release_gates.LOCAL_GATES
        ],
        [
            {
                "name": gate.name,
                "check_name": gate.check_name,
                "check_run_id": f"{7000 + index}",
                "run_id": "7100",
                "url": "https://example.invalid/check",
                "conclusion": "success",
                "head_sha": source_sha,
            }
            for index, gate in enumerate(release_gates.CHECK_SUITE_GATES)
        ],
    )
    document.update(overrides)
    target = directory / release_gates.RECEIPT_NAME
    target.write_text(json.dumps(document), "utf-8")
    return target


def _write_build_receipt(directory, kind, artifacts, *, source_sha=FAKE_HEAD):
    """The receipt the job that built those bytes writes beside them.

    Staging verifies one per artifact group against the frozen SHA, which is how
    "the wheel and the DMG are the same commit" becomes checkable. Each build job
    used to check out the mutable tag independently with nothing comparing them.
    """
    from scripts import release_receipts

    document = release_receipts.build_build_receipt(kind, source_sha, artifacts)
    target = directory / release_receipts.build_receipt_name(kind)
    target.write_text(json.dumps(document, indent=2), "utf-8")
    return target


def _receipt_dist(directory, *, source_sha=FAKE_HEAD):
    return _write_build_receipt(
        directory,
        "dist",
        [p for p in directory.glob("*") if p.suffix in (".whl", ".gz")],
        source_sha=source_sha,
    )


@pytest.fixture
def assets(tmp_path):
    directory = tmp_path / "dist"
    directory.mkdir()
    (directory / "openai4s-0.2.0-py3-none-any.whl").write_bytes(b"wheel-bytes")
    (directory / "openai4s-0.2.0.tar.gz").write_bytes(b"sdist-bytes")
    _write_receipt(directory)
    _receipt_dist(directory)
    return directory


def _signed_dmg(
    assets: Path,
    name: str = "OpenAI4S-0.2.0-arm64.dmg",
    *,
    notarized: bool = True,
    receipt: bool = True,
) -> Path:
    """A disk image with the receipt a macOS build job would have written.

    `notarized` is what the release gate now turns on. It defaults to true
    because a *publishable* image is the normal fixture; the tests that care
    about the un-notarized case set it false explicitly rather than relying on
    an omission, which is how the old gate came to check nothing.
    """
    dmg = assets / name
    dmg.write_bytes(b"dmg-bytes")
    dmg.with_name(dmg.name + ".codesign.json").write_text(
        json.dumps(
            {
                "authorities": [
                    "Developer ID Application: Example Inc (ABCDE12345)",
                    "Developer ID Certification Authority",
                    "Apple Root CA",
                ],
                "adhoc": False,
                "developer_id": True,
                # A real receipt records the deep verification result, and the
                # gate requires it to have succeeded.
                "verify_returncode": 0,
                # ...and the digest of the exact image it describes.
                "image_sha256": sha256_file(dmg),
                # Notarization, read from `xcrun stapler validate` by the macOS
                # job. Was hardcoded `None` and gated nothing.
                "notarized": notarized,
                "stapler_returncode": 0 if notarized else 1,
                "spctl_returncode": 0 if notarized else 1,
                # Stapling rewrites the DMG; the gate requires this post-staple
                # digest to match the bytes being staged.
                "post_staple_sha256": sha256_file(dmg) if notarized else "",
            }
        ),
        encoding="utf-8",
    )
    if receipt:
        _write_build_receipt(assets, "macos", [dmg])
    return dmg


def _matching_pypi(assets: Path):
    """PyPI digests that agree with the local wheel/sdist bytes.

    The default for the ordering tests: PyPI is the finalize anchor, so a
    success-path run needs a matching digest for every Python distribution.
    Only wheels and sdists live on PyPI.
    """

    def digests(_project, _version):
        return {
            path.name: sha256_file(path)
            for path in assets.glob("*")
            if path.name.endswith((".whl", ".tar.gz"))
        }

    return digests


def _pipeline(assets, **kw):
    kw.setdefault("runner", _git_aware())
    kw.setdefault("gh", lambda argv: _completed(0, b'{"assets": [], "isDraft": true}'))
    kw.setdefault("smoke", lambda wheel: "smoke injected")
    kw.setdefault("pypi_check", lambda project, version: True)
    kw.setdefault("pypi_digests", _matching_pypi(assets))
    # The `assets` fixture provides already-built distributions, which is the
    # staging job's input. Default to that mode so `step_build` does not clear
    # them and then find nothing to collect (the mock runner does not rebuild).
    # Tests that exercise the build/test steps themselves pass from_artifacts
    # explicitly.
    kw.setdefault("from_artifacts", True)
    return Pipeline("0.2.0", assets_dir=assets, **kw)


#: Local build evidence read from disk, never uploaded as release assets — so a
#: faithful release listing must not include them. The quality receipt joins
#: them: it is an *input* to staging, proving the gates ran at this commit, not
#: something the release publishes.
_LOCAL_ONLY_SIDECARS = (
    ".codesign.json",
    ".components.json",
    "quality-receipt.json",
    # Build receipts and the stage attestation are staging-side evidence. The
    # attestation in particular must NOT be a release asset: its whole purpose is
    # to reach the finalize job through a channel the draft cannot rewrite.
    "build-receipt-dist.json",
    "build-receipt-macos.json",
    "stage-attestation.json",
)


def _gh_for(assets: Path, *, is_draft=True, corrupt=None, drop=None, extra=None):
    """A gh stand-in that behaves like a release the assets were uploaded to.

    The listing reflects what `step_upload` actually uploads (``self.assets``):
    the distributions and the generated sbom/provenance/SHA256SUMS, but not the
    `.codesign.json`/`.components.json` sidecars, which are local evidence read
    from disk and never pushed to the release. `extra` injects an unexpected
    asset a prior staging attempt might have left behind.
    """

    def _uploaded_names():
        names = {
            path.name
            for path in assets.glob("*")
            if path.is_file()
            and path.name != drop
            and not path.name.endswith(_LOCAL_ONLY_SIDECARS)
        }
        if extra:
            names.add(extra)
        return names

    def gh(argv):
        verb = argv[1]
        if verb == "view" and "isDraft" in argv:
            return _completed(0, json.dumps({"isDraft": is_draft}).encode())
        if verb == "view":
            listing = [
                {
                    "name": name,
                    "size": (
                        (assets / name).stat().st_size
                        if (assets / name).is_file()
                        else 0
                    ),
                }
                for name in sorted(_uploaded_names())
            ]
            return _completed(0, json.dumps({"assets": listing}).encode())
        if verb == "download":
            pattern = argv[argv.index("--pattern") + 1]
            destination = Path(argv[argv.index("--dir") + 1])
            source = assets / pattern
            payload = source.read_bytes()
            if pattern == corrupt:
                payload = payload + b"-tampered"
            (destination / pattern).write_bytes(payload)
            return _completed()
        return _completed()

    return gh


# --------------------------------------------------------------------------
# the order is the safety property
# --------------------------------------------------------------------------


def test_publishing_is_the_last_step():
    """A package index does not forget a version, so everything that could
    stop a release has to run before the one step that cannot be undone."""
    assert STEPS[-1] == "publish"


def test_nothing_external_happens_before_everything_local_has_passed():
    first_external = min(STEPS.index(name) for name in EXTERNAL)
    for name in ("build", "test", "assets", "smoke", "sbom", "provenance", "verify"):
        assert STEPS.index(name) < first_external, f"{name} runs after going public"


def test_assets_are_verified_before_they_are_staged_and_again_after_upload():
    assert STEPS.index("verify") < STEPS.index("draft")
    assert STEPS.index("upload") < STEPS.index("reverify") < STEPS.index("publish")


def test_the_checksum_manifest_is_written_after_everything_it_covers():
    for produced in ("sbom", "provenance"):
        assert STEPS.index(produced) < STEPS.index("checksums")
    assert STEPS.index("checksums") < STEPS.index("upload")


def test_a_dry_run_touches_nothing_and_still_reports_every_step(assets):
    report = _pipeline(assets, dry_run=True).run()
    assert report["ok"] and report["published"] is False
    assert [step["step"] for step in report["steps"]] == list(STEPS)
    assert not (assets / "sbom.cdx.json").exists()


# --------------------------------------------------------------------------
# the installed daemon smoke crosses the default token gate
# --------------------------------------------------------------------------


def test_installed_daemon_smoke_bootstraps_and_loads_the_real_webui(
    monkeypatch, tmp_path
):
    """A healthy token-gated daemon must not look dead, or pass on health alone.

    The release smoke requested bare ``/`` after token auth became the default,
    received 401 forever, and reported that the installed daemon never served a
    page. Switching only to ``/health`` would hide the packaging failure this
    smoke exists to catch. Model both sides: unauthenticated root is genuinely
    refused, then the installed CLI's bootstrap URL must load the HTML shell and
    its JavaScript through the issued cookie.
    """
    import threading
    import urllib.error
    import urllib.parse
    import urllib.request
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    token = "release-smoke-token"
    observed: list[tuple[str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format, *_args):
            return

        def _reply(self, code, body=b"", content_type="text/plain", headers=()):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for name, value in headers:
                self.send_header(name, value)
            self.end_headers()
            if body:
                self.wfile.write(body)

        def do_GET(self):
            parsed = urllib.parse.urlsplit(self.path)
            cookie = self.headers.get("Cookie", "")
            observed.append((self.path, cookie))
            if parsed.path == "/health":
                self._reply(200, b'{"status":"ok"}', "application/json")
                return
            if parsed.path == "/" and urllib.parse.parse_qs(parsed.query).get(
                "token"
            ) == [token]:
                self._reply(
                    303,
                    headers=(
                        ("Location", "/"),
                        ("Set-Cookie", f"os_token={token}; Path=/; HttpOnly"),
                    ),
                )
                return
            authenticated = f"os_token={token}" in cookie
            if parsed.path == "/" and authenticated:
                self._reply(
                    200,
                    b'<title>OpenAI4S</title><div id="dashboard"></div>'
                    b'<script src="/static/app.js"></script>',
                    "text/html; charset=utf-8",
                )
                return
            if parsed.path == "/static/app.js" and authenticated:
                # Deliberately shares no source text with the real app.js: the
                # probe must judge the entrypoint by serving facts, and this
                # body fails the smoke if source-literal coupling comes back.
                self._reply(
                    200,
                    b"(() => { window.addEventListener('load', boot); })();",
                    "text/javascript; charset=utf-8",
                )
                return
            self._reply(401, b"unauthorized", "application/json")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}/"

    def installed_cli(argv, **_kwargs):
        assert argv[-4:] == ["-I", "-m", "openai4s", "url"]
        return _completed(0, f"{base_url}?token={token}\n".encode())

    monkeypatch.setattr(subprocess, "run", installed_cli)
    pipeline = Pipeline("0.2.0", assets_dir=tmp_path)
    try:
        with pytest.raises(urllib.error.HTTPError) as denied:
            urllib.request.urlopen(base_url, timeout=2)
        assert denied.value.code == 401, "the regression needs a real token gate"

        pipeline._probe_installed_daemon(
            Path("/installed/bin/python"), tmp_path, {}, base_url
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert any(path == "/health" for path, _cookie in observed)
    assert any(path == "/" and cookie for path, cookie in observed)
    assert any(path == "/static/app.js" and cookie for path, cookie in observed)


# --------------------------------------------------------------------------
# staging consumes; it does not produce
# --------------------------------------------------------------------------


def test_a_staging_run_does_not_rebuild_but_must_prove_the_suite_ran(assets):
    """Its inputs *are* an earlier verified build, so staging does not rebuild.

    But "does not re-test" used to mean "asserts the tests happened". The step
    returned `ok` with the sentence "not run: the suite gated the build that
    produced these artifacts" -- and the build job runs no suite at all. It
    checks out the tag, scans for secrets, builds, and verifies the wheel's
    metadata. That sentence was the only thing standing between a release and
    the claim that tests gated it, and it was false.

    Staging now consumes a receipt bound to the commit it is releasing.
    """
    ran: list[str] = []
    report = _pipeline(assets, from_artifacts=True, runner=_git_aware(ran.append)).run()

    assert report["ok"], report
    joined = " ".join(ran)
    assert "-m build" not in joined, "staging re-ran the build"
    assert "-m pytest" not in joined, "staging re-ran the suite"

    step = next(s for s in report["steps"] if s["step"] == "test")
    assert step["facts"]["source_sha"] == FAKE_HEAD

    from scripts import release_gates

    assert step["facts"]["gates"] == [g.name for g in release_gates.LOCAL_GATES]
    # The attested half has to survive too: without the check-run ids the
    # evidence bundle carries no pointer to the browser and Python matrices.
    assert len(step["facts"]["checks"]) == len(release_gates.CHECK_SUITE_GATES)

    for name in STAGING_SKIPPED:
        if name == "test":
            continue
        skipped = next(s for s in report["steps"] if s["step"] == name)
        assert skipped["facts"].get("from_artifacts") is True


def test_staging_refuses_a_receipt_that_is_not_for_these_sources(assets):
    """The binding is the whole value of the receipt.

    A document that records *a* SHA proves nothing unless the consumer
    re-derives the SHA it is actually releasing and compares. Without that it
    is another `identity_configured`: a field that reads as evidence and
    decides nothing.
    """
    _write_receipt(assets, source_sha="b" * 40)
    report = _pipeline(assets, from_artifacts=True).run()
    assert report["ok"] is False
    assert report["stopped_at"] == "test"
    failed = next(s for s in report["steps"] if s["step"] == "test")
    assert "did not run on these sources" in failed["detail"]


def test_staging_refuses_a_missing_receipt(assets):
    (assets / "quality-receipt.json").unlink()
    report = _pipeline(assets, from_artifacts=True).run()
    assert report["ok"] is False
    assert report["stopped_at"] == "test"
    failed = next(s for s in report["steps"] if s["step"] == "test")
    assert "no quality receipt" in failed["detail"]


def test_staging_refuses_a_receipt_whose_gates_failed(assets):
    """A receipt records exit codes and makes no judgement, so the consumer
    has to make one. Recording a failure and releasing anyway would be a more
    elaborate way of not checking."""
    _write_receipt(assets, failing=("harness-pr",))
    report = _pipeline(assets, from_artifacts=True).run()
    assert report["ok"] is False
    assert report["stopped_at"] == "test"
    failed = next(s for s in report["steps"] if s["step"] == "test")
    assert "harness-pr" in failed["detail"]


def test_a_distribution_that_changed_after_collection_stops_the_run(assets):
    pipeline = _pipeline(assets, from_artifacts=True)

    def rewrite(wheel):
        wheel.write_bytes(b"different-bytes")
        return "smoke"

    pipeline._smoke = rewrite
    report = pipeline.run()

    assert report["ok"] is False
    assert report["stopped_at"] == "verify"
    assert "different bytes" in report["steps"][-1]["detail"]


def test_stop_after_runs_a_prefix_and_only_runs_one_step(assets):
    staged = _pipeline(assets, stop_after="reverify").run()
    assert [s["step"] for s in staged["steps"]] == list(
        STEPS[: STEPS.index("reverify") + 1]
    )
    assert staged["published"] is False

    final = _pipeline(assets, mode="release", only="publish", gh=_gh_for(assets)).run()
    assert [s["step"] for s in final["steps"]] == ["publish"]


# --------------------------------------------------------------------------
# a failure stops it
# --------------------------------------------------------------------------


def test_a_failing_step_stops_the_pipeline_there(assets):
    def refuse(argv, cwd=None):
        if "build" in argv:
            return _completed(1, b"", b"no build backend")
        return _completed()

    # from_artifacts=False so the build step actually runs and can fail.
    pipeline = _pipeline(assets, from_artifacts=False, runner=refuse)
    report = pipeline.run()

    assert report["ok"] is False
    assert report["stopped_at"] == "build"
    assert [step["step"] for step in report["steps"]] == ["build"]
    assert pipeline.performed == [], "no step may be recorded as done after a stop"


def test_the_assets_dir_is_absolute_so_subprocesses_from_root_find_it(tmp_path):
    """The staging job passes `--assets-dir assets`, a sibling of the checkout,
    while `_run` executes from ROOT. A relative path made pip and gh look under
    ROOT/assets, where the wheel is not."""
    from scripts.release_pipeline import Pipeline

    pipe = Pipeline("0.2.0", assets_dir="assets")
    assert pipe.assets_dir.is_absolute(), (
        "a relative assets dir would resolve against each subprocess's cwd, "
        "not the directory the staging job actually populated"
    )


def test_the_build_uses_a_frontend_available_in_the_locked_environment(assets):
    """`python -m build` is not a locked dependency, so the documented
    `uv run python scripts/release_pipeline.py` failed to import it before any
    release check ran."""
    seen: list = []

    def runner(argv, cwd=None):
        seen.append([str(a) for a in argv])
        return _completed()

    _pipeline(assets, from_artifacts=False, runner=runner).run()
    build_cmds = [c for c in seen if "build" in c]
    assert build_cmds, "no build was invoked"
    assert build_cmds[0][0] == "uv", (
        "the build must use the uv frontend that exists in the locked env, "
        "not `python -m build`"
    )


def test_a_stale_distribution_from_a_previous_build_is_not_published(assets):
    """`dist` is reused; collecting every wheel it holds would smoke-test,
    hash and upload a previous version's artifacts."""
    (assets / "openai4s-0.1.9-py3-none-any.whl").write_bytes(b"old-wheel")
    report = _pipeline(assets, from_artifacts=True).run()
    assert report["ok"] is False
    assert report["stopped_at"] == "assets"
    assert "another version" in report["steps"][-1]["detail"]


def test_the_build_clears_stale_artifacts_before_building(tmp_path):
    """The non-staging path clears the directory, so a leftover cannot survive
    into the collected asset set."""
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "openai4s-0.1.9-py3-none-any.whl").write_bytes(b"old")

    def runner(argv, cwd=None):
        # Simulate the build writing this version's artifacts.
        (dist / "openai4s-0.2.0-py3-none-any.whl").write_bytes(b"new-wheel")
        (dist / "openai4s-0.2.0.tar.gz").write_bytes(b"new-sdist")
        return _completed()

    report = _pipeline(dist, from_artifacts=False, runner=runner).run()
    names = {
        Path(a).name
        for step in report["steps"]
        if step["step"] == "assets"
        for a in step["facts"].get("assets", [])
    }
    assert "openai4s-0.1.9-py3-none-any.whl" not in names, "the stale wheel survived"
    assert "openai4s-0.2.0-py3-none-any.whl" in names


def test_a_wheel_that_does_not_survive_a_clean_install_stops_the_run(assets):
    def broken(wheel):
        raise ReleaseError("the wheel does not install in a clean environment")

    report = _pipeline(assets, smoke=broken).run()
    assert report["ok"] is False
    assert report["stopped_at"] == "smoke"


def test_a_release_is_never_published_when_a_check_failed(assets):
    def refuse(argv, cwd=None):
        if "pytest" in " ".join(str(a) for a in argv):
            return _completed(1)
        return _completed()

    report = _pipeline(
        assets, mode="release", from_artifacts=False, runner=refuse
    ).run()
    assert report["published"] is False
    assert "publish" not in [step["step"] for step in report["steps"]]


def test_missing_assets_stop_the_run_before_anything_is_staged(tmp_path):
    empty = tmp_path / "dist"
    empty.mkdir()
    # A valid receipt, so this test still fails on the thing it names. Without
    # one the run stops earlier, at `test` -- also before anything is staged,
    # but that is the receipt gate's assertion, not this one's.
    _write_receipt(empty)
    report = _pipeline(empty).run()
    assert report["stopped_at"] == "assets"


# --------------------------------------------------------------------------
# signing: evidence, not configuration
# --------------------------------------------------------------------------


def test_release_mode_refuses_an_image_with_no_developer_id_signature(
    assets, monkeypatch
):
    """Setting the secret used to be enough. The build script only ad-hoc
    signs, so a configured identity made an ad-hoc image pass this gate as
    Developer-ID-signed — the exact outcome signing exists to prevent."""
    dmg = assets / "OpenAI4S-0.2.0-arm64.dmg"
    dmg.write_bytes(b"dmg-bytes")
    dmg.with_name(dmg.name + ".codesign.json").write_text(
        json.dumps({"authorities": [], "adhoc": True, "developer_id": False}),
        encoding="utf-8",
    )
    # A receipt, so this test still fails on the thing it names rather than on
    # the provenance gate that now runs first.
    _write_build_receipt(assets, "macos", [dmg])
    monkeypatch.setenv("OPENAI4S_MACOS_SIGNING_IDENTITY", "Developer ID: Example")

    report = _pipeline(assets, mode="release").run()

    assert report["ok"] is False
    assert report["stopped_at"] == "verify"
    assert "Developer ID Application" in report["steps"][-1]["detail"]


def test_a_real_developer_id_receipt_passes_the_gate(assets, monkeypatch):
    _signed_dmg(assets)
    monkeypatch.delenv("OPENAI4S_MACOS_SIGNING_IDENTITY", raising=False)

    report = _pipeline(assets, mode="release", gh=_gh_for(assets)).run()

    assert report["ok"], report
    verify = next(s for s in report["steps"] if s["step"] == "verify")
    signature = verify["facts"]["signatures"]["OpenAI4S-0.2.0-arm64.dmg"]
    assert signature["developer_id"] is True
    assert signature["source"] == "receipt"


def test_local_mode_builds_an_unsigned_image_without_pretending(assets, monkeypatch):
    """A laptop has no Developer ID, and the pipeline still has to be
    exercisable there — it just may not claim what it did not do."""
    dmg = assets / "OpenAI4S-0.2.0-arm64.dmg"
    dmg.write_bytes(b"dmg-bytes")
    dmg.with_name(dmg.name + ".codesign.json").write_text(
        json.dumps({"authorities": [], "adhoc": True, "developer_id": False}),
        encoding="utf-8",
    )
    _write_build_receipt(assets, "macos", [dmg])
    monkeypatch.delenv("OPENAI4S_MACOS_SIGNING_IDENTITY", raising=False)

    report = _pipeline(assets, mode="local").run()
    verify = next(s for s in report["steps"] if s["step"] == "verify")
    assert report["ok"] is True
    assert verify["facts"]["signatures"][dmg.name]["developer_id"] is False


def test_a_missing_receipt_is_not_read_as_a_signature(tmp_path):
    dmg = tmp_path / "x.dmg"
    dmg.write_bytes(b"not really an image")
    info = read_signature(dmg, lambda argv: _completed(1, b"", b""))
    assert info.get("developer_id") is not True


def test_a_developer_id_image_with_no_notarization_cannot_be_published(
    assets, monkeypatch
):
    """The gap the old gate left open, and the reason it was invisible.

    `step_verify` refused an ad-hoc image and passed anything carrying a
    Developer ID authority. `notarized` was hardcoded `None` and read by nothing.
    The release workflow already imports a signing certificate into a keychain
    when `MACOS_SIGNING_CERTIFICATE` is set, so the moment that secret exists a
    correctly signed, un-notarized DMG publishes — and Gatekeeper refuses it on
    a user's machine, which is the outcome a release gate is for.

    The remedy is notarize-or-omit; there is deliberately no downgrade label.
    """
    _signed_dmg(assets, notarized=False)
    report = _pipeline(assets, mode="release", gh=_gh_for(assets)).run()

    assert report["ok"] is False
    assert report["stopped_at"] == "verify"
    detail = report["steps"][-1]["detail"]
    assert "notarization" in detail
    assert "omit the macOS asset" in detail


def test_a_notarized_developer_id_image_is_publishable(assets, monkeypatch):
    """The success path, so the gate cannot pass by refusing everything.

    `verified` is reachable: it needs a stapled ticket bound to this image's
    digest. It used to be documented as unreachable, but the reason given was
    that this file hardcoded `None` — a statement about the pipeline, not about
    the image.
    """
    _signed_dmg(assets, notarized=True)
    report = _pipeline(assets, mode="release", gh=_gh_for(assets)).run()

    assert report["ok"], report
    verify = next(s for s in report["steps"] if s["step"] == "verify")
    assert verify["facts"]["signing_states"]["OpenAI4S-0.2.0-arm64.dmg"] == "verified"
    assert verify["facts"]["notarized"]["OpenAI4S-0.2.0-arm64.dmg"] is True
    assert verify["facts"]["macos_publishable"] is True


def test_a_stapler_result_from_another_image_does_not_notarize_this_one(assets):
    """The receipt is bound to a digest, so a copied stapler result proves
    nothing — the same trap the signature half already closed."""
    dmg = _signed_dmg(assets, notarized=True)
    payload = json.loads(dmg.with_name(dmg.name + ".codesign.json").read_text())
    payload["image_sha256"] = "0" * 64
    dmg.with_name(dmg.name + ".codesign.json").write_text(json.dumps(payload), "utf-8")

    report = _pipeline(assets, mode="release", gh=_gh_for(assets)).run()
    assert report["ok"] is False
    assert report["stopped_at"] == "verify"


def test_omitting_the_macos_asset_is_a_supported_release_shape(assets):
    """Without notary credentials the honest release carries no DMG at all.

    Recorded as `macos_asset: omitted` rather than left as an absence, and it
    must not require a `macos` build receipt for an image that is not there.
    """
    report = _pipeline(assets, mode="release", gh=_gh_for(assets)).run()
    assert report["ok"], report
    verify = next(s for s in report["steps"] if s["step"] == "verify")
    assert verify["facts"]["macos_asset"] == "omitted"
    assert verify["facts"]["macos_publishable"] is False


def test_a_stale_stapled_ticket_does_not_notarize_rebuilt_bytes(assets):
    """Stapling rewrites the DMG. A receipt that kept stapler_returncode==0
    from an earlier build, then pointed image_sha256 at new bytes, is a stale
    ticket: post_staple_sha256 no longer matches."""
    dmg = _signed_dmg(assets, notarized=True)
    payload = json.loads(dmg.with_name(dmg.name + ".codesign.json").read_text())
    payload["post_staple_sha256"] = "0" * 64
    dmg.with_name(dmg.name + ".codesign.json").write_text(json.dumps(payload), "utf-8")

    report = _pipeline(assets, mode="release", gh=_gh_for(assets)).run()
    assert report["ok"] is False
    assert report["stopped_at"] == "verify"
    detail = report["steps"][-1]["detail"]
    assert "notarization" in detail


def test_a_notarized_receipt_without_post_staple_digest_is_not_verified(assets):
    """The field is load-bearing: omitting it is how a pre-staple receipt
    would look, and it must not satisfy the gate."""
    dmg = _signed_dmg(assets, notarized=True)
    payload = json.loads(dmg.with_name(dmg.name + ".codesign.json").read_text())
    payload.pop("post_staple_sha256", None)
    dmg.with_name(dmg.name + ".codesign.json").write_text(json.dumps(payload), "utf-8")

    info = read_signature(dmg, lambda argv: _completed())
    assert info["notarized"] is False
    assert info["post_staple_digest_matches"] is False


def test_verify_records_post_staple_digest_and_assessment_returncodes(assets):
    _signed_dmg(assets, notarized=True)
    report = _pipeline(assets, mode="release", gh=_gh_for(assets)).run()
    assert report["ok"], report
    verify = next(s for s in report["steps"] if s["step"] == "verify")
    name = "OpenAI4S-0.2.0-arm64.dmg"
    assert verify["facts"]["post_staple_sha256"][name] == sha256_file(assets / name)
    assert verify["facts"]["stapler_returncode"][name] == 0
    assert verify["facts"]["spctl_returncode"][name] == 0
    assert verify["facts"]["macos_asset"] == "present"


def test_staging_records_the_linux_full_boundary_check_run_id(assets):
    pipeline = _pipeline(assets, mode="release", gh=_gh_for(assets))
    result = pipeline.step_test()
    assert result.ok
    assert result.facts["linux_sandbox_full_check_run_id"]
    names = {row["name"] for row in result.facts["checks"]}
    assert "ci-linux-sandbox-full" in names


# --------------------------------------------------------------------------
# the documents
# --------------------------------------------------------------------------


def test_the_sbom_names_the_assets_and_their_digests(assets):
    _pipeline(assets).run()
    document = json.loads((assets / "sbom.cdx.json").read_text())

    assert document["bomFormat"] == "CycloneDX"
    referenced = {
        ref["url"]: ref["hashes"][0]["content"]
        for ref in document["externalReferences"]
    }
    wheel = assets / "openai4s-0.2.0-py3-none-any.whl"
    assert referenced[wheel.name] == sha256_file(wheel)


def test_the_sbom_components_come_from_the_wheel_and_the_image(assets):
    """Not from the machine assembling the release, which on the staging job is
    an Ubuntu runner with none of this installed."""
    import zipfile

    wheel = assets / "openai4s-0.2.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(
            "openai4s-0.2.0.dist-info/METADATA",
            "Metadata-Version: 2.1\nName: openai4s\nVersion: 0.2.0\n"
            "Requires-Dist: numpy>=1.26 ; extra == 'science'\n",
        )
    dmg = _signed_dmg(assets)
    dmg.with_name(dmg.name + ".components.json").write_text(
        json.dumps(
            {
                "packages": [{"name": "scipy", "version": "1.14.0"}],
                # Bound to the exact image, as describe_macos_image writes it.
                "image_sha256": sha256_file(dmg),
            }
        ),
        encoding="utf-8",
    )

    _pipeline(assets).run()
    document = json.loads((assets / "sbom.cdx.json").read_text())
    names = {component["name"] for component in document["components"]}

    assert "openai4s" in names, "the shipped component is missing from its own SBOM"
    assert "scipy" in names, "the image's embedded runtime is not described"
    assert "numpy" in names


def test_an_image_with_no_component_inventory_is_named_not_omitted(assets):
    _signed_dmg(assets)
    _pipeline(assets).run()
    document = json.loads((assets / "sbom.cdx.json").read_text())
    properties = document["metadata"].get("properties") or []
    assert any("components-unread" in p["name"] for p in properties)


def test_a_components_sidecar_from_a_different_image_is_not_trusted(assets):
    """Codex P2: a `.components.json` left by an earlier rebuild with the same
    filename carries no binding to the bytes it describes. Its package list must
    not enter the SBOM for a different image; a stale/mismatched digest is read
    as unread, not trusted."""
    import zipfile

    wheel = assets / "openai4s-0.2.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(
            "openai4s-0.2.0.dist-info/METADATA",
            "Metadata-Version: 2.1\nName: openai4s\nVersion: 0.2.0\n",
        )
    dmg = _signed_dmg(assets)
    dmg.with_name(dmg.name + ".components.json").write_text(
        json.dumps(
            {
                "packages": [{"name": "ghost-pkg", "version": "9.9.9"}],
                # A digest for some *other* image — the binding does not match.
                "image_sha256": "0" * 64,
            }
        ),
        encoding="utf-8",
    )

    _pipeline(assets).run()
    document = json.loads((assets / "sbom.cdx.json").read_text())
    names = {component["name"] for component in document["components"]}
    assert "ghost-pkg" not in names, "a mismatched-image inventory was trusted"
    properties = document["metadata"].get("properties") or []
    assert any(
        "components-unread" in p["name"] for p in properties
    ), "the mismatched sidecar should be reported unread, not silently dropped"


def test_the_provenance_points_at_the_repository_this_source_lives_in(
    assets, monkeypatch
):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.delenv("GITHUB_SERVER_URL", raising=False)
    _pipeline(
        assets,
        runner=lambda argv, cwd=None: (
            _completed(0, b"git@github.com:PKU-YuanGroup/OpenAI4S.git\n")
            if "remote.origin.url" in " ".join(str(a) for a in argv)
            else _completed(0, FAKE_HEAD.encode())
        ),
    ).run()
    document = json.loads((assets / "provenance.intoto.json").read_text())
    uri = document["predicate"]["buildDefinition"]["resolvedDependencies"][0]["uri"]

    assert (
        "openai4s/openai4s" not in uri
    ), "the attestation pointed consumers at a repository this project is not"
    assert "PKU-YuanGroup/OpenAI4S" in uri


def test_the_canonical_uri_prefers_the_running_repository(monkeypatch):
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", "PKU-YuanGroup/OpenAI4S")
    assert canonical_source_uri() == "git+https://github.com/PKU-YuanGroup/OpenAI4S"


def test_the_pyproject_fallback_matches_the_host_and_not_a_substring(
    monkeypatch, tmp_path
):
    """CodeQL `py/incomplete-url-substring-sanitization`, and it is right.

    The fallback selected a line by `"github.com" in line`, which also accepts
    `github.com.example.net` and `evil-github.com` — and the value it selects is
    written into a *signed* provenance statement as the place a consumer should
    go to find this source. That is the one field in the document whose whole
    job is to be trustworthy, so it is matched on hostname.
    """
    import scripts.release_pipeline as pipeline_mod

    monkeypatch.delenv("GITHUB_SERVER_URL", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)

    def no_origin(*_a, **_kw):
        return _completed(1)

    fake_root = tmp_path / "repo"
    fake_root.mkdir()
    (fake_root / "pyproject.toml").write_text(
        'Homepage = "https://github.com.evil.example/PKU-YuanGroup/OpenAI4S"\n'
        'Source = "https://evil-github.com/PKU-YuanGroup/OpenAI4S"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(pipeline_mod, "ROOT", fake_root)

    with pytest.raises(ReleaseError, match="could not be determined"):
        canonical_source_uri(runner=no_origin)

    # ...and the genuine host still resolves, or the guard ate the feature.
    (fake_root / "pyproject.toml").write_text(
        'Source = "https://github.com/PKU-YuanGroup/OpenAI4S"\n', encoding="utf-8"
    )
    assert (
        canonical_source_uri(runner=no_origin)
        == "git+https://github.com/PKU-YuanGroup/OpenAI4S"
    )


def test_the_pyproject_fallback_skips_a_line_it_cannot_parse(monkeypatch, tmp_path):
    """A malformed URL is fail-closed, never a traceback out of a signing step.

    `urlsplit` raises on a malformed IPv6 authority, and `.hostname` normalises
    the netloc and can raise too. A line the parser chokes on must be skipped
    exactly as a substring miss is — the function's whole job is to refuse to
    sign a guess, so a crash on the way to that refusal is a regression. A
    genuine host on a later line still has to resolve, or the guard ate the
    feature it protects.
    """
    import scripts.release_pipeline as pipeline_mod

    monkeypatch.delenv("GITHUB_SERVER_URL", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)

    def no_origin(*_a, **_kw):
        return _completed(1)

    fake_root = tmp_path / "repo"
    fake_root.mkdir()
    # The first line makes `urlsplit` raise (Invalid IPv6 URL); the second is
    # the real one and must still be reached.
    (fake_root / "pyproject.toml").write_text(
        'Homepage = "https://[oops/PKU-YuanGroup/OpenAI4S"\n'
        'Source = "https://github.com/PKU-YuanGroup/OpenAI4S"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(pipeline_mod, "ROOT", fake_root)
    assert (
        canonical_source_uri(runner=no_origin)
        == "git+https://github.com/PKU-YuanGroup/OpenAI4S"
    )

    # ...and a malformed line on its own reaches the documented refusal rather
    # than propagating the parser's ValueError.
    (fake_root / "pyproject.toml").write_text(
        'Homepage = "https://[oops/PKU-YuanGroup/OpenAI4S"\n', encoding="utf-8"
    )
    with pytest.raises(ReleaseError, match="could not be determined"):
        canonical_source_uri(runner=no_origin)


def test_the_pyproject_fallback_survives_a_hostname_that_raises(monkeypatch, tmp_path):
    """The guard covers the authority normalisation, not only the split.

    `urlsplit` is not the only place a ValueError can come from on the way to a
    host decision — `.hostname` normalises the netloc and can raise too. The fix
    put the whole parse-and-match inside the one `try`, so this forces the
    `.hostname` half to raise (which a plain input cannot reliably do across
    Python versions) and asserts the line is skipped, exactly as an `urlsplit`
    failure is. Without the broadened guard this ValueError would propagate out
    of a signing step; the earlier test cannot catch that, because its input
    fails at `urlsplit` and never reaches `.hostname`.
    """
    import scripts.release_pipeline as pipeline_mod

    monkeypatch.delenv("GITHUB_SERVER_URL", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)

    def no_origin(*_a, **_kw):
        return _completed(1)

    real_urlsplit = pipeline_mod.urllib.parse.urlsplit

    class _HostnameRaises:
        scheme = "https"

        @property
        def hostname(self):
            raise ValueError("simulated authority-normalisation failure")

    def fake_urlsplit(value):
        # Only the sentinel line reaches `.hostname` and blows up there; the
        # genuine line splits for real so the feature still has to resolve it.
        if "raise-on-hostname" in value:
            return _HostnameRaises()
        return real_urlsplit(value)

    monkeypatch.setattr(pipeline_mod.urllib.parse, "urlsplit", fake_urlsplit)

    fake_root = tmp_path / "repo"
    fake_root.mkdir()
    (fake_root / "pyproject.toml").write_text(
        'Homepage = "https://raise-on-hostname/PKU-YuanGroup/OpenAI4S"\n'
        'Source = "https://github.com/PKU-YuanGroup/OpenAI4S"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(pipeline_mod, "ROOT", fake_root)
    assert (
        canonical_source_uri(runner=no_origin)
        == "git+https://github.com/PKU-YuanGroup/OpenAI4S"
    )

    # ...and on its own it reaches the refusal, not the raised ValueError.
    (fake_root / "pyproject.toml").write_text(
        'Homepage = "https://raise-on-hostname/PKU-YuanGroup/OpenAI4S"\n',
        encoding="utf-8",
    )
    with pytest.raises(ReleaseError, match="could not be determined"):
        canonical_source_uri(runner=no_origin)


def test_the_provenance_binds_the_digests_and_claims_no_author(assets):
    _pipeline(assets).run()
    document = json.loads((assets / "provenance.intoto.json").read_text())

    subjects = {item["name"]: item["digest"]["sha256"] for item in document["subject"]}
    wheel = assets / "openai4s-0.2.0-py3-none-any.whl"
    assert subjects[wheel.name] == sha256_file(wheel)
    assert document["predicate"]["unsigned"] is True
    assert "does not establish who produced" in document["predicate"]["note"]


def test_an_sbom_with_nothing_to_list_is_still_honest():
    document = build_sbom([], version="0.2.0", packages=[])
    assert document["components"] == []
    assert document["metadata"]["component"]["version"] == "0.2.0"


def test_provenance_subjects_are_sorted_so_two_runs_agree(tmp_path):
    first = tmp_path / "b.whl"
    second = tmp_path / "a.whl"
    first.write_bytes(b"1")
    second.write_bytes(b"2")
    document = build_provenance(
        [first, second], version="0.2.0", source={"uri": "x", "digest": {}}
    )
    assert [item["name"] for item in document["subject"]] == ["a.whl", "b.whl"]


def test_the_wheel_metadata_is_where_the_component_list_comes_from(tmp_path):
    import zipfile

    wheel = tmp_path / "openai4s-0.2.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(
            "openai4s-0.2.0.dist-info/METADATA",
            "Name: openai4s\nVersion: 0.2.0\nRequires-Dist: rich>=13\n",
        )
    components = wheel_components(wheel)
    assert components[0] == {"name": "openai4s", "version": "0.2.0", "scope": "shipped"}
    assert {"name": "rich", "version": "", "scope": "declared-dependency"} in components


# --------------------------------------------------------------------------
# checksums cover everything, and ship
# --------------------------------------------------------------------------


def test_the_checksum_manifest_covers_every_asset_and_is_itself_uploaded(assets):
    uploaded: list[str] = []

    def gh(argv):
        if argv[1] == "upload":
            uploaded.extend(Path(a).name for a in argv[3:] if not a.startswith("--"))
        return _gh_for(assets)(argv)

    _signed_dmg(assets)
    pipeline = _pipeline(assets, mode="release", gh=gh)
    report = pipeline.run()
    assert report["ok"], report

    manifest = (assets / "SHA256SUMS").read_text("utf-8")
    for name in ("sbom.cdx.json", "provenance.intoto.json", "openai4s-0.2.0.tar.gz"):
        assert name in manifest, f"{name} shipped unhashed"
    assert "SHA256SUMS" in uploaded, "the manifest itself was never uploaded"


# --------------------------------------------------------------------------
# staging and the read-back
# --------------------------------------------------------------------------


def test_the_draft_must_already_exist_and_still_be_a_draft(assets):
    def gh(argv):
        if argv[1] == "view" and "isDraft" in argv:
            return _completed(0, json.dumps({"isDraft": False}).encode())
        return _completed()

    report = _pipeline(assets, mode="release", gh=gh).run()
    assert report["ok"] is False
    assert report["stopped_at"] == "draft"
    assert "already public" in report["steps"][-1]["detail"]


def test_an_upload_that_lost_an_asset_stops_before_publish(assets):
    _signed_dmg(assets)
    report = _pipeline(
        assets, mode="release", gh=_gh_for(assets, drop="openai4s-0.2.0.tar.gz")
    ).run()
    assert report["ok"] is False
    assert report["stopped_at"] == "reverify"
    assert report["published"] is False


def test_an_asset_whose_name_survived_but_whose_bytes_did_not_is_caught(assets):
    """The check compared filenames, so a truncated or replaced asset passed."""
    _signed_dmg(assets)
    report = _pipeline(
        assets,
        mode="release",
        gh=_gh_for(assets, corrupt="openai4s-0.2.0-py3-none-any.whl"),
    ).run()

    assert report["ok"] is False
    assert report["stopped_at"] == "reverify"
    assert "do not match" in report["steps"][-1]["detail"]


def test_an_unexpected_asset_left_in_the_draft_stops_before_publish(assets):
    """Codex P1: `gh release upload --clobber` overwrites matching names but
    leaves anything extra in place, and the old one-way name check still passed.
    A leftover asset from an earlier staging attempt would then be published
    without appearing in checksums, provenance, or the read-back."""
    _signed_dmg(assets)
    report = _pipeline(
        assets,
        mode="release",
        gh=_gh_for(assets, extra="openai4s-0.1.9-py3-none-any.whl"),
    ).run()

    assert report["ok"] is False
    assert report["stopped_at"] == "reverify"
    assert report["published"] is False
    assert "did not produce" in report["steps"][-1]["detail"]


# --------------------------------------------------------------------------
# the cross-channel order
# --------------------------------------------------------------------------


def test_the_github_flip_waits_for_the_package_to_be_on_pypi(assets):
    """Flipping first left a public release with no matching package version
    whenever OIDC, the environment approval or the upload failed."""
    _signed_dmg(assets)
    report = _pipeline(
        assets,
        mode="release",
        gh=_gh_for(assets),
        pypi_check=lambda project, version: False,
    ).run()

    assert report["ok"] is False
    assert report["stopped_at"] == "publish"
    assert "not on PyPI" in report["steps"][-1]["detail"]
    assert "draft is untouched" in report["steps"][-1]["detail"]


def test_a_complete_release_publishes_last(assets):
    _signed_dmg(assets)
    calls: list[list[str]] = []

    inner = _gh_for(assets)

    def gh(argv):
        calls.append(list(argv))
        return inner(argv)

    report = _pipeline(assets, mode="release", gh=gh).run()

    assert report["ok"] and report["published"] is True
    verbs = [call[1] for call in calls]
    assert verbs.index("upload") < verbs.index("edit")
    assert calls[-1][-1] == "--draft=false", "publishing is the final act"


def _write_checksums(assets: Path) -> None:
    """A SHA256SUMS covering the uploaded assets, as step_checksums writes it.

    Excludes the local-only sidecars (they are never uploaded), so the manifest
    matches the release listing `_gh_for` serves.
    """
    lines = []
    for path in sorted(assets.glob("*")):
        if path.name == "SHA256SUMS" or path.name.endswith(_LOCAL_ONLY_SIDECARS):
            continue
        lines.append(f"{sha256_file(path)}  {path.name}\n")
    (assets / "SHA256SUMS").write_text("".join(lines), encoding="utf-8")


def test_the_finalize_step_revalidates_the_draft_before_the_flip(assets):
    """The documented compensation — PyPI has it, the draft is still a draft —
    but it must not flip blind. `--only publish` runs standalone after an
    approval delay; a draft asset deleted or replaced since staging would be made
    public unverified. It re-hashes the draft against its own SHA256SUMS first,
    then flips."""
    _signed_dmg(assets)
    _write_checksums(assets)
    calls: list[list[str]] = []
    inner = _gh_for(assets)

    def gh(argv):
        calls.append(list(argv))
        return inner(argv)

    report = _pipeline(assets, mode="release", only="publish", gh=gh).run()

    assert report["ok"] is True and report["published"] is True
    verbs = [call[1] for call in calls]
    assert "download" in verbs, "finalize published without re-hashing the draft"
    assert verbs[-1] == "edit" and calls[-1][-1] == "--draft=false"


def test_finalize_refuses_to_publish_a_draft_asset_replaced_since_staging(assets):
    """Codex P1: the exact risk finalize re-validation exists for. A draft asset
    whose bytes were replaced between attach and the flip must stop the publish,
    not go public unverified."""
    _signed_dmg(assets)
    _write_checksums(assets)
    # The wheel in the draft no longer matches its verified digest.
    gh = _gh_for(assets, corrupt="openai4s-0.2.0-py3-none-any.whl")

    report = _pipeline(assets, mode="release", only="publish", gh=gh).run()

    assert report["ok"] is False
    assert report["stopped_at"] == "publish"
    assert report["published"] is False
    assert "no longer matches" in report["steps"][-1]["detail"]


def test_finalize_refuses_when_the_draft_diverges_from_what_pypi_published(assets):
    """Codex P1: the SHA256SUMS the finalizer re-hashes against comes from the
    same *mutable* draft, so a second staging run for this tag that clobbered
    both the assets and the manifest would self-validate — while PyPI already
    holds the first run's bytes. PyPI is immutable per version, so it decides."""
    _signed_dmg(assets)
    _write_checksums(assets)
    sdist = assets / "openai4s-0.2.0.tar.gz"

    # The sdist matches, but PyPI holds *different* wheel bytes — another run
    # staged this tag.
    def diverging(project, version):
        return {
            "openai4s-0.2.0-py3-none-any.whl": "f" * 64,
            sdist.name: sha256_file(sdist),
        }

    report = _pipeline(
        assets,
        mode="release",
        only="publish",
        gh=_gh_for(assets),
        pypi_digests=diverging,
    ).run()

    assert report["ok"] is False
    assert report["stopped_at"] == "publish"
    assert report["published"] is False
    assert "disagree with what PyPI" in report["steps"][-1]["detail"]


def test_finalize_publishes_when_the_draft_matches_pypi(assets):
    """The same anchor must not block the normal path: matching digests publish."""
    _signed_dmg(assets)
    _write_checksums(assets)

    report = _pipeline(
        assets, mode="release", only="publish", gh=_gh_for(assets)
    ).run()  # default pypi_digests match the local dists

    assert report["ok"] is True and report["published"] is True


def test_finalize_refuses_when_pypi_returns_no_digests(assets):
    """Codex P1: the old `name in published` guard skipped every file when PyPI
    returned nothing, publishing unverified. An empty response is not a match —
    there is nothing to anchor against, so fail closed."""
    _signed_dmg(assets)
    _write_checksums(assets)

    report = _pipeline(
        assets,
        mode="release",
        only="publish",
        gh=_gh_for(assets),
        pypi_digests=lambda project, version: {},  # nothing to anchor against
    ).run()

    assert report["ok"] is False
    assert report["stopped_at"] == "publish"
    assert report["published"] is False
    assert "no file digests" in report["steps"][-1]["detail"]


def test_finalize_refuses_when_pypi_is_missing_a_distribution(assets):
    """A partial upload — the wheel landed but not the sdist — must not let the
    missing file ride onto GitHub unverified."""
    _signed_dmg(assets)
    _write_checksums(assets)
    wheel = assets / "openai4s-0.2.0-py3-none-any.whl"

    report = _pipeline(
        assets,
        mode="release",
        only="publish",
        gh=_gh_for(assets),
        # only the wheel is on PyPI; the sdist is missing
        pypi_digests=lambda project, version: {wheel.name: sha256_file(wheel)},
    ).run()

    assert report["ok"] is False
    assert report["stopped_at"] == "publish"
    assert report["published"] is False
    assert "do not carry the same" in report["steps"][-1]["detail"]
    assert "PyPI is missing" in report["steps"][-1]["detail"]


def test_finalize_refuses_a_draft_rewritten_to_drop_its_distributions(assets):
    """Codex P1: the one-way difference, and the direction it misses.

    The draft is mutable. Rewrite it after the PyPI upload to remove the wheel
    *and* the sdist — rewriting SHA256SUMS to match, which anyone able to
    replace the assets can also do — and `_revalidate_draft_from_checksums()`
    happily self-validates the reduced set. `draft_dists - published` is then
    empty, so with a valid immutable PyPI version the finalizer published a
    GitHub release whose distributions are simply absent, while PyPI says
    exactly what should have been there.
    """
    _signed_dmg(assets)
    wheel = assets / "openai4s-0.2.0-py3-none-any.whl"
    sdist = assets / "openai4s-0.2.0.tar.gz"
    # PyPI is immutable and still holds both.
    immutable = {wheel.name: sha256_file(wheel), sdist.name: sha256_file(sdist)}
    wheel.unlink()
    sdist.unlink()
    _write_checksums(assets)  # the manifest is rewritten to cover the rest

    report = _pipeline(
        assets,
        mode="release",
        only="publish",
        gh=_gh_for(assets),
        pypi_digests=lambda project, version: immutable,
    ).run()

    assert report["ok"] is False
    assert report["stopped_at"] == "publish"
    assert report["published"] is False
    detail = report["steps"][-1]["detail"]
    assert "do not carry the same" in detail
    assert "the draft is missing" in detail
    assert wheel.name in detail and sdist.name in detail


def test_finalize_refuses_a_draft_that_dropped_only_the_wheel(assets):
    """The partial form of the same rewrite: one distribution removed, the
    other still matching. A subset must not read as agreement."""
    _signed_dmg(assets)
    wheel = assets / "openai4s-0.2.0-py3-none-any.whl"
    sdist = assets / "openai4s-0.2.0.tar.gz"
    immutable = {wheel.name: sha256_file(wheel), sdist.name: sha256_file(sdist)}
    wheel.unlink()
    _write_checksums(assets)

    report = _pipeline(
        assets,
        mode="release",
        only="publish",
        gh=_gh_for(assets),
        pypi_digests=lambda project, version: immutable,
    ).run()

    assert report["ok"] is False
    assert report["published"] is False
    assert wheel.name in report["steps"][-1]["detail"]


def test_finalize_anchors_only_python_distributions_not_every_tarball(assets):
    """The desktop bundles ride on the draft; PyPI can never hold them.

    The anchor set was `endswith((".whl", ".tar.gz"))`, written when the sdist
    was the only tarball a draft carried. The Linux desktop bundle is
    `OpenAI4S-<version>-linux-<arch>.tar.gz`, so suffix-matching swept it into
    a set every member of which must be present on PyPI — a condition it can
    never satisfy.

    The ordering is what made that fatal rather than annoying: `finalize`
    `needs: [attach, pypi]`, and `--only publish` runs nowhere else. The
    immutable PyPI version is consumed first, and only then does the flip
    refuse, leaving the GitHub release a permanent draft that re-staging
    reproduces exactly.

    Note the Linux bundle differs from the sdist only in case
    (`OpenAI4S-` vs PEP 625's `openai4s-`), which is why the fix compares the
    sdist name exactly instead of case-folding a prefix.
    """
    _signed_dmg(assets)
    (assets / "OpenAI4S-0.2.0-linux-x86_64.tar.gz").write_bytes(b"linux-bundle")
    (assets / "OpenAI4S-0.2.0-windows-x86_64.zip").write_bytes(b"windows-zip")
    _write_checksums(assets)
    wheel = assets / "openai4s-0.2.0-py3-none-any.whl"
    sdist = assets / "openai4s-0.2.0.tar.gz"

    report = _pipeline(
        assets,
        mode="release",
        only="publish",
        gh=_gh_for(assets),
        # PyPI holds exactly what PyPI can hold: the wheel and the sdist.
        pypi_digests=lambda project, version: {
            wheel.name: sha256_file(wheel),
            sdist.name: sha256_file(sdist),
        },
    ).run()

    assert report["ok"] is True, report["steps"][-1]
    assert report["published"] is True


def test_finalize_still_refuses_when_the_sdist_itself_is_missing_from_pypi(assets):
    """The narrowed anchor must not become a hole.

    Matching the sdist by exact name rather than by suffix is only safe if the
    sdist is still anchored. A bundle beside it must not make the check lenient.
    """
    _signed_dmg(assets)
    (assets / "OpenAI4S-0.2.0-linux-x86_64.tar.gz").write_bytes(b"linux-bundle")
    _write_checksums(assets)
    wheel = assets / "openai4s-0.2.0-py3-none-any.whl"

    report = _pipeline(
        assets,
        mode="release",
        only="publish",
        gh=_gh_for(assets),
        pypi_digests=lambda project, version: {wheel.name: sha256_file(wheel)},
    ).run()

    assert report["ok"] is False
    assert report["published"] is False
    assert "openai4s-0.2.0.tar.gz" in report["steps"][-1]["detail"]


def test_finalize_refuses_a_draft_missing_its_checksum_manifest(assets):
    """Without SHA256SUMS there is nothing to re-validate against; publishing
    then would be a blind flip."""
    _signed_dmg(assets)  # but no SHA256SUMS written to the draft

    report = _pipeline(assets, mode="release", only="publish", gh=_gh_for(assets)).run()

    assert report["ok"] is False
    assert report["stopped_at"] == "publish"
    assert "SHA256SUMS" in report["steps"][-1]["detail"]


# --------------------------------------------------------------------------
# a signature that does not verify is not a signature; a version is exact
# --------------------------------------------------------------------------


def test_a_receipt_whose_deep_verification_failed_is_not_developer_id(tmp_path):
    """describe_macos_image records both the authorities and whether
    `codesign --verify` succeeded. A Developer ID authority string with a
    failed deep verification is a broken signature, not a valid one."""
    from scripts.release_pipeline import SIGNATURE_RECEIPT_SUFFIX, read_signature

    dmg = tmp_path / "x.dmg"
    dmg.write_bytes(b"img")
    dmg.with_name(dmg.name + SIGNATURE_RECEIPT_SUFFIX).write_text(
        json.dumps(
            {
                "authorities": ["Developer ID Application: Example Inc"],
                "adhoc": False,
                "verify_returncode": 1,  # the deep verification FAILED
            }
        ),
        encoding="utf-8",
    )
    info = read_signature(dmg, lambda argv: _completed())
    assert info["developer_id"] is False, (
        "a Developer ID authority with a failed deep verification must not "
        "count as signed"
    )


def test_release_mode_rejects_an_image_whose_signature_does_not_verify(assets):
    dmg = assets / "OpenAI4S-0.2.0-arm64.dmg"
    dmg.write_bytes(b"dmg")
    from scripts.release_pipeline import SIGNATURE_RECEIPT_SUFFIX

    dmg.with_name(dmg.name + SIGNATURE_RECEIPT_SUFFIX).write_text(
        json.dumps(
            {
                "authorities": ["Developer ID Application: Example Inc"],
                "adhoc": False,
                "verify_returncode": 1,
            }
        ),
        encoding="utf-8",
    )
    report = _pipeline(assets, mode="release").run()
    assert report["ok"] is False
    assert report["stopped_at"] == "verify"


@pytest.mark.parametrize(
    "filename,version,matches",
    [
        ("openai4s-0.2.0-py3-none-any.whl", "0.2.0", True),
        ("openai4s-0.2.0.tar.gz", "0.2.0", True),
        ("OpenAI4S-0.2.0-arm64.dmg", "0.2.0", True),
        ("openai4s-0.2.0rc1-py3-none-any.whl", "0.2.0", False),
        ("openai4s-10.2.0-py3-none-any.whl", "0.2.0", False),
        ("openai4s-0.2.0.post1.tar.gz", "0.2.0", False),
    ],
)
def test_the_version_is_matched_exactly_not_as_a_substring(filename, version, matches):
    from scripts.release_pipeline import _asset_version

    assert (_asset_version(filename) == version) is matches


def test_a_prerelease_leftover_does_not_stage_for_the_final_tag(assets):
    """The substring guard let `0.2.0rc1` satisfy a `0.2.0` release on the
    staging path, where it is the only version check."""
    (assets / "openai4s-0.2.0rc1-py3-none-any.whl").write_bytes(b"prerelease")
    report = _pipeline(assets, from_artifacts=True).run()
    assert report["ok"] is False
    assert report["stopped_at"] == "assets"
    assert "another version" in report["steps"][-1]["detail"]


def test_a_receipt_from_a_different_image_does_not_sign_this_one(tmp_path):
    """A stale or copied receipt from any signed image must not vouch for a
    different DMG: the receipt records the image digest, and the gate re-hashes
    and requires a match."""
    from scripts.release_pipeline import SIGNATURE_RECEIPT_SUFFIX, read_signature

    dmg = tmp_path / "unsigned.dmg"
    dmg.write_bytes(b"a-different-unsigned-image")
    dmg.with_name(dmg.name + SIGNATURE_RECEIPT_SUFFIX).write_text(
        json.dumps(
            {
                "authorities": ["Developer ID Application: Example Inc"],
                "adhoc": False,
                "verify_returncode": 0,
                # The digest of some *other* image, not this one.
                "image_sha256": "0" * 64,
            }
        ),
        encoding="utf-8",
    )
    info = read_signature(dmg, lambda argv: _completed())
    assert info["developer_id"] is False, (
        "a receipt whose recorded digest does not match this image must not "
        "vouch for it"
    )
    assert info["image_digest_matches"] is False


# --------------------------------------------------------------------------
# D11: the signing state vocabulary
# --------------------------------------------------------------------------


def test_the_signing_state_is_read_from_evidence_and_never_from_configuration():
    """Four scattered fields became one named state.

    `developer_id`, `adhoc`, `identity_configured` and `notarized: None` each
    said part of the answer, and a reader had to assemble it -- a reader who
    assembles it wrongly being exactly who this is for.

    Crucially it does not consult `OPENAI4S_MACOS_SIGNING_IDENTITY`. Treating a
    configured secret as proof of a signature is the specific mistake that once
    let an ad-hoc image pass the release gate as Developer-ID-signed.
    """
    from scripts.release_pipeline import SIGNING_STATES, signing_state

    assert signing_state({"developer_id": True, "notarized": True}) == "verified"
    assert signing_state({"developer_id": True, "notarized": None}) == "not_notarized"
    assert signing_state({"developer_id": False, "adhoc": True}) == "preview"
    assert signing_state({"developer_id": False, "adhoc": False}) == "not_configured"
    # Unreadable evidence is not evidence.
    assert signing_state({"error": "unreadable receipt"}) == "not_configured"
    assert signing_state(None) == "not_configured"

    for payload in ({"developer_id": True}, {"adhoc": True}, {}, None):
        assert signing_state(payload) in SIGNING_STATES


def test_verified_requires_a_ticket_and_the_build_script_alone_cannot_reach_it():
    """D11 froze the policy: no loosening. This states what that policy *is*.

    The previous version of this test asserted `'"notarized": None' in pipeline`
    -- a substring of the source, checking that the gate did not exist. Its own
    docstring said it would fail once notarization was wired up so the claim
    would be revisited; that is what happened, so the claim is revised here
    rather than the assertion relaxed.

    What is true now: `verified` needs a stapled ticket. `build_macos_dmg.sh`
    uses Developer ID when configured and otherwise ad-hoc signs, but it never
    submits to Apple's notary service, so the image it produces alone is either
    `preview` or `not_notarized` and a release carrying it is refused. What
    changed is that a *Developer-ID-signed* image is now refused too unless it
    is notarized -- previously it passed.
    """
    from scripts.release_pipeline import signing_state

    build = Path("scripts/build_macos_dmg.sh").read_text("utf-8")
    assert "codesign" in build
    # No notarization submission in the build script: `verified` cannot be
    # reached by building alone. The notary script is a separate file.
    assert "notarytool" not in build
    notary = Path("scripts/notarize_macos_dmg.sh").read_text("utf-8")
    assert "notarytool submit" in notary
    assert "--wait" in notary
    assert "stapler staple" in notary
    assert "stapler validate" in notary
    assert "spctl" in notary

    # Ad-hoc: preview, never publishable.
    assert signing_state({"developer_id": False, "adhoc": True}) == "preview"
    # Developer ID with no ticket: refused rather than published with a label.
    assert signing_state({"developer_id": True, "notarized": False}) == "not_notarized"
    # Developer ID with a ticket: the one publishable state.
    assert signing_state({"developer_id": True, "notarized": True}) == "verified"


# --------------------------------------------------------------------------
# P0-0.4: the release seals its own claims where they can be checked later
# --------------------------------------------------------------------------


def test_the_evidence_bundle_is_read_by_the_products_own_verifier(tmp_path):
    """A report on stdout is evidence for whoever was watching the job.

    It is nothing at all to the person holding the artifacts a week later,
    which is the person a release's claims are actually for. So the same facts
    are sealed into the archive format this product already ships a verifier
    for -- and checked with *that* verifier, not a second implementation that
    could drift from it and disagree about what "verified" means.
    """
    from openai4s.evidence import verify_package
    from scripts.release_pipeline import seal_evidence_bundle

    extra = tmp_path / "checksums.txt"
    extra.write_text("abc123  openai4s-0.3.0.whl\n", encoding="utf-8")
    report = {"version": "0.3.0", "mode": "local", "ok": True, "steps": []}
    bundle = tmp_path / "evidence.zip"
    seal_evidence_bundle(bundle, report, files=[extra])

    result = verify_package(bundle)
    assert result["ok"], result["problems"]
    assert result["format"] == "openai4s-release-evidence"
    assert result["files_verified"] == [
        "artifacts/checksums.txt",
        "release-report.json",
    ]


def test_a_tampered_bundle_fails_its_own_verification(tmp_path):
    """The only reason to seal anything. A bundle that could be edited without
    detection is a file with a report in it, not evidence."""
    import shutil
    import zipfile

    from openai4s.evidence import verify_package
    from scripts.release_pipeline import seal_evidence_bundle

    bundle = tmp_path / "evidence.zip"
    seal_evidence_bundle(bundle, {"version": "0.3.0", "ok": True})
    tampered = tmp_path / "tampered.zip"
    shutil.copy(bundle, tampered)

    with zipfile.ZipFile(tampered) as archive:
        contents = {name: archive.read(name) for name in archive.namelist()}
    contents["release-report.json"] = b'{"version": "9.9.9", "ok": true}'
    with zipfile.ZipFile(tampered, "w") as archive:
        for name, data in contents.items():
            archive.writestr(name, data)

    verdict = verify_package(tampered)
    assert verdict["ok"] is False
    assert any("content hash mismatch" in problem for problem in verdict["problems"])


def test_an_added_payload_is_caught_even_though_every_listed_file_matches(tmp_path):
    """Checking only the listed files would pass a bundle with something extra
    in it, which is exactly how a "verified" archive smuggles a payload."""
    import shutil
    import zipfile

    from openai4s.evidence import verify_package
    from scripts.release_pipeline import seal_evidence_bundle

    bundle = tmp_path / "evidence.zip"
    seal_evidence_bundle(bundle, {"version": "0.3.0", "ok": True})
    smuggled = tmp_path / "smuggled.zip"
    shutil.copy(bundle, smuggled)
    with zipfile.ZipFile(smuggled, "a") as archive:
        archive.writestr("artifacts/extra.sh", "#!/bin/sh\necho surprise\n")

    verdict = verify_package(smuggled)
    assert verdict["ok"] is False
    assert any("not in the manifest" in problem for problem in verdict["problems"])


def test_the_manifest_vouches_for_itself(tmp_path):
    """Without a self-hash an editor rewrites a payload and its recorded hash
    together, and every per-file check still passes."""
    import json
    import shutil
    import zipfile

    from openai4s.evidence import verify_package
    from scripts.release_pipeline import seal_evidence_bundle

    bundle = tmp_path / "evidence.zip"
    seal_evidence_bundle(bundle, {"version": "0.3.0", "ok": True})
    forged = tmp_path / "forged.zip"
    shutil.copy(bundle, forged)

    with zipfile.ZipFile(forged) as archive:
        contents = {name: archive.read(name) for name in archive.namelist()}
    payload = b'{"version": "9.9.9", "ok": true}'
    manifest = json.loads(contents["manifest.json"])
    # The consistent forgery: rewrite the file AND its recorded digest.
    import hashlib

    for entry in manifest["files"]:
        if entry["path"] == "release-report.json":
            entry["sha256"] = hashlib.sha256(payload).hexdigest()
            entry["size"] = len(payload)
    contents["release-report.json"] = payload
    contents["manifest.json"] = json.dumps(manifest, indent=2).encode("utf-8")
    with zipfile.ZipFile(forged, "w") as archive:
        for name, data in contents.items():
            archive.writestr(name, data)

    verdict = verify_package(forged)
    assert verdict["ok"] is False
    assert any("manifest itself was modified" in p for p in verdict["problems"])


def test_a_failed_release_still_seals_its_record(tmp_path):
    """A stopped run is the one somebody most wants the record of."""
    from openai4s.evidence import verify_package
    from scripts.release_pipeline import seal_evidence_bundle

    bundle = tmp_path / "evidence.zip"
    seal_evidence_bundle(
        bundle,
        {"version": "0.3.0", "ok": False, "stopped_at": "verify", "steps": []},
    )
    assert verify_package(bundle)["ok"] is True  # the *bundle* is intact


def test_a_dry_run_leaves_no_evidence_bundle_on_disk(tmp_path):
    """`--dry-run` is documented as performing no external call, and every
    other step short-circuits to "would write ...". Sealing broke that: a dry
    run left a real bundle behind, and the next real run would find a stale one
    sitting beside its artifacts.
    """
    pipeline = Pipeline(
        version="0.3.0",
        mode="local",
        dry_run=True,
        assets_dir=tmp_path,
        runner=lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    report = pipeline.run()
    assert report["ok"] is True
    assert (
        list(tmp_path.glob("*evidence.zip")) == []
    ), "a dry run wrote an evidence bundle"


# -- the four evidence gaps ---------------------------------------------------


def test_a_release_run_that_is_not_staging_still_needs_the_quality_receipt(assets):
    """The third path, which nothing held to the source quality proof.

    `--from-artifacts` cannot bypass it and `--dry-run` reaches nothing that
    publishes, but a plain `--mode release` ran `pytest -q -x` and nothing else
    -- no pre-commit, no mypy, no README check, no harness tier, no response
    schema or contract, no secret scan, no browser or Python-matrix attestation
    -- and then staged assets onto the draft on the strength of it. Only the
    final flip was blocked, and only because `step_publish` requires PyPI to
    already hold matching digests.

    A local suite is not eight gates run at a frozen SHA on a machine nobody
    can quietly reconfigure.
    """
    ran: list[str] = []
    report = _pipeline(
        assets, mode="release", from_artifacts=False, runner=_git_aware(ran.append)
    ).run()

    step = next(s for s in report["steps"] if s["step"] == "test")
    assert step["ok"], step
    assert "-m pytest" not in " ".join(
        ran
    ), "a release run answered the quality question with a local pytest"
    from scripts import release_gates

    assert step["facts"]["gates"] == [g.name for g in release_gates.LOCAL_GATES]


def test_a_release_run_without_a_receipt_stops_before_it_stages(assets):
    (assets / "quality-receipt.json").unlink()
    report = _pipeline(assets, mode="release", from_artifacts=False).run()

    assert report["ok"] is False
    assert report["stopped_at"] == "test"


def test_every_shipped_platform_needs_a_build_receipt(assets):
    """`("dist", "macos")` was the whole list.

    The Linux tarball and the Windows zip were staged with no document binding
    their bytes to the frozen commit -- covered only by the in-run `incoming`
    digests, which attest that `attach` downloaded what it downloaded, not that
    it was built from these sources. The kinds are now derived from the assets,
    so a platform that ships without a receipt is refused rather than unnoticed.
    """
    from scripts.release_pipeline import required_receipt_kinds

    tarball = assets / "OpenAI4S-0.2.0-linux-x86_64.tar.gz"
    tarball.write_bytes(b"linux-bundle-bytes")
    zipped = assets / "OpenAI4S-0.2.0-windows-x86_64.zip"
    zipped.write_bytes(b"windows-package-bytes")

    kinds = required_receipt_kinds(
        sorted(assets.glob("OpenAI4S-*")) + [assets / "openai4s-0.2.0-py3-none-any.whl"]
    )
    assert set(kinds) == {"dist", "linux", "windows"}, kinds

    # And the pipeline refuses when one of them is missing.
    report = _pipeline(assets, from_artifacts=True).run()
    assert report["ok"] is False
    assert report["stopped_at"] == "verify"
    failed = next(s for s in report["steps"] if s["step"] == "verify")
    assert "linux" in failed["detail"] or "windows" in failed["detail"], failed[
        "detail"
    ]


def test_an_omitted_platform_is_not_demanded(assets):
    """A partial release is a supported shape. Requiring a receipt for an
    artifact that is not there would refuse every one of them."""
    from scripts.release_pipeline import required_receipt_kinds

    assert required_receipt_kinds(sorted(assets.glob("*"))) == ("dist",)


def test_the_evidence_bundle_actually_carries_the_sbom(assets):
    """`step_sbom` writes `sbom.cdx.json`; the collector asked for
    `sbom.spdx.json`, a name nothing in this repository has ever produced.

    The `if path.is_file()` filter then dropped it without a word, so every
    bundle shipped without an SBOM while the step that built it reported
    success. To a reader opening the zip that is not a missing collector, it is
    "this release has no SBOM".
    """
    import zipfile

    from scripts.release_pipeline import SBOM_NAME

    report = _pipeline(assets, mode="local", from_artifacts=True).run()
    assert report["ok"], report

    bundles = sorted(assets.glob("*-evidence.zip"))
    assert bundles, "no evidence bundle was sealed"
    with zipfile.ZipFile(bundles[0]) as archive:
        names = archive.namelist()
    assert any(name.endswith(SBOM_NAME) for name in names), names
