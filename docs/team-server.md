# Running OpenAI4S as a lab server

[中文说明](team-server_zh.md)

This is the operator's page for the multi-user mode: what to turn on, in
what order, and what each switch actually exposes. The design decisions
behind it live in [`team-server-plan.md`](team-server-plan.md); this page
is what you do.

Everything here is **off by default**. A default install is the
single-user workbench it has always been — same routes, same behaviour,
same tests (INV-1). Nothing below happens because you upgraded.

## 1. Turn team mode on

```bash
export OPENAI4S_TEAM_MODE=1
openai4s serve
```

With team mode on, the browser path is `/login` and nothing else answers
without a session cookie. Create the first account from the machine
itself:

```bash
openai4s user add alice --role admin
```

The loopback CLI is admin-equivalent by decision D2 — whoever can read
the access-token file on the host owns the box anyway — and its actions
are audited as `cli` rather than impersonating a human account.

**The bind address is still the security boundary.** Team mode adds
accounts; it does not make the daemon safe to expose. Put it behind a
reverse proxy that terminates TLS, or reach it over SSH — see
[`security.md`](security.md). A password over plaintext HTTP on a lab
network is a password on that network.

## 2. Projects, visibility and quotas

A session belongs to whoever created it and, optionally, to a project.
`project` visibility means the project's members can read it; `private`
means the owner alone. A session with no project is private by
construction, and a session with no ownership row at all — pre-team
history, CLI runs, demo seeds — is admin-only. That last one is a
deliberate fail-closed choice: "we do not know whose this is" must not
resolve to "everyone's".

An admin reading a private session writes a `admin_read_private` row to
the audit log. That is the whole of what admin access costs, and it is
per view rather than per session.

Quotas are set per user or per project, per kind, per window:

```bash
curl -X PUT .../api/v1/team/quotas -d '{"scope":"user","scope_id":"...",
  "kind":"llm_output_tokens","limit_amount":2000000,"window":"month"}'
```

Only kinds with a real enforcement point may be set. A limit nobody
consults is worse than no limit, because somebody will plan around it.

## 3. Cluster sessions (optional)

Two things have to be true before a session can run on a scheduler: the
site has to be described, and the daemon has to accept workers dialling
back.

**Describe the site** in `<data_dir>/cluster.toml`. Profiles are the only
vocabulary users ever see — the queue and service class each maps to stay
in this file (decision D5, INV-2):

```toml
job_name_prefix = "openai4s"

[profiles.cpu-interactive]
cpus = 8
memory_mb = 32768
walltime_s = 14400
partition = "compute"          # never leaves this file

[profiles.gpu-interactive]
cpus = 16
memory_mb = 131072
gpus = 1
walltime_s = 14400
partition = "gpu"
qos = "interactive"
```

**Accept workers** by naming an address the compute nodes can reach:

```bash
export OPENAI4S_WORKER_LISTEN=0.0.0.0:8761      # where workers dial in
export OPENAI4S_WORKER_ADVERTISE=head01.lab     # what they are told to dial
```

`OPENAI4S_WORKER_LISTEN` is what turns the listener on at all. It is off
by default because a listener on every laptop that will never run a
cluster job is an attack surface, not a convenience. Set
`OPENAI4S_WORKER_ADVERTISE` whenever the bind address is not a name a
compute node can resolve — binding `0.0.0.0` is how you accept from
anywhere, and `0.0.0.0` is not a place anything can dial.

What protects that port is the bootstrap credential, not the network. A
worker presents an HMAC over `(allocation, epoch, rank, expiry, nonce)`
signed with a per-daemon secret, and the gateway verifies and burns it
**before** a single protocol byte is exchanged — this socket carries Host
RPC, so a listener that served first and checked later would be a remote
execution surface for the duration of "later". Refusals say only
"refused": the difference between expired, replayed and forged is an
oracle for somebody guessing.

The credential travels as a `0600` file and the scheduler is told only
its path (INV-9). A job's environment is readable by anyone who can ask
the scheduler about the job, so the submission environment refuses
credential-shaped variable names outright.

### Leases

A cluster session holds real resources, so it has two clocks: an idle TTL
(default 2h) and a maximum lifetime (default 48h). **A worker being alive
is not a user being present** — a session whose kernel is healthy and
whose socket is connected is still idle if nobody has run anything in it,
and it is still holding what somebody else is queued for. Only a user's
execution, or an explicit renewal, renews the lease.

Which clock ran out decides what the user is told:
`SESSION_IDLE_TIMEOUT` means "come back and it will be here again";
`SESSION_MAX_LIFETIME_EXCEEDED` means "this one is over regardless".

### When a node dies

Recovery is `WORKSPACE_ONLY` and says so. The files survive because they
were always on the shared filesystem; the kernel's memory does not —
variables, imports, the seed somebody set three cells ago. The session
continues on a new epoch and the UI raises a `KERNEL_STATE_LOST` banner,
because results produced after a silent reconnect look exactly like
results from the session that was lost (INV-11).

`CHECKPOINT` is declared and refused with `501`. A real implementation
needs process-level snapshotting the cluster must also support, and half
of one would restore some state and quietly drop the rest — the worst of
the three possible behaviours.

## 4. Per-user LLM keys (optional)

A member can supply their own credential per provider through
`PUT /api/v1/auth/me/llm-key`. The key goes to the same secret broker as
every other credential; the database keeps a reference. Absence is the
fallback, so a member who sets nothing runs on the group's key exactly as
before.

A configured key that cannot be read **refuses the turn** rather than
falling back. The user asked for their own credential; quietly charging
the group is a decision they did not make.

## 5. Reaching it from outside the lab

The daemon binds loopback by default and that is the recommendation. Two
supported ways to reach it from elsewhere, in order of preference:

1. **SSH tunnel.** `ssh -N -L 8760:127.0.0.1:8760 you@lab-host`. Nothing
   is exposed, authentication is your existing SSH setup, and there is no
   new component to operate.
2. **Reverse proxy with TLS** on the lab network, with team mode on. The
   proxy terminates TLS and forwards to loopback; the `Host` allowlist
   stays on, so you must include the proxy's hostname in
   `OPENAI4S_ALLOWED_HOSTS`.

**The relay is not a third way to run a lab server.** `openai4s relay` and
`openai4s share` exist for a different purpose — a read-only, redacted
snapshot of *one* session, sent through a tunnel the daemon dials out to
([`webshare.md`](webshare.md)). The relay sees plaintext, and the share
projection is deliberately not a login surface: it carries no cookie, no
mutation routes, and no live kernel. Pointing it at a team deployment
would publish a projection of one session, not serve the workbench.

If you need the workbench itself from off-site, use option 1 or 2.

## 6. What to check after setting it up

```bash
openai4s doctor                     # configuration, credentials, kernels
curl -s localhost:8760/api/v1/auth/status
```

The daemon prints why a cluster is unavailable rather than refusing to
boot: a malformed `cluster.toml` degrades to local-only with the reason on
stderr, and so does a worker listener that cannot bind. An operator's typo
in a config file should not take the workbench down for everybody.
