import { filesT } from "../../features/artifacts/copy";
import { artifactDraftRevision, artifactEditors } from "../../features/artifacts/state";

/** Recovery remains reachable even if the source file or session was deleted. */
export function DraftsPanel() {
  void artifactDraftRevision.value;
  const drafts = [...artifactEditors.drafts.values()];
  return <details class="editor-drafts">
    <summary>{filesT("editor.drafts", drafts.length)}</summary>
    <p>{filesT("editor.memory")}</p>
    {!drafts.length && <p>{filesT("editor.noDrafts")}</p>}
    {drafts.map((draft) => <div class="editor-draft" key={draft.key}>
      <strong>{draft.artifact.filename || draft.artifact.id}</strong>
      <p>{filesT("editor.draftIdentity", draft.sessionId, draft.baseline?.versionId || "—")}</p>
      <textarea readOnly value={draft.text} aria-label={filesT("editor.copy")} />
      <button class="outline-btn small" disabled={draft.phase === "saving"} onClick={() => {
        if (confirm(filesT("editor.discardCloseConfirm"))) artifactEditors.discard(draft);
      }}>{filesT("editor.discard")}</button>
    </div>)}
  </details>;
}
