import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { MOL_VENDOR_SRC } from "./mol";

const here = dirname(fileURLToPath(import.meta.url));
const artifactsDir = join(here, "../features/artifacts");

const CDN = "https://3Dmol.org/build/3Dmol-min.js";
const STATIC_IMPORT = /(?:from|import)\s*\(?\s*['"][^'"]*3dmol[^'"]*['"]/i;
const REQUIRE_3DMOL = /require\s*\(\s*['"][^'"]*3dmol[^'"]*['"]/i;

function codeLines(src: string): string[] {
  return src.split("\n").filter((line) => {
    const stripped = line.trim();
    return stripped !== "" && !stripped.startsWith("//") && !stripped.startsWith("*");
  });
}

function collectLaneSources(): Array<{ path: string; src: string }> {
  const files: Array<{ path: string; src: string }> = [];
  for (const name of readdirSync(here)) {
    if (!name.endsWith(".ts") || name.endsWith(".test.ts")) continue;
    files.push({ path: join(here, name), src: readFileSync(join(here, name), "utf8") });
  }
  files.push({
    path: join(artifactsDir, "renderers.ts"),
    src: readFileSync(join(artifactsDir, "renderers.ts"), "utf8"),
  });
  return files;
}

describe("3Dmol lazy injection (app.js:9665-9672)", () => {
  it("loads only the vendored script URL", () => {
    expect(MOL_VENDOR_SRC).toBe("/static/vendor/3Dmol-min.js");
  });

  it("keeps the deleted-CDN safety comment next to the script tag", () => {
    const src = readFileSync(join(here, "mol.ts"), "utf8");
    expect(src).toContain(CDN);
    expect(src).toContain('s.src = MOL_VENDOR_SRC');
    expect(src).toContain("Vendored copy only");
    const inject = src.slice(src.indexOf("Vendored copy only"));
    expect(inject).toContain("document.head.appendChild(s)");
    expect(inject).toContain("el(\"script\")");
  });

  it("has no static import of 3Dmol and no live CDN URL in lane sources", () => {
    const files = collectLaneSources();
    const offenders: string[] = [];
    for (const file of files) {
      if (STATIC_IMPORT.test(file.src) || REQUIRE_3DMOL.test(file.src)) {
        offenders.push(`${file.path}: static import/require of 3Dmol`);
      }
      for (const line of codeLines(file.src)) {
        if (line.includes(CDN) || /https?:\/\/3[Dd]mol\.org/.test(line)) {
          offenders.push(`${file.path}: live CDN URL: ${line.trim()}`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });
});
