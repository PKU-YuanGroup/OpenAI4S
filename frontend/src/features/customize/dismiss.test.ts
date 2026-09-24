import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { _modalFocus, closeModalEl, resetModalTrap, trapModalKeydown } from "../chrome/modal";
import {
  customizeOnTop,
  followCustomizeDom,
  installCustomizeEscape,
  onCustomizeKeydown,
} from "./dismiss";
import { customizeOpen, nestedEditor } from "./state";

class FakeEl {
  readonly classes: Set<string>;
  constructor(
    readonly id: string,
    hidden: boolean,
  ) {
    this.classes = new Set(hidden ? ["modal", "hidden"] : ["modal"]);
  }
  readonly classList = {
    add: (name: string) => void this.classes.add(name),
    remove: (name: string) => void this.classes.delete(name),
    contains: (name: string) => this.classes.has(name),
  };
}

let modals: Record<string, FakeEl>;

function keydown(key = "Escape") {
  const event = {
    key,
    defaultPrevented: false,
    isComposing: false,
    preventDefault() {
      event.defaultPrevented = true;
    },
  };
  // Chrome's listener is registered at boot, Customize's after its first
  // paint, so chrome's trap sees the key first.
  trapModalKeydown(event);
  onCustomizeKeydown(event);
  return event;
}

beforeEach(() => {
  modals = { cust: new FakeEl("cust", false), modal: new FakeEl("modal", true) };
  const doc = {
    getElementById: (id: string) => modals[id] || null,
    querySelector: (sel: string) => (sel.startsWith("#") ? modals[sel.slice(1)] || null : null),
    contains: () => false,
    activeElement: null,
  };
  vi.stubGlobal("document", doc);
  vi.stubGlobal("window", { closeModalEl });
  resetModalTrap();
  _modalFocus.stack.push({ el: modals.cust as unknown as HTMLElement, prev: null });
  customizeOpen.value = true;
  nestedEditor.value = { kind: "skill", name: "cryo" };
  installCustomizeEscape();
});

afterEach(() => {
  resetModalTrap();
  customizeOpen.value = false;
  nestedEditor.value = null;
  vi.unstubAllGlobals();
});

describe("Customize Escape", () => {
  it("closes only the nested editor, and Customize on the next press", () => {
    keydown();
    expect(nestedEditor.value).toBeNull();
    expect(customizeOpen.value).toBe(true);
    expect(modals.cust!.classList.contains("hidden")).toBe(false);

    keydown();
    expect(customizeOpen.value).toBe(false);
    expect(modals.cust!.classList.contains("hidden")).toBe(true);
  });

  it("leaves a modal opened above Customize to chrome", () => {
    modals.modal!.classList.remove("hidden");
    _modalFocus.stack.push({ el: modals.modal as unknown as HTMLElement, prev: null });
    expect(customizeOnTop()).toBe(false);

    keydown();
    expect(modals.modal!.classList.contains("hidden")).toBe(true);
    expect(nestedEditor.value).not.toBeNull();
    expect(customizeOpen.value).toBe(true);
    expect(modals.cust!.classList.contains("hidden")).toBe(false);
  });

  it("ignores the Escape that ends an IME composition", () => {
    const event = { key: "Escape", isComposing: true, defaultPrevented: false, preventDefault: vi.fn() };
    trapModalKeydown(event);
    onCustomizeKeydown(event);
    expect(event.preventDefault).not.toHaveBeenCalled();
    expect(nestedEditor.value).not.toBeNull();
    expect(modals.cust!.classList.contains("hidden")).toBe(false);
  });
});

describe("Customize open state", () => {
  it("follows #cust when another path hides it", () => {
    let notify: () => void = () => {};
    class FakeObserver {
      constructor(callback: () => void) {
        notify = callback;
      }
      observe() {}
      disconnect() {}
    }
    vi.stubGlobal("MutationObserver", FakeObserver);
    followCustomizeDom(modals.cust as unknown as HTMLElement);

    // chrome's backdrop binding: `.hidden` goes on, nobody calls closeCust().
    closeModalEl(modals.cust as unknown as HTMLElement);
    notify();
    expect(customizeOpen.value).toBe(false);
    expect(nestedEditor.value).toBeNull();
  });
});
