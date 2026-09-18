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
| [`runtime.ts`](runtime.ts) | `t` / `tOptional` / `setLang` / `applyStaticI18n` / `planModePayload`。活跃语言字典与 zh 兜底字典同时加载；首次加载后按切换语言的方式重绘静态标签，zh 兜底加载失败不会取消这次重绘。 |
| [`index.ts`](index.ts) | 给后续 F 系列工作项的公开导出。 |
| [`i18n.test.ts`](i18n.test.ts) | 键集对齐、抽取结果与 app.js 的 diff、`t()` 语义、计划模式 payload。 |
| [`static-i18n-race.test.ts`](static-i18n-race.test.ts) | 把语言分包挡在闸门后：字典到达前应用的静态标签保留可读的兜底文字，字典到达后连同语言切换按钮和语言钩子一起重绘。每个分包各设一道闸门：en 与 zh 同时请求；仅 zh 兜底失败时仍以英文重绘；活跃语言分包失败时仍然 reject。 |
