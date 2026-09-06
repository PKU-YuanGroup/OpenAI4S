import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  activateExistingModelProfile: vi.fn(),
  dispatch: vi.fn(),
  setBusy: vi.fn(),
  setStatus: vi.fn(),
  state: {
    surface: "wizard",
    step: "path",
    decided: ["path"],
    providerRequests: 0,
    testClicked: false,
    path: {
      kind: "existing",
      profileId: "profile/selected",
      provider: "chatgpt",
      model: "gpt-test",
      baseUrl: "",
      name: "Selected",
    },
    receipt: null,
    probeDetail: "",
    error: null,
    skipped: false,
    complete: false,
  },
  status: {
    provider: "chatgpt",
    model: "gpt-test",
    base_url: "",
    has_api_key: true,
    complete: false,
    platform: "test",
    native_runtime_supported: true,
    outbound: 0,
    contacted: false,
    network: { allow_network: false, egress: "off", contacted: false },
    profiles: [],
    active_id: "profile/selected",
    protocols: [],
    local_model_catalog: { endpoints: [] },
    environment: null,
  },
}));

vi.mock("preact/hooks", () => ({
  useEffect: vi.fn(),
  useReducer: () => [mocks.state, mocks.dispatch],
  useRef: () => ({ current: true }),
  useState: (initial: unknown) => {
    if (initial === null) return [mocks.status, mocks.setStatus];
    if (initial === false) return [false, mocks.setBusy];
    return [initial, vi.fn()];
  },
}));

vi.mock("./api", async (importOriginal) => {
  const original = await importOriginal<typeof import("./api")>();
  return {
    ...original,
    activateExistingModelProfile: mocks.activateExistingModelProfile,
  };
});

import { WizardHost } from "../../components/onboarding/Wizard";

type TestNode = {
  type?: unknown;
  props?: {
    children?: unknown;
    class?: string;
    onClick?: () => void;
  };
};

function findSolidButton(node: unknown): TestNode | null {
  if (!node || typeof node !== "object") return null;
  const current = node as TestNode;
  if (current.type === "button" && current.props?.class === "solid-btn") return current;
  const children = current.props?.children;
  for (const child of Array.isArray(children) ? children : [children]) {
    const match = findSolidButton(child);
    if (match) return match;
  }
  return null;
}

describe("existing-profile Wizard continuation", () => {
  beforeEach(() => {
    mocks.activateExistingModelProfile.mockReset();
    mocks.dispatch.mockReset();
    mocks.setBusy.mockReset();
    mocks.setStatus.mockReset();
  });

  it("activates the chosen profile before dispatching NEXT", async () => {
    let releaseActivation!: () => void;
    mocks.activateExistingModelProfile.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          releaseActivation = () => resolve(mocks.status);
        }),
    );

    const button = findSolidButton(WizardHost());
    expect(button?.props?.onClick).toBeTypeOf("function");
    button!.props!.onClick!();

    expect(mocks.activateExistingModelProfile).toHaveBeenCalledWith("profile/selected");
    expect(mocks.dispatch).not.toHaveBeenCalledWith({ type: "next" });

    releaseActivation();
    await vi.waitFor(() => expect(mocks.dispatch).toHaveBeenCalledWith({ type: "next" }));
    expect(mocks.activateExistingModelProfile.mock.invocationCallOrder[0]).toBeLessThan(
      mocks.dispatch.mock.invocationCallOrder[0]!,
    );
    expect(mocks.setStatus).toHaveBeenCalledWith(mocks.status);
  });
});
