# CUA Skill

This bundled Skill is the recipe for handing a user's objective to the CUA
cloud Windows computer over the managed `cua` MCP connector and following its
outcome state machine. The objective reaches the desktop verbatim, and only a
`completed` invocation's `result.text` is treated as the final answer.

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Ping first, delegate the verbatim objective, drive `cua_watch` / `cua_answer` on the outcome envelope, fetch a short-lived desktop access link with `cua_observe`, and cancel only on the user's explicit request. |
