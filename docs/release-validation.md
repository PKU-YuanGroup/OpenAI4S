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
uv run openai4s verify-package dist/openai4s-0.3.0-evidence.zip
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
python scripts/verify_release_tag.py v0.1.0
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
unprivileged-userns restriction. That profile may change the historical
hosted-runner loopback result, but the complete Linux filesystem-and-egress
boundary has not yet been re-evaluated there and stays a separate manual
smoke. The normal CI browser smoke and nightly macOS Seatbelt smoke likewise
remain separate because they exercise runtime/browser and operating-system
boundaries rather than archive integrity.

## Trusted publication

Publishing is isolated in `.github/workflows/release.yml`. A non-prerelease
GitHub Release whose tag starts with `v` builds from that immutable tag. The
build job requires an exact `vMAJOR.MINOR.PATCH` match in both `pyproject.toml`
and `openai4s.__version__`, scans the sources, builds and verifies the wheel and
sdist, then uploads those exact files as a short-lived Actions artifact. A
separate `publish` job can only download that artifact and invoke PyPA's
publisher. Only this final job receives `id-token: write`.

Before the first publication, a repository administrator must:

1. create the protected GitHub environment `pypi` and require a maintainer
   review;
2. configure a PyPI pending/trusted publisher for repository
   `PKU-YuanGroup/OpenAI4S`, workflow `release.yml`, environment `pypi`;
3. protect `v*` tags and the release workflow through repository rules;
4. create an annotated tag from a green `main` commit, then publish the GitHub
   Release for that tag.

The workflow uses GitHub/PyPI OIDC and does not accept a long-lived PyPI token.
Its publish job also creates PyPI's default provenance attestations through the
official PyPA action.

## Deliberate remaining external gates

Pull-request CI does not publish packages, sign/notarize native executables, or
perform live-provider, GPU, SSH, and laboratory validation. Publication needs
an approved GitHub Release and the separately protected OIDC environment above;
the other operations require an explicit identity, network service, or
hardware and remain outside the secret-free default gate.


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

All of it lives in [`scripts/release_pipeline.py`](../scripts/release_pipeline.py),
not in the workflow YAML, so it can be exercised without cutting a release:

```bash
uv run python scripts/release_pipeline.py --version 0.2.0 --dry-run
uv run python scripts/release_pipeline.py --version 0.2.0 --mode local
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
