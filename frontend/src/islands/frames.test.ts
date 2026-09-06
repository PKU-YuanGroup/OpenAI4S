import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import {
  ARTIFACT_IFRAME_SANDBOX,
  KETCHER_ALLOW,
  KETCHER_PATH,
  applyArtifactIframeSandbox,
  applyKetcherFrame,
  htmlPreviewSrc,
  ketcherFrameSrc,
} from "./frames";

const here = dirname(fileURLToPath(import.meta.url));

class FakeFrame {
  src = "";
  attrs = new Map<string, string>();
  setAttribute(name: string, value: string): void {
    this.attrs.set(name, value);
  }
  getAttribute(name: string): string | null {
    return this.attrs.has(name) ? (this.attrs.get(name) as string) : null;
  }
  removeAttribute(name: string): void {
    this.attrs.delete(name);
  }
}

describe("artifact iframe sandbox (app.js:8663-8664)", () => {
  it("uses the empty sandbox token (no allow-scripts / allow-forms)", () => {
    expect(ARTIFACT_IFRAME_SANDBOX).toBe("");
  });

  it("stamps sandbox=\"\" on html-preview", () => {
    const frame = new FakeFrame();
    applyArtifactIframeSandbox(frame, "html-preview");
    expect(frame.getAttribute("sandbox")).toBe("");
    expect(frame.getAttribute("sandbox")).not.toContain("allow-scripts");
    expect(frame.getAttribute("sandbox")).not.toContain("allow-forms");
  });

  it("stamps sandbox=\"\" on PDF (audit hardening)", () => {
    const frame = new FakeFrame();
    applyArtifactIframeSandbox(frame, "pdf");
    expect(frame.getAttribute("sandbox")).toBe("");
    expect(frame.getAttribute("sandbox")).not.toContain("allow-scripts");
    expect(frame.getAttribute("sandbox")).not.toContain("allow-forms");
  });

  it("html-preview src is sandboxOrigin + /preview/{id}", () => {
    expect(htmlPreviewSrc("https://sb.example", "art-1")).toBe("https://sb.example/preview/art-1");
    expect(htmlPreviewSrc("", "a b")).toBe("/preview/a%20b");
  });

  it("F-17 pdf glue and html-preview glue both call applyArtifactIframeSandbox", () => {
    const src = readFileSync(join(here, "../features/artifacts/renderers.ts"), "utf8");
    expect(src).toContain('applyArtifactIframeSandbox(frame, "pdf")');
    expect(src).toContain('applyArtifactIframeSandbox(frame, "html-preview")');
    const pdfFn = src.slice(src.indexOf("function renderPdfGlue"), src.indexOf("function renderHtmlPreviewGlue"));
    expect(pdfFn).toContain("applyArtifactIframeSandbox");
    expect(pdfFn).not.toContain("allow-scripts");
  });
});

describe("Ketcher iframe (app.js:10834, embeddable headers)", () => {
  it("points at /ketcher, never a sandboxed origin path", () => {
    expect(KETCHER_PATH).toBe("/ketcher");
    expect(ketcherFrameSrc("", null)).toBe("/ketcher");
    expect(ketcherFrameSrc("https://sb.example", "mol-1")).toBe(
      "https://sb.example/ketcher?artifact_id=mol-1",
    );
  });

  it("sets clipboard allow and does not set sandbox", () => {
    const frame = new FakeFrame();
    frame.setAttribute("sandbox", "stale");
    applyKetcherFrame(frame, "", "a1");
    expect(frame.src).toBe("/ketcher?artifact_id=a1");
    expect(frame.getAttribute("allow")).toBe(KETCHER_ALLOW);
    expect(frame.getAttribute("sandbox")).toBeNull();
  });
});
