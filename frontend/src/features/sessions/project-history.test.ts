import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./api", () => ({ api: vi.fn(), API: "/api/v1", apiErrorText: (error: unknown) => String(error) }));

import { api } from "./api";
import { binds } from "./binds";
import { projectHistoryCopy as copy } from "./copy";
import { openProjectHistory } from "./project-history";
import { _modalMode } from "../../stores/ui";

class Node {
  className = "";
  textContent = "";
  value = "";
  type = "";
  id = "";
  href = "";
  download = "";
  disabled = false;
  style: Record<string, string> = {};
  attrs: Record<string, string> = {};
  dataset: Record<string, string> = {};
  children: Node[] = [];
  onclick: (() => void) | null = null;
  onchange: (() => void) | null = null;
  constructor(public tag = "div") {}
  classList = {
    contains: (cls: string) => this.className.split(" ").includes(cls),
    add: (cls: string) => { if (!this.classList.contains(cls)) this.className += ` ${cls}`; },
    remove: (cls: string) => { this.className = this.className.split(" ").filter((item) => item !== cls).join(" "); },
  };
  set innerHTML(_value: string) { this.children = []; this.textContent = ""; }
  setAttribute(key: string, value: string) { this.attrs[key] = value; }
  appendChild(child: Node) { this.children.push(child); return child; }
  all(): Node[] { return [this, ...this.children.flatMap((child) => child.all())]; }
  texts(): string[] { return this.all().map((node) => node.textContent).filter(Boolean); }
  button(label: string): Node {
    const node = this.all().find((item) => item.tag === "button" && item.textContent === label);
    expect(node, `button ${label}`).toBeDefined();
    return node!;
  }
  click(label: string) { this.button(label).onclick!(); }
}

let nodes: Record<string, Node>;
let body: Node;
function deferred() {
  let resolve!: (value: unknown) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { resolve, reject, promise };
}
function listing(overrides: Record<string, unknown> = {}) {
  return {
    directory: "/research/.openai4s", status: "saved", last_saved_at: 1700000000000,
    sessions: [{ session_id: "session/one", title: "Study one", revision: "r1", message_count: 2, cell_count: 1, file_count: 1, can_continue: true }],
    ...overrides,
  };
}
function snapshot(overrides: Record<string, unknown> = {}) {
  return {
    session_id: "session/one", title: "Study one", revision: "r1", saved_at: 1700000000000, can_continue: true,
    revisions: [{ revision: "r1", saved_at: 1700000000000 }, { revision: "r0", saved_at: 1699999900000 }],
    messages: [{ role: "user", content: "Analyze data" }, { role: "assistant", content: "The average is 3." }],
    cells: [{ language: "python", source: "print(3)", stdout: "3", stderr: "warning", error: "" }],
    files: [{ path: "results/means.csv", size_bytes: 20, sha256: "abcdef", version_id: "version-1" }],
    settings: { name: "Research", description: "Assay", context: "Be careful" }, omissions: [], ...overrides,
  };
}
async function openDetail(data = snapshot()): Promise<void> {
  vi.mocked(api).mockResolvedValueOnce(listing());
  await openProjectHistory("project/one", "Research");
  vi.mocked(api).mockResolvedValueOnce(data);
  body.click(copy("open"));
  await vi.waitFor(() => expect(body.texts()).toContain(copy("revisionId", String(data.revision))));
}

beforeEach(() => {
  vi.mocked(api).mockReset();
  binds.openConversation = vi.fn();
  nodes = Object.fromEntries(["#modal", "#modal-body", "#modal-title", "#modal-download"].map((key) => [key, new Node()]));
  body = nodes["#modal-body"]!;
  vi.stubGlobal("document", { createElement: (tag: string) => new Node(tag), querySelector: (sel: string) => nodes[sel] ?? null });
});
afterEach(() => vi.unstubAllGlobals());

describe("local history save status", () => {
  it("shows the exact save directory, confirmed status, timestamp and session counts", async () => {
    vi.mocked(api).mockResolvedValueOnce(listing());
    await openProjectHistory("project/one", "Research");
    expect(api).toHaveBeenCalledExactlyOnceWith("/projects/project%2Fone/history", undefined);
    expect(body.texts()).toEqual(expect.arrayContaining(["/research/.openai4s", copy("saved"), "Study one", copy("counts", 2, 1, 1)]));
    expect(body.texts()).toContain(copy("automatic"));
  });

  it.each(["pending", "saving", "error", "unavailable", "unexpected"])("does not claim a save for backend status %s", async (status) => {
    vi.mocked(api).mockResolvedValueOnce(listing({ status, last_saved_at: null, sessions: [], error: status === "error" ? "Disk full" : null }));
    await openProjectHistory("p", "P");
    expect(body.texts()).not.toContain(copy("saved"));
    expect(body.texts()).toContain(copy(status === "unexpected" ? "unknown" : status));
    expect(body.texts()).toContain(copy("neverSaved"));
    if (status === "error") expect(body.texts()).toContain("Disk full");
  });

  it("saves on explicit request and displays a reported failure even for HTTP success", async () => {
    vi.mocked(api).mockResolvedValueOnce(listing());
    await openProjectHistory("p", "P");
    const pending = deferred();
    vi.mocked(api).mockReturnValueOnce(pending.promise);
    body.click(copy("saveNow"));
    expect(body.button(copy("saveNow")).disabled).toBe(true);
    expect(body.texts()).toContain(copy("saving"));
    expect(api).toHaveBeenLastCalledWith("/projects/p/history", { method: "POST", body: "{}" });
    pending.resolve(listing({ status: "error", error: "Disk full", last_saved_at: null }));
    await vi.waitFor(() => expect(body.texts()).toContain("Disk full"));
    expect(body.texts()).not.toContain(copy("saved"));
    expect(body.button(copy("saveNow")).disabled).toBe(false);
  });

  it("reports a failed save request and offers a real retry", async () => {
    vi.mocked(api).mockResolvedValueOnce(listing());
    await openProjectHistory("p", "P");
    vi.mocked(api).mockRejectedValueOnce(new Error("unavailable"));
    body.click(copy("saveNow"));
    await vi.waitFor(() => expect(body.texts()).toContain(copy("error")));
    expect(body.texts().join(" ")).toContain("unavailable");
    vi.mocked(api).mockResolvedValueOnce(listing({ sessions: [] }));
    body.click(copy("retry"));
    await vi.waitFor(() => expect(body.texts()).toContain(copy("saved")));
    expect(api).toHaveBeenLastCalledWith("/projects/p/history", { method: "POST", body: "{}" });
  });

  it("rejects malformed history rather than presenting an empty saved list", async () => {
    vi.mocked(api).mockResolvedValueOnce({ status: "saved" });
    await openProjectHistory("p", "P");
    expect(body.texts().join(" ")).toContain(copy("invalid"));
    expect(body.texts()).not.toContain(copy("saved"));
    expect(body.texts()).not.toContain(copy("empty"));
  });
});

describe("immutable local snapshots", () => {
  it("labels a saved branch view without implying that Continue rewinds it, and accepts older snapshots without a branch", async () => {
    await openDetail(snapshot({ branch_id: "branch-previous" }));
    expect(body.texts()).toContain(copy("branch", "branch-previous"));
    expect(body.texts()).toContain(copy("continueHelp"));
    expect(binds.openConversation).not.toHaveBeenCalled();
    await openDetail(snapshot());
    expect(body.all().some((node) => node.className.includes("project-history-branch"))).toBe(false);
  });

  it("renders untrusted conversation, code and selected settings only as text", async () => {
    const html = "<script>window.stolen=true</script>";
    await openDetail(snapshot({
      messages: [{ role: "assistant", content: html }],
      cells: [{ language: "python", source: html, stdout: "42", stderr: "diagnostic", error: "failed" }],
      settings: { name: html, description: "Desc", context: "Context", api_key: "excluded-value" },
    }));
    expect(body.all().find((node) => node.tag === "pre")!.textContent).toBe(html);
    expect(body.all().some((node) => node.tag === "script")).toBe(false);
    body.click(copy("cells"));
    expect(body.texts()).toEqual(expect.arrayContaining([html, "42", "diagnostic", "failed"]));
    body.click(copy("settings"));
    expect(body.texts()).toEqual(expect.arrayContaining([html, "Desc", "Context"]));
    expect(body.texts()).not.toContain("excluded-value");
  });

  it("shows omitted files and constructs version-bound same-origin download links", async () => {
    const path = "results/a?x=<script>&name=.csv";
    await openDetail(snapshot({ files: [{ path, size_bytes: 20, sha256: "checksum" }], omissions: [{ path: "large.bin", reason: "file size limit" }] }));
    expect(body.texts()).toContain("large.bin — file size limit");
    expect(body.texts()).toContain(copy("omissionsHelp"));
    body.click(copy("files"));
    const link = body.all().find((node) => node.tag === "a")!;
    expect(link.href).toBe(`/api/v1/projects/project%2Fone/history/session%2Fone/file?revision=r1&path=${encodeURIComponent(path)}`);
    expect(link.download).toBe("a?x=<script>&name=.csv");
    expect(body.texts()).toContain(path);
  });

  it("reads a selected revision without changing or rewinding the live conversation", async () => {
    await openDetail();
    const select = body.all().find((node) => node.tag === "select")!;
    vi.mocked(api).mockResolvedValueOnce(snapshot({ revision: "r0", messages: [{ role: "user", content: "Old question" }] }));
    select.value = "r0";
    select.onchange!();
    await vi.waitFor(() => expect(body.texts()).toContain("Old question"));
    expect(api).toHaveBeenLastCalledWith("/projects/project%2Fone/history/session%2Fone?revision=r0");
    expect(binds.openConversation).not.toHaveBeenCalled();
  });

  it("shows archived-only records without offering executable restoration", async () => {
    await openDetail(snapshot({ can_continue: false }));
    expect(body.texts()).toContain(copy("archivedOnly"));
    expect(body.texts()).not.toContain(copy("continue"));
    expect(binds.openConversation).not.toHaveBeenCalled();
  });

  it("rechecks live ownership before continuing an existing conversation", async () => {
    await openDetail();
    expect(body.texts()).toContain(copy("continueHelp"));
    const pending = deferred();
    vi.mocked(api).mockReturnValueOnce(pending.promise);
    body.click(copy("continue"));
    expect(binds.openConversation).not.toHaveBeenCalled();
    pending.resolve(snapshot());
    await vi.waitFor(() => expect(binds.openConversation).toHaveBeenCalledExactlyOnceWith("session/one", "project/one"));
    expect(nodes["#modal"]!.classList.contains("hidden")).toBe(true);
  });

  it("does not continue a conversation deleted since its history was displayed", async () => {
    await openDetail();
    vi.mocked(api).mockResolvedValueOnce(snapshot({ can_continue: false }));
    body.click(copy("continue"));
    await vi.waitFor(() => expect(body.texts().join(" ")).toContain(copy("cannotContinue")));
    expect(binds.openConversation).not.toHaveBeenCalled();
    expect(nodes["#modal"]!.classList.contains("hidden")).toBe(false);
  });
});

describe("local history request ownership", () => {
  it.each(["resolve", "reject"])("drops an older list %s after reopening even the same project", async (outcome) => {
    const old = deferred();
    vi.mocked(api).mockReturnValueOnce(old.promise);
    const opening = openProjectHistory("p", "Old");
    vi.mocked(api).mockResolvedValueOnce(listing({ directory: "/new/.openai4s" }));
    await openProjectHistory("p", "New");
    if (outcome === "resolve") old.resolve(listing({ directory: "/old/.openai4s" }));
    else old.reject(new Error("old request failed"));
    await opening;
    expect(body.texts()).toContain("/new/.openai4s");
    expect(body.texts()).not.toContain("/old/.openai4s");
    expect(body.texts().join(" ")).not.toContain("old request failed");
  });

  it("does not paint a late snapshot over a refreshed list", async () => {
    vi.mocked(api).mockResolvedValueOnce(listing());
    await openProjectHistory("p", "P");
    const old = deferred();
    vi.mocked(api).mockReturnValueOnce(old.promise);
    body.click(copy("open"));
    vi.mocked(api).mockResolvedValueOnce(listing({ sessions: [] }));
    body.click(copy("refresh"));
    await vi.waitFor(() => expect(body.texts()).toContain(copy("empty")));
    old.resolve(snapshot());
    await Promise.resolve();
    expect(body.texts()).not.toContain("The average is 3.");
  });

  it("abandons a pending Continue after the user closes or replaces the modal", async () => {
    await openDetail();
    const old = deferred();
    vi.mocked(api).mockReturnValueOnce(old.promise);
    body.click(copy("continue"));
    _modalMode.value = "another-view";
    old.resolve(snapshot());
    await Promise.resolve();
    expect(binds.openConversation).not.toHaveBeenCalled();
  });
});
