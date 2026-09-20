import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { exportSession } from "./actions";
import { hint } from "./chrome";
import { sessions } from "../../stores/session";
import { resetStoreFields } from "../../stores/signal-field";
const messages = vi.hoisted(() => ({ fetchAllMessages: vi.fn() }));
vi.mock("./messages", () => ({ ...messages, fetchRecentMessages: vi.fn() }));
vi.mock("./chrome", () => ({ hint: vi.fn(), openMenu: vi.fn() }));

describe("Markdown export requires confirmed reads", () => {
  const blobs: Blob[] = [];
  const clicks = vi.fn();
  beforeEach(() => {
    resetStoreFields(); vi.mocked(hint).mockClear(); clicks.mockClear(); blobs.length = 0;
    messages.fetchAllMessages.mockReset().mockResolvedValue({ messages: [], complete: true });
    sessions.value = [{ id: "f", name: "Original session" }];
    vi.stubGlobal("document", { createElement: () => ({ click: clicks }) });
    vi.spyOn(URL, "createObjectURL").mockImplementation((blob) => { blobs.push(blob as Blob); return "blob:export"; });
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    vi.useFakeTimers();
  });
  afterEach(() => { vi.runOnlyPendingTimers(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

  it.each(["http", null, {}, [null], [{ filename: 42 }]])("refuses unavailable or malformed artifact rows: %j", async (body) => {
    vi.stubGlobal("fetch", async () => new Response(JSON.stringify(body === "http" ? { error: "artifact read failed" } : body), { status: body === "http" ? 503 : 200 }));
    await exportSession("f");
    expect(blobs).toHaveLength(0); expect(clicks).not.toHaveBeenCalled();
    expect(hint).toHaveBeenLastCalledWith(expect.any(String), true);
  });

  it("allows a confirmed empty artifact list and preserves the truncation warning", async () => {
    messages.fetchAllMessages.mockResolvedValue({ messages: [{ role: "user", content: "recorded message" }], complete: false });
    vi.stubGlobal("fetch", async () => new Response("[]"));
    await exportSession("f");
    expect(clicks).toHaveBeenCalledOnce();
    const content = await blobs[0]!.text();
    expect(content).toContain("recorded message"); expect(content).toContain("> ");
    expect(hint).toHaveBeenLastCalledWith(expect.any(String));
  });

  it("freezes the export title before asynchronous reads", async () => {
    let finish!: (response: Response) => void;
    vi.stubGlobal("fetch", () => new Promise<Response>((resolve) => { finish = resolve; }));
    const exportDone = exportSession("f");
    sessions.value = [];
    finish(new Response("[]")); await exportDone;
    expect(clicks).toHaveBeenCalledOnce();
    expect(await blobs[0]!.text()).toContain("# Original session");
  });
});
