# `openai4s/telemetry/`

Opt-in, anonymous telemetry. **Off by default**: with no consent recorded,
nothing here opens a socket, resolves a name, or starts a thread.

The frozen decision ([`docs/v02-decisions.md`](../../docs/v02-decisions.md)) is
"counts and enumerations only — zero free text", to be enforced by "an
allowlist … asserting the outgoing payload contains no key outside it".

A **key** allowlist does not do that job, and the attack is one line long:

```python
{"error_type": "ValueError"}                                     # an enumeration
{"error_type": "FileNotFoundError: /home/y/unpublished/cohort.csv"}
```

Same key. The second carries a research subject and a person's home directory,
and unpublished research data is exactly what this product handles. So the
allowlist here is over **values**: every field declares a domain, and a value
outside its domain never reaches the wire.

| File | Purpose |
| --- | --- |
| `__init__.py` | Re-exports `classify_error` and the two sanitisers. Imports nothing that can send; importing this package must remain free of side effects, because "off by default" has to hold at import time too. |
| `schema.py` | The complete declaration of what telemetry may say: five domain classes (`Enum`, `Count`, `Bucket`, `Version`, `OpaqueId`) and the field table. There is deliberately no `STRING`, `TEXT`, `JSON`, `MAP` or `LIST` domain, so adding a field that *could* carry prose requires adding a domain class — a diff that reads as a privacy decision rather than one more routine line. `classify_error` is the only permitted way to turn an exception into a value, by membership and never by passthrough. |
| `consent.py` | Whether telemetry may run, and the identity it runs under. The install id lives **inside** the consent record, so revoking destroys permission and identity in one operation — with two rows, "revoke" could clear the flag and leave a stable identifier behind, and an id that outlives its consent is not anonymous but pseudonymous with a longer memory than the user agreed to. Re-consenting mints a fresh id. `OPENAI4S_TELEMETRY` can only turn telemetry **off**: refusing needs no permission, but a line in a Dockerfile is not consent. |
| `wire.py` | The only code that turns records into bytes. `seal()` builds the envelope field by field from the declaration — nothing is copied through from a dict someone else assembled — and returns a `SealedPayload` whose constructor requires a module-private sentinel, so no other file can hand the transport bytes that skipped validation. Returns `None` rather than an empty envelope: sending "I have nothing to say" is still a packet, and still tells a listener this install is running right now. |
| `sender.py` | The only code in the tree that transmits telemetry, and everything interesting about it is a refusal. No consent, no send — checked *before* anything is resolved, because a DNS lookup of `log.openai4s.org` is itself the signal consent is asked for. No redirect (a third party choosing where research telemetry goes), no plain HTTP, no credentials in the URL, no payload that did not come from `seal`. No queue that survives a revoke, no retry that outlives one, no flush at exit. |
| `emit.py` | The one call the rest of the program makes. It never raises (a telemetry bug must not become a failed turn), never blocks (the send is on a daemon thread, so a slow collector adds nothing to a turn), and reads consent every time (no cached "enabled" outliving a revoke). On the common path — no consent — it is one settings read that returns. `turn_outcome` maps the engine's stop-reason vocabulary to the declared `outcome` enum as a pure, tested function rather than a guess at the call site. |
| `gate.py` | The transmission gate: one worker, one bounded queue, one revoke barrier. `transmitting()` encloses the consent read *and* the socket open, and `consent.revoke` takes the same lock — so once revoke returns, no request can begin. Delivery is a single worker behind a 32-deep queue; past it the newest payload is dropped and counted, because dropping the oldest would reorder a best-effort stream and blocking the caller would put telemetry on the critical path. A revoke also discards whatever is queued: it was sealed under an identity that no longer exists. |
| `collector.py` | The receiving end, a **reference implementation** — here so the wire format has a second reader, not so this repo runs a public server. Mirrors `openai4s/share/relay.py`: a stdlib `ThreadingHTTPServer`, a per-client token bucket, a body cap enforced *before* the read, and a Host allowlist (the relay's DNS-rebinding history is exactly what a naive copy would inherit). What it adds and a relay has no reason to: it **does not trust the sender** — every envelope is re-validated against the same declaration the client sanitises with, so "counts and enumerations only" holds even against a client that ignores it. Stores counts keyed by event, never a per-install log. |

## Fields that look like enumerations and are not

Each of these was verified against real code paths, and each would pass a
casual review:

- **`type(e).__name__`** — a kernel cell runs agent-authored code, so a class
  name is user-authored text. `class Cohort4471NonResponder(Exception)` names a
  research subject.
- **a skill's name** — taken verbatim from its `SKILL.md` frontmatter.
- **an environment name** — a directory name under a user-configured root.
- **an LLM provider's `error.code`** — providers return whole sentences there,
  routinely echoing the request.

## What must never be reused here

`openai4s.observability.redact` is calibrated for *credentials*, and
`_looks_opaque` exempts anything starting with `/` on purpose, with a test
pinning that. Pointed at research data it does the opposite of what is wanted:
it passes an absolute path through untouched while redacting a harmless
environment name. A gate that looks like protection and is not is worse than no
gate, so a test asserts this package does not import it.
