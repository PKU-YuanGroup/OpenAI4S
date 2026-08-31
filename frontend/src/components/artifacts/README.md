# frontend/src/components/artifacts

[中文说明](README_zh.md)

F-17 Files dock view. Frozen DOM ids (`#dock-files`, `#results-list`, `#results-count`, `#files-scope`) match the E2E contract. Mount from the workbench shell when that lane lands.

## Files

| File | Responsibility |
| --- | --- |
| [`FilesPanel.tsx`](FilesPanel.tsx) | Filename search, content-type / origin filters, Load more. `mountFilesPanel` paints into the shell `#dock-files`. |
| [`index.ts`](index.ts) | Re-export `FilesPanel` / `mountFilesPanel`. |
