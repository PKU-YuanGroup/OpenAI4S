# frontend/src/features/autocomplete

[English](README.md)

F-12 作曲框（`@` / `#` / `/`）与右侧编辑器自动补全。关键词表来自 F-08 `features/md/highlight.ts` 的 `editorKeywords`——本车道不另留一份 EDKW。window 名字（`ac`、`edacTeardown`）由本模块赋值，不再留给 F-05 占位。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`detect.ts`](detect.ts) | `acDetectFrom`（`@/#/`）与 `edacDetectFrom`（ASCII 标识符 ≥2）。 |
| [`detect.test.ts`](detect.test.ts) | 触发词解析：边界、空 query、词中、汉字/IME。 |
| [`rank.ts`](rank.ts) | 作曲框过滤 + 上限 8；编辑器先关键词再缓冲区标识符。 |
| [`rank.test.ts`](rank.test.ts) | 排序、按身份去重、F-08 词表、无私有 EDKW。 |
| [`composer.ts`](composer.ts) | 现场 `ac` 控制器、项目文件缓存、`#composer-ac` 弹出层。列表晚于更新按键返回的补全会被丢弃；列表到达后按光标处当前的 token 过滤并确定替换起点。 |
| [`catalog.ts`](catalog.ts) | `loadSkillsCatalog`：`/` 补全与 `send()` 的 `/skill` 指令共用的唯一技能目录加载器（命令面板也可改用它）。并发调用共享同一个请求；读取失败时 reject 且不缓存，下一次调用会重试。 |
| [`composer.test.ts`](composer.test.ts) | 文件列表乱序返回：以最新按键为准；替换起点取加载完成后光标处的 token；光标已离开 token 时关闭弹层。技能目录读取失败后 `/` 补全能恢复；共享目录不缓存失败，并发加载只发一次请求。 |
| [`editor.ts`](editor.ts) | 每编辑器一个控制器、光标镜像、`execCommand('insertText')`。编辑器在挂载处自行绑定（`bindEditorAutocomplete`）；`watchEditAreas` 只绑定一次当时已有的，不再对整个文档做 MutationObserver。 |
| [`index.ts`](index.ts) | `installAutocomplete` 往 window 赋值；绑定作曲框与 `.edit-area`。 |
| [`install.test.ts`](install.test.ts) | `ac` 是现场对象；`edacTeardown` 通过 `isReady`；不会用 MutationObserver 监视整个文档来找编辑器文本框。 |
