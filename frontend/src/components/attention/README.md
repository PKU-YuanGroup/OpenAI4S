# frontend/src/components/attention

[中文说明](README_zh.md)

M-02 Dashboard attention cards. Mounted into `#dash-attention` by `features/attention/boot.ts` (injected above `.dash-grid`). Cards are real buttons — click builds navigation locally from the closed target set.

## Files

| File | Responsibility |
| --- | --- |
| [`AttentionCard.tsx`](AttentionCard.tsx) | One card: safe title, project/session, updated time, next-action hint. |
| [`AttentionStream.tsx`](AttentionStream.tsx) | Card list bound to the lane-local `attentionCards` signal. |
| [`attention.css`](attention.css) | Lane-local card chrome. Global tokens stay with F-21. |
| [`index.ts`](index.ts) | Re-exports `AttentionStream` / `AttentionCard`. |
