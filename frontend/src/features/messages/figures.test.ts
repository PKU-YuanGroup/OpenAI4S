import { afterEach, describe, expect, it, vi } from "vitest";
import { MAX_MESSAGE_FIGURES, embedArtifactFigures } from "./figures";

type Img = { className: string; src: string; alt: string; loading: string };

function item() {
  const children: unknown[] = [];
  return {
    children,
    querySelector: (sel: string) =>
      sel === ".msg-fig" ? (children.find((c) => (c as Img).className === "msg-fig") ?? null) : null,
    appendChild: (node: unknown) => {
      children.push(node);
      return node;
    },
  };
}

function link(href: string, text: string, holder = item()) {
  return { getAttribute: (n: string) => (n === "href" ? href : null), textContent: text, closest: () => holder, holder };
}

afterEach(() => vi.unstubAllGlobals());

describe("figures under a finished answer's links (ART-8)", () => {
  it("shows an image Artifact under its link, and only daemon Artifact links", () => {
    vi.stubGlobal("document", { createElement: () => ({ className: "", src: "", alt: "", loading: "" }) });
    const fig = link("/api/v1/artifacts/versions/v-1", "fig1_survival.png");
    const table = link("/api/v1/artifacts/a-2", "cox_results.csv");
    const outside = link("https://example.org/plot.png", "plot.png");
    const added = embedArtifactFigures({ querySelectorAll: () => [fig, table, outside] });
    expect(added).toBe(1);
    const img = fig.holder.children[0] as Img;
    expect(img.className).toBe("msg-fig");
    expect(img.src).toBe("/api/v1/artifacts/versions/v-1");
    expect(img.alt).toBe("fig1_survival.png");
    expect(table.holder.children).toHaveLength(0);
    expect(outside.holder.children).toHaveLength(0);
  });

  it("stops after a few figures and never adds a second one to the same item", () => {
    vi.stubGlobal("document", { createElement: () => ({ className: "", src: "", alt: "", loading: "" }) });
    const links = Array.from({ length: 6 }, (_, i) => link(`/api/v1/artifacts/a-${i}`, `fig${i}.png`));
    expect(embedArtifactFigures({ querySelectorAll: () => links })).toBe(MAX_MESSAGE_FIGURES);
    expect(embedArtifactFigures({ querySelectorAll: () => links.slice(0, 1) })).toBe(0);
  });
});
