# frontend/src/components/artifacts

[中文说明](README_zh.md)

F-17 Files dock view. Frozen DOM ids (`#dock-files`, `#results-list`, `#results-count`, `#files-scope`) match the E2E contract. Mount from the workbench shell when that lane lands.

## Files

| File | Responsibility |
| --- | --- |
| [`FilesPanel.tsx`](FilesPanel.tsx) | Filename search, a human type select (ART-12: tables, images, reports, code, other) and origin filters, the session-scope sort (newest, oldest, name), "show hidden", Load more. `mountFilesPanel` paints into the shell `#dock-files`. |
| [`FilesPanel.test.tsx`](FilesPanel.test.tsx) | The content-type filter keeps typed text across re-renders until a change applies it. |
| [`index.ts`](index.ts) | Re-export `FilesPanel` / `mountFilesPanel`. |
| [`DraftsPanel.tsx`](DraftsPanel.tsx) | Reachable memory drafts for copying/discarding even after source deletion. |
