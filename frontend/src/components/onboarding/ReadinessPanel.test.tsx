import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// The standard-profile readiness card is shared by first-run setup and
// Settings -> Compute; its Copy buttons go through the one clipboard helper.

const hookState: unknown[] = [];
let hookCursor = 0;

vi.mock("preact/hooks", () => ({
  useState: (initial: unknown) => {
    const index = hookCursor++;
    if (hookState.length === index) hookState.push(initial);
    return [
      hookState[index],
      (value: unknown) => {
        hookState[index] =
          typeof value === "function" ? (value as (prev: unknown) => unknown)(hookState[index]) : value;
      },
    ];
  },
  useEffect: vi.fn(),
}));

const mocks = vi.hoisted(() => ({ hint: vi.fn() }));
vi.mock("../../i18n", () => ({
  LANG: "en",
  t: (key: string, ...args: unknown[]) => [key, ...args].join(" "),
  tOptional: () => null,
}));
vi.mock("../../features/customize/host", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../features/customize/host")>()),
  hint: mocks.hint,
}));

import { copyFailedText } from "../../features/chrome/clipboard";
import type { StandardReadiness } from "../../features/customize/environment";
import { CopyCommand, EnvironmentCard } from "./ReadinessPanel";

type Node = { type?: unknown; props?: Record<string, unknown> & { children?: unknown } };

function find(node: unknown, match: (node: Node) => boolean): Node | null {
  if (Array.isArray(node)) {
    for (const child of node) {
      const found = find(child, match);
      if (found) return found;
    }
    return null;
  }
  if (!node || typeof node !== "object") return null;
  const current = node as Node;
  if (match(current)) return current;
  return find(current.props?.children, match);
}

function text(node: unknown): string {
  if (Array.isArray(node)) return node.map(text).join("");
  if (typeof node === "string" || typeof node === "number") return String(node);
  return node && typeof node === "object" ? text((node as Node).props?.children) : "";
}

const ITEM = { command: "openai4s setup --profile standard", label: "Install" };

function copyButton(): Node {
  hookCursor = 0;
  return find(CopyCommand({ item: ITEM }), (node) => node.type === "button")!;
}

/** A plain-http page: no async clipboard, only the selection copy. */
function plainHttp(execCopy: boolean) {
  vi.stubGlobal("navigator", {});
  const area = { value: "", style: {}, setAttribute() {}, select() {}, remove() {} };
  vi.stubGlobal("document", {
    body: { appendChild() {} },
    activeElement: null,
    createElement: () => area,
    execCommand: () => execCopy,
  });
  return area;
}

beforeEach(() => {
  hookState.length = 0;
  hookCursor = 0;
  mocks.hint.mockReset();
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("readiness Copy", () => {
  it("copies over plain http through the selection copy", async () => {
    const area = plainHttp(true);
    await (copyButton().props!.onClick as () => Promise<void>)();
    expect(area.value).toBe(ITEM.command);
    expect(text(copyButton())).toBe("code.copied");
    expect(mocks.hint).toHaveBeenCalledWith("environment.readiness.copied");
  });

  it("says so when no copy happened, and does not claim one", async () => {
    plainHttp(false);
    await (copyButton().props!.onClick as () => Promise<void>)();
    expect(text(copyButton())).toBe("environment.readiness.copy");
    expect(mocks.hint).toHaveBeenCalledWith(copyFailedText(), true);
  });
});

describe("readiness card", () => {
  it("carries the action a host puts in its head", () => {
    const readiness = {
      enabled: true,
      ready: false,
      state: "missing",
      missing_environments: [],
      missing_packages: {},
      remediation: null,
    } as unknown as StandardReadiness;
    const card = EnvironmentCard({ readiness, action: <button data-refresh="1" /> }) as Node;
    const head = find(card, (node) => node.props?.class === "standard-readiness-head")!;
    expect(find(head, (node) => node.props?.["data-refresh"] === "1")).not.toBeNull();
  });
});
