---
name: cua
description: Operate the CUA cloud Windows computer through the managed `cua` MCP connector when a task needs a real remote desktop — ping first, delegate the user's verbatim objective, then drive the outcome state machine.
origin: openai4s
---

# CUA cloud computer

CUA is a hosted Windows desktop operated by its own cloud-side agent. OpenAI4S
hands it a whole objective and follows the result; it does not script
individual mouse or keyboard actions. Use only the managed `cua` connector and
its six tools — `cua_ping`, `cua_delegate`, `cua_watch`, `cua_answer`,
`cua_cancel`, `cua_observe` — through `host.mcp.call("cua", ...)` in a Python
cell. `host.mcp.tools("cua")` lists the same six descriptors.

`cua_ping`, `cua_watch`, and `cua_observe` are read-only and allowed by
default. `cua_delegate`, `cua_answer`, and `cua_cancel` operate a real cloud
desktop, so each call asks for user approval by default; the user can
pre-approve them in the permission panel.

## Input

Accept the user's goal as one non-empty string named `objective` — their own
words, unchanged.

## Result envelope

`host.mcp.call` returns `{"is_error": bool, "text": str, "raw": {...}}`, where
`text` joins the content text blocks and `raw["structuredContent"]` carries the
structured result when the server sends one. Every recipe below extracts the
CUA JSON envelope with this helper:

```python
import json

def cua_envelope(result):
    raw = result.get("raw") if isinstance(result, dict) else None
    structured = raw.get("structuredContent") if isinstance(raw, dict) else None
    if isinstance(structured, dict):
        return structured
    text = result.get("text") if isinstance(result, dict) else None
    if isinstance(text, str) and text.strip():
        try:
            parsed = json.loads(text)
        except ValueError:
            return None
        if isinstance(parsed, dict):
            return parsed
    return None
```

## Preflight: cua_ping

When connectivity is uncertain, check it with `cua_ping` and nothing else;
never use another CUA tool as a connectivity test. The call is read-only: it
creates no task, observes no desktop, and issues no access link.

```python
ping = host.mcp.call("cua", "cua_ping", {})
text = ping.get("text") if isinstance(ping, dict) else ""
if isinstance(ping, dict) and ping.get("is_error"):
    # Auth failures arrive as a JSON document like
    # {"error": "AuthError", "status": 401, ...} in the content text.
    detail = None
    if isinstance(text, str) and text.strip():
        try:
            detail = json.loads(text)
        except ValueError:
            detail = None
    if (isinstance(detail, dict) and detail.get("error") == "AuthError") or (
        isinstance(text, str) and "AuthError" in text
    ):
        raise RuntimeError(
            "CUA API Key 无效或未授权，请在 Customize → Connectors 配置"
        )
    raise RuntimeError(f"cua_ping failed: {text!r}")
env = cua_envelope(ping)
if not (isinstance(env, dict) and env.get("ok") is True):
    raise RuntimeError(f"cua_ping did not confirm connectivity: {text!r}")
print("CUA connectivity OK")
```

The credential is a dedicated CUA API Key saved under Customize → Connectors →
CUA. It is not the `ark-` Agent Plan Key; the CUA service rejects `ark-` keys.

## Delegate and follow the outcome

Pass `objective` to `cua_delegate` verbatim: no decomposition, no rewriting, no
hidden requirements, no implementation steps the user did not provide. Leave
`wait_ms` as `None` (the server default) unless the user specified a wait.

```python
if type(objective) is not str or not objective.strip():
    raise ValueError("objective must be a non-empty string")

def cua_drive(env):
    """Wait out in_progress; return the first non-in_progress envelope."""
    while isinstance(env, dict) and env.get("outcome") == "in_progress":
        # A slow task is normal: keep watching, never cancel for slowness,
        # and never answer the user's goal yourself while it runs.
        watch = host.mcp.call(
            "cua",
            "cua_watch",
            {"invocation_id": env.get("invocation_id"), "wait_ms": None},
        )
        env = cua_envelope(watch)
        if env is None:
            raise RuntimeError(f"cua_watch returned no envelope: {watch!r}")
    return env

call = host.mcp.call(
    "cua",
    "cua_delegate",
    {"objective": objective, "wait_ms": None},
)
if isinstance(call, dict) and call.get("is_error"):
    raise RuntimeError(f"cua_delegate failed: {call.get('text')!r}")
env = cua_envelope(call)
if env is None:
    raise RuntimeError("cua_delegate returned no envelope")
env = cua_drive(env)

outcome = env.get("outcome")
result_text = (env.get("result") or {}).get("text")
if outcome == "completed":
    # result.text is the authoritative final answer. Never synthesize one
    # from screenshots or progress messages once it exists.
    print(result_text)
elif outcome == "needs_input":
    request = env.get("input_request") or {}
    print("CUA needs the user's answer before it can continue.")
    print(f"question: {request.get('question')}")
    for choice in request.get("choices") or []:
        if isinstance(choice, dict):
            print(f"  - {choice.get('id')}: {choice.get('label')}")
    print(f"invocation_id: {env.get('invocation_id')}")
else:
    # failed or cancelled — report it; do not silently retry elsewhere.
    print(f"CUA invocation ended as {outcome!r}: {result_text!r}")
```

On `needs_input`, end the cell with the question as its output, relay it to the
user, and wait for their next message — never invent an answer. Once the user
has answered, resume the same invocation:

```python
followup = host.mcp.call(
    "cua",
    "cua_answer",
    # user_answer is the user's reply to input_request.question.
    {"invocation_id": invocation_id, "answer": user_answer, "wait_ms": None},
)
env = cua_drive(cua_envelope(followup))
# Handle `env` exactly as after cua_delegate.
```

A long delegation does not have to hold one cell open. It is equally valid to
note the `invocation_id`, end the cell, and check again later — `cua_watch`
also recovers state after a disconnect:

```python
status = host.mcp.call(
    "cua",
    "cua_watch",
    {"invocation_id": invocation_id, "wait_ms": None},
)
env = cua_envelope(status)
```

## Desktop visibility: cua_observe

`cua_observe` is read-only visibility — it starts nothing and operates nothing.
Use it when the user wants to see the desktop or take over manually.

```python
observe = host.mcp.call(
    "cua",
    "cua_observe",
    {"invocation_id": None, "include_screenshot": False},
)
env = cua_envelope(observe)
access_url = env.get("access_url") if isinstance(env, dict) else None
print(access_url)
```

Pass an `invocation_id` to observe that invocation's environment; `None` means
the user's default environment. The `access_url` is short-lived: if the user
reports it failed to open or expired, call `cua_observe` again for a fresh one.
Never use `cua_observe` to decide whether a task finished — that is
`cua_watch`'s job.

## Cancel: cua_cancel

Only when the user explicitly asks to stop — never because a task takes long.
Cancellation does not roll back desktop actions that already happened.

```python
cancel = host.mcp.call("cua", "cua_cancel", {"invocation_id": invocation_id})
print(cua_envelope(cancel))
```

## Rules

These are the CUA server's own usage rules. Follow them exactly.

- When connectivity is uncertain, run `cua_ping` first; never use any other CUA
  tool as a connectivity test.
- Pass the user's original objective to `cua_delegate` verbatim. Do not
  decompose it, rewrite it, add hidden requirements, or prescribe
  implementation steps the user did not explicitly provide.
- `needs_input`: relay `input_request.question` to the user; call `cua_answer`
  only after the user has actually answered.
- `in_progress`: keep waiting with `cua_watch` or check back later. Never
  cancel a task for taking long, and never answer the user's goal yourself
  while the outcome is `in_progress`.
- Once a goal is delegated to CUA, do not pursue the same goal with your own
  search, browser, or other tools unless the user explicitly redirects.
- `completed`: `result.text` is the authoritative final result. When it exists,
  never derive the answer from screenshots or progress messages instead.
- `cua_cancel` only on the user's explicit request; it does not roll back
  actions already performed.
- `cua_observe` is desktop visibility only. Its `access_url` is temporary —
  fetch a new one when it expires — and it never decides task completion.
