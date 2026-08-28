# Protocol fuzzing

[中文说明](README_zh.md)

Coverage-guided tests for the byte parsers exposed to untrusted WebSocket and
relay peers. The target is bounded, offline, and uses no session data or
credentials. A short run executes on every pull request and a longer run each
week through `.github/workflows/fuzz.yml`.

## Files

| File | Purpose |
| --- | --- |
| `protocol_fuzzer.py` | Feeds arbitrary bytes to the WebSocket frame reader and share-tunnel control/data decoders. Documented protocol rejections are expected; unexpected exceptions remain crashes. |
