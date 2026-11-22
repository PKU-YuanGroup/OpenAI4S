/**
 * I18N-1: one word per thing in the Chinese UI. The owner's terms are 产物
 * (never 制品), 单元 (never Cell or 代码单元), 标注 (never 批注), and Notebook
 * stays untranslated (never 笔记本). Both spellings were on screen at once:
 * "Notebook" on the right-hand tab and "笔记本" above the composer, "Cell" and
 * "代码单元" in the same session.
 *
 * Reads the generated zh dictionary and every Chinese string literal in the
 * workbench source, the way a reviewer would.
 */

import { readdirSync, readFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import zh from "./zh";

const SRC = join(dirname(fileURLToPath(import.meta.url)), "..");

/** An old term, or the English word "Cell" inside Chinese copy. */
const OLD_TERM = /制品|代码单元|批注|笔记本|(?<![A-Za-z_.-])[Cc]ells?(?![A-Za-z_])/;
const CJK = /[一-鿿]/;

function sources(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) sources(path, out);
    else if (/\.(ts|tsx)$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name)) out.push(path);
  }
  return out;
}

/** String literals on lines that are not comments. */
function literals(text: string): string[] {
  const out: string[] = [];
  for (const line of text.split("\n")) {
    if (/^\s*(\/\/|\*|\/\*)/.test(line)) continue;
    for (const match of line.matchAll(/"((?:[^"\\]|\\.)*)"|`((?:[^`\\]|\\.)*)`|'((?:[^'\\]|\\.)*)'/g)) {
      out.push(match[1] ?? match[2] ?? match[3] ?? "");
    }
  }
  return out;
}

describe("zh terminology (I18N-1)", () => {
  it("the generated zh dictionary uses 产物 / 单元 / 标注 / Notebook", () => {
    const offending = Object.entries(zh as Record<string, string>)
      .filter(([, value]) => CJK.test(value) && OLD_TERM.test(value))
      .map(([key, value]) => `${key}: ${value}`);
    expect(offending).toEqual([]);
  });

  it("no Chinese copy in the workbench source uses an old term", () => {
    const offending: string[] = [];
    for (const path of sources(SRC)) {
      if (path.includes(`${join("src", "i18n")}`)) continue;
      for (const value of literals(readFileSync(path, "utf8"))) {
        if (CJK.test(value) && OLD_TERM.test(value)) offending.push(`${relative(SRC, path)}: ${value}`);
      }
    }
    expect(offending).toEqual([]);
  });
});
