import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { currentId, project } from "../../stores/session";
import { resetStoreFields } from "../../stores/signal-field";
import {
  _resetAttachments,
  addAttachment,
  attachmentSize,
  bindAttachment,
  composerAttachments,
  removeAttachment,
  renderAttachments,
  restoreAttachments,
  settleAttachment,
  takeAttachments,
  visibleAttachments,
} from "./attachments";

/** Just enough DOM for the tray: elements with classes, text, children and attributes. */
type Node = {
  tagName: string;
  className: string;
  textContent: string;
  children: Node[];
  attrs: Record<string, string>;
  dataset: Record<string, string>;
  title: string;
  type?: string;
  disabled?: boolean;
  onclick: ((e: { preventDefault(): void; stopPropagation(): void }) => void) | null;
  firstChild: Node | null;
  innerHTML: string;
  classList: { add(c: string): void; remove(c: string): void; contains(c: string): boolean };
  appendChild(child: Node): Node;
  setAttribute(name: string, value: string): void;
};

function node(tag: string): Node {
  const classes = new Set<string>();
  const n: Node = {
    tagName: tag.toUpperCase(),
    get className() {
      return [...classes].join(" ");
    },
    set className(v: string) {
      classes.clear();
      v.split(/\s+/).filter(Boolean).forEach((c) => classes.add(c));
    },
    textContent: "",
    children: [],
    attrs: {},
    dataset: {},
    title: "",
    onclick: null,
    get firstChild() {
      return n.children[0] || null;
    },
    get innerHTML() {
      return "";
    },
    set innerHTML(v: string) {
      n.children = v ? [node("svg")] : [];
    },
    classList: {
      add: (c) => classes.add(c),
      remove: (c) => classes.delete(c),
      contains: (c) => classes.has(c),
    },
    appendChild(child: Node) {
      n.children.push(child);
      return child;
    },
    setAttribute(name, value) {
      n.attrs[name] = value;
    },
  } as Node;
  return n;
}

function text(n: Node): string {
  return n.textContent + n.children.map(text).join("");
}

let tray: Node;

function chips(): Node[] {
  return tray.children.filter((c) => c.classList.contains("attach"));
}

function stubFetch(handler: (path: string, method: string) => { status: number; body?: unknown }) {
  const calls: Array<{ path: string; method: string }> = [];
  vi.stubGlobal("fetch", (url: string, opts: { method?: string }) => {
    const path = String(url).replace("/api/v1", "");
    const method = (opts && opts.method) || "GET";
    calls.push({ path, method });
    const out = handler(path, method);
    return Promise.resolve({
      ok: out.status < 400,
      status: out.status,
      text: () => Promise.resolve(JSON.stringify(out.body ?? {})),
    });
  });
  return calls;
}

describe("M-10 composer attachment chips", () => {
  beforeEach(() => {
    resetStoreFields();
    _resetAttachments();
    tray = node("div");
    tray.className = "composer-attach hidden";
    vi.stubGlobal("document", {
      createElement: (tag: string) => node(tag),
      querySelector: (sel: string) => (sel === "#composer-attachments" ? tray : null),
      getElementById: (id: string) => (id === "composer-attachments" ? tray : null),
    });
    vi.stubGlobal("window", {});
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("shows a chosen file at once, with its name, its state and a remove button", () => {
    currentId.value = "f-1";
    addAttachment({ name: "cohort.csv", size: 12_640 }, "f-1", null);
    expect(tray.classList.contains("hidden")).toBe(false);
    // The tray says what the chips are for, not only which files they are.
    expect(text(tray.children[0]!)).toMatch(/next message/);
    const [chip] = chips();
    expect(chip).toBeDefined();
    expect(text(chip!)).toContain("cohort.csv");
    expect(text(chip!)).toMatch(/Uploading/);
    const x = chip!.children.find((c) => c.tagName === "BUTTON");
    expect(x?.attrs["aria-label"]).toBe("Remove cohort.csv");
  });

  it("names the stored file and its size once the upload lands", () => {
    currentId.value = "f-1";
    const entry = addAttachment({ name: "cohort.csv", size: 12_640 }, "f-1", null);
    settleAttachment(entry, { ok: true, frameId: "f-1", filename: "cohort-2.csv", artifactId: "a-1", created: true });
    const [chip] = chips();
    expect(text(chip!)).toContain("cohort-2.csv");
    expect(text(chip!)).toContain("12.3 KB");
    expect(chip!.classList.contains("ready")).toBe(true);
  });

  it("shows only the open composer's files, and a first upload's files once its session exists", () => {
    project.value = "p-1";
    const early = addAttachment({ name: "a.csv", size: 1 }, null, "p-1");
    addAttachment({ name: "b.csv", size: 1 }, "f-other", "p-1");
    expect(visibleAttachments().map((a) => a.name)).toEqual(["a.csv"]);
    bindAttachment(early, "f-new");
    currentId.value = "f-new";
    expect(visibleAttachments().map((a) => a.name)).toEqual(["a.csv"]);
    currentId.value = "f-other";
    expect(visibleAttachments().map((a) => a.name)).toEqual(["b.csv"]);
  });

  it("× removes a file the upload created from the session", async () => {
    currentId.value = "f-1";
    const calls = stubFetch(() => ({ status: 200, body: { ok: true } }));
    const entry = addAttachment({ name: "wrong.csv", size: 5 }, "f-1", null);
    settleAttachment(entry, { ok: true, frameId: "f-1", filename: "wrong.csv", artifactId: "a-9", created: true });
    await removeAttachment(entry);
    expect(calls).toEqual([{ path: "/artifacts/a-9", method: "DELETE" }]);
    expect(composerAttachments()).toEqual([]);
    expect(tray.classList.contains("hidden")).toBe(true);
  });

  it("× on a new version of an existing file only leaves the tray: deleting it would take the older versions", async () => {
    currentId.value = "f-1";
    const calls = stubFetch(() => ({ status: 200 }));
    const entry = addAttachment({ name: "data.csv", size: 5 }, "f-1", null);
    settleAttachment(entry, { ok: true, frameId: "f-1", filename: "data.csv", artifactId: "a-1", created: false });
    await removeAttachment(entry);
    expect(calls).toEqual([]);
    expect(composerAttachments()).toEqual([]);
  });

  it("a removal the server refuses keeps the chip", async () => {
    currentId.value = "f-1";
    stubFetch(() => ({ status: 409, body: { error: "busy", code: "trusted_capture_busy" } }));
    const entry = addAttachment({ name: "x.csv", size: 5 }, "f-1", null);
    settleAttachment(entry, { ok: true, frameId: "f-1", filename: "x.csv", artifactId: "a-1", created: true });
    await removeAttachment(entry);
    expect(composerAttachments()).toEqual([entry]);
    expect(chips()).toHaveLength(1);
  });

  it("× while uploading hides the chip now and removes the file once it lands", async () => {
    currentId.value = "f-1";
    const calls = stubFetch(() => ({ status: 200 }));
    const entry = addAttachment({ name: "big.csv", size: 5 }, "f-1", null);
    await removeAttachment(entry);
    expect(chips()).toHaveLength(0);
    expect(calls).toEqual([]);
    settleAttachment(entry, { ok: true, frameId: "f-1", filename: "big.csv", artifactId: "a-2", created: true });
    await vi.waitFor(() => expect(calls).toEqual([{ path: "/artifacts/a-2", method: "DELETE" }]));
    await vi.waitFor(() => expect(composerAttachments()).toEqual([]));
  });

  it("a cancelled same-name upload leaves the tray; a failed one stays, marked, until removed", async () => {
    currentId.value = "f-1";
    const skipped = addAttachment({ name: "dup.csv", size: 5 }, "f-1", null);
    settleAttachment(skipped, { ok: true, skipped: true, frameId: "f-1" });
    const failed = addAttachment({ name: "bad.csv", size: 5 }, "f-1", null);
    settleAttachment(failed, { ok: false, frameId: "f-1" });
    expect(composerAttachments()).toEqual([failed]);
    expect(text(chips()[0]!)).toMatch(/Not uploaded/);
    await removeAttachment(failed);
    expect(composerAttachments()).toEqual([]);
  });

  it("sending takes the session's settled files; a refusal puts them back", () => {
    currentId.value = "f-1";
    const sent = addAttachment({ name: "a.csv", size: 5 }, "f-1", null);
    settleAttachment(sent, { ok: true, frameId: "f-1", artifactId: "a-1", created: true });
    // Chosen after the send's upload barrier: it goes with the next message.
    const later = addAttachment({ name: "b.csv", size: 5 }, "f-1", null);
    const elsewhere = addAttachment({ name: "c.csv", size: 5 }, "f-2", null);
    settleAttachment(elsewhere, { ok: true, frameId: "f-2", artifactId: "a-3", created: true });
    const taken = takeAttachments("f-1");
    expect(taken).toEqual([sent]);
    expect(composerAttachments()).toEqual([later, elsewhere]);
    restoreAttachments(taken);
    expect(composerAttachments()).toContain(sent);
  });

  it("sizes read the way a file manager says them", () => {
    expect(attachmentSize(900)).toBe("900 B");
    expect(attachmentSize(12_640)).toBe("12.3 KB");
    expect(attachmentSize(5 * 1048576)).toBe("5.0 MB");
  });

  it("an empty tray is hidden", () => {
    renderAttachments();
    expect(tray.classList.contains("hidden")).toBe(true);
  });
});
