# frontend/src/i18n

[English](README.md)

F-07 车道：机械抽取的 zh/en 字典，以及 `t()` / `tOptional` 运行时。非活跃语言是 `import()` 分包。不要手改 `zh.ts` / `en.ts`。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`extract-i18n.mjs`](extract-i18n.mjs) | 用 `new Function` 执行 app.js 的 `Object.assign(I18N.zh/en, …)` 块，写出 `zh.ts` / `en.ts`。`--check` / `--self-test`。 |
| [`extract-i18n.d.mts`](extract-i18n.d.mts) | 抽取脚本的 TypeScript 声明，供 Vitest 导入。 |
| [`zh.ts`](zh.ts) | 生成的中文字典（app.js:250-1458）。 |
| [`en.ts`](en.ts) | 生成的英文字典（app.js:1459-2668）。 |
| [`runtime.ts`](runtime.ts) | `t` / `tOptional` / `setLang` / `applyStaticI18n` / `planModePayload`。 |
| [`index.ts`](index.ts) | 给后续 F 系列工作项的公开导出。 |
| [`i18n.test.ts`](i18n.test.ts) | 键集对齐、抽取结果与 app.js 的 diff、`t()` 语义、计划模式 payload。 |
