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
