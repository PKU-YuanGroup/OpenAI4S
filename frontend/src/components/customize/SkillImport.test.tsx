import { beforeEach, describe, expect, it, vi } from "vitest";

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
          typeof value === "function"
            ? (value as (prev: unknown) => unknown)(hookState[index])
            : value;
      },
    ];
  },
  useEffect: vi.fn(),
}));

const mocks = vi.hoisted(() => ({
  api: vi.fn(),
  hint: vi.fn(),
  dropSkillsCatalog: vi.fn(),
  custTab: vi.fn(),
  nestedEditor: { value: { kind: "skill-import" as const } },
}));

vi.mock("../../features/customize/api", () => ({
  api: (...args: unknown[]) => mocks.api(...args),
  apiErrorText: (e: unknown) => String((e as Error).message || e),
}));

vi.mock("../../features/customize/actions", () => ({
  custTab: (...args: unknown[]) => mocks.custTab(...args),
}));

vi.mock("../../features/customize/state", () => ({
  nestedEditor: mocks.nestedEditor,
}));

vi.mock("../../features/customize/host", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../features/customize/host")>();
  return {
    ...original,
    hint: (...args: unknown[]) => mocks.hint(...args),
    dropSkillsCatalog: () => mocks.dropSkillsCatalog(),
  };
});

vi.mock("../../i18n", () => ({
  LANG: "en",
  t: (key: string, ...args: unknown[]) =>
    [key, ...args].filter((item) => item !== undefined).join(" "),
}));

import { SkillImport } from "./NestedEditor";

type Node = {
  type?: unknown;
  props?: Record<string, unknown> & {
    children?: unknown;
    class?: string;
    onClick?: () => void | Promise<void>;
    onInput?: (event: { target: { value: string } }) => void;
  };
};

function render(): Node {
  hookCursor = 0;
  return SkillImport() as Node;
}

function walk(node: unknown, visit: (current: Node) => void): void {
  if (!node || typeof node !== "object") return;
  const current = node as Node;
  visit(current);
  const children = current.props?.children;
  for (const child of Array.isArray(children) ? children : [children]) walk(child, visit);
}

function findByAttr(node: unknown, attr: string): Node | null {
  let found: Node | null = null;
  walk(node, (current) => {
    if (current.props && current.props[attr] != null) found = current;
  });
  return found;
}

function findType(node: unknown, type: string): Node | null {
  let found: Node | null = null;
  walk(node, (current) => {
    if (current.type === type) found = current;
  });
  return found;
}

function findSolidButton(node: unknown): Node | null {
  let found: Node | null = null;
  walk(node, (current) => {
    if (current.type === "button" && current.props?.class === "solid-btn") found = current;
  });
  return found;
}

describe("SkillImport review", () => {
  beforeEach(() => {
    hookState.length = 0;
    hookCursor = 0;
    mocks.api.mockReset();
    mocks.hint.mockReset();
    mocks.dropSkillsCatalog.mockReset();
    mocks.custTab.mockReset();
    mocks.nestedEditor.value = { kind: "skill-import" };
  });

  it("shows requirements, capabilities, and readiness before enable", async () => {
    mocks.api.mockResolvedValue({
      ok: true,
      name: "cryo-import",
      slug: "cryo-import",
      origin: "user",
      review: {
        requirements: ["cuda", "gpu"],
        capabilities: {
          network: {
            mode: "host_only",
            domains: ["api.openalex.org", "doi.org"],
            ui: {
              label_en: "host-mediated network only",
              label_zh: "仅 Host 网络",
            },
          },
        },
        readiness: {
          state: "needs_setup",
          missing: ["gpu"],
          unverifiable: [],
          blocked_on: [],
          ready: false,
        },
        warnings: ["missing:gpu"],
      },
    });

    const typed = render();
    const textarea = findType(typed, "textarea");
    expect(textarea?.props?.onInput).toBeTypeOf("function");
    textarea!.props!.onInput!({
      target: { value: "---\nname: cryo-import\n---\nbody" },
    });

    const ready = render();
    const button = findSolidButton(ready);
    expect(button?.props?.onClick).toBeTypeOf("function");
    await button!.props!.onClick!();

    const reviewed = render();
    expect(findByAttr(reviewed, "data-skill-import-review")).not.toBeNull();
    expect(findByAttr(reviewed, "data-review-requirements")?.props?.["data-review-requirements"]).toBe(
      "cuda,gpu",
    );
    expect(findByAttr(reviewed, "data-review-capabilities")?.props?.["data-review-capabilities"]).toBe(
      "host_only",
    );
    expect(findByAttr(reviewed, "data-review-readiness")?.props?.["data-review-readiness"]).toBe(
      "needs_setup",
    );
    expect(mocks.custTab).not.toHaveBeenCalled();
    expect(mocks.nestedEditor.value).toEqual({ kind: "skill-import" });
    expect(mocks.api).toHaveBeenCalledWith("/skills/import", {
      method: "POST",
      body: JSON.stringify({ content: "---\nname: cryo-import\n---\nbody" }),
    });
    // The cached catalog is dropped as soon as the import lands, not only by
    // the Close button: Escape, the backdrop and the header X dismiss the
    // review pane too, and every reader of the catalog must see the new Skill.
    expect(mocks.dropSkillsCatalog).toHaveBeenCalledTimes(1);
  });
});
