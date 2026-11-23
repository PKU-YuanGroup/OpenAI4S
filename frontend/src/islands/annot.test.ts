import { afterEach, describe, expect, it, vi } from "vitest";

const hints = vi.hoisted(() => [] as Array<{ text: unknown; err: unknown }>);
vi.mock("../features/sessions/chrome", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../features/sessions/chrome")>()),
  hint: (text: unknown, err?: unknown) => {
    hints.push({ text, err });
  },
}));

import {
  annotRefusalText,
  annotationId,
  annotationIsHeld,
  annotationStatus,
  openAnnotations,
  renderAnnotatableImage,
  updateAnnotBadge,
} from "./annot";
import { setArtifactsFetch } from "../features/artifacts/api";
import { filesT } from "../features/artifacts/copy";
import { LANG, i18nReady, setLang } from "../i18n/runtime";
import { annotations, currentId } from "../stores/session";
import { resetStoreFields } from "../stores/signal-field";

describe("annotationStatus (app.js:9244-9253)", () => {
  it("maps reserved/pending to pending and unknown rather than open", () => {
    expect(annotationStatus({ status: "open" })).toBe("open");
    expect(annotationStatus({ status: "sent" })).toBe("sent");
    expect(annotationStatus({ status: "resolved" })).toBe("resolved");
    expect(annotationStatus({ status: "dismissed" })).toBe("dismissed");
    expect(annotationStatus({ status: "reserved" })).toBe("pending");
    expect(annotationStatus({ status: "pending" })).toBe("pending");
    expect(annotationStatus({ status: "weird" })).toBe("unknown");
    expect(annotationStatus({})).toBe("open");
  });

  it("holds pending and unknown pins so the user cannot delete them", () => {
    expect(annotationIsHeld({ status: "pending" })).toBe(true);
    expect(annotationIsHeld({ status: "reserved" })).toBe(true);
    expect(annotationIsHeld({ status: "weird" })).toBe(true);
    expect(annotationIsHeld({ status: "open" })).toBe(false);
    expect(annotationIsHeld({ status: "sent" })).toBe(false);
  });

  it("annotationId prefers id then annotation_id", () => {
    expect(annotationId({ id: "a", annotation_id: "b" })).toBe("a");
    expect(annotationId({ annotation_id: "b" })).toBe("b");
    expect(annotationId(null)).toBeFalsy();
  });
});

describe("openAnnotations (app.js:8966)", () => {
  it("filters status===open from the session store", () => {
    resetStoreFields();
    annotations.value = [
      { id: "1", status: "open" },
      { id: "2", status: "sent" },
      { id: "3", status: "pending" },
    ];
    expect(openAnnotations().map((a) => a.id)).toEqual(["1"]);
    resetStoreFields();
  });
});

describe("composer comment list (AUDIT A47)", () => {
  class FakeEl {
    children: FakeEl[] = [];
    parent: FakeEl | null = null;
    id = ""; className = ""; title = ""; textContent = ""; disabled = false;
    classList = { add() {}, remove() {} };
    isConnected = true;
    attrs: Record<string, string> = {};
    setAttribute(name: string, value: string): void { this.attrs[name] = value; }
    addEventListener(): void {}
    removeEventListener(): void {}
    onclick: ((event: { stopPropagation(): void; preventDefault(): void }) => unknown) | null = null;
    get parentElement(): FakeEl | null { return this.parent; }
    set innerHTML(_value: string) {
      for (const child of this.children) child.parent = null;
      this.children = [];
    }
    appendChild(child: FakeEl): FakeEl { child.parent = this; this.children.push(child); return child; }
    remove(): void {
      if (this.parent) this.parent.children = this.parent.children.filter((node) => node !== this);
      this.parent = null;
    }
    contains(node: unknown): boolean { return walk(this).includes(node as FakeEl); }
  }
  const walk = (node: FakeEl): FakeEl[] => [node, ...node.children.flatMap(walk)];
  const click = (node: FakeEl | undefined) => node?.onclick?.({ stopPropagation() {}, preventDefault() {} });

  afterEach(() => {
    setArtifactsFetch(null);
    vi.unstubAllGlobals();
    resetStoreFields();
  });

  it("reopens the list on the repainted chip after removing one comment", async () => {
    resetStoreFields();
    const bar = new FakeEl();
    bar.id = "annot-bar";
    vi.stubGlobal("document", {
      createElement: () => new FakeEl(),
      addEventListener() {},
      removeEventListener() {},
      querySelectorAll: () => [],
      querySelector: (sel: string) => {
        if (sel === "#annot-bar") return bar;
        if (sel === "#annot-bar .annot-chip") return walk(bar).find((node) => node.className === "annot-chip") ?? null;
        if (sel === "#annot-list-pop") return walk(bar).find((node) => node.id === "annot-list-pop") ?? null;
        return null;
      },
    });
    setArtifactsFetch(async () => new Response("{}"));
    annotations.value = [
      { id: "1", status: "open", number: 1, body: "first" },
      { id: "2", status: "open", number: 2, body: "second" },
    ];
    updateAnnotBadge();
    click(walk(bar).find((node) => node.className === "annot-chip-main"));
    const list = () => walk(bar).find((node) => node.id === "annot-list-pop");
    expect(list()).toBeDefined();
    click(walk(list()!).find((node) => node.className === "annot-mini danger"));
    await vi.waitFor(() => expect(openAnnotations().map((an) => an.id)).toEqual(["2"]));
    await vi.waitFor(() => expect(list()).toBeDefined());
    const rows = walk(list()!).filter((node) => node.className === "annot-list-row");
    expect(rows).toHaveLength(1);
  });

  it("shows the classified failure card when an image preview read is refused", async () => {
    const body = new FakeEl();
    vi.stubGlobal("document", {
      createElement: () => new FakeEl(),
      addEventListener() {},
      removeEventListener() {},
      querySelectorAll: () => [],
      querySelector: () => null,
    });
    setArtifactsFetch(async () => new Response("no", { status: 413 }));
    renderAnnotatableImage(
      body as unknown as HTMLElement,
      { id: "pic", filename: "plot.png", content_type: "image/png" },
      "/api/v1/artifacts/pic",
    );
    await vi.waitFor(() =>
      expect(walk(body).some((node) => (node.className || "").includes("renderer-fallback"))).toBe(true),
    );
    expect(walk(body).some((node) => node.textContent === filesT("viewer.read.retry"))).toBe(true);
    expect(walk(body).some((node) => node.textContent === "plot.png" && (node.className || "").includes("annot-img"))).toBe(
      false,
    );
  });
});

// M-66: an imported read-only copy offered "click the image to annotate", and
// the save was refused by the server in English ("imported Session is
// quarantined and view-only; ... before mutating the Session").
describe("comments in a read-only session (M-66)", () => {
  class Node {
    children: Node[] = [];
    parent: Node | null = null;
    className = ""; id = ""; title = ""; textContent = ""; value = ""; placeholder = ""; alt = ""; src = "";
    disabled = false; draggable = true; rows = 0; clientWidth = 200; clientHeight = 100;
    isConnected = true;
    style: Record<string, string> = {};
    dataset: Record<string, string> = {};
    listeners: Record<string, (event: unknown) => unknown> = {};
    onclick: ((event?: unknown) => unknown) | null = null;
    classList = { add() {}, remove() {}, contains: () => false };
    set innerHTML(_value: string) { this.children = []; }
    setAttribute(): void {}
    addEventListener(type: string, fn: (event: unknown) => unknown): void { this.listeners[type] = fn; }
    removeEventListener(): void {}
    appendChild(child: Node): Node { child.parent = this; this.children.push(child); return child; }
    remove(): void {
      if (this.parent) this.parent.children = this.parent.children.filter((node) => node !== this);
      this.parent = null;
    }
    focus(): void {}
    scrollTo(): void {}
    contains(node: unknown): boolean { return tree(this).includes(node as Node); }
    querySelector(sel: string): Node | null {
      const cls = sel.replace(/^\./, "");
      return tree(this).find((node) => node !== this && node.className.split(" ").includes(cls)) ?? null;
    }
    querySelectorAll(): Node[] { return []; }
    getBoundingClientRect() { return { left: 0, top: 0, width: 200, height: 100 }; }
  }
  const tree = (node: Node): Node[] => [node, ...node.children.flatMap(tree)];
  const byClass = (root: Node, cls: string) => tree(root).find((node) => node.className === cls);

  function mountImage(readOnlyCopy: boolean): { body: Node; layer: Node } {
    const body = new Node();
    const banner = readOnlyCopy ? new Node() : null;
    vi.stubGlobal("document", {
      createElement: () => new Node(),
      addEventListener() {},
      removeEventListener() {},
      querySelectorAll: () => [],
      querySelector: (sel: string) => (sel === "#imported-banner" ? banner : null),
    });
    vi.stubGlobal("requestAnimationFrame", () => 0);
    // The image bytes never arrive: only the stage and its pin layer matter.
    setArtifactsFetch(() => new Promise<Response>(() => {}));
    renderAnnotatableImage(
      body as unknown as HTMLElement,
      { id: "pic", filename: "plot.png", content_type: "image/png" },
      "/api/v1/artifacts/pic",
    );
    return { body, layer: byClass(body, "annot-layer")! };
  }
  const clickImage = (layer: Node) => layer.listeners.click!({ target: layer, clientX: 50, clientY: 50 });

  afterEach(async () => {
    setArtifactsFetch(null);
    vi.unstubAllGlobals();
    resetStoreFields();
    hints.length = 0;
  });

  it("does not open a comment draft on a read-only copy, and says why in the UI language", async () => {
    await i18nReady();
    const original = LANG;
    try {
      for (const lang of ["zh", "en"] as const) {
        await setLang(lang);
        const { body, layer } = mountImage(true);
        clickImage(layer);
        expect(byClass(layer, "annot-pop edit")).toBeUndefined();
        expect(byClass(layer, "annot-pin draft")).toBeUndefined();
        const said = String(hints[hints.length - 1]?.text || "");
        expect(said).toMatch(lang === "zh" ? /只读副本/ : /read-only copy/);
        // Nor does the image's own hint offer "click the image to annotate".
        const zoomHint = byClass(body, "zoom-hint")!.textContent;
        expect(zoomHint).toMatch(lang === "zh" ? /只读副本不能添加标注/ : /read-only copy takes no comments/);
        vi.unstubAllGlobals();
      }
    } finally {
      await setLang(original);
    }
  });

  it("still opens the draft in an ordinary session", () => {
    const { layer } = mountImage(false);
    clickImage(layer);
    expect(byClass(layer, "annot-pop edit")).toBeDefined();
  });

  it("says the server's read-only refusal in the UI language, not the server's English", async () => {
    await i18nReady();
    const original = LANG;
    const refusal =
      "imported Session is quarantined and view-only; use the confirmed restart_fresh recovery action before mutating the Session";
    try {
      for (const lang of ["zh", "en"] as const) {
        await setLang(lang);
        // The banner is not up yet (its kernel read is still out): the draft
        // opens and the save reaches the server.
        currentId.value = "frame-1";
        const { layer } = mountImage(false);
        clickImage(layer);
        const pop = byClass(layer, "annot-pop edit")!;
        byClass(pop, "annot-input")!.value = "the outlier at 3 o'clock";
        setArtifactsFetch(async () =>
          new Response(JSON.stringify({ error: refusal, code: "locked" }), { status: 423 }),
        );
        await byClass(pop, "annot-btn solid")!.onclick!();
        const last = hints[hints.length - 1]!;
        expect(last.err).toBe(true);
        expect(String(last.text)).not.toContain("quarantined");
        expect(String(last.text)).toBe(annotRefusalText({ status: 423, code: "locked" }, "save"));
        expect(String(last.text)).toMatch(lang === "zh" ? /标注没有保存.*只读/ : /not saved.*read-only/);
        vi.unstubAllGlobals();
      }
    } finally {
      await setLang(original);
    }
  });

  it("leaves any other failure its own text", () => {
    expect(annotRefusalText({ status: 500, code: "internal_error" }, "save")).toBeNull();
    expect(annotRefusalText({ status: 409, code: "annotation_reserved" }, "remove")).toBeNull();
    expect(annotRefusalText(new Error("network"), "save")).toBeNull();
  });
});
