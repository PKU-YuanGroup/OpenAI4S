# `workflows/delegation/`

**What the parent is told a child did** — A child's `submitted` says only that it chose to submit; it says nothing about whether the task is done. This workflow drives the real `DelegationRunner`, real child `Agent`s and the real Store with a scripted model, and asserts the machine-readable `task_status` on both surfaces the parent can read: the returned envelope and the durable projection.

Terminal states only. There are no timing handshakes here on purpose — delegation timing is flaky on a loaded runner, and a case that waits on it measures the runner rather than the contract.

Steps: `open_session`, `run_delegation`
Permissions: `workspace:read`, `workspace:write`, `kernel:execute`

| File | Purpose |
| --- | --- |
| `workflow.json` | The versioned manifest: steps, permissions, failure conditions, the scripted child replies, and the cases below. Version `1.0.0`. |

## Cases

| Case | Declared outcome | What it pins |
| --- | --- | --- |
| `delegation/blocked-child-is-not-done` | `provenance` | A child that honestly declares `blocked` reads as `blocked` in the envelope AND in the durable projection, and never as complete. Its lifecycle status is still `done` — it did submit — which is exactly why `task_status` is the column a parent must read |
| `delegation/completed-child-is-done` | `provenance` | A genuinely completed child agrees with itself across both records |
| `delegation/max-turns-child-fails` | `provenance` | A child that talks without acting until its turn budget runs out lands in the `failed` lifecycle with `max_turns` durably recorded as its stop reason |
