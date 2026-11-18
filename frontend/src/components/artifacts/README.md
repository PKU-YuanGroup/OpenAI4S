# frontend/src/components/artifacts

[中文说明](README_zh.md)

F-17 Files dock view. Frozen DOM ids (`#dock-files`, `#results-list`, `#results-count`, `#files-scope`) match the E2E contract. Mount from the workbench shell when that lane lands.

## Files

| File | Responsibility |
| --- | --- |
| [`FilesPanel.tsx`](FilesPanel.tsx) | Filename search, a human type select (ART-12: tables, images, reports, code, other) and origin filters, the session-scope sort (newest, oldest, name), "show hidden", Load more, and the read-only project folder section (UP-5). `mountFilesPanel` paints into the shell `#dock-files`. |
| [`FilesPanel.test.tsx`](FilesPanel.test.tsx) | The type filter is a select of human kinds that applies on change (ART-12). |
| [`ProjectFolderSection.tsx`](ProjectFolderSection.tsx) | UP-5: the bound project folder, read-only, at the top of the Files panel: its path, the first 12 top-level entries (each opens the project-files modal at that folder or file) and "browse"; nothing here writes to the folder. |
| [`index.ts`](index.ts) | Re-export `FilesPanel` / `mountFilesPanel`. |
| [`DraftsPanel.tsx`](DraftsPanel.tsx) | Reachable memory drafts for copying/discarding even after source deletion. |
