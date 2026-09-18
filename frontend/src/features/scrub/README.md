# frontend/src/features/scrub

[中文说明](README_zh.md)

Credential-shaped substring redaction for user-visible strings. Port of app.js `publicText`, plus `publicModelId` for model ids and protocol names, where a credential prefix alone (`ark-code-latest`) is not a key.

## Files

| File | Responsibility |
| --- | --- |
| [`scrub.ts`](scrub.ts) | `publicText`; `publicModelId` (redacts a prefixed token only when it also carries a key-shaped run: a 30+ character body with digits, however chunked, as in Ark's `ark-` + UUID keys; or a long unbroken random segment). |
| [`scrub.test.ts`](scrub.test.ts) | Bearer / key-shaped tokens / query redaction; ellipsis cap; model ids kept while OpenAI, Anthropic and Ark (`ark-` + UUID) key shapes are still redacted. |
