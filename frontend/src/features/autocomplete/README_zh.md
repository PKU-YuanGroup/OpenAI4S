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
| [`composer.ts`](composer.ts) | 现场 `ac` 控制器、项目文件缓存、`#composer-ac` 弹出层。 |
| [`editor.ts`](editor.ts) | 每编辑器一个控制器、光标镜像、`execCommand('insertText')`。 |
| [`index.ts`](index.ts) | `installAutocomplete` 往 window 赋值；绑定作曲框与 `.edit-area`。 |
| [`install.test.ts`](install.test.ts) | `ac` 是现场对象；`edacTeardown` 通过 `isReady`。 |
