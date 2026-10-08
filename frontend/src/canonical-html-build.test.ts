import { mkdirSync, mkdtempSync, readFileSync, readdirSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, dirname, join } from "node:path";
import { build, createServer } from "vite";
import { afterEach, expect, it } from "vitest";
import { cspBuildGuard } from "../vite.config";

const scratchRoot = realpathSync(tmpdir());
const fixtures: string[] = [];

function fixture(eol: string, inline = false): string {
  const dir = mkdtempSync(join(scratchRoot, "openai4s-html-build-"));
  fixtures.push(dir);
  const html = [
    "<!doctype html>",
    "<html><body>",
    '<div id="app"></div>',
    '<script type="module" src="./entry.js"></script>',
    ...(inline ? ["<script>window.__buildSmoke = true;</script>"] : []),
    "</body></html>",
    "",
  ].join(eol);
  writeFileSync(join(dir, "index.html"), html);
  writeFileSync(join(dir, "entry.js"), 'console.info("build-output-smoke");\n');
  return dir;
}

async function buildFixture(dir: string): Promise<void> {
  const outputDir = join(dir, "dist");
  await build({
    configFile: false,
    root: dir,
    cacheDir: join(dir, ".vite"),
    logLevel: "silent",
    plugins: [cspBuildGuard(outputDir)],
    build: {
      outDir: outputDir,
      emptyOutDir: true,
      modulePreload: { polyfill: false },
    },
  });
}

afterEach(() => {
  for (const dir of fixtures.splice(0)) {
    if (dirname(realpathSync(dir)) !== scratchRoot || !basename(dir).startsWith("openai4s-html-build-")) {
      throw new Error("refuse to remove a build fixture outside its scratch root");
    }
    rmSync(dir, { recursive: true, force: true });
  }
});

it("builds LF and CRLF module-script input into identical canonical HTML bytes", async () => {
  const lf = fixture("\n");
  const crlf = fixture("\r\n");
  await buildFixture(lf);
  await buildFixture(crlf);
  const expected = readFileSync(join(lf, "dist/index.html"));
  const actual = readFileSync(join(crlf, "dist/index.html"));
  expect(expected.includes(13)).toBe(false);
  expect(actual.includes(13)).toBe(false);
  expect(actual).toEqual(expected);
}, 15_000);

it("still rejects a classic inline script in CRLF build input", async () => {
  await expect(buildFixture(fixture("\r\n", true))).rejects.toThrow("violates CSP script-src 'self'");
}, 15_000);

it("leaves existing output bytes alone when a Vite test server closes", async () => {
  const dir = fixture("\n");
  const outputDir = join(dir, "dist");
  mkdirSync(outputDir);
  const original = Buffer.from("<!doctype html>\r\n<div>recorded output</div>\r\n");
  writeFileSync(join(outputDir, "index.html"), original);
  const server = await createServer({
    configFile: false,
    root: dir,
    cacheDir: join(dir, ".vite"),
    logLevel: "silent",
    plugins: [cspBuildGuard(outputDir)],
    build: { outDir: outputDir, modulePreload: { polyfill: false } },
    server: { middlewareMode: true, watch: null, hmr: false },
  });
  await server.close();
  expect(readFileSync(join(outputDir, "index.html"))).toEqual(original);
  expect(readdirSync(outputDir)).toEqual(["index.html"]);
}, 15_000);
