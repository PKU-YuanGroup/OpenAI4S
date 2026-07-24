# `workflows/telemetry-identity/`

**Revoking telemetry destroys the identity with it** — Revocation destroys permission and identity in one operation; re-consenting mints a fresh one, and the two participation periods must not be linkable.

Steps: `telemetry_identity_cycle`
Permissions: `telemetry:consent`
Declared artifacts: —

| File | Purpose |
| --- | --- |
| `workflow.json` | The versioned manifest: steps, permissions, declared artifacts, failure conditions, and the cases below. Version `1.0.0`. JSON rather than YAML for the same reason the core is, and versioned because a benchmark whose cases can change silently measures nothing across time. |

## Cases

| Case | Declared outcome | What it pins |
| --- | --- | --- |
| `telemetry-identity/revoked` | `success` | After a revoke, the old payload must not be sent |
| `telemetry-identity/current` | `success` | The current identity's payload is not blocked by this check |

## Failure conditions the manifest declares

- the old identity is still sent after a revoke
- re-consenting reuses the old identity
