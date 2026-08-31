import { afterEach, describe, expect, it } from "vitest";
import { isReady } from "../compat/stub";
import { contractStub } from "../compat/stub";
import { installIslands } from "./index";

describe("installIslands window names", () => {
  afterEach(() => {
    const g = globalThis as unknown as Record<string, unknown>;
    for (const name of [
      "molecule",
      "renderAnnotatableImage",
      "annotationStatus",
      "annotationIsHeld",
      "openAnnotations",
      "loadAnnotations",
      "renderPins",
      "openPinPop",
      "openKetcher",
      "renderLocatorComments",
    ]) {
      delete g[name];
    }
  });

  it("overwrites F-05 stubs so isReady passes (not typeof === function)", () => {
    const target: Record<string, unknown> = {
      molecule: contractStub("molecule"),
      renderAnnotatableImage: contractStub("renderAnnotatableImage"),
      openKetcher: contractStub("openKetcher"),
      annotationStatus: contractStub("annotationStatus"),
    };
    expect(isReady(target.molecule)).toBe(false);
    installIslands(target);
    expect(isReady(target.molecule)).toBe(true);
    expect(isReady(target.renderAnnotatableImage)).toBe(true);
    expect(isReady(target.openKetcher)).toBe(true);
    expect(isReady(target.annotationStatus)).toBe(true);
    expect(isReady(target.annotationIsHeld)).toBe(true);
    expect(isReady(target.openAnnotations)).toBe(true);
    expect(isReady(target.loadAnnotations)).toBe(true);
    expect(isReady(target.renderPins)).toBe(true);
    expect(isReady(target.openPinPop)).toBe(true);
    expect(isReady(target.renderLocatorComments)).toBe(true);
  });
});
