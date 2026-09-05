import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { currentId, project } from "../../stores/session";
import { resetStoreFields } from "../../stores/signal-field";
import {
  UPLOAD_STATE,
  UPLOAD_WAIT_LIMIT_MS,
  createUploadSession,
  pendingUploadsFor,
  readUploadFile,
  uploadBatchMatches,
  uploadFailureMatches,
  uploadFiles,
  waitForPendingUploads,
  type UploadBatch,
} from "./upload";

type FetchCall = { path: string; body: Record<string, unknown> };

function stubDom(workspaceHidden = false): void {
  const workspace = {
    classList: { contains: (cls: string) => cls === "hidden" && workspaceHidden },
  };
  vi.stubGlobal("document", {
    querySelector: (sel: string) => (sel === "#workspace" ? workspace : null),
    getElementById: () => null,
  });
  vi.stubGlobal("window", {});
}

/** A FileReader that yields `data:...;base64,<payload>` on the next microtask. */
function stubReader(payload: (file: { name: string }) => string | Error): void {
  class FakeReader {
    result: string | null = null;
    error: unknown = null;
    onload: (() => void) | null = null;
    onerror: (() => void) | null = null;
    onabort: (() => void) | null = null;
    readAsDataURL(file: { name: string }): void {
      const out = payload(file);
      void Promise.resolve().then(() => {
        if (out instanceof Error) {
          this.error = out;
          this.onerror?.();
          return;
        }
        this.result = out;
        this.onload?.();
      });
    }
  }
  vi.stubGlobal("FileReader", FakeReader);
}

function file(name: string): File {
  return { name } as unknown as File;
}

function stubFetch(handler: (path: string, body: Record<string, unknown>) => unknown): FetchCall[] {
  const calls: FetchCall[] = [];
  vi.stubGlobal("fetch", (url: string, opts: { body?: string }) => {
    const path = String(url).replace("/api/v1", "");
    const body = opts && opts.body ? (JSON.parse(opts.body) as Record<string, unknown>) : {};
    calls.push({ path, body });
    return Promise.resolve({
      ok: true,
      status: 200,
      text: () => Promise.resolve(JSON.stringify(handler(path, body) ?? {})),
    });
  });
  return calls;
}

function batch(over: Partial<UploadBatch>): UploadBatch {
  return {
    frameAtSelection: null,
    targetFrameId: null,
    projectId: null,
    targetSource: null,
    targetPromise: null,
    promise: null,
    ...over,
  };
}

describe("F-20 composer uploads (app.js:11049-11199)", () => {
  beforeEach(() => {
    resetStoreFields();
    UPLOAD_STATE.pending.clear();
    UPLOAD_STATE.failures.clear();
    UPLOAD_STATE.creations.clear();
    stubDom();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("readUploadFile rejects a data URL with no comma instead of uploading empty bytes", async () => {
    stubReader(() => "not-a-data-url");
    await expect(readUploadFile(file("a.csv"))).rejects.toThrow("file could not be encoded");
  });

  it("readUploadFile rejects on reader error", async () => {
    stubReader(() => new Error("boom"));
    await expect(readUploadFile(file("a.csv"))).rejects.toThrow("boom");
  });

  it("createUploadSession shares one flight per project, and `fresh` opts out", async () => {
    let n = 0;
    const calls = stubFetch(() => ({ id: `frame_${++n}` }));
    currentId.value = "already_open"; // keeps the auto-open branch out of the way
    const a = createUploadSession("proj_1");
    const b = createUploadSession("proj_1");
    expect(b).toBe(a);
    const fresh = createUploadSession("proj_1", { fresh: true });
    expect(fresh).not.toBe(a);
    expect(await a).toBe("frame_1");
    expect(await fresh).toBe("frame_2");
    expect(calls.filter((c) => c.path === "/frames")).toHaveLength(2);
    // The shared entry is released once the whole flight (`opened`) settles,
    // so the next Attach re-creates.
    await a.opened;
    expect(UPLOAD_STATE.creations.size).toBe(0);
  });

  it("createUploadSession's `opened` settles only after the adopted conversation opened", async () => {
    // The first send used to dispatch as soon as the id was published, while
    // the creation was still running loadSessions()+openConversation(); the
    // open then reset the turn it had just started. `opened` is the seam a
    // sender awaits so its dispatch follows the reset instead of racing it.
    stubDom();
    const order: string[] = [];
    let releaseSessions: () => void = () => {};
    const sessionsHeld = new Promise<void>((resolve) => {
      releaseSessions = resolve;
    });
    vi.stubGlobal("window", {
      loadSessions: () => {
        order.push("loadSessions");
        return sessionsHeld;
      },
      openConversation: (fid: string) => {
        order.push(`openConversation:${fid}`);
        return Promise.resolve();
      },
    });
    stubFetch(() => ({ id: "frame_new" }));
    currentId.value = null;
    project.value = "proj_1";

    const creation = createUploadSession("proj_1");
    expect(await creation).toBe("frame_new");
    // Published and subscribed before the sidebar refresh resolves...
    expect(currentId.value).toBe("frame_new");
    expect(order).toEqual(["loadSessions"]);
    let opened = false;
    void creation.opened.then(() => {
      opened = true;
    });
    await Promise.resolve();
    expect(opened).toBe(false); // ...and `opened` waits for the open itself.
    releaseSessions();
    await creation.opened;
    expect(order).toEqual(["loadSessions", "openConversation:frame_new"]);
    expect(UPLOAD_STATE.creations.size).toBe(0);
  });

  it("createUploadSession's `opened` settles without adopting when the frame was not adopted", async () => {
    stubDom();
    vi.stubGlobal("window", {
      loadSessions: () => Promise.reject(new Error("must not be called")),
      openConversation: () => Promise.reject(new Error("must not be called")),
    });
    stubFetch(() => ({ id: "frame_new" }));
    currentId.value = "already_open";
    const creation = createUploadSession("proj_1", { fresh: true });
    expect(await creation).toBe("frame_new");
    await expect(creation.opened).resolves.toBeUndefined();
    expect(currentId.value).toBe("already_open");
  });

  it("createUploadSession does not adopt the new frame when the user navigated away", async () => {
    stubFetch(() => ({ id: "frame_new" }));
    currentId.value = null;
    project.value = "proj_1";
    const creating = createUploadSession("proj_1");
    currentId.value = "user_opened_this"; // navigation during POST /frames
    expect(await creating).toBe("frame_new");
    expect(currentId.value).toBe("user_opened_this");
  });

  it("createUploadSession does not adopt the frame while the workspace is hidden", async () => {
    stubDom(true);
    stubFetch(() => ({ id: "frame_new" }));
    currentId.value = null;
    project.value = null;
    expect(await createUploadSession(null)).toBe("frame_new");
    expect(currentId.value).toBeNull();
  });

  it("uploadBatchMatches: a sibling frame's bytes never hold up an unrelated send", () => {
    // This used to match on project alone whenever the batch's destination was
    // still unresolved, to cover the window where createUploadSession had
    // published currentId but not yet resolved. That guess also caught a batch
    // bound to a DIFFERENT frame: attach from an empty composer, open an
    // existing conversation, and its next send waited on those bytes -- up to
    // the full stall budget. The window is closed at the source now (the frame
    // is published as soon as POST /frames answers), so the guess is gone.
    const unresolved = batch({ projectId: "proj_1" });
    expect(uploadBatchMatches(unresolved, "frame_sibling", "proj_1", null)).toBe(false);

    // What still matches: the send adopted the very creation the batch is on.
    const creation = Promise.resolve("frame_x");
    const adopted = batch({ projectId: "proj_2", targetSource: creation, targetFrameId: "frame_x" });
    expect(uploadBatchMatches(adopted, null, "proj_2", creation)).toBe(true);
    // ...and once the destination is known, it matches by id on any path.
    expect(uploadBatchMatches(adopted, "frame_x", null, null)).toBe(true);

    const settled = batch({ frameAtSelection: "frame_a", targetFrameId: "frame_a" });
    expect(uploadBatchMatches(settled, "frame_a", null, null)).toBe(true);
    expect(uploadBatchMatches(settled, "frame_b", null, null)).toBe(false);
  });

  it("createUploadSession names its frame as soon as POST /frames answers", () => {
    // The property that made the project-wide guess unnecessary: callers wait
    // for the destination, not for loadSessions()+openConversation().
    stubDom();
    let releaseSessions: () => void = () => {};
    const sessionsHeld = new Promise<void>((resolve) => {
      releaseSessions = resolve;
    });
    vi.stubGlobal("window", {
      loadSessions: () => sessionsHeld,
      openConversation: () => Promise.resolve(),
    });
    vi.stubGlobal("fetch", (_url: string) =>
      Promise.resolve({
        ok: true,
        status: 200,
        text: () => Promise.resolve(JSON.stringify({ id: "frame_new" })),
      }),
    );

    const pending = createUploadSession("proj_1");
    // Resolves while the opening work is still parked on loadSessions().
    return pending.then((frameId) => {
      expect(frameId).toBe("frame_new");
      releaseSessions();
    });
  });

  it("uploadFailureMatches keys on the frame when there is one, else the project", () => {
    const frameless = { frameId: null, projectId: "proj_1", results: [] };
    expect(uploadFailureMatches(frameless, null, "proj_1")).toBe(true);
    expect(uploadFailureMatches(frameless, "frame_a", "proj_1")).toBe(false);
    const bound = { frameId: "frame_a", projectId: "proj_1", results: [] };
    expect(uploadFailureMatches(bound, "frame_a", null)).toBe(true);
  });

  it("uploadFiles binds bytes to the conversation open at SELECTION, not at POST", async () => {
    stubReader((f) => `data:text/csv;base64,${f.name}`);
    const calls = stubFetch(() => ({}));
    currentId.value = "frame_at_selection";
    const done = uploadFiles([file("a.csv")]);
    currentId.value = "frame_the_user_switched_to"; // while FileReader works
    const results = await done;
    expect(results[0]?.ok).toBe(true);
    expect(results[0]?.frameId).toBe("frame_at_selection");
    const upload = calls.find((c) => c.path === "/uploads");
    expect(upload?.body.frame_id).toBe("frame_at_selection");
    expect(upload?.body.content_base64).toBe("a.csv");
  });

  it("uploadFiles registers the batch before any await, so Enter cannot overtake it", () => {
    stubReader((f) => `data:text/csv;base64,${f.name}`);
    stubFetch(() => ({}));
    currentId.value = "frame_a";
    const done = uploadFiles([file("a.csv")]);
    expect(pendingUploadsFor("frame_a", null, null)).toHaveLength(1);
    return done.then(() => expect(UPLOAD_STATE.pending.size).toBe(0));
  });

  it("waitForPendingUploads keeps looping while new matching batches appear", async () => {
    stubReader((f) => `data:text/csv;base64,${f.name}`);
    stubFetch(() => ({}));
    currentId.value = "frame_a";
    void uploadFiles([file("a.csv")]);
    const first = [...UPLOAD_STATE.pending][0];
    void first?.promise?.then(() => {
      void uploadFiles([file("b.csv")]); // a second selection lands mid-wait
    });
    const ready = await waitForPendingUploads("frame_a", null, null);
    expect(ready.ok).toBe(true);
    expect(UPLOAD_STATE.pending.size).toBe(0);
  });

  it("a failure is recorded for the destination and a fresh selection supersedes it", async () => {
    stubReader(() => new Error("unreadable"));
    stubFetch(() => ({}));
    currentId.value = "frame_a";
    await uploadFiles([file("a.csv")]);
    expect(UPLOAD_STATE.failures.size).toBe(1);
    const blocked = await waitForPendingUploads("frame_a", null, null);
    expect(blocked.ok).toBe(false);
    expect(blocked.failures).toHaveLength(1);

    stubReader((f) => `data:text/csv;base64,${f.name}`);
    await uploadFiles([file("a.csv")]); // deliberate retry
    expect(UPLOAD_STATE.failures.size).toBe(0);
    const ok = await waitForPendingUploads("frame_a", null, null);
    expect(ok.ok).toBe(true);
  });

  it("the failure set is bounded at 64", async () => {
    stubReader(() => new Error("unreadable"));
    stubFetch(() => ({}));
    for (let i = 0; i < 70; i++) {
      currentId.value = `frame_${i}`;
      await uploadFiles([file(`f${i}.csv`)]);
    }
    expect(UPLOAD_STATE.failures.size).toBe(64);
  });

  it("the stall budget stays at the app.js value", () => {
    expect(UPLOAD_WAIT_LIMIT_MS).toBe(120000);
  });
});
