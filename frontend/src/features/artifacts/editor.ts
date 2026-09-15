/** Conditional text edits. State outlives Viewer DOM and session navigation. */
import { API, api, ApiError, artifactsFetch } from "./api";
import type { ArtifactRow } from "./types";

export const EDITOR_MAX_DRAFTS = 10;
export const EDITOR_MAX_BYTES = 8 * 1024 * 1024;
const utf8 = (text: string): number => new TextEncoder().encode(text).byteLength;
export type EditorPhase = "loading" | "ready" | "saving" | "error";
export type EditorProblem = "load" | "capacity" | "conflict" | "unknown" | "rejected" | null;
type Baseline = Readonly<{ sessionId: string; artifactId: string; versionId: string; original: string }>;
type Head = { versionId: string; sizeBytes: number; checksum: string };
type Saved = { ok: true; artifact_id: string; version_id: string; size_bytes: number; unchanged: boolean };
export interface EditorIO {
  head(id: string, versionId?: string): Promise<Head>;
  text(versionId: string, limit: number, checksum: string): Promise<string>;
  save(id: string, versionId: string, content: string): Promise<unknown>;
}

export async function readEditorHead(id: string, versionId?: string): Promise<Head> {
  const result = await api(`/artifacts/${encodeURIComponent(id)}/versions`);
  const rows = (result as { versions?: unknown } | null)?.versions;
  if (!Array.isArray(rows) || !rows.length || rows.some((row) =>
    !row || typeof row.version_id !== "string" || !row.version_id.trim() ||
    typeof row.is_latest !== "boolean")) {
    throw new Error("invalid artifact versions");
  }
  const heads = rows.filter((row) => versionId ? row.version_id === versionId : row.is_latest);
  if (heads.length !== 1) throw new Error("unconfirmed artifact head");
  const selected = heads[0];
  if (!Number.isSafeInteger(selected.size_bytes) || selected.size_bytes < 0 ||
    typeof selected.checksum !== "string" || !/^[a-f0-9]{64}$/i.test(selected.checksum)) throw new Error("unverified artifact version");
  return { versionId: heads[0].version_id, sizeBytes: heads[0].size_bytes, checksum: heads[0].checksum };
}

async function readEditorText(versionId: string, limit: number, checksum: string): Promise<string> {
  const response = await artifactsFetch(`${API}/artifacts/versions/${encodeURIComponent(versionId)}`);
  if (!response.ok) throw new ApiError(null, response.status);
  if (!response.body) throw new Error("missing artifact body");
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true });
  let total = 0;
  const chunks: string[] = [];
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > limit) throw new EditorCapacityError();
      chunks.push(decoder.decode(value, { stream: true }));
    }
    chunks.push(decoder.decode());
    const text = chunks.join("");
    // Older servers may serve mutable live bytes when a snapshot is missing.
    // Never call those bytes the immutable baseline without matching evidence.
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
    const actual = [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
    if (actual !== checksum.toLowerCase()) throw new Error("artifact version checksum mismatch");
    return text;
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

const defaultIO: EditorIO = {
  head: readEditorHead,
  text: readEditorText,
  save: (id, versionId, content) => api(`/artifacts/${encodeURIComponent(id)}/edit`, {
    method: "POST", body: JSON.stringify({ content, expected_version_id: versionId }),
  }),
};
class EditorCapacityError extends Error {}

export class ArtifactEditorStore {
  /** The empty version is a loading reservation; completed drafts use the exact baseline. */
  readonly drafts = new Map<string, ArtifactEditor>();
  onChange: (() => void) | null = null;
  constructor(readonly io: EditorIO = defaultIO) {}
  get bytes(): number {
    return [...this.drafts.values()].reduce((sum, draft) => sum + draft.bytes, 0);
  }
  get needsLeaveConfirmation(): boolean {
    return [...this.drafts.values()].some((draft) => draft.dirty || draft.phase === "saving" || draft.problem === "unknown");
  }
  open(sessionId: string, artifact: ArtifactRow): ArtifactEditor {
    const existing = [...this.drafts.values()].find((draft) =>
      draft.sessionId === sessionId && draft.artifact.id === artifact.id);
    if (existing) return existing;
    const editor = new ArtifactEditor(this, sessionId, Object.freeze({ ...artifact }));
    if (!sessionId || artifact._exactVersion || this.drafts.size >= EDITOR_MAX_DRAFTS) {
      editor.phase = "error";
      editor.problem = artifact._exactVersion || !sessionId ? "load" : "capacity";
      return editor;
    }
    this.drafts.set(editor.key, editor);
    void editor.load();
    return editor;
  }
  discard(editor: ArtifactEditor): boolean {
    if (editor.phase === "saving") return false;
    if (this.drafts.get(editor.key) === editor) this.drafts.delete(editor.key);
    editor.onChange = null;
    this.onChange?.();
    return true;
  }
}

export class ArtifactEditor {
  phase: EditorPhase = "loading";
  problem: EditorProblem = null;
  baseline: Baseline | null = null;
  text = "";
  inputAtCapacity = false;
  onChange: (() => void) | null = null;
  /** Read-only reconciliation facts, never an acknowledgement of this write. */
  observed: { versionId: string; matchesDraft: boolean } | null = null;
  checking = false;
  checkFailed = false;
  constructor(readonly store: ArtifactEditorStore, readonly sessionId: string, readonly artifact: Readonly<ArtifactRow>) {}
  get key(): string { return JSON.stringify([this.sessionId, this.artifact.id, this.baseline?.versionId ?? ""]); }
  get bytes(): number { return this.baseline ? utf8(this.baseline.original) + utf8(this.text) : 0; }
  get dirty(): boolean { return this.baseline !== null && this.text !== this.baseline.original; }
  get canSave(): boolean { return this.phase === "ready" && this.baseline !== null && this.store.drafts.get(this.key) === this; }
  private emit(): void { this.store.onChange?.(); this.onChange?.(); }

  async load(): Promise<void> {
    // Retry reads only for a failed initial load, never replace an existing draft.
    if (this.baseline || (this.phase !== "loading" && this.problem !== "load")) return;
    this.phase = "loading"; this.problem = null; this.emit();
    try {
      const version = this.artifact.version_id || this.artifact.latest_version_id;
      const head = await this.store.io.head(this.artifact.id, version || undefined);
      if (head.sizeBytes * 2 > EDITOR_MAX_BYTES) throw new EditorCapacityError();
      const text = await this.store.io.text(head.versionId, EDITOR_MAX_BYTES / 2, head.checksum);
      if (this.store.drafts.get(this.key) !== this) return;
      if (this.store.bytes + 2 * utf8(text) > EDITOR_MAX_BYTES) throw new EditorCapacityError();
      this.store.drafts.delete(this.key);
      this.baseline = Object.freeze({ sessionId: this.sessionId, artifactId: this.artifact.id, versionId: head.versionId, original: text });
      this.text = text;
      this.store.drafts.set(this.key, this);
      this.phase = "ready";
    } catch (error) {
      this.phase = "error";
      this.problem = error instanceof EditorCapacityError ? "capacity" : "load";
    }
    this.emit();
  }

  change(text: string): boolean {
    if (!this.canSave) return false;
    if (this.store.bytes - utf8(this.text) + utf8(text) > EDITOR_MAX_BYTES) {
      this.inputAtCapacity = true; this.emit(); return false;
    }
    this.text = text; this.inputAtCapacity = false; this.emit(); return true;
  }

  async save(): Promise<Saved | null> {
    if (!this.canSave || !this.baseline) return null;
    const { artifactId, versionId } = this.baseline;
    const content = this.text;
    this.phase = "saving"; this.problem = null; this.emit();
    try {
      const result = await this.store.io.save(artifactId, versionId, content) as Partial<Saved> | null;
      if (!result || result.ok !== true || result.artifact_id !== artifactId ||
        typeof result.version_id !== "string" || !result.version_id.trim() ||
        result.size_bytes !== utf8(content) || typeof result.unchanged !== "boolean" ||
        (result.unchanged ? result.version_id !== versionId : result.version_id === versionId)) throw new Error("unconfirmed edit result");
      this.phase = "ready";
      this.store.discard(this);
      return result as Saved;
    } catch (error) {
      this.phase = "error";
      this.problem = error instanceof ApiError && error.status >= 400 && error.status < 500
        ? error.code === "artifact_version_conflict" ? "conflict" : "rejected"
        : "unknown";
      this.emit();
      if (this.problem === "unknown" || this.problem === "conflict") await this.check();
      return null;
    }
  }

  async check(): Promise<void> {
    if (this.checking || !this.baseline || this.phase === "saving") return;
    this.checking = true; this.checkFailed = false; this.emit();
    try {
      const head = await this.store.io.head(this.artifact.id);
      const text = await this.store.io.text(head.versionId, EDITOR_MAX_BYTES, head.checksum);
      this.observed = { versionId: head.versionId, matchesDraft: text === this.text };
    } catch {
      this.observed = null; this.checkFailed = true;
    } finally { this.checking = false; this.emit(); }
  }
}
