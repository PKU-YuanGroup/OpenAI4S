import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./ui", () => ({ presentViewer: vi.fn() }));

import { artifacts } from "../../stores/artifacts";
import { resetStoreFields } from "../../stores/signal-field";
import { bindArtifactLinks, linkedArtifact, openLinkedArtifact, resetArtifactLinkBinding } from "./links";
import { presentViewer } from "./ui";

const REPORT = { id: "a-bc112472a980", filename: "report.md", content_type: "text/markdown" };

/** A click whose target sits inside `selector`-matching markup, or outside it. */
function click(href: string | null, extra: Record<string, unknown> = {}) {
  const anchor = href === null ? null : { getAttribute: (name: string) => (name === "href" ? href : null) };
  return {
    altKey: false,
    button: 0,
    ctrlKey: false,
    defaultPrevented: false,
    metaKey: false,
    shiftKey: false,
    preventDefault: vi.fn(),
    target: { closest: (selector: string) => (selector === ".md a[href]" ? anchor : null) },
    ...extra,
  } as unknown as MouseEvent;
}

beforeEach(() => {
  resetStoreFields();
  resetArtifactLinkBinding();
  vi.mocked(presentViewer).mockClear();
  artifacts.value = [REPORT];
});

describe("artifact links in rendered markdown", () => {
  it("opens the session's Artifact in the Viewer instead of leaving for its raw bytes", () => {
    const event = click("/api/v1/artifacts/a-bc112472a980");
    expect(openLinkedArtifact(event)).toBe(true);
    expect(event.preventDefault).toHaveBeenCalledTimes(1);
    expect(presentViewer).toHaveBeenCalledWith(REPORT);
  });

  it.each([
    ["a modified click", "/api/v1/artifacts/a-bc112472a980", { metaKey: true }],
    ["a ctrl click", "/api/v1/artifacts/a-bc112472a980", { ctrlKey: true }],
    ["a middle click", "/api/v1/artifacts/a-bc112472a980", { button: 1 }],
    ["an Artifact of another session", "/api/v1/artifacts/a-elsewhere", {}],
    ["an exact-version read", "/api/v1/artifacts/versions/v-1", {}],
    ["a cache-busted read", "/api/v1/artifacts/a-bc112472a980?_=3", {}],
    ["an external link", "https://doi.org/10.1056/NEJMoa2105281", {}],
    ["a link outside rendered markdown", null, {}],
  ])("leaves %s to the browser", (_name, href, extra) => {
    const event = click(href, extra);
    expect(openLinkedArtifact(event)).toBe(false);
    expect(event.preventDefault).not.toHaveBeenCalled();
    expect(presentViewer).not.toHaveBeenCalled();
  });

  it("matches the row by artifact_id too, and survives a malformed escape", () => {
    artifacts.value = [{ id: "row-1", artifact_id: "a-1", filename: "fig.png" }];
    expect(linkedArtifact("/api/v1/artifacts/a-1")).toEqual(artifacts.value[0]);
    expect(linkedArtifact("/api/v1/artifacts/%E0%A4%A")).toBeNull();
  });

  it("binds one delegated listener", () => {
    const addEventListener = vi.fn();
    bindArtifactLinks({ addEventListener });
    bindArtifactLinks({ addEventListener });
    expect(addEventListener).toHaveBeenCalledTimes(1);
    const listener = addEventListener.mock.calls[0]![1] as (event: unknown) => void;
    listener(click("/api/v1/artifacts/a-bc112472a980"));
    expect(presentViewer).toHaveBeenCalledWith(REPORT);
  });
});
