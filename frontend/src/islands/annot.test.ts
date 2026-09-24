import { afterEach, describe, expect, it, vi } from "vitest";
import { annotationId, annotationIsHeld, annotationStatus, openAnnotations, updateAnnotBadge } from "./annot";
import { setArtifactsFetch } from "../features/artifacts/api";
import { annotations } from "../stores/session";
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
});
