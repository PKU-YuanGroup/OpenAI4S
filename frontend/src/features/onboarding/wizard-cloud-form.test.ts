import { beforeEach, describe, expect, it, vi } from "vitest";

// FR-2: with no model the wizard opened on the empty "configured models" tab;
// the cloud form asked for the key last, so "Fetch model list" above it failed;
// the missing-name message appeared beside that button; there was only a
// protocol dropdown; the modal covered the language switch; and a "Checklist"
// view repeated the step rail.

const hooks = vi.hoisted(() => {
  const slots: unknown[] = [];
  let cursor = 0;
  const set = (index: number, value: unknown) => {
    slots[index] = typeof value === "function" ? (value as (v: unknown) => unknown)(slots[index]) : value;
  };
  return {
    slots,
    reset() {
      slots.length = 0;
      cursor = 0;
    },
    begin() {
      cursor = 0;
    },
    useState(initial: unknown) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = initial;
      return [slots[index], (value: unknown) => set(index, value)];
    },
    useRef(initial: unknown) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = { current: initial };
      return slots[index];
    },
    useReducer(reducer: (state: unknown, action: unknown) => unknown, initial: unknown) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = initial;
      return [slots[index], (action: unknown) => set(index, reducer(slots[index], action))];
    },
  };
});

vi.mock("preact/hooks", () => ({
  useEffect: vi.fn(),
  useReducer: hooks.useReducer,
  useRef: hooks.useRef,
  useState: hooks.useState,
}));

import { WizardHost } from "../../components/onboarding/Wizard";
import { t } from "../../i18n";
import { ot } from "./copy";
import { INITIAL_WIZARD } from "./machine";
import { keyPage, presetLabel, readProviderPresets } from "./presets";
import { sanitizeOnboardingStatus } from "./status";

type VNode = { type?: unknown; props?: Record<string, unknown> & { children?: unknown } };

function textOf(node: unknown): string {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join("");
  return textOf((node as VNode).props?.children);
}

function all(node: unknown, match: (node: VNode) => boolean, out: VNode[] = []): VNode[] {
  if (Array.isArray(node)) {
    for (const child of node) all(child, match, out);
    return out;
  }
  if (!node || typeof node !== "object") return out;
  const vnode = node as VNode;
  if (match(vnode)) out.push(vnode);
  return all(vnode.props?.children, match, out);
}

const PRESETS = [
  { id: "ark", base_url: "https://ark.example.test/api/v3", model: "ark-model" },
  { id: "chatgpt", base_url: "https://openai.example.test/v1", model: "gpt-model" },
];

function status(overrides: Record<string, unknown> = {}) {
  return sanitizeOnboardingStatus({
    provider: "ark",
    profiles: [],
    protocols: ["ark", "chatgpt"],
    provider_presets: PRESETS,
    local_model_catalog: { endpoints: [] },
    ...overrides,
  });
}

/** The wizard shell, and the model step rendered inside it. */
function render(): { shell: unknown; path: unknown } {
  hooks.begin();
  const shell = WizardHost();
  const step = all(shell, (node) => typeof node.props?.onChoose === "function")[0]!;
  return { shell, path: (step.type as (props: unknown) => unknown)(step.props) };
}

const selectedTab = (path: unknown) =>
  textOf(all(path, (node) => node.props?.role === "tab" && node.props?.["aria-selected"] === true)[0]);
const has = (node: unknown, attr: string) => all(node, (n) => !!n.props && attr in n.props);

beforeEach(() => {
  hooks.reset();
  hooks.slots[0] = { ...INITIAL_WIZARD, surface: "wizard", step: "path" };
  hooks.slots[1] = status();
});

describe("first-run wizard: the cloud model form", () => {
  it("opens on the cloud form when no model is configured, and on the list when one is", () => {
    expect(selectedTab(render().path)).toBe(ot("onboarding.path.cloud"));

    hooks.reset();
    hooks.slots[0] = { ...INITIAL_WIZARD, surface: "wizard", step: "path" };
    hooks.slots[1] = status({ profiles: [{ id: "mp-1", name: "Saved", provider: "ark", model: "m" }] });
    expect(selectedTab(render().path)).toBe(ot("onboarding.path.existing"));
  });

  it("asks for the API key before the model", () => {
    const labels = all(render().path, (node) => node.props?.class === "skill-lbl").map(textOf);
    expect(labels).toEqual([
      ot("onboarding.path.provider"),
      t("cust.connectors.namePlaceholder"),
      t("cust.models.label.baseUrl"),
      t("cust.models.label.apiKey"),
      t("label.model"),
    ]);
  });

  it("offers providers by name with the registry's endpoint and a documented key page", () => {
    const { path } = render();
    const options = all(path, (node) => node.type === "option").map(textOf);
    expect(options).toEqual([ot("onboarding.preset.ark"), ot("onboarding.preset.chatgpt")]);
    expect(textOf(has(path, "data-onboarding-preset")[0])).toContain("ark-model");
    const baseUrl = all(path, (node) => node.type === "input" && String(node.props?.placeholder).includes("ark.example.test"));
    expect(baseUrl).toHaveLength(1);
    const link = all(has(path, "data-onboarding-key-source")[0], (node) => node.type === "a")[0]!;
    expect(link.props!.href).toBe("https://console.volcengine.com/ark");

    // A provider the docs name no page for gets words, not an invented link.
    const select = has(path, "data-onboarding-provider")[0]!;
    (select.props!.onChange as (e: unknown) => void)({ currentTarget: { value: "chatgpt" } });
    const next = render().path;
    expect(all(has(next, "data-onboarding-key-source")[0], (node) => node.type === "a")).toHaveLength(0);
    expect(textOf(has(next, "data-onboarding-key-source")[0])).toBe(ot("onboarding.path.getKeyVendor"));
  });

  it("puts the missing-name message under the name field", async () => {
    const first = render().path;
    expect(has(first, "data-onboarding-name-error")).toHaveLength(0);
    const next = all(first, (node) => node.type === "button" && textOf(node) === ot("onboarding.next"))[0]!;
    await (next.props!.onClick as () => Promise<void>)();

    const path = render().path;
    const form = all(path, (node) => node.props?.class === "skill-form")[0]!;
    const children = (form.props!.children as unknown[]).flat().filter(Boolean) as VNode[];
    const nameAt = children.findIndex((node) => !!node.props && "data-onboarding-name" in node.props);
    expect(textOf(children[nameAt + 1])).toBe(t("toast.specialist.enterName"));
    expect(children[nameAt + 1]!.props!.role).toBe("alert");
    expect(all(path, (node) => node.props?.class === "onb-model-discovery-error")).toHaveLength(0);
  });

  it("has a language switch inside the modal and no second list of the steps", () => {
    const { shell } = render();
    expect(textOf(has(shell, "data-onboarding-lang")[0])).toBe(ot("onboarding.lang.switch"));
    const buttons = all(shell, (node) => node.type === "button").map(textOf);
    expect(buttons).not.toContain(ot("onboarding.checklist"));
    // tests/browser_auth.mjs dismisses the wizard through this attribute: the
    // first outline button in the dialog is no longer Skip.
    expect(textOf(has(shell, "data-onboarding-skip")[0])).toBe(ot("onboarding.skip"));
  });
});

describe("provider presets", () => {
  it("reads the registry's presets and drops anything malformed", () => {
    expect(readProviderPresets([...PRESETS, null, { id: "" }, { id: "ark", base_url: "x" }])).toEqual([
      { id: "ark", baseUrl: "https://ark.example.test/api/v3", model: "ark-model" },
      { id: "chatgpt", baseUrl: "https://openai.example.test/v1", model: "gpt-model" },
    ]);
    expect(readProviderPresets(undefined)).toEqual([]);
  });

  it("names a provider, and lists a key page only where the docs do", () => {
    expect(presetLabel("claude", "fallback")).toBe(ot("onboarding.preset.claude"));
    expect(presetLabel("custom_wire", "Custom")).toBe("Custom");
    expect(keyPage("gemini")).toEqual({ url: "https://aistudio.google.com", label: "aistudio.google.com" });
    expect(keyPage("chatgpt")).toBeNull();
    expect(keyPage("openai_responses")).toBeNull();
  });

  it("words the missing-environment hint the way the CLI banner does", () => {
    expect(ot("onboarding.env.remediation")).toMatch(/openai4s setup|`openai4s setup`/);
  });
});
