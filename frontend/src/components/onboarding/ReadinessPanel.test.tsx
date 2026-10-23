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
vi.mock("../../features/onboarding/copy", () => ({
  ot: (key: string, ...args: unknown[]) => [key, ...args].join(" "),
}));
vi.mock("../../features/customize/host", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../features/customize/host")>()),
  hint: mocks.hint,
}));

import { copyFailedText } from "../../features/chrome/clipboard";
import type { StandardReadiness } from "../../features/customize/environment";
import type { OnboardingStatus } from "../../features/onboarding/status";
import { CopyCommand, EnvironmentCard, ReadinessPanel } from "./ReadinessPanel";

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

// O1: first-run setup can re-read the local readiness projection in place.
// The same card that lists the missing environments owns the refresh action;
// the read state (in flight / last read / failed but retryable) is shown next
// to the cards rather than replacing them.

const ONBOARDING = {
  provider: "chatgpt",
  model: "gpt-test",
  base_url: "",
  has_api_key: true,
  complete: false,
  platform: "linux",
  native_runtime_supported: true,
  outbound: 0,
  contacted: false,
  network: { allow_network: false, egress: "off", contacted: false },
  profiles: [],
  active_id: "",
  protocols: [],
  local_model_catalog: { endpoints: [] },
  environment: {
    schema_version: 1,
    enabled: true,
    ready: false,
    state: "needs_setup",
    reason: "",
    requirements_digest: "",
    missing_environments: ["python"],
    missing_packages: {},
    remediation: null,
  },
} as unknown as OnboardingStatus;

/** Invoke the EnvironmentCard child; its head (and the action slot) is inside it. */
function environmentCard(panel: Node): Node {
  const card = find(panel, (node) => node.type === EnvironmentCard)!;
  const built = (card.type as (props: unknown) => unknown)(card.props) as Node;
  return built;
}

describe("readiness in-place refresh", () => {
  it("puts the refresh action in the readiness card head and reports the last read", () => {
    const onRefresh = vi.fn();
    const panel = ReadinessPanel({
      status: ONBOARDING,
      onRefresh,
      refreshing: false,
      // The plan's timestamp is the client time of the last successful
      // refresh; it is shown as "last read: <time>".
      refreshedAt: Date.UTC(2026, 0, 2, 3, 4, 5),
      refreshError: "",
    }) as Node;
    const head = find(environmentCard(panel), (node) => node.props?.class === "standard-readiness-head")!;
    const button = find(head, (node) => node.type === "button")!;
    expect(text(button)).toBe("onboarding.readiness.refresh");
    expect(button.props?.disabled).toBe(false);
    (button.props!.onClick as () => void)();
    expect(onRefresh).toHaveBeenCalledTimes(1);
    expect(text(panel)).toContain("onboarding.readiness.lastRead");
  });

  it("disables the action and says it is refreshing while a read is in flight", () => {
    const panel = ReadinessPanel({
      status: ONBOARDING,
      onRefresh: vi.fn(),
      refreshing: true,
    }) as Node;
    const button = find(environmentCard(panel), (node) => node.type === "button")!;
    expect(button.props?.disabled).toBe(true);
    expect(text(button)).toBe("onboarding.readiness.refreshing");
    expect(text(panel)).toContain("onboarding.readiness.refreshing");
  });

  it("keeps the old snapshot visible, says it was not refreshed, and stays retryable", () => {
    const onRefresh = vi.fn();
    const panel = ReadinessPanel({
      status: ONBOARDING,
      onRefresh,
      refreshing: false,
      refreshedAt: Date.UTC(2026, 0, 2, 3, 4, 5),
      refreshError: "disconnected",
    }) as Node;
    const button = find(environmentCard(panel), (node) => node.type === "button")!;
    expect(button.props?.disabled).toBe(false);
    const alert = find(panel, (node) => node.props?.role === "alert")!;
    expect(text(alert)).toContain("onboarding.readiness.refreshFailed");
    expect(text(alert)).toContain("disconnected");
    // The previous readiness result is still rendered, not cleared.
    expect(text(environmentCard(panel))).toContain("environment.readiness.needsSetup");
    // The action is the retry.
    (button.props!.onClick as () => void)();
    expect(onRefresh).toHaveBeenCalledTimes(1);
  });

  it("still offers the action when the daemon reports no environment card", () => {
    const status = { ...ONBOARDING, environment: null } as unknown as OnboardingStatus;
    const onRefresh = vi.fn();
    const panel = ReadinessPanel({ status, onRefresh }) as Node;
    const button = find(panel, (node) => node.type === "button")!;
    expect(text(button)).toBe("onboarding.readiness.refresh");
    (button.props!.onClick as () => void)();
    expect(onRefresh).toHaveBeenCalled();
  });

  it("renders the old panel unchanged when no refresh action is supplied", () => {
    const panel = ReadinessPanel({ status: ONBOARDING }) as Node;
    expect(find(panel, (node) => node.type === "button")).toBeNull();
    expect(text(panel)).not.toContain("onboarding.readiness.lastRead");
  });
});
