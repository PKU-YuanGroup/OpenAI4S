# frontend/src/features/attention

[English](README.md)

M-02 仪表盘「需要处理」卡片流。数据来自 `GET /api/v1/attention`（B-05）。导航由客户端按闭集 `target.surface` + `target.dock` 本地生成，忽略服务端 URL 字段。轮询沿用现有 4 秒窗口，且只在仪表盘页面可见时发请求。retry / approve / restore 仍走现有 mutation route——卡片点击只打开已经带安全检查的 dock。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`api.ts`](api.ts) | `GET /attention` 分页拉取；页面不可见时丢弃迟到响应。 |
| [`boot.ts`](boot.ts) | 挂载 `#dash-attention`、4 秒轮询、可见性与仪表盘 class 门闩。 |
| [`cards.test.ts`](cards.test.ts) | 六类 fixture 各一张卡；idle/completed 为 0；mutation 路由名。 |
| [`copy.ts`](copy.ts) | M-02 覆盖文案（不改生成的 i18n 字典）。 |
| [`index.ts`](index.ts) | 对外 re-export。 |
| [`mutations.ts`](mutations.ts) | 把 approve/restore/retry hint 映射到现有 POST 路由。 |
| [`navigate.test.ts`](navigate.test.ts) | 闭集 target → 本地 session 路径 + exact dock；忽略 URL 字段。 |
| [`navigate.ts`](navigate.ts) | `navigationFromTarget` / `applyNavigation` / `localSessionPath`。 |
| [`parse.ts`](parse.ts) | 闭集 item 解析与 `cardsFromItems` 映射。 |
| [`poll.ts`](poll.ts) | `shouldFetchAttention` / `ATTENTION_POLL_MS = 4000`。 |
| [`state.ts`](state.ts) | 车道局部 signal。不上升进 `stores/`。 |
| [`types.ts`](types.ts) | B-05 item/target 类型，以及闭集 `SOURCE_KINDS` / `SURFACES` / `DOCKS`。 |
