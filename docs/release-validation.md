# Release validation

OpenAI4S treats the installable artifacts as a separate contract from the
source checkout. A passing source-tree test run is not sufficient: the wheel
must contain the Web workbench, R worker, compute templates, bundled Skills,
and conda environment specifications, and it must remain importable without
installing optional science packages.


## Signing state, and why macOS has no publishable path in this version

The signing evidence used to be four separate fields — `developer_id`,
`adhoc`, `identity_configured`, `notarized: null` — from which a reader had to
assemble the answer. It is now one named state per image, computed from
evidence and never from configuration (treating a configured
`OPENAI4S_MACOS_SIGNING_IDENTITY` as proof of a signature is the specific
mistake that once let an ad-hoc image pass the release gate as
Developer-ID-signed):

| State | Meaning | Publishable |
| --- | --- | --- |
| `verified` | Developer ID signature **and** completed notarization | yes |
| `not_notarized` | Developer ID signature, notarization not established | no |
| `preview` | ad-hoc signature — verifies happily, says nothing about who produced it | no |
| `not_configured` | no signature evidence, or none that could be read | no |

**`verified` is reachable only through `scripts/notarize_macos_dmg.sh`.**
`build_macos_dmg.sh` uses a Developer ID certificate when
`OPENAI4S_MACOS_SIGNING_IDENTITY` names one that is available in the keychain;
otherwise it falls back to an ad-hoc signature. It never submits to Apple.
The release workflow input `macos_asset` defaults to `omit` and does not
upload a preview DMG. With `macos_asset=notarized` the macOS job fail-fast
prechecks the smallest Developer ID + notary secret set, then runs sign →
`notarytool submit --wait` → staple → `stapler validate` → `spctl`.
`describe_macos_image.py` records `developer_id`, `notarized`, stapler/spctl
return codes, and the **post-staple** digest bound to this image. A stale
ticket whose digest does not match is not notarized. `--mode release` still
refuses any image that is not both Developer-ID-signed and stapled; the
supported path without credentials is to omit the asset.

## The evidence bundle

Every run seals `openai4s-<version>-evidence.zip` beside the distributions,
in the archive format `openai4s.evidence.verify_package` already reads — the
product's own verifier, not a second implementation that could drift from it
and disagree about what "verified" means.

```bash
uv run openai4s verify-package dist/openai4s-X.Y.Z-evidence.zip
```

It establishes internal consistency: the manifest vouches for itself, every
listed file matches its recorded hash, and anything present but unlisted is a
problem — checking only the listed files would pass a bundle with an added
payload, which is exactly how a "verified" archive smuggles something. It does
**not** establish who produced the bundle; that is the signing question above,
and conflating the two is the failure this separation exists to prevent.

A run that *stopped* seals its record too. A failed release is the one somebody
most wants the evidence for. Sealing is best-effort: a release that succeeded
must not be reported as failed because a directory was read-only.

`--dry-run` seals nothing, like every other step. A dry run that left a real
bundle on disk would be a dry run with a side effect, and the next real run
would find a stale one sitting beside its artifacts.


## Local gate

Run the source scan before building. It considers Git-tracked and non-ignored
files, suppresses matched values from its output, and has a deterministic
filesystem fallback for unpacked source archives.

```bash
python scripts/source_secret_scan.py
python scripts/verify_release_tag.py vX.Y.Z   # the tag you are about to create
uv build --no-sources --out-dir dist --clear
python scripts/verify_release_artifacts.py dist
```

Then install the wheel in a new environment without resolving or downloading
runtime dependencies. Run the smoke script from outside the checkout so an
editable/source import cannot produce a false pass.

```bash
python -m venv /tmp/openai4s-release-venv
/tmp/openai4s-release-venv/bin/python -m pip install \
  --no-index --no-deps dist/openai4s-*.whl
(cd /tmp && env -u PYTHONPATH \
  /tmp/openai4s-release-venv/bin/python \
  "$OLDPWD/scripts/release_import_smoke.py")
```

The build backend itself is declared by `pyproject.toml` and may need to be
bootstrapped by `uv` on a cold machine. Artifact verification, wheel
installation, and import/CLI smoke use no package index and no application
credentials.

Existing databases with a schema newer than this program supports are refused before application initialization writes. The CLI reads configuration without creating directories, then performs a normal SQLite read-only preflight (including committed WAL state); Store rechecks its formal connection before hardening, DDL, seeds or migration. Foreground and detached startup report `future_schema` with actual/supported versions. Use a compatible program version; this protection does not downgrade a database or rewrite its version. A corrupt database retains its distinct SQLite failure.

That refusal exists from 0.3.0 on. It does not protect a downgrade to a release that predates it: 0.2.0 and earlier do not check the schema version, so 0.2.0 opens a database that 0.3.0 has migrated to schema 32 without a warning and writes to it. The migration's own pre-upgrade copy (`openai4s.db.v27.bak`) is deleted once the migration succeeds. Users who may roll back must therefore back up `<data_dir>/openai4s.db` before the first 0.3.0 start; [`upgrading.md`](upgrading.md) gives the procedure. Keep every migration additive while 0.2.x installs can still reach an upgraded database, since nothing on the 0.2.x side would refuse a table or column change it does not understand.

The [pre-upgrade snapshot design](pre-upgrade-snapshot-design.md) fixes a future
one-copy retention and independent-directory restore acceptance contract. It is
P2 preparation only: successful migrations still attempt to remove their `.bak`
file, no retained-snapshot feature is enabled, and DB-only recovery does not
restore workspace bytes, external/keychain/environment-injected credentials or
external job state. Legacy plaintext credentials stored in SQLite can be present
in the database copy.

[预升级快照约定](pre-upgrade-snapshot-design_zh.md) 仅完成 P2 保留及独立目录恢复
的验收准备；当前成功迁移仍尝试删除 `.bak`，未启用快照保留。DB 恢复不包含
完整工作区字节、外部／钥匙串／环境注入凭据或外部任务状态；SQLite 中的遗留
明文凭据仍可能存在于数据库副本中。

The [inbound connection design](inbound-connection-design.md) is also P2
preparation: separate header/body/upload/WS budgets, capacity refusal, status
headroom and observer detachment are future acceptance requirements. It enables
no new inbound deadlines or connection quotas. Status reserve does not promise
immediate service before headers identify a request, and losing an observation
connection never authorizes task cancellation or resubmission.

[入站连接约定](inbound-connection-design_zh.md) 同样仅为 P2 准备，未启用入站
期限或连接配额。状态余量不能保证请求头解析前立即可达；观察连接中断不授权
取消或重提已接受任务。

## macOS app image

The `.dmg` is a third contract, and neither of the checks above can see it. It
does not install the wheel: the kernel spawns its worker through
`sys.executable`, so the image embeds a relocatable standalone CPython with the
science stack from `scripts/bundled_packages.txt` pre-baked into it — the
pip-installable superset of the default `python.yml` kernel env, so a downloaded
app runs cheminformatics (rdkit), single-cell (scanpy), and dataframe workflows
offline with no `pip install` — and ships the source tree as loose `.py` files.
That manifest is the single source of truth: `build_macos_dmg.sh` installs its
pip names and `verify_macos_bundle.py` asserts each import resolves from inside
the bundle, so the installed set and the checked set cannot drift. What can
silently break is therefore different — a runtime that does not relocate, a
science stack that half-installed, a missing Web UI or R worker, an invalidated
signature, or a maintainer's `.env` swept into the bundle.

```bash
bash scripts/build_macos_dmg.sh                                  # Apple Silicon
python3 scripts/verify_macos_bundle.py dist/OpenAI4S-*.dmg
```

The verifier attaches the image read-only and fails closed on every one of those
cases. The build cannot be cross-compiled — the science wheels are native — so
the release job runs it on an Apple Silicon runner and Intel machines install
from PyPI instead.

Two properties of the image are deliberate. A local build without a configured
Developer ID identity is **ad-hoc signed**; a configured release build may use
Developer ID. Neither path currently submits the image for notarization or
staples a ticket, so the release gate rejects the DMG even when the signature is
valid. For local preview images Gatekeeper may therefore refuse first launch,
and the shipped `READ ME` gives both the macOS 15+ ("Open Anyway" in Privacy &
Security) and the macOS 12–14 (right-click → Open) paths, since Sequoia removed
the latter. The image also bundles **Python only**: the R kernel needs a conda
environment, which is far too large to ship inside a DMG, so the R channel
reports that its interpreter is unavailable rather than silently falling back
to Python. The app therefore also ships the `openai4s` CLI at
`Contents/Resources/runtime/bin/openai4s` — without it, `openai4s setup` (the
one documented way to add that R environment) would be unreachable for anyone
who only downloaded the image.

Two contracts hold the runtime to that promise from opposite ends. Bytecode is
precompiled with `--invalidation-mode unchecked-hash` **before** signing, so the
app never writes `__pycache__` into its own bundle — which would invalidate the
signature on first use and force a full recompile of the stdlib and science
stack on every launch from a read-only install. And `Contents/Resources/runtime/pip.conf`
redirects on-demand installs to a private user site under the data directory:
the kernel strips `PIP_*` from every Cell's environment, so config inside the
bundle is the only redirect that also covers `host.bash("pip install …")`.

## Linux app bundle

A fourth contract, and structurally the DMG's twin: the same embedded
relocatable CPython, the same pre-baked science stack from
`scripts/bundled_packages.txt`, the same loose source tree — packed as a
relocatable directory rather than a signed image. What differs is the desktop
integration. `Exec=` and `Icon=` in a `.desktop` entry are absolute paths, and a
tarball does not know where it will be unpacked, so the entry ships as a
template and `install.sh` substitutes the real location at install time.
Shipping a pre-baked path would produce a menu entry that launches nothing.
The installer encodes the executable path using the desktop-entry quoting and
escaping rules. Installation paths containing spaces or shell punctuation must
still launch the same bundled executable. A literal percent sign is doubled as
the specification requires, but that is where escaping ends: GLib (2.36 and
later, so GNOME and anything on `GDesktopAppInfo`) and KIO both check that the
`Exec` program exists *before* they expand `%%`, so a bundle unpacked under a
path containing `%` gets an entry those desktops drop. `install.sh` warns about
such a path rather than implying it works; the CLI link is unaffected. The
renderer also fails, without writing an entry, if the template no longer
carries exactly the `Exec` and `Icon` lines it replaces. Because the installer
now depends on the bundled interpreter, `verify_linux_bundle.py` runs the
shipped `install.sh` and `uninstall.sh` against the unpacked bundle on a
matching Linux host, rather than only reading them.

```bash
bash scripts/build_linux_bundle.sh                               # native
python3 scripts/verify_linux_bundle.py dist/OpenAI4S-*-linux-x86_64.tar.gz
```

Unlike the DMG this **can** be cross-built, because nothing in it is compiled:
`uv` fetches a relocatable CPython built for the target and manylinux wheels
only, and bytecode magic tracks the CPython version rather than the machine.
What a foreign host cannot do is *execute* the result, so the verifier reports
two depths and names which one it reached — a static inspection anywhere, and on
a matching Linux host the import probe that actually proves the science stack
imports rather than merely being present on disk. The release job therefore runs
on a Linux runner, and a cross-build says out loud that it has not been run.

## Windows package

Not a native Windows build. `openai4s/platform_support.py` refuses to start a
kernel on `win32`, so the Windows deliverable is a Windows launcher wrapped
around the Linux bundle, which it installs into WSL2 on first run — see
[`platforms.md`](platforms.md) for why that is the honest packaging rather than
a workaround. The release job consumes the Linux job's artifact instead of
rebuilding, which is what makes the payload byte-identical to the Linux tarball
the same release publishes rather than a second build that ought to match.

```bash
bash scripts/build_windows_zip.sh
python3 scripts/verify_windows_zip.py dist/OpenAI4S-*-windows-x86_64.zip
```

The packaged path now carries the same operational baseline as the documented
WSL flow: Ubuntu 24.04 is preferred, WSL1 is refused, bubblewrap 0.8.0+ must
successfully apply the runtime lifecycle, IPC, UTS, and network namespace
flags, and the daemon starts with
`OPENAI4S_KERNEL_SANDBOX=enforce`. The browser target must come from
`openai4s url`; the bare root intentionally fails local authentication and is
not a valid first-launch URL. The verifier pins all of those strings and also
has negative tests that weaken the sandbox version or restore the bare URL.

The failure modes are specific to the format. `wsl/bootstrap.sh` must arrive
LF-only, because a carriage return makes WSL fail it with `bad interpreter` — on
the user's machine, not here. The payload's checksum sidecar must match its
bytes, or every install refuses for the wrong reason. And the launcher must not
have grown a native execution path: the verifier fails on any shipped `.exe`,
`.dll` or `.pyd`, and on a launcher that starts Python on the Windows side.

One check cannot run anywhere else. A syntax error in `openai4s.ps1` is
invisible to every Linux and macOS job and would surface on the first user's
machine, so a `windows-latest` job parses the packaged launcher with the
PowerShell parser and — on a runner that has no WSL, which is exactly the
machine the guidance was written for — asserts that it refuses with the
`wsl --install` instructions instead of proceeding.

## Enforced contracts

The release jobs in `.github/workflows/ci.yml` run on pull requests, pushes to
`main`/`next`, the nightly schedule, and manual dispatch. They enforce:

- no credential-shaped token or private-key material in release sources;
- exactly one wheel and one sdist with safe archive paths;
- no `.env`, VCS metadata, cache directories, or bytecode in either archive;
- `Requires-Python >=3.10`, a `py3-none-any` wheel, and the `openai4s` console
  entry point;
- no non-extra `Requires-Dist` metadata (the core remains zero-dependency);
- presence of Web UI, R, compute, Skills, environment, provider SDK, and worker
  runtime resources;
- install with `pip --no-index --no-deps`, representative architecture imports,
  installed-resource checks, and an isolated `python -m openai4s --help`;
- real Linux bubblewrap Python and R kernels under team read isolation, with
  private PID namespaces, the info-fd/procfs/pidfd command identity path,
  SIGINT delivery, and same-generation execution after the interrupt.

The Linux interrupt check deliberately allows raw worker networking so its
process-identity evidence is independent of private-network setup. It
therefore attests only the private-PID interrupt and persistence contract. On
Ubuntu 24.04 it loads the distribution's
`bwrap-userns-restrict` AppArmor profile, which permits bwrap's namespace setup
but strips capabilities from the worker; it does not turn off the host-wide
unprivileged-userns restriction.

The complete Linux filesystem-and-egress boundary now runs as the independent
CI job `Linux bubblewrap full filesystem/egress boundary` and is attested at
the frozen SHA as check-suite gate `ci-linux-sandbox-full`. That is
`ci_attestation`. The release workflow still does not re-execute that smoke
inside `platform-checks`; that absence is `release_reexecution: unproven`,
not a second status on the same fact. A receipt that flattens both into one
`passed`/`unproven` field is refused.

The container image runs Python 3.14. That series is in the full offline CI
matrix and in the quality-receipt check-suite gates. Container smoke is not a
substitute for that matrix.

A candidate SHA is not publishable on the strength of a `publish=true`
dispatch. Schema-2 build receipts record the candidate commit, the workflow
run id, the dispatch inputs, the builder platform and artifact checksums (the
macOS receipt also the notary/staple result). Check-run ids are recorded on
the stage attestation only: they come from the quality receipt, and the
`--check-runs-json` slot on a build receipt exists but the workflow does not
fill it, so a build receipt's `check_runs` is `[]`. `publish=false` is a
rehearsal: PyPI and the public GitHub Release must not change, and
`macos_asset=omit` (or a missing notary success) means DMG count is zero.
Old schema-1 receipts remain readable JSON and cannot satisfy this gate.

## Trusted publication

Publishing is isolated in `.github/workflows/release.yml`, and only a
maintainer's dispatch starts it. No release event triggers it. A maintainer
creates a **draft** release for an annotated tag, then dispatches the workflow
against that tag ([Draft-first](#draft-first-from-v02) below explains why).
A `publish=true` run builds from the immutable commit the tag peels to. It
re-runs the offline gates into a quality receipt, builds and verifies every
asset, stages the assets onto the draft, publishes the wheel and sdist to PyPI,
and only then makes the GitHub Release public. The build job requires an exact
`vMAJOR.MINOR.PATCH` match in both `pyproject.toml` and `openai4s.__version__`,
and it scans the sources before building. The PyPI job can only download the
verified distributions artifact and invoke PyPA's publisher. It is the only job
that receives `id-token: write`.

Before the first publication, a repository administrator must:

1. create the GitHub environment `pypi`, require a maintainer review on it, and
   restrict its deployment refs to `main` and `v*` tags. Without that
   restriction, a workflow dispatched from any branch can request the
   environment's OIDC token, and PyPI's trusted publisher checks the workflow
   file name and the environment, not the branch;
2. configure a PyPI trusted publisher for repository `PKU-YuanGroup/OpenAI4S`,
   workflow `release.yml`, environment `pypi`;
3. protect `v*` tags with a tag ruleset that restricts creation and blocks
   update and deletion;
4. give the `ghcr` environment the same review and ref restriction, because
   `publish-image.yml` pushes the container image from it.

To publish `vX.Y.Z` from a green `main` commit `S`:

1. Confirm that CI is green at `S`. The quality job attests the latest attempt
   of every required check recorded against `S`. A failed scheduled run at that
   commit therefore blocks the release until that job is re-run.
2. Create and push an annotated tag. Both `publish` and `pypi_only` refuse a
   lightweight tag.

   ```bash
   git tag -a vX.Y.Z S -m "OpenAI4S vX.Y.Z"
   python scripts/verify_release_tag.py vX.Y.Z
   git push origin refs/tags/vX.Y.Z
   ```

3. Create the draft. Do not mark it as a prerelease; the guard refuses one.

   ```bash
   gh release create vX.Y.Z --draft --verify-tag --title "OpenAI4S vX.Y.Z" --notes-file notes.md
   ```

4. Optionally, rehearse. `gh workflow run release.yml --ref vX.Y.Z -f tag=vX.Y.Z`
   builds and verifies everything and publishes nothing.
5. Publish, then approve the `pypi` deployment when the run asks for it:

   ```bash
   gh workflow run release.yml --ref vX.Y.Z -f tag=vX.Y.Z -f publish=true
   ```

   Add `-f macos_asset=notarized` only when the complete Developer ID and
   notary credential set exists. The default, `omit`, publishes no DMG.
6. Confirm the container image. Check that `publish-image.yml` ran for
   `vX.Y.Z`, and if it did not, dispatch it:
   `gh workflow run publish-image.yml --ref vX.Y.Z -f ref=vX.Y.Z`.

**Never publish the GitHub Release by hand.** A release that is already public
cannot be staged. The guard refuses it, and the only mode left, `pypi_only`,
uploads the wheel and sdist to PyPI and attaches nothing to the release. That
is how v0.2.0 shipped: its GitHub Release carries the Linux tarball, the DMG
and `SHA256SUMS`, and no wheel, sdist, SBOM, provenance or evidence bundle.
`pypi_only` exists only to recover from that state.

A version number can be uploaded to PyPI only once. If `finalize` fails after
the PyPI job has succeeded, re-run the failed job instead of dispatching a new
build. A new `publish=true` dispatch is refused by the guard once PyPI has the
version: the rebuild is not byte-identical, and staging it would overwrite the
draft's assets with files PyPI does not have.

The guard job holds `contents: write` although it only reads. GitHub shows a
draft release only to a caller with push access, so with the workflow's
read-only token the guard reported an existing draft as missing. That is how
v0.3.0's first publish dispatch stopped.

The workflow uses GitHub/PyPI OIDC and does not accept a long-lived PyPI token.
Its PyPI job also creates PyPI's default provenance attestations through the
official PyPA action.

## Deliberate remaining external gates

Pull-request CI does not publish packages, sign/notarize native executables, or
perform live-provider, GPU, SSH, and laboratory validation. Publication needs
a non-prerelease draft release, a maintainer's `publish=true` dispatch, and the
separately protected OIDC environment above. The other operations require an
explicit identity, network service, or hardware and remain outside the
secret-free default gate.


## Draft-first (from v0.2)

The pipeline no longer starts after the release is public. `release.yml` is
`workflow_dispatch` only: a maintainer creates the draft, then runs the
workflow against that tag, and every step runs while nothing is visible. In
the Actions UI, select a branch whose tip is the tagged commit and enter the
tag in the `tag` input; from the CLI, prefer passing both `--ref TAG` and
`-f tag=TAG`. The workflow requires that the tag peel to the immutable
`github.sha`, so repository code is never selected by the mutable input.

It used to trigger on `release: [created]`. GitHub does not emit that event for
a *draft*, so the intended entry point could never fire and the pipeline was
unreachable by construction — which is why the trigger is now explicit.

    build → test → assets → smoke → SBOM → provenance → verify → evidence →
    checksums → draft → upload → re-verify → publish

`publish` is last because it is the only step that cannot be undone. `verify`
runs before the evidence bundle and checksums are sealed, so signing and
notarization facts are part of the evidence and every resulting artifact is
covered before staging. `re-verify` reads the assets back *after* upload,
because a local checksum cannot see a transfer that dropped bytes.

Before upload and again after downloading the draft for publication, the
pipeline checks the relationships between assets: provenance and SBOM hashes,
the sealed evidence's artifact inventory and build receipts, and the Windows
package's embedded Linux payload must agree with the exact distributions being
released. This check also applies to a hand-run `--only publish` without a stage
attestation. Updating `SHA256SUMS` after replacing a ZIP cannot make its older
build receipt valid. These checks establish consistency; the out-of-band stage
attestation remains the protection against replacing an entire draft and its
evidence together. When `--source-sha` or `--workflow-run-id` is supplied, the
sealed report and the sealed build receipts must also name that commit and that
run, as they must at staging.

Run a hand-run `--only publish` from a checkout of the release tag. The sealed
quality receipt is verified against the gate manifest of the checkout running
the script, so a later `main` whose gate list has moved refuses an intact draft
— after PyPI has already taken the version. The refusal names the release
commit to check out. A staging retry in the same `--assets-dir` is supported:
the previous attempt's `-evidence.zip` and `-evidence-stopped.zip` are this
pipeline's own outputs and are never collected as distributions.

If a draft asset was replaced, restore the original verified bytes and their
matching evidence. If the replacement changes the release source, merge the
fixes and build a new immutable tag through CI. Do not edit an old receipt to
claim the replacement was the build it originally recorded, move a release tag,
or rebuild wheel/sdist files already published under the same PyPI version.

All of it lives in [`scripts/release_pipeline.py`](../scripts/release_pipeline.py),
not in the workflow YAML, so it can be exercised without cutting a release:

```bash
uv run python scripts/release_pipeline.py --version X.Y.Z --dry-run
uv run python scripts/release_pipeline.py --version X.Y.Z --mode local
```

`--dry-run` performs no external call and is how the *ordering* is tested.
`--mode local` really builds, hashes, and writes `sbom.cdx.json` (CycloneDX
1.5) and `provenance.intoto.json` (in-toto/SLSA), and stops before anything is
published.

### What is and is not claimed about signing

* `--mode release` **fails closed** unless a `.dmg` is both
  Developer-ID-signed and notarized. The judgement comes from evidence — a
  receipt written by the macOS job, or inspection of the image — and the digest
  in that evidence must bind to the image being released.
* It deliberately does **not** consult `OPENAI4S_MACOS_SIGNING_IDENTITY`.
  Reading a non-empty environment variable as "this is signed" is exactly what
  once let an ad-hoc image pass the gate as Developer-ID-signed. The build
  script may use the named identity, but configuration is not evidence; the
  verifier inspects the resulting image instead.
* Consequence worth stating plainly: a DMG produced by `build_macos_dmg.sh`
  alone cannot pass `--mode release`. The publishable path is
  `macos_asset=notarized` with a complete credential set, which runs
  `scripts/notarize_macos_dmg.sh` and records the post-staple digest. Without
  those credentials the supported path is `macos_asset=omit` (the default),
  not uploading a preview image labelled as signed.
* `describe_macos_image.py` runs `xcrun stapler validate` and records the
  boolean result, stapler/spctl return codes, and `post_staple_sha256`. It
  validates existing notarization evidence; it does not submit the image or
  staple a ticket. The notary script is what creates that evidence. Default
  unit tests never contact Apple's notary: they cover credentials, ticket
  digest binding, and omission.

The provenance statement is **unsigned** and says so: it binds the listed
digests to the build's parameters, and it does not establish who produced them.
That needs a signature this format does not yet carry.
