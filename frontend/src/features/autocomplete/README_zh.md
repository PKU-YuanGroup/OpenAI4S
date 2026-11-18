# frontend/src/features/autocomplete

[English](README.md)

F-12 作曲框（`@` / `#` / `/`）与右侧编辑器自动补全。关键词表来自 F-08 `features/md/highlight.ts` 的 `editorKeywords`——本车道不另留一份 EDKW。window 名字（`ac`、`edacTeardown`）由本模块赋值，不再留给 F-05 占位。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`detect.ts`](detect.ts) | `acDetectFrom`（`@/#/`）与 `edacDetectFrom`（ASCII 标识符 ≥2）。 |
| [`detect.test.ts`](detect.test.ts) | 触发词解析：边界、空 query、词中、汉字/IME。 |
| [`rank.ts`](rank.ts) | 作曲框过滤 + 上限 8；编辑器先关键词再缓冲区标识符。`@` 文件候选只插入文件名，版本放在 `mention` 里随行携带。 |
| [`rank.test.ts`](rank.test.ts) | 排序、按身份去重、F-08 词表、无私有 EDKW；文件候选的版本钉在文件名旁边，而不写进文件名。 |
| [`mentions.ts`](mentions.ts) | 一份草稿携带的 `@` 钉选（ART-16）：选中候选时输入框只写 `@name`，`{filename, version_id}` 留在这里，由 `send()` 以 `mentions` 字段发出。只有 `@name` 仍在文字里时钉选才算数（服务端用同一条规则）；同名文件被选的次数多于文中出现的次数时，保留最近的几次。`stripRefVersions` 把文本形式的 `@name#v-<id>` 显示为 `@name`。 |
| [`mentions.test.ts`](mentions.test.ts) | token 边界（标点、中文、含空格与正则字符的文件名、文本形式）、删除和重选后保留哪些钉选、数量上限、清空/恢复、token 抹除，以及显示时去掉版本号。 |
| [`composer.ts`](composer.ts) | 现场 `ac` 控制器与 `#composer-ac` 弹出层。`@` 按文件名请求一页 artifact-index（limit 20，150 ms 防抖），项目、会话、查询或更新代际已过期的响应会被丢弃；弹层仍最多 8 项，按光标处的 token 确定替换起点，插入文本为 `@name`，所选版本钉在 `mentions.ts` 里。搜索在途（防抖或请求中）时，Enter、Tab 和发送按钮会等这一页落地，再补全按下时所在的那个 `@` token；页里带来同一文件的新版本时，高亮项按 artifact 身份保持不变；请求超过 8 秒仍未返回时显示失败提示。候选在 `.ac-list` 里滚动，提示行始终显示在列表下方。 |
| [`catalog.ts`](catalog.ts) | `loadSkillsCatalog`：`/` 补全与 `send()` 的 `/skill` 指令共用的唯一技能目录加载器（命令面板也可改用它）。并发调用共享同一个请求；读取失败时 reject 且不缓存，下一次调用会重试。 |
| [`composer.test.ts`](composer.test.ts) | 文件列表乱序返回：以最新按键为准；替换起点取加载完成后光标处的 token；光标已离开 token 时关闭弹层。技能目录读取失败后 `/` 补全能恢复；共享目录不缓存失败，并发加载只发一次请求。 |
| [`editor.ts`](editor.ts) | 每编辑器一个控制器、光标镜像、`execCommand('insertText')`。编辑器在挂载处自行绑定（`bindEditorAutocomplete`）；`watchEditAreas` 只绑定一次当时已有的，不再对整个文档做 MutationObserver。 |
| [`index.ts`](index.ts) | `installAutocomplete` 往 window 赋值；绑定作曲框与 `.edit-area`。 |
| [`install.test.ts`](install.test.ts) | `ac` 是现场对象；`edacTeardown` 通过 `isReady`；不会用 MutationObserver 监视整个文档来找编辑器文本框。 |
