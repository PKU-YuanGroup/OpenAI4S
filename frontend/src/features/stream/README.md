# frontend/src/features/stream

[中文说明](README_zh.md)

Live tool-output cap. Port of app.js `appendLiveOutput`: 1MB, then the truncation marker; further appends are no-ops.

## Files

| File | Responsibility |
| --- | --- |
| [`cap.ts`](cap.ts) | `appendLiveOutput`, `LIVE_OUTPUT_CHAR_CAP`, `LIVE_OUTPUT_TRUNCATION`. |
| [`cap.test.ts`](cap.test.ts) | Under-cap concat; truncation idempotent. |
