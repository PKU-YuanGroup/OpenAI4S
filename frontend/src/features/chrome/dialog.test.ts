/**
 * M-45: confirm / text / notice dialogs are the workbench's own, and no
 * native prompt() / confirm() / alert() is left in the workbench source.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./modal", () => ({
  openModalEl: vi.fn((modal: FakeEl) => modal.classList.remove("hidden")),
  closeModalEl: vi.fn((modal: FakeEl) => modal.classList.add("hidden")),
}));

import { askConfirm, askText, showNotice } from "./dialog";
import { closeModalEl, openModalEl } from "./modal";

type Listener = (event: FakeEvent) => void;
type FakeEvent = {
  type: string;
  key?: string;
  target: FakeEl;
  isComposing?: boolean;
  defaultPrevented: boolean;
  propagationStopped: boolean;
  preventDefault: () => void;
  stopPropagation: () => void;
};

class FakeEl {
  id = "";
  type = "";
  value = "";
  textContent = "";
  className = "";
  selected = false;
  parent: FakeEl | null = null;
  children: FakeEl[] = [];
  attrs = new Map<string, string>();
  listeners = new Map<string, Listener[]>();
  constructor(readonly tagName: string) {}
  get classList() {
    const own = this;
    const tokens = () => own.className.split(/\s+/).filter(Boolean);
    return {
      add: (name: string) => {
        if (!tokens().includes(name)) own.className = [...tokens(), name].join(" ");
      },
      remove: (name: string) => {
        own.className = tokens().filter((token) => token !== name).join(" ");
      },
      contains: (name: string) => tokens().includes(name),
    };
  }
  setAttribute(name: string, value: string) {
    this.attrs.set(name, value);
  }
  hasAttribute(name: string) {
    return this.attrs.has(name);
  }
  appendChild(child: FakeEl) {
    child.parent = this;
    this.children.push(child);
    return child;
  }
  remove() {
    if (this.parent) this.parent.children = this.parent.children.filter((child) => child !== this);
    this.parent = null;
  }
  select() {
    this.selected = true;
  }
  addEventListener(type: string, fn: Listener) {
    this.listeners.set(type, [...(this.listeners.get(type) || []), fn]);
  }
  /** Bubbles from this node up to the root, like a real event. */
  fire(type: string, init: Partial<FakeEvent> = {}): FakeEvent {
    const event: FakeEvent = {
      type,
      target: this,
      defaultPrevented: false,
      propagationStopped: false,
      preventDefault() {
        event.defaultPrevented = true;
      },
      stopPropagation() {
        event.propagationStopped = true;
      },
      ...init,
    };
    for (let node: FakeEl | null = this; node && !event.propagationStopped; node = node.parent) {
      for (const fn of node.listeners.get(type) || []) fn(event);
    }
    return event;
  }
  walk(): FakeEl[] {
    return [this, ...this.children.flatMap((child) => child.walk())];
  }
  find(cls: string): FakeEl | undefined {
    return this.walk().find((node) => node.classList.contains(cls));
  }
}

let body: FakeEl;

beforeEach(() => {
  body = new FakeEl("body");
  vi.stubGlobal("document", { body, createElement: (tag: string) => new FakeEl(tag) });
  vi.mocked(openModalEl).mockClear();
  vi.mocked(closeModalEl).mockClear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const dialog = () => body.children[0]!;

describe("in-app dialogs (M-45)", () => {
  it("a confirm opens on the shared trap and answers yes only from its confirming button", async () => {
    const answer = askConfirm("Delete this session?", { okLabel: "Delete" });
    const modal = dialog();
    expect(openModalEl).toHaveBeenCalledWith(modal);
    expect(modal.attrs.get("role")).toBe("alertdialog");
    expect(modal.attrs.get("aria-modal")).toBe("true");
    expect(modal.find("app-dialog-msg")!.textContent).toBe("Delete this session?");
    const ok = modal.find("app-dialog-ok")!;
    expect(ok.textContent).toBe("Delete");
    ok.fire("click");
    expect(await answer).toBe(true);
    expect(closeModalEl).toHaveBeenCalledWith(modal);
    expect(body.children).toEqual([]);
  });

  it("Cancel, Escape and the scrim all answer no, and Escape goes no further", async () => {
    const cancelled = askConfirm("Restart the kernel?");
    dialog().find("app-dialog-cancel")!.fire("click");
    expect(await cancelled).toBe(false);

    const escaped = askConfirm("Restart the kernel?");
    const press = dialog().find("app-dialog-ok")!.fire("keydown", { key: "Escape" });
    expect(press.propagationStopped).toBe(true);
    expect(press.defaultPrevented).toBe(true);
    expect(await escaped).toBe(false);

    const scrim = askConfirm("Restart the kernel?");
    dialog().fire("pointerdown");
    dialog().fire("click");
    expect(await scrim).toBe(false);
    expect(body.children).toEqual([]);
  });

  it("a press that starts in the dialog and ends on the scrim does not dismiss it", async () => {
    const answer = askConfirm("Delete?");
    dialog().find("app-dialog-msg")!.fire("pointerdown");
    dialog().fire("click");
    expect(body.children).toHaveLength(1);
    dialog().find("app-dialog-ok")!.fire("click");
    expect(await answer).toBe(true);
  });

  it("a destructive confirm puts the first focus on Cancel and marks its button", async () => {
    const answer = askConfirm("Delete folder?", { danger: true });
    expect(dialog().find("app-dialog-cancel")!.hasAttribute("data-autofocus")).toBe(true);
    expect(dialog().find("app-dialog-ok")!.hasAttribute("data-autofocus")).toBe(false);
    expect(dialog().find("app-dialog-ok")!.classList.contains("danger")).toBe(true);
    dialog().find("app-dialog-cancel")!.fire("click");
    expect(await answer).toBe(false);
  });

  it("a text dialog starts from the given value, selects it, and Enter submits what was typed", async () => {
    const answer = askText("Rename folder", "Assays");
    const input = dialog().find("app-dialog-input")!;
    expect(dialog().attrs.get("role")).toBe("dialog");
    expect(input.value).toBe("Assays");
    expect(input.hasAttribute("data-autofocus")).toBe(true);
    input.fire("focus");
    expect(input.selected).toBe(true);
    input.value = "Assays 2026";
    input.fire("keydown", { key: "Enter" });
    expect(await answer).toBe("Assays 2026");
  });

  it("Enter while an IME is composing does not submit", async () => {
    const answer = askText("Folder name");
    const input = dialog().find("app-dialog-input")!;
    input.value = "实验";
    input.fire("keydown", { key: "Enter", isComposing: true });
    expect(body.children).toHaveLength(1);
    dialog().find("app-dialog-cancel")!.fire("click");
    expect(await answer).toBeNull();
  });

  it("a notice has one button and resolves when dismissed", async () => {
    const done = showNotice("Upload failed: HTTP 500");
    expect(dialog().find("app-dialog-cancel")).toBeUndefined();
    dialog().find("app-dialog-ok")!.fire("click");
    await expect(done).resolves.toBeUndefined();
  });

  it("with no document it cancels instead of hanging or answering yes", async () => {
    vi.stubGlobal("document", undefined);
    expect(await askConfirm("Delete?")).toBe(false);
    expect(await askText("Name")).toBeNull();
  });
});

describe("no native dialog is left in the workbench (M-45)", () => {
  const src = join(dirname(fileURLToPath(import.meta.url)), "../..");

  function sources(dir: string): string[] {
    return readdirSync(dir).flatMap((name) => {
      const path = join(dir, name);
      if (statSync(path).isDirectory()) return sources(path);
      return /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name) ? [path] : [];
    });
  }

  it("finds no prompt() / confirm() / alert() call, bare or through window / globalThis", () => {
    const call = /(^|[^\w.$])(?:(?:window|globalThis|self)\s*\.\s*)?(?:prompt|confirm|alert)\s*\(/;
    const indexed = /\b(?:window|globalThis|self)\s*\[\s*["'`](?:prompt|confirm|alert)["'`]\s*\]/;
    const hits: string[] = [];
    for (const file of sources(src)) {
      // Comments and string contents may name the functions; calls may not.
      const code = readFileSync(file, "utf8").replace(/\/\*[\s\S]*?\*\//g, (block) => block.replace(/[^\n]/g, " "));
      code.split("\n").forEach((line, i) => {
        const bare = line
          .replace(/"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|`(?:[^`\\]|\\.)*`/g, '""')
          .replace(/\/\/.*$/, "");
        if (call.test(bare) || indexed.test(line)) hits.push(`${relative(src, file)}:${i + 1}: ${line.trim()}`);
      });
    }
    expect(hits).toEqual([]);
  });
});
