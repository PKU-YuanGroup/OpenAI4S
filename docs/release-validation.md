# Release validation

OpenAI4S treats the installable artifacts as a separate contract from the
source checkout. A passing source-tree test run is not sufficient: the wheel
must contain the Web workbench, R worker, compute templates, bundled Skills,
and conda environment specifications, and it must remain importable without
installing optional science packages.

## Local gate

Run the source scan before building. It considers Git-tracked and non-ignored
files, suppresses matched values from its output, and has a deterministic
filesystem fallback for unpacked source archives.

```bash
python scripts/source_secret_scan.py
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
science stack from `scripts/dmg_bundled_packages.txt` pre-baked into it — the
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

Two properties of the image are deliberate. It is **ad-hoc signed and not
notarized**, because notarization requires a paid Apple Developer identity;
Gatekeeper therefore refuses it on first launch, and the shipped `READ ME` gives
both the macOS 15+ ("Open Anyway" in Privacy & Security) and the macOS 12–14
(right-click → Open) paths, since Sequoia removed the latter. And it bundles
**Python only**: the R kernel needs a conda environment, which is far too large
to ship inside a DMG, so the R channel reports that its interpreter is
unavailable rather than silently falling back to Python. The app therefore also
ships the `openai4s` CLI at
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
  installed-resource checks, and an isolated `python -m openai4s --help`.

The normal CI browser smoke and nightly macOS Seatbelt smoke remain separate
because they exercise runtime/browser and operating-system boundaries rather
than archive integrity.

## Deliberate remaining external gates

This repository does not claim that offline CI performs package publication,
release signing/notarization, vulnerability-database lookup, or live-provider,
GPU, SSH, and laboratory validation. Those operations require an explicit
release identity, network service, credential, or hardware and must stay out
of secret-free pull-request execution. A maintainer must perform them in a
separately authorized release workflow before public distribution.
