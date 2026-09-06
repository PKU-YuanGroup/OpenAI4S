# frontend/src/features/attention

[中文说明](README_zh.md)

M-02 Dashboard "needs attention" stream. Cards come from `GET /api/v1/attention` (B-05). Navigation is built locally from the closed `target.surface` + `target.dock` set; server URL fields are ignored. Polling uses the existing 4-second window and only fetches while the dashboard page is visible. retry / approve / restore stay on the existing mutation routes — the card click opens the dock that already owns those checks.

## Files

| File | Responsibility |
| --- | --- |
| [`api.ts`](api.ts) | `GET /attention` page fetch; drops late responses when the page is hidden. |
| [`boot.ts`](boot.ts) | Mounts `#dash-attention`, 4s poll, visibility + dashboard-class gates. |
| [`cards.test.ts`](cards.test.ts) | Six-kind fixture → one card each; idle/completed → 0; mutation route names. |
| [`copy.ts`](copy.ts) | M-02 overlay strings (does not rewrite generated i18n). |
| [`index.ts`](index.ts) | Public re-exports. |
| [`mutations.ts`](mutations.ts) | Maps approve/restore/retry hints onto existing POST routes. |
| [`navigate.test.ts`](navigate.test.ts) | Closed-set target → local session path + exact dock; URL fields ignored. |
| [`navigate.ts`](navigate.ts) | `navigationFromTarget` / `applyNavigation` / `localSessionPath`. |
| [`parse.ts`](parse.ts) | Closed-set item parse and `cardsFromItems` mapping. |
| [`poll.ts`](poll.ts) | `shouldFetchAttention` / `ATTENTION_POLL_MS = 4000`. |
| [`state.ts`](state.ts) | Lane-local signals. Not promoted into `stores/`. |
| [`types.ts`](types.ts) | B-05 item/target types and closed `SOURCE_KINDS` / `SURFACES` / `DOCKS`. |
