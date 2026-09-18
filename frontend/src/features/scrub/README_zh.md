# frontend/src/features/scrub

[English](README.md)

给用户可见字符串涂抹凭证样串。移植 app.js `publicText`，另有用于模型 id 与协议名的 `publicModelId`：仅有凭证前缀（如 `ark-code-latest`）并不等于密钥。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`scrub.ts`](scrub.ts) | `publicText`；`publicModelId`（带前缀的串只有同时带有密钥样片段时才涂抹：含数字且 30 字符以上的主体（无论如何分段，Ark 的 `ark-` + UUID 密钥即如此），或较长的无分隔随机段）。 |
| [`scrub.test.ts`](scrub.test.ts) | Bearer / 密钥样串 / query 涂抹；超限省略号；模型 id 保持原样而 OpenAI、Anthropic 与 Ark（`ark-` + UUID）密钥样串仍被涂抹。 |
