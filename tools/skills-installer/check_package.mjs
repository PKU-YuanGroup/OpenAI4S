#!/usr/bin/env node
/**
 * Assert that `npm pack` would publish a usable package.
 *
 * `package.json`'s `files` list is the whole contract between this repository
 * and what a user gets from `npx openai4s-skills`, and it is a list of globs
 * with nothing checking it against the tree. The failure it protects against
 * is not a crash: an entry that stopped matching publishes a CLI that runs,
 * finds no bundled Skills, and silently falls back to downloading 100 MB of
 * source tarball — or, worse, publishes 561 recipes' worth of `__pycache__`.
 */

import { execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");

const REQUIRED = ["tools/skills-installer/cli.mjs", "skills/README.md", "LICENSE"];
const MIN_SKILLS = 500;
const MAX_PACKED_BYTES = 40 * 1024 * 1024;

const raw = execFileSync("npm", ["pack", "--dry-run", "--json"], {
  cwd: REPO_ROOT,
  encoding: "utf8",
  maxBuffer: 64 * 1024 * 1024,
});
const pack = JSON.parse(raw)[0];
const paths = pack.files.map((file) => file.path);
const present = new Set(paths);
const problems = [];

for (const required of REQUIRED) {
  if (!present.has(required)) problems.push(`missing ${required}`);
}

const skills = paths.filter((file) => file.endsWith("/SKILL.md")).length;
if (skills < MIN_SKILLS) problems.push(`only ${skills} Skills (expected >= ${MIN_SKILLS})`);

const junk = paths.filter((file) => file.includes("__pycache__") || /\.py[cod]$/.test(file));
if (junk.length) problems.push(`${junk.length} build artefacts, e.g. ${junk[0]}`);

if (pack.size > MAX_PACKED_BYTES) {
  problems.push(`packed size ${pack.size} exceeds ${MAX_PACKED_BYTES}`);
}

if (problems.length) {
  process.stderr.write(`npm package check failed:\n  ${problems.join("\n  ")}\n`);
  process.exitCode = 1;
} else {
  process.stdout.write(
    `npm package: ${paths.length} files, ${skills} Skills, ` +
      `${(pack.size / 1048576).toFixed(1)} MB packed\n`
  );
}
