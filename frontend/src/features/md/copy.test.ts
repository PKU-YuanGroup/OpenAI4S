import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setLang, t } from "../../i18n/runtime";
import { copyFailedText } from "../chrome/clipboard";
import { bindCodeCopy, copyCodeBlock, resetCodeCopyBinding } from "./copy";
import { mdCodeBlock } from "./render";

/** A code block as mdCodeBlock builds it: .codeblock > .cb-head > button.cb-copy, and pre > code. */
class Node {
  tag: string;
  classes: Set<string>;
  attrs: Record<string, string> = {};
  children: Node[] = [];
  parent: Node | null = null;
  textContent = "";
  title = "";
  constructor(tag: string, cls = "", text = "") {
    this.tag = tag;
    this.classes = new Set(cls.split(" ").filter(Boolean));
    this.textContent = text;
  }
  classList = {
    toggle: (name: string, on?: boolean) => {
      const next = on === undefined ? !this.classes.has(name) : on;
      if (next) this.classes.add(name);
      else this.classes.delete(name);
      return next;
    },
    remove: (name: string) => void this.classes.delete(name),
    contains: (name: string) => this.classes.has(name),
  };
  add(child: Node): Node {
    child.parent = this;
    this.children.push(child);
    return child;
  }
  getAttribute(name: string): string | null {
    return this.attrs[name] ?? null;
  }
  setAttribute(name: string, value: string): void {
    this.attrs[name] = value;
  }
  private is(sel: string): boolean {
    return sel.startsWith(".") ? this.classes.has(sel.slice(1)) : this.tag === sel;
  }
  closest(sel: string): Node | null {
    for (let node: Node | null = this; node; node = node.parent) if (node.is(sel)) return node;
    return null;
  }
  querySelector(sel: string): Node | null {
    const parts = sel.split(" ");
    const last = parts.pop()!;
    const walk = (node: Node): Node | null => {
      for (const child of node.children) {
        if (child.is(last) && parts.every((p) => child.parent?.closest(p))) return child;
        const hit = walk(child);
        if (hit) return hit;
      }
      return null;
    };
    return walk(this);
  }
}

function codeBlock(code: string) {
  const block = new Node("div", "codeblock");
  const head = block.add(new Node("div", "cb-head"));
  const button = head.add(new Node("button", "cb-copy"));
  const glyph = button.add(new Node("svg", "ic-svg"));
  const label = button.add(new Node("span", "cb-copy-t", "Copy"));
  block.add(new Node("pre")).add(new Node("code", "", code));
  return { block, button, glyph, label };
}

beforeEach(async () => {
  await setLang("en");
  resetCodeCopyBinding();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("code block Copy", () => {
  it("the button reads the dictionary, not its keys", async () => {
    const en = mdCodeBlock("x = 1", "python");
    expect(en).toContain('title="Copy code"');
    expect(en).toContain('<span class="cb-copy-t">Copy</span>');
    expect(en).not.toContain("code.copy.title");
    expect(en).not.toContain("msgAction.copy");
    await setLang("zh");
    expect(mdCodeBlock("x = 1", "python")).toContain('title="复制代码"');
  });

  it("a click anywhere on the button copies that block's code, through one delegated listener", async () => {
    const writeText = vi.fn(() => Promise.resolve());
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    const handlers: Record<string, (event: { target: unknown }) => void> = {};
    bindCodeCopy({ addEventListener: (type: string, fn: never) => void (handlers[type] = fn) } as never);
    bindCodeCopy({ addEventListener: () => { throw new Error("bound twice"); } } as never);
    const { glyph, label } = codeBlock("print('hi')\n");

    handlers.click!({ target: new Node("p", "", "not a code block") });
    handlers.click!({ target: glyph });
    await vi.waitFor(() => expect(label.textContent).toBe(t("code.copied")));
    expect(writeText).toHaveBeenCalledTimes(1);
    expect(writeText).toHaveBeenCalledWith("print('hi')\n");
  });

  it("says Copied only for a confirmed write, then goes back", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("navigator", { clipboard: { writeText: () => Promise.resolve() } });
    const { button, label } = codeBlock("a = 1");
    expect(await copyCodeBlock(button as never)).toBe(true);
    expect(button.classes.has("copied")).toBe(true);
    expect(label.textContent).toBe("Copied");
    vi.advanceTimersByTime(1400);
    expect(button.classes.has("copied")).toBe(false);
    expect(label.textContent).toBe("Copy");
  });

  it("a refused copy says so instead of claiming success", async () => {
    vi.useFakeTimers();
    // No clipboard API (plain-http LAN) and no selection fallback.
    vi.stubGlobal("navigator", {});
    const { button, label } = codeBlock("a = 1");
    expect(await copyCodeBlock(button as never)).toBe(false);
    expect(button.classes.has("copied")).toBe(false);
    expect(label.textContent).toBe("Copy failed");
    expect(button.title).toBe(copyFailedText());
    vi.advanceTimersByTime(4000);
    expect(label.textContent).toBe("Copy");
    expect(button.title).toBe(t("code.copy.title"));
  });
});
