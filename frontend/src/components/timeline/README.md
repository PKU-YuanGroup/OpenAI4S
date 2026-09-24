# frontend/src/components/timeline

[中文说明](README_zh.md)

Retired. This directory held a `Timeline` Preact container that nothing imported: the workbench renders the `#dock-timeline` host itself in [`../dashboard/Shell.tsx`](../dashboard/Shell.tsx), the imperative island in [`../../features/timeline/island.ts`](../../features/timeline/island.ts) fills it, and the island's layout rules (46px rows, absolute positioning, overview SVG size) live in `openai4s/server/webui/style.css`. The container's stylesheet was never bundled for the same reason.

The directory stays only because [`../README.md`](../README.md) still lists it; remove both together.

## Files

No source files.
