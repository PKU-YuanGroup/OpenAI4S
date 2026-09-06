import { beforeEach, describe, expect, it, vi } from "vitest";

const hookState: unknown[] = [];
let hookCursor = 0;
const effects: Array<() => void> = [];

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
  useEffect: (fn: () => unknown) => {
    effects.push(() => {
      fn();
    });
  },
}));

const mocks = vi.hoisted(() => ({
  getStatus: vi.fn(),
  runChecks: vi.fn(),
  downloadBundle: vi.fn(),
  custTab: vi.fn(),
}));

vi.mock("../../features/customize/api", () => ({
  apiErrorText: (e: unknown) => String((e as Error).message || e),
  getDiagnosticsStatus: (...args: unknown[]) => mocks.getStatus(...args),
  runDiagnosticsChecks: (...args: unknown[]) => mocks.runChecks(...args),
  downloadDiagnosticsBundle: (...args: unknown[]) => mocks.downloadBundle(...args),
}));

vi.mock("../../features/customize/actions", () => ({
  custTab: (...args: unknown[]) => mocks.custTab(...args),
}));

vi.mock("../../i18n", () => ({
  LANG: "en",
  t: (key: string) => key,
}));

vi.mock("./use-timer-lease", () => ({
  useAlive: () => () => true,
}));

import { DiagnosticsTab } from "./DiagnosticsTab";

type Node = {
  type?: unknown;
  props?: Record<string, unknown> & {
    children?: unknown;
    class?: string;
    onClick?: () => void | Promise<void>;
    disabled?: boolean;
  };
};

function render(): Node {
  hookCursor = 0;
  effects.length = 0;
  return DiagnosticsTab() as Node;
}

function flushEffects() {
  for (const effect of effects.splice(0)) effect();
}

function walk(node: unknown, visit: (current: Node) => void): void {
  if (!node || typeof node !== "object") return;
  const current = node as Node;
  visit(current);
  const children = current.props?.children;
  for (const child of Array.isArray(children) ? children : [children]) walk(child, visit);
}

function buttons(node: unknown): Node[] {
  const found: Node[] = [];
  walk(node, (current) => {
    if (current.type === "button") found.push(current);
  });
  return found;
}

describe("DiagnosticsTab", () => {
  beforeEach(() => {
    hookState.length = 0;
    hookCursor = 0;
    effects.length = 0;
    mocks.getStatus.mockReset();
    mocks.runChecks.mockReset();
    mocks.downloadBundle.mockReset();
    mocks.custTab.mockReset();
    mocks.getStatus.mockResolvedValue({
      security: { kernel_sandbox: "auto", egress: "off" },
      environment: { python: "3.12.0" },
      request_id: "req-diag-1",
    });
  });

  it("loads posture with a single GET and does not run checks or download", async () => {
    render();
    flushEffects();
    await Promise.resolve();
    await Promise.resolve();
    expect(mocks.getStatus).toHaveBeenCalledTimes(1);
    expect(mocks.runChecks).not.toHaveBeenCalled();
    expect(mocks.downloadBundle).not.toHaveBeenCalled();
  });

  it("runs checks and downloads only after an explicit click", async () => {
    mocks.runChecks.mockResolvedValue({
      status: "ok",
      request_id: "req-diag-2",
      checks: [{ name: "model", status: "ok", detail: "configured" }],
    });
    mocks.downloadBundle.mockResolvedValue(undefined);
    const tree = render();
    flushEffects();
    await Promise.resolve();
    const found = buttons(tree);
    const checkBtn = found.find((btn) => String(btn.props?.children).includes("Run checks"));
    const bundleBtn = found.find((btn) =>
      String(btn.props?.children).includes("Download support bundle"),
    );
    expect(checkBtn).toBeTruthy();
    expect(bundleBtn).toBeTruthy();
    await checkBtn?.props?.onClick?.();
    expect(mocks.runChecks).toHaveBeenCalledTimes(1);
    await bundleBtn?.props?.onClick?.();
    expect(mocks.downloadBundle).toHaveBeenCalledTimes(1);
  });

  it("jumps to Models, Network, and Compute remediation", () => {
    const tree = render();
    const found = buttons(tree);
    const models = found.find((btn) => btn.props?.children === "Models");
    const network = found.find((btn) => btn.props?.children === "Network");
    const compute = found.find((btn) => btn.props?.children === "Compute");
    models?.props?.onClick?.();
    network?.props?.onClick?.();
    compute?.props?.onClick?.();
    expect(mocks.custTab.mock.calls.map((call) => call[0])).toEqual([
      "models",
      "network",
      "compute",
    ]);
  });
});
