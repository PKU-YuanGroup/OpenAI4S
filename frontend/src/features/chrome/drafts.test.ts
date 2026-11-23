import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { currentId } from "../../stores/session";
import { resetStoreFields } from "../../stores/signal-field";
import { clearMentions, composerMentions, rememberMention } from "../autocomplete/mentions";
import {
  DRAFT_STORAGE_KEY,
  MAX_DRAFT_CHARS,
  _resetDrafts,
  bootDrafts,
  carryDraftTo,
  clearDraft,
  draftFor,
  switchDraft,
} from "./drafts";

type Box = { value: string; style: Record<string, string>; scrollHeight: number; listeners: Array<() => void> };

let box: Box;
let storage: Map<string, string>;

function type(text: string): void {
  box.value = text;
  box.listeners.forEach((fn) => fn());
}

function stubStorage(throwing = false): void {
  storage = new Map();
  vi.stubGlobal("localStorage", {
    getItem: (k: string) => {
      if (throwing) throw new Error("blocked");
      return storage.has(k) ? storage.get(k)! : null;
    },
    setItem: (k: string, v: string) => {
      if (throwing) throw new Error("blocked");
      storage.set(k, v);
    },
    removeItem: (k: string) => {
      if (throwing) throw new Error("blocked");
      storage.delete(k);
    },
  });
}

describe("SYNC-24 composer drafts are per session", () => {
  beforeEach(() => {
    resetStoreFields();
    _resetDrafts();
    clearMentions();
    box = { value: "", style: {}, scrollHeight: 20, listeners: [] };
    vi.stubGlobal("document", {
      querySelector: (sel: string) => (sel === "#composer" ? box : null),
      getElementById: () => null,
    });
    vi.stubGlobal("window", { addEventListener: () => {} });
    (box as unknown as { addEventListener: (t: string, fn: () => void) => void }).addEventListener = (
      t: string,
      fn: () => void,
    ) => {
      if (t === "input") box.listeners.push(fn);
    };
    stubStorage();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    clearMentions();
  });

  it("New does not carry A's half-written question, and A gets it back", () => {
    currentId.value = "f-growth";
    bootDrafts();
    type("DRAFT-IN-GROWTH: half-written question");
    currentId.value = "f-new";
    expect(box.value).toBe("");
    type("draft in the new session");
    currentId.value = "f-growth";
    expect(box.value).toBe("DRAFT-IN-GROWTH: half-written question");
    currentId.value = "f-new";
    expect(box.value).toBe("draft in the new session");
  });

  it("a reload puts the open session's draft back", () => {
    currentId.value = "f-a";
    bootDrafts();
    type("survives a reload");
    // A fresh page: nothing in memory, the same storage.
    _resetDrafts();
    box.value = "";
    currentId.value = null;
    bootDrafts();
    currentId.value = "f-a";
    expect(box.value).toBe("survives a reload");
  });

  it("sending clears the session's draft, in memory and in storage", () => {
    currentId.value = "f-a";
    bootDrafts();
    type("send me");
    expect(storage.get(DRAFT_STORAGE_KEY)).toContain("send me");
    box.value = "";
    clearDraft("f-a");
    expect(draftFor("f-a")).toBe("");
    expect(storage.has(DRAFT_STORAGE_KEY)).toBe(false);
  });

  it("a session created from the composer of no session takes the draft along", () => {
    bootDrafts();
    type("first question");
    carryDraftTo("f-created");
    currentId.value = "f-created";
    expect(box.value).toBe("first question");
    expect(draftFor("f-created")).toBe("first question");
  });

  it("the @ pins go and come back with their draft", () => {
    currentId.value = "f-a";
    bootDrafts();
    rememberMention({ filename: "cohort.csv", version_id: "v-9f5c06efc35e", artifact_id: "a", root_frame_id: "f-a" });
    type("@cohort.csv rows?");
    currentId.value = "f-b";
    expect(composerMentions("@cohort.csv rows?")).toEqual([]);
    currentId.value = "f-a";
    expect(composerMentions(box.value).map((m) => m.version_id)).toEqual(["v-9f5c06efc35e"]);
  });

  it("blocked storage still switches drafts within the page", () => {
    stubStorage(true);
    currentId.value = "f-a";
    bootDrafts();
    type("kept in memory");
    switchDraft("f-b");
    expect(box.value).toBe("");
    switchDraft("f-a");
    expect(box.value).toBe("kept in memory");
  });

  it("a draft too long for the reload copy is not written there", () => {
    currentId.value = "f-a";
    bootDrafts();
    type("x".repeat(MAX_DRAFT_CHARS + 1));
    expect(storage.has(DRAFT_STORAGE_KEY)).toBe(false);
    switchDraft("f-b");
    switchDraft("f-a");
    expect(box.value.length).toBe(MAX_DRAFT_CHARS + 1);
  });
});
