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
