"""Release documentation must describe the release machinery that exists.

The docs a maintainer follows when cutting a release, and the docs a user
follows when installing one, are prose. Prose is the one thing no workflow
executes, so it drifted from `.github/workflows/release.yml` without any gate
noticing:

* `docs/release-validation.md` told maintainers to "publish the GitHub Release"
  for the tag. `release.yml` is dispatch-only and draft-first. A maintainer who
  did what the doc said made the release public before the pipeline ran, so the
  guard refused to stage onto it. The only mode left was `pypi_only`, which
  attaches nothing, and that is how v0.2.0's GitHub Release ended up with no
  wheel, sdist, SBOM, provenance or evidence bundle.

Every check here reads a fact from the machinery (the workflow file, the
version declaration) and a claim from the docs, and fails when they disagree.
The phrases are matched after whitespace normalisation, so rewrapping a
paragraph cannot hide one.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RELEASE_VALIDATION = ROOT / "docs" / "release-validation.md"
CONTRIBUTING = ROOT / ".github" / "CONTRIBUTING.md"


def _normalised(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").split())


def _section(path: Path, heading: str) -> str:
    """The body of one `## ` section, up to the next `## ` heading."""
    text = path.read_text(encoding="utf-8")
    start = text.index(f"\n{heading}\n")
    end = text.find("\n## ", start + len(heading) + 2)
    return text[start : end if end != -1 else len(text)]


def _project_version() -> str:
    # Not tomllib: the suite also runs on Python 3.10.
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"$', pyproject, re.MULTILINE)
    assert match, "pyproject.toml no longer declares a project version"
    return match.group(1)


# -- trusted publication is draft-first --------------------------------------


def test_the_release_docs_never_tell_a_maintainer_to_publish_the_release_by_hand():
    """Each phrase is an instruction that makes the release public before the
    workflow runs, which `release.yml`'s guard then refuses to stage onto."""
    forbidden = (
        "publish the GitHub Release for that tag",
        "from a non-prerelease GitHub Release.",
        "GitHub Release whose tag starts with `v` builds",
        "Publication needs an approved GitHub Release",
    )
    for path in (RELEASE_VALIDATION, CONTRIBUTING):
        text = _normalised(path)
        present = [phrase for phrase in forbidden if phrase in text]
        assert not present, f"{path.relative_to(ROOT)} still says {present}"


def test_trusted_publication_spells_out_the_draft_first_dispatch():
    """The section a maintainer reads must name the commands the workflow
    actually needs: an annotated tag, a draft that is verified against that
    tag, and a `publish=true` dispatch against the tag ref."""
    section = " ".join(_section(RELEASE_VALIDATION, "## Trusted publication").split())
    for needed in (
        "git tag -a vX.Y.Z",
        "gh release create vX.Y.Z --draft --verify-tag",
        "gh workflow run release.yml --ref vX.Y.Z -f tag=vX.Y.Z -f publish=true",
        "Never publish the GitHub Release by hand.",
        "`pypi_only` exists only to recover",
    ):
        assert needed in section, f"Trusted publication no longer says {needed!r}"


def test_the_workflow_really_is_dispatch_only_and_draft_first():
    """The two facts the doc tests above rely on. If the workflow changes,
    these fail first, and the doc has to be rewritten to match."""
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text("utf-8")
    trigger = workflow.split("\non:\n", 1)[1].split("\n\n", 1)[0]
    assert re.search(r"^  workflow_dispatch:$", trigger, re.MULTILINE)
    assert not re.search(
        r"^  (release|push):", trigger, re.MULTILINE
    ), "release.yml is triggered by an event again, not only by a dispatch"
    assert "is already public; staging assets onto it" in workflow


def test_concrete_version_examples_match_the_tree_or_are_placeholders():
    """`verify_release_tag.py v0.1.0` exits 1 against any later tree, so a
    stale example is a command that fails when copied. An example either names
    the version this tree declares or uses the `X.Y.Z` placeholder."""
    text = RELEASE_VALIDATION.read_text(encoding="utf-8")
    version = _project_version()
    patterns = (
        r"verify_release_tag\.py v(\d+\.\d+\.\d+)",
        r"release_pipeline\.py --version (\d+\.\d+\.\d+)",
        r"openai4s-(\d+\.\d+\.\d+)-evidence\.zip",
    )
    stale = [
        match.group(0)
        for pattern in patterns
        for match in re.finditer(pattern, text)
        if match.group(1) != version
    ]
    assert not stale, (
        f"docs/release-validation.md has version examples that are not "
        f"{version} and not a placeholder: {stale}"
    )


# -- upgrading from 0.2.x ------------------------------------------------------

UPGRADING = ROOT / "docs" / "upgrading.md"
UPGRADING_ZH = ROOT / "docs" / "upgrading_zh.md"
MIGRATIONS = ROOT / "openai4s" / "storage" / "migrations.py"

#: The schema the published 0.2.0 wheel creates. A fact about a release that
#: already exists, so it is a constant rather than something read from the tree.
V020_SCHEMA = 27


def _migration_deletes_its_backup_on_success() -> bool:
    source = MIGRATIONS.read_text(encoding="utf-8")
    tail = source.split("Only now that the upgrade is committed", 1)
    return len(tail) == 2 and "backup.unlink()" in tail[1]


def test_the_future_schema_refusal_is_not_read_as_protecting_a_downgrade():
    """The refusal paragraph read as if a newer schema were always refused.
    0.2.0 predates the guard, opens a schema-32 database silently and writes to
    it, so the paragraph has to say which versions it covers and send a user
    who may roll back to a backup."""
    text = _normalised(RELEASE_VALIDATION)
    start = text.index("schema newer than this program supports")
    window = text[start : start + 1600]
    assert "0.2.0 and earlier do not check the schema version" in window
    assert "back up `<data_dir>/openai4s.db`" in window
    assert "upgrading.md" in window


def test_the_upgrade_guide_states_the_schema_change_the_backup_and_no_downgrade():
    """Both halves must tell a 0.2.x user the three things that cannot be
    undone by reinstalling: the schema moves, the migration's own copy is gone
    after success, and 0.2.x will not refuse the upgraded database."""
    current = int(
        re.search(r"^SCHEMA_VERSION = (\d+)$", MIGRATIONS.read_text("utf-8"), re.M)[1]
    )
    assert current >= 32, "the upgrade guide names a schema this tree does not reach"
    english = _normalised(UPGRADING)
    chinese = _normalised(UPGRADING_ZH)
    # A container keeps the database on its volume, not under ~/.openai4s, so
    # the backup instruction has to name the directory the image really uses.
    image_data_dir = re.search(
        r"OPENAI4S_DATA_DIR=(\S+)", (ROOT / "Dockerfile").read_text("utf-8")
    )[1]
    for text in (english, chinese):
        assert f"OPENAI4S_DATA_DIR={image_data_dir}" in text
        assert "PersistentVolumeClaim" in text
        assert f"schema **{V020_SCHEMA}**" in text
        assert "schema **32**" in text
        assert f"openai4s.db.v{V020_SCHEMA}.bak" in text
        assert "cp -a ~/.openai4s ~/.openai4s-0.2-backup" in text
        assert "OPENAI4S_REQUIRE_TOKEN=0" in text
        assert "openai4s url" in text
    assert "Going back to 0.2.x is not supported" in english
    assert "不支持退回 0.2.x" in chinese
    # The claim about the backup is read from the code, not assumed: if a
    # migration starts keeping its pre-upgrade copy, this fails until both
    # halves stop telling users it is deleted.
    deletes = _migration_deletes_its_backup_on_success()
    assert deletes == ("deletes it once the migration succeeds" in english)
    assert deletes == ("迁移成功后会删除它" in chinese)


def test_the_upgrade_guide_says_what_old_records_and_a_failed_upgrade_look_like():
    """UPG3-07, and the two upgrade behaviours a 0.2.x user meets first.

    Each sentence is tied to the code that makes it true, so a change there
    fails here until both halves are corrected:

    * `OPENAI4S_WEBUI=legacy` was offered as a fallback, but only the workbench
      rewrites the `/api/artifacts/<id>` links 0.2.x stored in messages; the
      frozen app.js inserts them as written and the server has no alias.
    * a failed migration is one `error:` line naming the kept copy (the CLI
      catches MigrationError, whose message names the backup);
    * 0.2.x artifacts' environment reads as Python with the package list
      unknown (the Store read relabels the `repl` rows, and never fills in a
      list).
    """
    english = _normalised(UPGRADING)
    chinese = _normalised(UPGRADING_ZH)

    render = (ROOT / "frontend" / "src" / "features" / "md" / "render.ts").read_text(
        "utf-8"
    )
    assert "/^\\/api\\/artifacts\\/([^/?#]+)$/" in render
    assert '"/api/v1/artifacts/"' in render
    # The frozen legacy UI has no rewrite of the stored form, in either spelling.
    legacy = (ROOT / "openai4s" / "server" / "webui" / "app.js").read_text("utf-8")
    assert "/api/artifacts/" not in legacy and "\\/api\\/artifacts" not in legacy
    cli = (ROOT / "openai4s" / "cli" / "main.py").read_text("utf-8")
    assert cli.count("except MigrationError as exc:") >= 2
    assert "A pre-upgrade backup is at" in MIGRATIONS.read_text("utf-8")
    storage = (ROOT / "openai4s" / "storage" / "artifacts.py").read_text("utf-8")
    assert "the package list is unknown" in storage

    for text in (english, chinese):
        assert "`OPENAI4S_WEBUI=legacy`" in text
        assert "`/api/artifacts/<id>`" in text
        assert "`error:`" in text
        assert "`repl`" in text
    assert "open only in the default workbench" in english
    assert "只在默认工作台里能打开" in chinese
    assert "exit with status 2" in english
    assert "退出码为 2" in chinese
    assert "package list is unknown" in english
    assert "包列表未知" in chinese


# -- what each platform can actually download ----------------------------------

WORKFLOWS = ROOT / ".github" / "workflows"
USER_INSTALL_DOCS = (
    ROOT / "README.md",
    ROOT / "README_zh.md",
    ROOT / "docs" / "startup-guide.md",
)


def _macos_asset_defaults_to_omit() -> bool:
    workflow = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
    block = workflow.split("      macos_asset:", 1)
    return len(block) == 2 and bool(
        re.search(r"^        default: omit$", block[1].split("\n\n", 1)[0], re.M)
    )


def test_no_install_doc_sends_a_mac_user_to_the_latest_release_for_a_dmg():
    """While the release workflow omits the DMG by default, a release it
    produces has no macOS asset. v0.3.0's preview image was attached by hand,
    so `releases/latest` carries a DMG only until the next workflow release. A
    `macos-arm64.dmg` named a few lines from a `releases/latest` link is a
    download that will not be there. A proximity window rather than one line,
    because the two sat on adjacent wrapped lines."""
    if not _macos_asset_defaults_to_omit():
        pytest.skip("release.yml publishes a DMG by default again")
    offenders = []
    for path in USER_INSTALL_DOCS:
        lines = path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            if "releases/latest" not in line:
                continue
            window = lines[max(0, index - 3) : index + 4]
            if any("macos-arm64.dmg" in near for near in window):
                offenders.append(f"{path.relative_to(ROOT)}:{index + 1}")
    assert not offenders, f"DMG download pointed at the latest release: {offenders}"


def test_install_docs_pin_the_v030_preview_image_to_its_release_page():
    """The other half of the rule above: a doc that names the v0.3.0 image has
    to say where it is, and that is the pinned release page."""
    for path in USER_INSTALL_DOCS:
        text = path.read_text(encoding="utf-8")
        assert "OpenAI4S-0.3.0-macos-arm64.dmg" in text, path.name
        assert "/releases/tag/v0.3.0" in text, path.name
        assert "ad-hoc" in text, path.name


def test_platforms_does_not_claim_a_notarized_dmg_ships():
    if not _macos_asset_defaults_to_omit():
        pytest.skip("release.yml publishes a DMG by default again")
    text = _normalised(ROOT / "docs" / "platforms.md")
    assert "macOS ships as a signed, notarized" not in text
    assert "No release has met this gate" in text
    assert "v0.3.0's image is an ad-hoc-signed preview" in text
    assert "v0.3.0 publishes no DMG" not in text


#: Wording that defers a package to a later release, per README half.
_DEFERRAL = {
    "README.md": re.compile(
        r"\b(?:coming|later|future|upcoming|next) release\b|ships? later"
        r"|still stabiliz|not yet (?:published|shipped|released)",
        re.IGNORECASE,
    ),
    "README_zh.md": re.compile(
        r"后续版本|之后的版本|以后的版本|将来的版本|稳定化|尚未发布"
    ),
}


def _install_sections(path: Path) -> str:
    """The Linux and Windows install sections of a README half: from the
    `### Linux` heading to the heading after `### Windows`. The news entries
    for older releases sit outside it and may keep their historical wording."""
    text = path.read_text(encoding="utf-8")
    start = text.index("\n### Linux")
    windows = text.index("\n### Windows", start)
    end = text.index("\n### ", windows + 1)
    return text[start:end]


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[。；])|(?<=[.;])\s+|\n", text)
    return [part for part in parts if part.strip()]


def test_the_readmes_do_not_say_the_windows_package_ships_later():
    """`release.yml` stages the Windows zip on every publish; nothing can omit
    it. An install note saying the package ships in a coming release
    contradicts the release it is published with. Matched per sentence rather
    than as one exact phrase, so a paraphrase of the deferral is caught too."""
    workflow = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
    assert "Download the verified Windows package" in workflow
    for name, deferral in _DEFERRAL.items():
        sections = _install_sections(ROOT / name)
        assert "Windows" in sections
        deferring = [
            " ".join(sentence.split())
            for sentence in _sentences(sections)
            if "Windows" in sentence and deferral.search(sentence)
        ]
        assert not deferring, f"{name} still defers Windows: {deferring}"


PARITY_AUDIT = ROOT / "docs" / "windows-wsl-parity-audit.md"

#: A limitation the audit's final verification section states, and the words
#: each README half must use to disclose it. Keyed by the audit's own wording,
#: so an audit that later certifies one of them releases the docs from it.
_WINDOWS_LIMITATIONS = {
    # D13 names this one first; the audit's final section says it "does not
    # certify macOS execution" because the macOS side was read from source.
    "macOS execution": ("side-by-side macOS run", "与 macOS 并排运行"),
    "Windows reboot": ("A Windows reboot", "Windows 整机重启"),
    "Conda provisioning": ("Conda environment provisioning", "Conda 环境准备"),
    "real provider login/inference": (
        "Real provider sign-in and inference",
        "真实的供应商登录与推理",
    ),
    "The unmodified full browser script is **not passed**": (
        "full browser smoke did not pass",
        "完整浏览器 smoke 未通过",
    ),
    "WSL service connection timeouts": (
        "WSL service connection timeouts",
        "WSL 服务连接超时",
    ),
}


def test_the_windows_limitations_follow_the_audits_final_verification():
    """The parity audit's "Unverified scope" line belongs to its pre-fix plan.
    The "Fix verification — 2026-09-07" section supersedes it and names more:
    a Windows reboot (only a WSL-distribution restart passed), Conda
    provisioning, and a full browser smoke that did not pass. The disclosure
    copied the plan's list, so it both missed those and said a WSL restart was
    unverified when one had passed."""
    audit = _normalised(PARITY_AUDIT)
    heading = "## Fix verification — 2026-09-07"
    assert heading in audit, "the parity audit's final verification section moved"
    final = audit.split(heading, 1)[1]
    stated = {
        fact: words for fact, words in _WINDOWS_LIMITATIONS.items() if fact in final
    }
    assert stated, "the audit's final section no longer states any known limitation"
    decisions = _normalised(ROOT / "docs" / "v03-decisions.md")
    for index, name in enumerate(("README.md", "README_zh.md")):
        section = " ".join(
            _install_sections(ROOT / name).split("\n### Windows", 1)[1].split()
        )
        missing = [
            words[index] for words in stated.values() if words[index] not in section
        ]
        assert not missing, f"{name}'s Windows section does not disclose {missing}"
    for superseded in (
        "complete recovery after a WSL or Windows restart",
        "WSL 或 Windows 重启后的完整恢复",
    ):
        for path in (ROOT / "README.md", ROOT / "README_zh.md"):
            assert superseded not in _normalised(path), f"{path.name}: {superseded}"
        assert superseded not in decisions, f"v03-decisions.md: {superseded}"
    for fact in (
        "Windows reboot",
        "Conda provisioning",
        "full browser smoke did not pass",
    ):
        assert fact in decisions, f"D13 in v03-decisions.md does not name {fact!r}"


def test_security_does_not_credit_the_one_file_environ_mask_with_linux():
    """The single-user bubblewrap policy keeps the host PID namespace and masks
    exactly one file, `/proc/<daemon>/environ`. The same environment is also
    `/proc/<daemon>/task/<tid>/environ`, and MCP connector children and a
    `uv run` parent carry secrets in theirs. What refuses those reads on a
    default Linux host is bubblewrap's user namespace; setuid bubblewrap runs
    without one and leaves them readable. The doc said the mask "closes" the
    threat, a parity claim with macOS the mask cannot meet."""
    text = _normalised(ROOT / "docs" / "security.md")
    assert "closes on Linux by masking" not in text
    linux = text.split("**Linux — reading another process's environment.**", 1)
    assert len(linux) == 2, "docs/security.md lost its Linux environ paragraph"
    paragraph = linux[1].split("**", 1)[0]
    for claim in (
        "user namespace",
        "task/<tid>/environ",
        "backstop",
        "setuid",
        "--unshare-pid",
    ):
        assert claim in paragraph, f"the Linux environ paragraph omits {claim!r}"


def test_platforms_names_the_published_container_image():
    if not (WORKFLOWS / "publish-image.yml").is_file():
        pytest.skip("no container image workflow")
    text = _normalised(ROOT / "docs" / "platforms.md")
    assert "No registry publishes it" not in text
    assert "ghcr.io/pku-yuangroup/openai4s" in text


def test_the_workflow_readmes_do_not_call_the_linux_boundary_smoke_manual():
    """`ci.yml` runs `harness.smoke.linux_sandbox` as its own job on every CI
    event, and the release receipt attests it. Calling it manual, or saying it
    is unproven "because it is not a current CI gate", describes a workflow
    that no longer exists."""
    ci = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    if "python -m harness.smoke.linux_sandbox" not in ci:
        pytest.skip("ci.yml no longer runs the full Linux boundary smoke")
    english = _normalised(WORKFLOWS / "README.md")
    chinese = _normalised(WORKFLOWS / "README_zh.md")
    assert "Linux boundary smoke remains manual" not in english
    assert "because it is not a current CI gate" not in english
    assert "仍需手动执行" not in chinese
    assert "不是当前 CI gate" not in chinese
    assert "ci-linux-sandbox-full" in english and "ci-linux-sandbox-full" in chinese
