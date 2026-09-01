# frontend/src/components/timeline

[中文说明](README_zh.md)

Preact container for the Action Timeline island. Lifecycle only: mount `#dock-timeline`, call `renderActionTimeline`, destroy `_timelineView` on unmount. Class names (`.timeline-ledger-row` and siblings) are frozen for E2E.

## Files

| File | Responsibility |
| --- | --- |
| [`index.ts`](index.ts) | Re-export `Timeline`. |
| [`Timeline.css`](Timeline.css) | Island layout: 46px row height, absolute rows, overview SVG size. Visual tokens stay with F-21. |
| [`Timeline.tsx`](Timeline.tsx) | `#dock-timeline` host and mount/unmount. |
