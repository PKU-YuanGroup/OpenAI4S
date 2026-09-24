# frontend/src/features/judgment

[English](README.md)

实验性语义判断层的功能本地文案，以及 `search_skills` 推荐 chip。设置控件在 `components/customize/ExperimentsTab.tsx`。不要手改生成的 `i18n/en.ts` / `zh.ts`。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`copy.ts`](copy.ts) | 功能本地中英文案（`judgmentT`，基于 onboarding 的 `copyLookup`）。 |
| [`chips.ts`](chips.ts) | 解析 list / dict 两种 `search_skills` 形态；渲染实验性推荐 chip。 |
| [`chips.test.ts`](chips.test.ts) | list 形态保持原样；dict 形态绘制 chip、p_fit、confidence、悬停版本与状态提示。 |
| [`judgment.css`](judgment.css) | Customize 区块与 chip 样式。只用已有 CSS token。 |
| [`index.ts`](index.ts) | 对外再导出。 |
