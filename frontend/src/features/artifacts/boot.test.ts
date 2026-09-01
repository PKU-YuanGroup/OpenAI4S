import { afterEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  mountFilesPanel: vi.fn(),
  consumeArtifactDeepLink: vi.fn(async () => undefined),
  setActiveTab: vi.fn(),
}));

vi.mock("../../components/artifacts/FilesPanel", () => ({
  mountFilesPanel: mocks.mountFilesPanel,
}));
vi.mock("./ui", async (importOriginal) => {
  const original = await importOriginal<typeof import("./ui")>();
  return {
    ...original,
    consumeArtifactDeepLink: mocks.consumeArtifactDeepLink,
    setActiveTab: mocks.setActiveTab,
  };
});

import { bootArtifacts, finishArtifactsBoot } from "./boot";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

describe("Artifact boot ordering", () => {
  afterEach(() => {
    mocks.mountFilesPanel.mockClear();
    mocks.consumeArtifactDeepLink.mockClear();
    mocks.setActiveTab.mockClear();
    vi.unstubAllGlobals();
  });

  it("keeps pre-render boot DOM-free", () => {
    bootArtifacts({});
    expect(mocks.mountFilesPanel).not.toHaveBeenCalled();
    expect(mocks.consumeArtifactDeepLink).not.toHaveBeenCalled();
  });

  it("mounts after Shell exists and waits for initial routing before the deep link", async () => {
    vi.stubGlobal("document", {
      getElementById: () => null,
    });
    const route = deferred<void>();
    const finished = finishArtifactsBoot(route.promise);

    expect(mocks.mountFilesPanel).toHaveBeenCalledTimes(1);
    expect(mocks.consumeArtifactDeepLink).not.toHaveBeenCalled();

    route.resolve();
    await finished;
    expect(mocks.consumeArtifactDeepLink).toHaveBeenCalledTimes(1);
    expect(mocks.mountFilesPanel.mock.invocationCallOrder[0]).toBeLessThan(
      mocks.consumeArtifactDeepLink.mock.invocationCallOrder[0]!,
    );
  });

  it("gives one Files click to the artifact lane", async () => {
    const filesButton = {
      dataset: {} as Record<string, string>,
      onclick: null as (() => void) | null,
    };
    vi.stubGlobal("document", {
      getElementById: (id: string) => (id === "files-btn" ? filesButton : null),
    });

    await finishArtifactsBoot();
    filesButton.onclick?.();

    expect(filesButton.dataset.f17Bound).toBe("1");
    expect(mocks.setActiveTab).toHaveBeenCalledTimes(1);
    expect(mocks.setActiveTab).toHaveBeenCalledWith("files");
  });
});
