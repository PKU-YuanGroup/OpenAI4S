---
title: Deployment
description: Deploy the public static documentation and operate the OpenAI4S workbench as two separate services.
canonical: true
last_verified: 2026-07-14
verification: code-and-tests
status: current
audience: [operators, contributors]
verified_commit: a92e736
owner: OpenAI4S maintainers
---

# Deployment

OpenAI4S has two deployable surfaces with different trust models:

1. **Documentation** is a public, static VitePress build. It belongs at `openai4s.org/docs/` and can be served by an ordinary static web server or CDN.
2. **Workbench** is a stateful, code-executing, single-user daemon. Keep it on loopback on a local or trusted host and reach it through SSH or a trusted VPN.

Never publish the Workbench by placing it behind the public documentation path. The Workbench includes host-side mutation routes and a local compute endpoint; its optional access token is not a multi-user authentication system.

## Public documentation

The documentation toolchain is not part of the zero-dependency Python core. It uses the versions pinned in `package-lock.json` and builds with Node.js:

```bash
npm ci
npm run docs:build
```

The output is `docs/.vitepress/dist/`. The VitePress base is `/docs/`, so test the generated site under that prefix, not at `/`:

```bash
npm run docs:preview
```

Publish immutable release directories and switch one symlink only after the build has passed. A representative layout is:

```text
/srv/openai4s-docs/
  releases/<source-revision>/
  current -> releases/<source-revision>/
```

Copy only the generated files into a new release directory. Do not copy `.env`, the Workbench data directory, source-tree credentials, or server configuration into the document root. Point the web server's `/docs/` location at `current`, preserve the existing landing page at `/`, and redirect `/docs` to `/docs/`.

A static-server configuration should implement the equivalent of:

```nginx
location = /docs {
    return 308 /docs/;
}

location /docs/ {
    # The server document root contains docs -> /srv/openai4s-docs/current.
    # Prefer the generated clean-URL HTML file over a same-named section directory.
    try_files $uri.html $uri $uri/ =404;
}
```

Exact `root`/`alias` paths are deployment-specific. Validate them with the server's configuration test before reload. After publication, check the English and Chinese roots, a clean URL, a search, static assets, and a direct deep-link refresh. Rollback is an atomic switch of `current` to the preceding immutable release followed by a web-server reload; it does not involve the Workbench database.

## Workbench prerequisites

- Python 3.10 or newer on macOS or Linux. Native Windows scientific kernels are not supported; use WSL2.
- `uv` for the source checkout workflow.
- A real `Rscript` only if R Cells are required.
- Seatbelt (`sandbox-exec`) on supported macOS or bubblewrap (`bwrap`) on Linux if the kernel sandbox is required.
- Optional conda/mamba/micromamba for the four environment specifications.
- Optional SSH, Docker, GPUs, and provider credentials only for the corresponding remote-compute paths.

For a source deployment:

```bash
git clone https://github.com/PKU-YuanGroup/OpenAI4S.git
cd OpenAI4S
./setup.sh
uv run pytest
```

`setup.sh` creates `.venv`, installs the `science` extra and development tools, and installs the pre-commit hook. A packaged production release should instead follow the artifact-validation procedure in [Release validation](../release-validation.md) and use a release-specific virtual environment.

### What “pure stdlib core” means

The package has no mandatory runtime dependency: the engine, LLM transport, daemon, WebSocket implementation, storage layer, and kernel protocol are standard-library Python. This does **not** mean a deployed scientific runtime contains no third-party packages.

On `serve`, the Gateway calls `ensure_core(background=True)`. Missing packages in the scientific and networking stack are installed into the daemon interpreter in a background thread with `pip`, and startup continues even if that installation fails. This has three operational consequences:

- a listening `/health` endpoint does not prove that the scientific package set is ready;
- first boot may need package-index egress and may mutate the virtual environment;
- an immutable or offline deployment must populate and verify the environment before service start.

To prepare the exact release environment synchronously:

```bash
.venv/bin/python -c \
  'from openai4s.kernel.preinstall import ensure_core; print(ensure_core(background=False))'
```

Run this during a controlled release build, then inspect `GET /api/environments/status` after start. Do not let multiple releases share a mutable virtual environment.

## Dedicated service account

Run the daemon under a dedicated account that does not own unrelated repositories, cloud credentials, browser profiles, or administrator files. Create the data directory before first boot:

```bash
install -d -m 0700 -o openai4s -g openai4s /var/lib/openai4s
```

Use a private environment file owned by root or the service account and mode `0600`. At minimum set:

```dotenv
OPENAI4S_HOST=127.0.0.1
OPENAI4S_PORT=8760
OPENAI4S_DATA_DIR=/var/lib/openai4s
OPENAI4S_KERNEL_SANDBOX=enforce
OPENAI4S_NO_OPEN=1
```

Choose `auto` instead of `enforce` only if visible unsandboxed degradation is acceptable for this trusted-host deployment. Keep provider secrets out of command arguments, unit files checked into source control, and world-readable environment files.

## Service supervision

`openai4s serve` is a foreground process and fits an OS supervisor. This minimal systemd example intentionally avoids hardening options that would silently break scientific interpreters, bubblewrap, SSH, or remote compute:

```ini
[Unit]
Description=OpenAI4S single-user scientific workbench
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=openai4s
Group=openai4s
WorkingDirectory=/opt/openai4s/current
EnvironmentFile=/etc/openai4s/openai4s.env
UMask=0077
ExecStart=/opt/openai4s/current/.venv/bin/openai4s serve --no-open
Restart=on-failure
RestartSec=5
TimeoutStopSec=60

[Install]
WantedBy=multi-user.target
```

Any additional supervisor sandboxing must be tested with Python and R worker startup, the selected OS kernel sandbox, artifact writes, package imports, SSH, and graceful shutdown. Do not add a second process manager around the same data directory.

## Remote access

Keep the bind on loopback. For one operator, an SSH tunnel is the simplest supported path:

```bash
ssh -N -L 8760:127.0.0.1:8760 user@trusted-host
```

Then open `http://127.0.0.1:8760/` locally. A trusted VPN that restricts reachability to the operator is also acceptable, but retaining the daemon's loopback bind behind a local tunnel or authenticated reverse proxy is safer than a non-loopback bind.

Binding to a non-loopback address activates one process-wide bearer-like token,
generated at startup and reusable for that process lifetime through the query
parameter or cookie. It provides neither TLS, user identities, role separation,
session isolation, brute-force controls, nor a complete CSRF boundary. It is a
last defensive layer for a trusted network, not authorization to expose the
daemon to the Internet.

## Upgrade

Use release directories so code rollback does not depend on a mutable checkout:

1. Record the current source revision and runtime versions.
2. Stop admission and stop the daemon cleanly.
3. Take a stopped, whole-data-directory backup as described in [Data management](data-management.md).
4. Prepare a new release directory and its own virtual environment.
5. Run offline tests, release artifact checks when applicable, synchronous scientific-package preparation, and sandbox self-tests.
6. Switch the `current` symlink to the new release and start the daemon.
7. Verify `/health`, Python and R startup as applicable, sandbox status, an existing session and artifact, a new tool-only turn, and a scientific Cell.

The Store applies schema migrations when the new process opens the database. Treat the first open as a data-changing step even when no user session is running.

## Rollback

If validation fails:

1. Stop the new daemon.
2. Preserve its data directory separately for diagnosis.
3. Restore the complete pre-upgrade data snapshot; do not combine an older SQLite file with newer artifact/CAS/workspace trees.
4. Switch `current` back to the matching previous code release.
5. Start on loopback and repeat the restore validation checklist.

There is no general down-migration contract. Switching only the code while retaining a database already migrated by a newer release is not a safe rollback plan.
