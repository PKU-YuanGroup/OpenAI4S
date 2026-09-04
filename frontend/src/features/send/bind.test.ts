import { afterEach, describe, expect, it, vi } from "vitest";
import { installSend } from "./index";
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

function stubDocument(nodes: Record<string, unknown>) {
  vi.stubGlobal("document", {
    getElementById: (id: string) => nodes[id] ?? null,
  });
}

const enter = (): KeyEvent => ({ key: "Enter", preventDefault: vi.fn() });
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("bindComposer", () => {
  it("binds #composer and the mode toggles once, however often it is called", () => {
    const composer = fakeComposer();
    const plan = fakeToggle();
    const explore = fakeToggle();
    stubDocument({ composer, "plan-toggle": plan, "explore-toggle": explore });

    bindComposer(vi.fn());
    bindComposer(vi.fn());

    expect(composer.dataset.sendBound).toBe("1");
    expect(composer.listeners.keydown).toHaveLength(1);
    expect(plan.dataset.sendBound).toBe("1");
    expect(typeof plan.onclick).toBe("function");
    expect(explore.dataset.sendBound).toBe("1");
    expect(typeof explore.onclick).toBe("function");
  });

  it("installSend() stays DOM-free; main.tsx binds after render(<App/>)", () => {
    // installSend() runs at module import, before Shell.tsx has rendered the
    // composer. An import-time bind found nothing and never tried again,
    // which is how Enter shipped dead. The bind belongs to the post-render
    // slot in main.tsx, next to bootChrome(), not to install.
    const composer = fakeComposer();
    stubDocument({ composer });

    installSend({});

    expect(composer.dataset.sendBound).toBeUndefined();
    expect(composer.listeners.keydown).toBeUndefined();
  });

  it("Enter dispatches the composer text; Shift+Enter, IME and an open autocomplete do not", () => {
    const composer = fakeComposer();
    stubDocument({ composer });
    const dispatch = vi.fn(() => Promise.resolve());
    bindComposer(dispatch);
    const onKey = composer.listeners.keydown![0]!;
    composer.value = "hello";

    for (const e of [
      { key: "Enter", shiftKey: true, preventDefault: vi.fn() },
      { key: "Enter", isComposing: true, preventDefault: vi.fn() },
      { key: "Enter", keyCode: 229, preventDefault: vi.fn() },
      { key: "a", preventDefault: vi.fn() },
    ]) {
      onKey(e);
      expect(e.preventDefault).not.toHaveBeenCalled();
    }
    vi.stubGlobal("ac", { open: true });
    const withAutocomplete = enter();
    onKey(withAutocomplete);
    expect(withAutocomplete.preventDefault).not.toHaveBeenCalled();
    vi.stubGlobal("ac", undefined);
    expect(dispatch).not.toHaveBeenCalled();

    const plain = enter();
    onKey(plain);
    expect(plain.preventDefault).toHaveBeenCalledTimes(1);
    expect(dispatch).toHaveBeenCalledTimes(1);
    expect(dispatch).toHaveBeenCalledWith("hello");
  });

  it("drops a repeated Enter while the previous dispatch is still in flight", async () => {
    // send() clears the composer only after it has awaited POST /frames on a
    // fresh session (or the skills catalog for a /skill token). A held or
    // double Enter inside that window created a second session and sent the
    // same text twice.
    const composer = fakeComposer();
    stubDocument({ composer });
    let settle!: () => void;
    const dispatch = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          settle = resolve;
        }),
    );
    bindComposer(dispatch);
    const onKey = composer.listeners.keydown![0]!;
    composer.value = "hello";

    const first = enter();
    const repeat = enter();
    onKey(first);
    onKey(repeat);
    onKey(enter());
    expect(dispatch).toHaveBeenCalledTimes(1);
    // The key is still consumed: a swallowed Enter must not insert a newline.
    expect(repeat.preventDefault).toHaveBeenCalledTimes(1);

    settle();
    await tick();
    composer.value = "again";
    onKey(enter());
    expect(dispatch).toHaveBeenCalledTimes(2);
    expect(dispatch).toHaveBeenLastCalledWith("again");
  });
});
