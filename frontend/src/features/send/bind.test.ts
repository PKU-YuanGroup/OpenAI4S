import { afterEach, describe, expect, it, vi } from "vitest";
import { bindComposer } from "./send";

type KeyEvent = {
  key?: string;
  shiftKey?: boolean;
  isComposing?: boolean;
  keyCode?: number;
  preventDefault: () => void;
};
type Listener = (e: KeyEvent) => void;

function fakeToggle() {
  return {
    dataset: {} as Record<string, string>,
    onclick: null as (() => void) | null,
    classList: { toggle: vi.fn(), remove: vi.fn() },
  };
}

function fakeComposer() {
  const listeners: Record<string, Listener[]> = {};
  return {
    dataset: {} as Record<string, string>,
    value: "",
    listeners,
    addEventListener: (type: string, fn: Listener) => {
      (listeners[type] ||= []).push(fn);
    },
  };
}

type Observer = { cb: () => void; observe: ReturnType<typeof vi.fn>; disconnect: ReturnType<typeof vi.fn> };

function stubMutationObserver(): Observer[] {
  const observers: Observer[] = [];
  vi.stubGlobal(
    "MutationObserver",
    class {
      observe = vi.fn();
      disconnect = vi.fn();
      constructor(cb: () => void) {
        observers.push({ cb, observe: this.observe, disconnect: this.disconnect });
      }
    },
  );
  return observers;
}

function stubDocument(nodes: () => Record<string, unknown>) {
  const documentElement = {};
  vi.stubGlobal("document", {
    documentElement,
    body: {},
    getElementById: (id: string) => nodes()[id] ?? null,
  });
  return documentElement;
}

afterEach(() => {
  delete (globalThis as { ac?: unknown }).ac;
  vi.unstubAllGlobals();
});

describe("bindComposer", () => {
  it("binds #composer and the mode toggles when they are already mounted", () => {
    const composer = fakeComposer();
    const plan = fakeToggle();
    const explore = fakeToggle();
    const observers = stubMutationObserver();
    stubDocument(() => ({ composer, "plan-toggle": plan, "explore-toggle": explore }));

    bindComposer();

    expect(composer.dataset.sendBound).toBe("1");
    expect(composer.listeners.keydown).toHaveLength(1);
    expect(plan.dataset.sendBound).toBe("1");
    expect(explore.dataset.sendBound).toBe("1");
    expect(observers).toHaveLength(0);
  });

  it("waits for Shell to render #composer instead of giving up at import time", () => {
    // installSend() runs at module import, before Shell.tsx has mounted the
    // composer. The first pass must not be the last one.
    const composer = fakeComposer();
    let mounted = false;
    const observers = stubMutationObserver();
    const root = stubDocument(() => (mounted ? { composer } : {}));

    bindComposer();
    expect(composer.dataset.sendBound).toBeUndefined();
    expect(observers).toHaveLength(1);
    expect(observers[0]!.observe).toHaveBeenCalledWith(root, { childList: true, subtree: true });

    // Unrelated DOM churn before the composer exists: keep watching.
    observers[0]!.cb();
    expect(composer.dataset.sendBound).toBeUndefined();
    expect(observers[0]!.disconnect).not.toHaveBeenCalled();

    mounted = true;
    observers[0]!.cb();
    expect(composer.dataset.sendBound).toBe("1");
    expect(composer.listeners.keydown).toHaveLength(1);
    expect(observers[0]!.disconnect).toHaveBeenCalledTimes(1);

    // A later call is a no-op: no second listener, no second observer.
    bindComposer();
    expect(composer.listeners.keydown).toHaveLength(1);
    expect(observers).toHaveLength(1);
  });

  it("sends on Enter and leaves Shift+Enter, IME composition and an open autocomplete alone", () => {
    const composer = fakeComposer();
    stubMutationObserver();
    stubDocument(() => ({ composer }));
    bindComposer();
    const onKey = composer.listeners.keydown![0]!;

    const shift = { key: "Enter", shiftKey: true, preventDefault: vi.fn() };
    onKey(shift);
    expect(shift.preventDefault).not.toHaveBeenCalled();

    const composing = { key: "Enter", isComposing: true, preventDefault: vi.fn() };
    onKey(composing);
    expect(composing.preventDefault).not.toHaveBeenCalled();

    const ime = { key: "Enter", keyCode: 229, preventDefault: vi.fn() };
    onKey(ime);
    expect(ime.preventDefault).not.toHaveBeenCalled();

    (globalThis as { ac?: { open?: boolean } }).ac = { open: true };
    const withAutocomplete = { key: "Enter", preventDefault: vi.fn() };
    onKey(withAutocomplete);
    expect(withAutocomplete.preventDefault).not.toHaveBeenCalled();
    delete (globalThis as { ac?: unknown }).ac;

    // Empty composer: send() returns early, but the key is still consumed.
    const enter = { key: "Enter", preventDefault: vi.fn() };
    onKey(enter);
    expect(enter.preventDefault).toHaveBeenCalledTimes(1);
  });
});
