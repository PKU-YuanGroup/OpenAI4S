import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { exportSession, exportSessionPackage } from "./actions";
import { packageT } from "./package";
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
    vi.stubGlobal("document", { createElement: () => ({ click: clicks }), querySelector: () => null });
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
    // Artifacts and the execution log are both read (TEAM-12).
    const pending: Array<(response: Response) => void> = [];
    vi.stubGlobal("fetch", () => new Promise<Response>((resolve) => { pending.push(resolve); }));
    const exportDone = exportSession("f");
    sessions.value = [];
    await vi.waitFor(() => expect(pending.length).toBe(2));
    pending.forEach((finish) => finish(new Response("[]"))); await exportDone;
    expect(clicks).toHaveBeenCalledOnce();
    expect(await blobs[0]!.text()).toContain("# Original session");
  });
});

describe("session package export says what it left as written (EXP-1)", () => {
  const clicks = vi.fn();
  beforeEach(() => {
    vi.mocked(hint).mockClear(); clicks.mockClear();
    vi.stubGlobal("document", {
      createElement: () => ({ click: clicks, remove: () => {} }),
      body: { appendChild: () => {} },
      querySelector: () => null,
    });
    vi.spyOn(URL, "createObjectURL").mockImplementation(() => "blob:package");
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    vi.useFakeTimers();
  });
  afterEach(() => { vi.runOnlyPendingTimers(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

  it("names the count of local paths left in the package", async () => {
    vi.stubGlobal("fetch", async () => new Response("zip", { headers: { "X-OpenAI4S-Local-Paths-Left": "3" } }));
    await exportSessionPackage("f", { name: "Plate 3" });
    expect(clicks).toHaveBeenCalledOnce();
    // Lane E's saved-size line, carrying EXP-1's count of paths left as written.
    expect(hint).toHaveBeenLastCalledWith(packageT("exportedPackageLeft", "3 B", 3));
  });

  it("says plainly when nothing was left, and shows a refusal instead of saving it", async () => {
    vi.stubGlobal("fetch", async () => new Response("zip", { headers: { "X-OpenAI4S-Local-Paths-Left": "0" } }));
    await exportSessionPackage("f");
    expect(hint).toHaveBeenLastCalledWith(packageT("exportedPackage", "3 B"));
    clicks.mockClear();
    vi.stubGlobal("fetch", async () => new Response(JSON.stringify({ error: "revert awaiting recovery" }), { status: 409 }));
    await exportSessionPackage("f");
    expect(clicks).not.toHaveBeenCalled();
    expect(hint).toHaveBeenLastCalledWith(expect.stringContaining("revert awaiting recovery"), true);
  });
});
