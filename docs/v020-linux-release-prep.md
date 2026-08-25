# v0.2.0 Linux desktop — release notes draft and build recipe

This is the maintainer packet for the Linux half of v0.2.0. It does **not**
publish anything. Windows (WSL) and macOS images are built on those hosts and
attached later; one annotated tag and one draft release cover all three.

## Release notes draft (v0.2.0)

**OpenAI4S 0.2.0** is the first release that ships a Linux desktop bundle next
to the existing macOS image and the Windows/WSL launcher.

### Downloads (once published)

| Asset | What it is |
| --- | --- |
| `OpenAI4S-0.2.0-linux-x86_64.tar.gz` | Relocatable Linux desktop bundle: embedded CPython 3.13, pre-baked science stack, loose source tree, `./OpenAI4S` launcher, `.desktop` template, per-user `install.sh`. **Not an AppImage and not a `.deb`** — a FUSE squashfs would nest user namespaces inside a FUSE mount and fail at cell-execution time. |
| `OpenAI4S-0.2.0-macos-arm64.dmg` | Apple Silicon app (built on macOS). Ad-hoc signed unless a Developer ID identity is configured; notarization is still unreachable, so `--mode release` still refuses the DMG. |
| `OpenAI4S-0.2.0-windows-x86_64.zip` | Windows launcher around **the exact Linux tarball** above. Requires WSL2. Built from the Linux artifact, not a second compile. |
| `openai4s-0.2.0-py3-none-any.whl` / `openai4s-0.2.0.tar.gz` | Zero-dependency wheel and sdist for any supported platform that already has Python ≥ 3.10. |

### What landed since v0.1.0

- **Linux and Windows desktop packages** — same embedded interpreter and science
  stack as the macOS image; Linux unpacks anywhere and runs `./OpenAI4S`.
- **Read-only session sharing** — `openai4s share` / `openai4s relay` over an
  outbound tunnel you operate.
- **Seven source-attributed public-database connectors** — UniProt, RCSB PDB,
  Ensembl, ChEMBL, PubChem, arXiv, OpenAlex.
- **Versioned `/api/v1`** — keyset pagination, one error envelope, resumable
  WebSocket cursor.
- **Environments as a transaction** — `openai4s env plan|apply|list|rollback|recover`.
- **Support surfaces** — redacted `openai4s doctor` / `openai4s diagnostics`,
  consent-gated revocable telemetry.
- **Benchmark** — workflows against the real Store, kernels, and dispatcher.
- **CLI `--version`** — prints `openai4s 0.2.0` without requiring a subcommand.

### Linux install (end user)

```bash
tar -xzf OpenAI4S-0.2.0-linux-x86_64.tar.gz
cd OpenAI4S-0.2.0-linux-x86_64
./OpenAI4S                 # daemon + browser at http://127.0.0.1:8760/
./install.sh               # optional: CLI on PATH + application-menu entry
# recommended:
#   Debian/Ubuntu: sudo apt install bubblewrap
#   Fedora:        sudo dnf install bubblewrap
#   Arch:          sudo pacman -S bubblewrap
```

Headless: `./bin/openai4s serve --no-open`. Data lives in `~/.openai4s`. The R
kernel is not bundled; add it with `./bin/openai4s setup` after installing
micromamba/mamba/conda. Only `x86_64` is published; arm64 Linux installs from
PyPI (`pip install openai4s`).

### Known limitations to state in the public notes

- The Linux sandbox tier is **beta**. The full filesystem-and-egress bubblewrap
  smoke is still a manual run (`OPENAI4S_KERNEL_SANDBOX=enforce uv run python -m
  harness.smoke.linux_sandbox`). CI proves the private-PID interrupt path.
- `OPENAI4S_REQUIRE_TOKEN=0` remains a loopback-only escape hatch until 0.3.0
  (`LEGACY_TOKEN_OPT_OUT_REMOVED_IN`). Default is still token-required.
- macOS `--mode release` still cannot pass: Developer ID + notarization is
  unreachable in this tree. Do not flip the GitHub release public until the
  macOS owner decides how to handle that gate.
- Do **not** tag, `gh release create`, or publish to PyPI from a packaging
  branch. One annotated `v0.2.0` on `main` after all three platforms are ready.

## Reproducible Linux build

Run this on Linux x86_64 (native). The script can cross-build from macOS, but
`verify_linux_bundle.py`'s import probe only executes on a matching Linux host.

### System packages

Debian/Ubuntu (this is what the Linux packaging VM used):

```bash
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
  ca-certificates curl tar gzip rsync coreutils
# optional, for doctor/sandbox smoke on the build host:
# sudo apt-get install -y bubblewrap
```

Install `uv` if it is not already on `PATH`:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
# then ensure ~/.local/bin is on PATH
```

Fedora: `sudo dnf install tar gzip rsync curl`. Arch: `sudo pacman -S tar gzip rsync curl`.

No extra compilers are required. The bundle embeds a relocatable CPython from
`uv python install` and only manylinux wheels (`scripts/bundled_packages.txt`).
Pillow is pulled ephemerally by `uv run --no-project --with pillow` when the
icon ladder is sliced.

### Commands

From a checkout whose `[project] version` and `openai4s.__version__` are both
`0.2.0`:

```bash
# 0) identity
git rev-parse HEAD
uv run --locked python scripts/verify_release_tag.py v0.2.0

# 1) lightweight control env (pytest / mypy / uv build)
uv sync --locked --extra science

# 2) wheel + sdist
uv run --locked python scripts/source_secret_scan.py
uv build --no-sources --out-dir dist --clear
uv run --locked python scripts/verify_release_artifacts.py dist

# 3) Linux desktop tarball (slow: downloads CPython 3.13 + science wheels)
bash scripts/build_linux_bundle.sh
python3 scripts/verify_linux_bundle.py dist/OpenAI4S-0.2.0-linux-x86_64.tar.gz

# 4) optional receipt, same shape the release job writes
uv run --locked python scripts/release_receipts.py \
  --kind linux --source-sha "$(git rev-parse HEAD)" \
  dist/OpenAI4S-0.2.0-linux-x86_64.tar.gz
```

On macOS, step 3 is `bash scripts/build_macos_dmg.sh` then
`python3 scripts/verify_macos_bundle.py dist/OpenAI4S-*.dmg`. On a machine that
already has the Linux tarball, the Windows zip is
`bash scripts/build_windows_zip.sh` (it wraps that exact `.tar.gz`).

### Smoke the unpacked Linux bundle

```bash
tar -xzf dist/OpenAI4S-0.2.0-linux-x86_64.tar.gz -C /tmp
APP=/tmp/OpenAI4S-0.2.0-linux-x86_64
test "$(cat "$APP/VERSION")" = "0.2.0"
"$APP/bin/openai4s" --version          # openai4s 0.2.0
"$APP/bin/openai4s" doctor
OPENAI4S_DATA_DIR=/tmp/openai4s-smoke \
  "$APP/bin/openai4s" serve --host 127.0.0.1 --port 8760 --no-open &
# wait for listen, then:
curl -fsS http://127.0.0.1:8760/ >/tmp/index.html
"$APP/bin/openai4s" stop
```

The daemon mints a loopback access token by default. `curl` of `/` without it
is expected to be 401 or the HTML recovery page; the workbench document is
still served to a browser that presents the token. For an unauthenticated
liveness check use `/health` if the gate allows it, otherwise open the URL
printed on stderr (`http://localhost:8760/?token=…`).

### Offline suite on the build host

```bash
OPENAI4S_SECRET_STORE=plaintext \
  uv run pytest -n auto --maxprocesses=4 --dist loadfile
uv run mypy
```

One artifact-capture test
(`test_capture_detects_same_length_rewrite_that_restores_mtime`) can fail on
kernels whose `ctime` resolution is coarse. That is a host filesystem artifact,
not a product bug — do not "fix" it in the tree.
