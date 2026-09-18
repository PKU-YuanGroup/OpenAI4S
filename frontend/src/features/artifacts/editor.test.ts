import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, setArtifactsFetch } from "./api";
import { ArtifactEditorStore, EDITOR_MAX_BYTES, readEditorHead, type EditorIO } from "./editor";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function fixture() {
  const io: EditorIO = {
    head: vi.fn(async (_id, versionId) => ({ versionId: versionId || "v2", sizeBytes: 3, checksum: "a".repeat(64) })),
    text: vi.fn().mockResolvedValue("old"),
    save: vi.fn(async (id, _version, content) => ({ ok: true, artifact_id: id, version_id: "v2", size_bytes: new TextEncoder().encode(content).length, unchanged: false })),
  };
  const store = new ArtifactEditorStore(io);
  const artifact = { id: "a", version_id: "v1", filename: "notes.txt" };
  return { io, store, artifact };
}
async function ready(store: ArtifactEditorStore, artifact = { id: "a", version_id: "v1" }, session = "s") {
  const editor = store.open(session, artifact);
  await vi.waitFor(() => expect(editor.phase).not.toBe("loading"));
  return editor;
}
afterEach(() => setArtifactsFetch(null));

describe("conditional editor lifecycle", () => {
  it("sends nothing during loading or after a failed read, and freezes the version before awaits", async () => {
    const { io, store, artifact } = fixture();
    const body = deferred<string>();
    vi.mocked(io.text).mockReturnValue(body.promise);
    const editor = store.open("s", artifact);
    artifact.version_id = "v2";
    expect(editor.canSave).toBe(false);
    await editor.save();
    expect(io.save).not.toHaveBeenCalled();
    await vi.waitFor(() => expect(io.text).toHaveBeenCalledWith("v1", EDITOR_MAX_BYTES / 2, "a".repeat(64)));
    body.reject(new Error("read failed"));
    await vi.waitFor(() => expect(editor.problem).toBe("load"));
    await editor.save();
    expect(io.save).not.toHaveBeenCalled();
    vi.mocked(io.text).mockResolvedValue("old");
    await editor.load();
    expect(editor.baseline?.versionId).toBe("v1");
    editor.change("changed");
    await editor.save();
    expect(io.save).toHaveBeenCalledWith("a", "v1", "changed");
  });

  it("retains the immutable baseline and draft across mutable rows, redraws and sessions", async () => {
    const { store, artifact } = fixture();
    const editor = await ready(store, artifact);
    editor.change("草稿🙂");
    artifact.version_id = "v9";
    expect(store.open("s", artifact)).toBe(editor);
    expect(editor.text).toBe("草稿🙂");
    expect(editor.baseline).toEqual({ sessionId: "s", artifactId: "a", versionId: "v1", original: "old" });
    expect(store.drafts.get(JSON.stringify(["s", "a", "v1"]))).toBe(editor);
    const other = await ready(store, artifact, "other");
    expect(other).not.toBe(editor);
    expect(other.baseline?.versionId).toBe("v9");
    expect(other.text).toBe("old");
    expect(store.needsLeaveConfirmation).toBe(true);
  });

  it("freezes pending writes, deduplicates clicks and retains state through an early event", async () => {
    const { io, store, artifact } = fixture();
    const response = deferred<unknown>();
    vi.mocked(io.save).mockReturnValue(response.promise);
    const editor = await ready(store, artifact);
    editor.change("new");
    const saving = editor.save();
    expect(editor.phase).toBe("saving");
    expect(editor.change("later")).toBe(false);
    await editor.save();
    artifact.version_id = "v2";
    expect(store.open("s", artifact)).toBe(editor);
    expect(editor.baseline?.versionId).toBe("v1");
    expect(store.discard(editor)).toBe(false);
    response.resolve({ ok: true, artifact_id: "a", version_id: "v2", size_bytes: 3, unchanged: false });
    expect(await saving).not.toBeNull();
    expect(io.save).toHaveBeenCalledTimes(1);
    expect(store.drafts.size).toBe(0);
  });

  it.each([new Error("response lost"), null, "invalid JSON", {}, { ok: true, artifact_id: "a", version_id: "v2" }])("reconciles unknown results without another POST or clearing the draft (%s)", async (result) => {
    const { io, store } = fixture();
    vi.mocked(io.save).mockImplementation(async () => { if (result instanceof Error) throw result; return result; });
    const editor = await ready(store);
    editor.change("new");
    vi.mocked(io.text).mockResolvedValue("new");
    expect(await editor.save()).toBeNull();
    expect(editor.problem).toBe("unknown");
    expect(editor.observed).toEqual({ versionId: "v2", matchesDraft: true });
    expect(editor.text).toBe("new");
    expect(editor.baseline?.original).toBe("old");
    expect(store.needsLeaveConfirmation).toBe(true);
    expect(store.drafts.size).toBe(1);
    await editor.save();
    await editor.check();
    expect(io.save).toHaveBeenCalledTimes(1);
    expect(io.text).toHaveBeenLastCalledWith("v2", EDITOR_MAX_BYTES, "a".repeat(64));
  });

  it("keeps conflict bytes when reconciliation differs or fails; discard creates a new baseline", async () => {
    const { io, store, artifact } = fixture();
    const editor = await ready(store, artifact);
    editor.change("mine");
    vi.mocked(io.save).mockRejectedValue(new ApiError({ code: "artifact_version_conflict" }, 409));
    await editor.save();
    expect(editor.problem).toBe("conflict");
    expect(editor.observed?.matchesDraft).toBe(false);
    vi.mocked(io.head).mockRejectedValue(new Error("deleted"));
    await editor.check();
    expect(editor.checkFailed).toBe(true);
    expect(editor.observed).toBeNull();
    expect(editor.text).toBe("mine");
    expect(store.discard(editor)).toBe(true);
    vi.mocked(io.head).mockResolvedValue({ versionId: "v2", sizeBytes: 3, checksum: "a".repeat(64) });
    const next = await ready(store, { ...artifact, version_id: "v2" });
    expect(next.baseline?.versionId).toBe("v2");
    expect(next.text).toBe("old");
    expect(io.save).toHaveBeenCalledTimes(1);
  });

  it("never treats a pinned artifact as editable", async () => {
    const { store, io, artifact } = fixture();
    const editor = store.open("s", { ...artifact, _exactVersion: true });
    await editor.save();
    expect(editor.canSave).toBe(false);
    expect(io.text).not.toHaveBeenCalled();
    expect(io.save).not.toHaveBeenCalled();
  });
});

describe("draft capacity", () => {
  it("does not evict the first ten drafts to accept an eleventh", async () => {
    const { store } = fixture();
    for (let i = 0; i < 10; i++) (await ready(store, { id: String(i), version_id: "v1" })).change(`draft ${i}`);
    expect((await ready(store, { id: "eleventh", version_id: "v1" })).problem).toBe("capacity");
    expect(store.drafts.size).toBe(10);
    expect([...store.drafts.values()].map((draft) => draft.text)).toEqual(Array.from({ length: 10 }, (_, i) => `draft ${i}`));
  });
  it("counts original plus modified UTF-8 bytes across drafts and rejects growth without eviction", async () => {
    const { store, io } = fixture();
    vi.mocked(io.text).mockResolvedValue("");
    const first = await ready(store);
    expect(first.change("🙂".repeat(EDITOR_MAX_BYTES / 4))).toBe(true);
    expect(store.bytes).toBe(EDITOR_MAX_BYTES);
    expect(first.change(first.text + "中")).toBe(false);
    expect(first.inputAtCapacity).toBe(true);
    expect(store.bytes).toBe(EDITOR_MAX_BYTES);
    vi.mocked(io.text).mockResolvedValue("中");
    const second = await ready(store, { id: "second", version_id: "v1" });
    expect(second.problem).toBe("capacity");
    expect(first.text.length).toBe(EDITOR_MAX_BYTES / 2);
  });
  it("keeps oversized files read-only without reading their body", async () => {
    const { store, io, artifact } = fixture();
    vi.mocked(io.head).mockResolvedValue({ versionId: "v1", sizeBytes: EDITOR_MAX_BYTES, checksum: "a".repeat(64) });
    const editor = store.open("s", { ...artifact, size_bytes: EDITOR_MAX_BYTES });
    await vi.waitFor(() => expect(editor.problem).toBe("capacity"));
    expect(io.text).not.toHaveBeenCalled();
    expect(editor.canSave).toBe(false);
  });
  it("a read that cannot be kept is not streamed", async () => {
    // `bytes` counts committed drafts only, so a load opened against a full
    // store downloaded its whole allowance and was refused afterwards. The
    // limit is the remaining budget, so the read stops at the first byte.
    //
    // Deliberately bounded by COMMITTED bytes and not by a reservation held
    // across the read: a promise that outlives a read which never settles
    // refuses every other editor with a capacity error the view offers no
    // retry for, which is worse than the over-read it would prevent.
    const { store, io, artifact } = fixture();
    const half = EDITOR_MAX_BYTES / 2;
    vi.mocked(io.head).mockResolvedValue({ versionId: "v1", sizeBytes: half, checksum: "a".repeat(64) });
    vi.mocked(io.text).mockResolvedValue("a".repeat(half));
    const first = await ready(store, { ...artifact, id: "first" });
    expect(first.phase).toBe("ready");
    expect(store.bytes).toBe(EDITOR_MAX_BYTES);
    expect(store.readLimit).toBe(0);

    vi.mocked(io.head).mockResolvedValue({ versionId: "v1", sizeBytes: 3, checksum: "a".repeat(64) });
    const second = await ready(store, { ...artifact, id: "second" });
    expect(second.problem).toBe("capacity");
    expect(io.text).toHaveBeenLastCalledWith("v1", 0, "a".repeat(64));
  });

  it("an empty store still offers a whole draft's worth of read", async () => {
    // The limit must not become a second, tighter cap on the common case.
    const { store, io, artifact } = fixture();
    store.open("s", artifact);
    await vi.waitFor(() => expect(io.text).toHaveBeenCalledWith("v1", EDITOR_MAX_BYTES / 2, "a".repeat(64)));
    expect(store.readLimit).toBeLessThanOrEqual(EDITOR_MAX_BYTES / 2);
  });

  it("a load that never produced a draft releases its reservation", async () => {
    // open() registers the editor before load() runs. A capacity failure has
    // no draft to protect, and the view offers no retry for it, so keeping the
    // reservation held one of ten slots and made open() hand back the same
    // dead object for that artifact for the rest of the session.
    const { store, io, artifact } = fixture();
    vi.mocked(io.head).mockResolvedValue({ versionId: "v1", sizeBytes: EDITOR_MAX_BYTES, checksum: "a".repeat(64) });
    const first = store.open("s", { ...artifact, size_bytes: EDITOR_MAX_BYTES });
    await vi.waitFor(() => expect(first.problem).toBe("capacity"));
    expect(store.drafts.size).toBe(0);
    vi.mocked(io.head).mockResolvedValue({ versionId: "v1", sizeBytes: 3, checksum: "a".repeat(64) });
    const second = await ready(store, artifact);
    expect(second).not.toBe(first);
    expect(second.canSave).toBe(true);
  });
});

it.each([null, {}, { versions: [] }, { versions: [{ version_id: "v1" }] },
  { versions: [{ version_id: "v1", is_latest: true, size_bytes: 1 }, { version_id: "v2", is_latest: true, size_bytes: 1 }] }])("rejects malformed successful head reads (%s)", async (result) => {
  setArtifactsFetch(async () => new Response(JSON.stringify(result), { status: 200 }));
  await expect(readEditorHead("a")).rejects.toThrow();
});


it("refuses mutable live fallback bytes with a mismatching version checksum", async () => {
  setArtifactsFetch(async (url) => url.endsWith("/versions")
    ? new Response(JSON.stringify({ versions: [{ version_id: "v1", size_bytes: 3, checksum: "a".repeat(64), is_latest: true }] }))
    : new Response("different live bytes"));
  const store = new ArtifactEditorStore();
  const editor = await ready(store);
  expect(editor.problem).toBe("load");
  expect(editor.baseline).toBeNull();
  expect(editor.canSave).toBe(false);
});


it("verifies a BOM baseline and tolerates unrelated historical versions without evidence", async () => {
  const original = "\ufeff中\n";
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(original));
  const checksum = [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
  const writes: unknown[] = [];
  setArtifactsFetch(async (url, options) => {
    if (url.endsWith("/versions")) return new Response(JSON.stringify({ versions: [
      { version_id: "legacy", size_bytes: null, checksum: null, is_latest: false },
      { version_id: "v1", size_bytes: 7, checksum, is_latest: true },
    ] }));
    if (url.endsWith("/edit")) {
      writes.push(JSON.parse(String(options?.body)));
      return new Response(JSON.stringify({ ok: true, artifact_id: "a", version_id: "v1", size_bytes: 7, unchanged: true }));
    }
    return new Response(original);
  });
  const editor = await ready(new ArtifactEditorStore());
  expect(editor.text).toBe(original);
  expect(editor.dirty).toBe(false);
  expect(await editor.save()).not.toBeNull();
  expect(writes).toEqual([{ content: original, expected_version_id: "v1" }]);
  await expect(readEditorHead("a", "legacy")).rejects.toThrow("unverified");
});
