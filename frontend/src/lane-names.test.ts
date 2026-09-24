/**
 * Every late-bound window name the workbench calls is assigned somewhere.
 *
 * `callLane`, `callWindow`, `hostFn`, `laneCall` and `runIsland` return
 * undefined for a name nobody assigned instead of throwing, so a missing
 * name is a button that silently does nothing. Several shipped that way
 * ("Save as skill", "Run location", the load-earlier review badges, the
 * Action Timeline teardown). This test reads the source the way a reviewer
 * would: collect every name passed to one of the lookups, then require an
 * assignment of that name -- a `target.name =` line or an entry in an
 * installer table -- outside import and export lists. A name whose only
 * caller handles its absence with a local fallback goes in FALLBACK, with
 * that reason.
 */

import { readdirSync, readFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const SRC = dirname(fileURLToPath(import.meta.url));

/** Names whose callers carry a working local fallback when the name is absent. */
const FALLBACK: Record<string, string> = {};

function sources(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) sources(path, out);
    else if (/\.(ts|tsx)$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name)) out.push(path);
  }
  return out;
}

/** Drop comments and import/export-from lists, which name functions without assigning them. */
function strip(text: string): string {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(^|[^:"'`])\/\/[^\n]*/g, "$1")
    .replace(/(^|\n)\s*(import|export)\s*(type\s*)?\{[^}]*\}\s*from\s*["'][^"']+["'];?/g, "$1");
}

const files = sources(SRC).map((path) => ({ path, text: strip(readFileSync(path, "utf8")) }));

function calledNames(): Map<string, string[]> {
  const called = new Map<string, string[]>();
  const lookup = /\b(?:callLane|callWindow|hostFn|laneCall|runIsland)\(\s*["'`](\w+)["'`]/g;
  for (const { path, text } of files) {
    for (const match of text.matchAll(lookup)) {
      const name = match[1]!;
      const sites = called.get(name) || [];
      sites.push(relative(SRC, path));
      called.set(name, sites);
    }
  }
  return called;
}

function assigned(name: string): boolean {
  const assignment = new RegExp(
    `(\\.${name}\\s*=(?!=))|(\\[["']${name}["']\\]\\s*=(?!=))|(^\\s*${name}\\s*[:,]\\s*)|(^\\s*${name}\\s*$)`,
    "m",
  );
  return files.some(({ path, text }) => !path.endsWith("window-exports.ts") && assignment.test(text));
}

describe("late-bound window names", () => {
  it("assigns every name that callLane / callWindow / hostFn looks up", () => {
    const missing = [...calledNames().entries()]
      .filter(([name]) => !(name in FALLBACK) && !assigned(name))
      .map(([name, sites]) => `${name} <- ${sites.join(", ")}`);
    expect(missing).toEqual([]);
  });

  it("keeps the fallback list honest: each entry is still called and still unassigned", () => {
    const called = calledNames();
    for (const name of Object.keys(FALLBACK)) {
      expect(called.has(name), `${name} is no longer looked up; drop it from FALLBACK`).toBe(true);
      expect(assigned(name), `${name} is assigned now; drop it from FALLBACK`).toBe(false);
    }
  });
});
