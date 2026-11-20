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
| [`runtime.ts`](runtime.ts) | `t` / `tOptional` / `setLang` / `applyStaticI18n` / `planModePayload`。活跃语言字典与 zh 兜底字典同时加载；首次加载后按切换语言的方式重绘静态标签，zh 兜底加载失败不会取消这次重绘。`languageRevision` 每次重绘都会递增，`t()` / `tOptional()` 会读取它，所以经由它们渲染的视图会自动重绘；`onLanguageChange` 钩子会收到刚应用的语言。没有已保存的选择（`os-lang`）时，界面语言取浏览器语言列表中按用户排序第一个受支持的语言，并随浏览器的 `languagechange` 更新；`setLang("system")` 会移除已保存的选择；存储拒绝写入时，选择只在本页保留；其他标签页的选择经 `storage` 事件同步过来。 |
| [`copy.ts`](copy.ts) | `copyLookup` / `CopyTable`：各功能本地文案表（`filesT`、`ot`、`judgmentT`）共用的查找：先查字典，再查当前语言，再查英文，最后返回键名。 `overrideLookup`：功能模块对生成词典的更正（当前语言下，表里列出的键以表为准，其余仍用词典），用于生成的中文词典里是英文的条目。 |
| [`index.ts`](index.ts) | 给后续 F 系列工作项的公开导出。 |
| [`i18n.test.ts`](i18n.test.ts) | 键集对齐、抽取结果与 app.js 的 diff、`t()` 语义（经由 `t()` 的渲染在切换语言后会重绘）、计划模式 payload、按浏览器语言顺序的首次启动检测以及跟随系统模式。 |
| [`static-i18n-race.test.ts`](static-i18n-race.test.ts) | 把语言分包挡在闸门后：字典到达前应用的静态标签保留可读的兜底文字，字典到达后连同语言切换按钮和语言钩子一起重绘。每个分包各设一道闸门：en 与 zh 同时请求；仅 zh 兜底失败时仍以英文重绘；活跃语言分包失败时仍然 reject。 |
| [`system-language.test.ts`](system-language.test.ts) | 跟随浏览器语言：加载 runtime 时会注册 `languagechange` 监听，在没有保存选择时切换页面语言；存储被阻止时，用户的选择仍在本页生效；其他标签页（共享同一存储的第二个 runtime 实例）保存语言或改回跟随系统时，本页会跟随；`theme-bootstrap.js`（在 VM 中运行）对每一组浏览器语言列表写出的 `<html lang>` 与 `systemLang` 一致。 |
| [`terminology.test.ts`](terminology.test.ts) | I18N-1：生成的 zh 词典和工作台源码里的每个中文字符串都用「产物」「单元」「标注」和 Notebook，不用「制品」「Cell / 代码单元」「批注」「笔记本」。 |
