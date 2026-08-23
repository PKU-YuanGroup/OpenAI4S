#!/usr/bin/env node
/**
 * Self-test for the Skill installer. Run it with `node tools/skills-installer/selftest.mjs`.
 *
 * It is a script rather than a pytest module because everything it exercises is
 * JavaScript, and the Python suite's value is that it can be trusted to run
 * with nothing installed. What it must cover is the part of this command that
 * can hurt someone: extraction of a remote archive, and overwriting or deleting
 * files in a directory the user owns.
 */

import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import zlib from "node:zlib";

import { buildCatalog, flatten, parseFrontmatter, select } from "./catalog.mjs";
import {
  inspectExisting,
  installSkills,
  readManifest,
  uninstallSkills,
} from "./install.mjs";
import { readTar, safeRelativePath } from "./targz.mjs";

const MODULE_DIR = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(MODULE_DIR, "..", "..");

let passed = 0;
const failures = [];

function test(name, body) {
  try {
    body();
    passed += 1;
    process.stdout.write(`  ok   ${name}\n`);
  } catch (error) {
    failures.push({ name, error });
    process.stdout.write(`  FAIL ${name}\n         ${error.message}\n`);
  }
}

// --- a minimal tar writer, so the reader is tested against bytes ----------

function tarHeader({ name, size, typeflag = "0", mode = 0o644 }) {
  const header = Buffer.alloc(512);
  header.write(name, 0, 100, "utf8");
  header.write(mode.toString(8).padStart(7, "0") + "\0", 100, 8, "ascii");
  header.write("0000000\0", 108, 8, "ascii");
  header.write("0000000\0", 116, 8, "ascii");
  header.write(size.toString(8).padStart(11, "0") + "\0", 124, 12, "ascii");
  header.write("00000000000\0", 136, 12, "ascii");
  header.write("        ", 148, 8, "ascii"); // checksum placeholder
  header.write(typeflag, 156, 1, "ascii");
  header.write("ustar\0", 257, 6, "ascii");
  header.write("00", 263, 2, "ascii");
  let sum = 0;
  for (const byte of header) sum += byte;
  header.write(sum.toString(8).padStart(6, "0") + "\0 ", 148, 8, "ascii");
  return header;
}

function tarOf(entries) {
  const blocks = [];
  for (const entry of entries) {
    const body = Buffer.from(entry.body || "", "utf8");
    blocks.push(tarHeader({ name: entry.name, size: body.length, typeflag: entry.typeflag }));
    blocks.push(body);
    const pad = (512 - (body.length % 512)) % 512;
    if (pad) blocks.push(Buffer.alloc(pad));
  }
  blocks.push(Buffer.alloc(1024));
  return Buffer.concat(blocks);
}

// --- tar reader -----------------------------------------------------------

test("reads a ustar archive", () => {
  const buf = tarOf([
    { name: "root/skills/demo/SKILL.md", body: "---\nname: demo\n---\nbody\n" },
    { name: "root/skills/demo/", typeflag: "5" },
  ]);
  const entries = [...readTar(buf)];
  assert.equal(entries.length, 2);
  assert.equal(entries[0].type, "file");
  assert.equal(entries[0].data.toString("utf8").includes("name: demo"), true);
  assert.equal(entries[1].type, "dir");
});

test("reads a pax long path", () => {
  const long = `root/skills/${"d".repeat(180)}/SKILL.md`;
  const record = `path=${long}\n`;
  const payload = `${String(record.length + String(record.length + 3).length + 1).padStart(1)} ${record}`;
  const buf = tarOf([
    { name: "root/PaxHeader", typeflag: "x", body: payload },
    { name: "root/short", body: "x" },
  ]);
  const entries = [...readTar(buf)];
  assert.equal(entries.length, 1);
  assert.equal(entries[0].path, long);
});

test("survives gzip round-trip", () => {
  const buf = tarOf([{ name: "root/a.txt", body: "hello" }]);
  const back = zlib.gunzipSync(zlib.gzipSync(buf));
  assert.equal([...readTar(back)][0].data.toString(), "hello");
});

test("refuses a corrupt header", () => {
  const buf = tarOf([{ name: "root/a.txt", body: "hello" }]);
  buf[10] = buf[10] ^ 0xff;
  assert.throws(() => [...readTar(buf)], /checksum mismatch/);
});

test("refuses every path that escapes the extraction root", () => {
  for (const hostile of ["../etc/passwd", "/etc/passwd", "a/../../b", "C:/Windows", "a\u0000b"]) {
    assert.throws(() => safeRelativePath(hostile, 1), /tar entry|absolute|drive-qualified/);
  }
  assert.equal(safeRelativePath("OpenAI4S-main/skills/x/SKILL.md", 1), "skills/x/SKILL.md");
  assert.equal(safeRelativePath("OpenAI4S-main", 1), null);
});

test("reports a symlink member as an unsupported type rather than skipping it", () => {
  const buf = tarOf([{ name: "root/link", typeflag: "2", body: "" }]);
  const entries = [...readTar(buf)];
  assert.equal(entries[0].type, "2");
});

// --- frontmatter and catalogue -------------------------------------------

test("parses folded and inline frontmatter", () => {
  const fields = parseFrontmatter(
    ["---", "name: demo", "description: >", "  one", "  two", "license: MIT", "---", "body"].join("\n")
  );
  assert.equal(fields.name, "demo");
  assert.equal(fields.description, "one two");
  assert.equal(fields.license, "MIT");
});

test("discovers the repository's own Skill tree", () => {
  const catalog = buildCatalog(REPO_ROOT);
  assert.ok(catalog.skills.length >= 40, `curated skills: ${catalog.skills.length}`);
  assert.ok(catalog.collections.length >= 1);
  for (const entry of flatten(catalog)) {
    const skillPath = path.join(REPO_ROOT, ...entry.relPath.split("/"), "SKILL.md");
    assert.ok(fs.existsSync(skillPath), `missing ${skillPath}`);
    assert.ok(entry.dir && !entry.dir.includes("/"), `bad dir ${entry.dir}`);
  }
});

test("resolves a Skill by directory name and by frontmatter name", () => {
  const catalog = buildCatalog(REPO_ROOT);
  const { chosen, unknown } = select(catalog, ["alphafold2", "no-such-skill"]);
  assert.equal(chosen.length, 1);
  assert.deepEqual(unknown, ["no-such-skill"]);
});

// --- install / uninstall --------------------------------------------------

function withTempDirs(body) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "openai4s-skills-test-"));
  try {
    body(root);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
}

function fakeSource(root) {
  const dir = path.join(root, "source", "skills", "demo");
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, "SKILL.md"), "---\nname: demo\ndescription: d\n---\nbody\n");
  fs.mkdirSync(path.join(dir, "scripts"));
  fs.writeFileSync(path.join(dir, "scripts", "run.py"), "print('hi')\n");
  return {
    sourceRoot: path.join(root, "source"),
    entry: { name: "demo", dir: "demo", collection: null, relPath: "skills/demo" },
  };
}

test("installs, records a manifest, and reinstalls idempotently", () => {
  withTempDirs((root) => {
    const { sourceRoot, entry } = fakeSource(root);
    const target = path.join(root, "target");
    const provenance = { kind: "test" };

    const first = installSkills({ sourceRoot, targetDir: target, entries: [entry], provenance });
    assert.equal(first[0].action, "installed");
    assert.ok(fs.existsSync(path.join(target, "demo", "scripts", "run.py")));

    const manifest = readManifest(target);
    assert.equal(Object.keys(manifest.installs.demo.files).length, 2);
    assert.equal(inspectExisting(target, "demo", manifest).state, "managed");

    const second = installSkills({ sourceRoot, targetDir: target, entries: [entry], provenance });
    assert.equal(second[0].action, "replaced");
  });
});

test("refuses to overwrite a locally modified Skill without --force", () => {
  withTempDirs((root) => {
    const { sourceRoot, entry } = fakeSource(root);
    const target = path.join(root, "target");
    installSkills({ sourceRoot, targetDir: target, entries: [entry], provenance: {} });
    fs.appendFileSync(path.join(target, "demo", "scripts", "run.py"), "# mine\n");

    const blocked = installSkills({ sourceRoot, targetDir: target, entries: [entry], provenance: {} });
    assert.equal(blocked[0].action, "skipped");
    assert.match(blocked[0].reason, /locally modified/);
    assert.match(fs.readFileSync(path.join(target, "demo", "scripts", "run.py"), "utf8"), /# mine/);

    const forced = installSkills({
      sourceRoot,
      targetDir: target,
      entries: [entry],
      provenance: {},
      force: true,
    });
    assert.equal(forced[0].action, "replaced");
  });
});

test("refuses to overwrite a directory it did not install", () => {
  withTempDirs((root) => {
    const { sourceRoot, entry } = fakeSource(root);
    const target = path.join(root, "target");
    fs.mkdirSync(path.join(target, "demo"), { recursive: true });
    fs.writeFileSync(path.join(target, "demo", "SKILL.md"), "someone else's\n");

    const blocked = installSkills({ sourceRoot, targetDir: target, entries: [entry], provenance: {} });
    assert.equal(blocked[0].action, "skipped");
    assert.equal(fs.readFileSync(path.join(target, "demo", "SKILL.md"), "utf8"), "someone else's\n");
  });
});

test("uninstall removes only what it installed", () => {
  withTempDirs((root) => {
    const { sourceRoot, entry } = fakeSource(root);
    const target = path.join(root, "target");
    installSkills({ sourceRoot, targetDir: target, entries: [entry], provenance: {} });
    fs.writeFileSync(path.join(target, "demo", "notes.md"), "user notes\n");

    const results = uninstallSkills({ targetDir: target, names: ["demo"] });
    assert.equal(results[0].action, "skipped", "an unexpected extra file must block removal");

    fs.rmSync(path.join(target, "demo", "notes.md"));
    const second = uninstallSkills({ targetDir: target, names: ["demo"] });
    assert.equal(second[0].action, "removed");
    assert.equal(fs.existsSync(path.join(target, "demo")), false);
    assert.deepEqual(Object.keys(readManifest(target).installs), []);
  });
});

test("dry run writes nothing", () => {
  withTempDirs((root) => {
    const { sourceRoot, entry } = fakeSource(root);
    const target = path.join(root, "target");
    const results = installSkills({
      sourceRoot,
      targetDir: target,
      entries: [entry],
      provenance: {},
      dryRun: true,
    });
    assert.equal(results[0].action, "would-install");
    assert.equal(fs.existsSync(path.join(target, "demo")), false);
  });
});

test("installs a real Skill from the repository checkout", () => {
  withTempDirs((root) => {
    const catalog = buildCatalog(REPO_ROOT);
    const entry = catalog.skills.find((s) => s.dir === "example_stats") || catalog.skills[0];
    const target = path.join(root, "target");
    const results = installSkills({
      sourceRoot: REPO_ROOT,
      targetDir: target,
      entries: [entry],
      provenance: { kind: "checkout" },
    });
    assert.equal(results[0].action, "installed");
    const installed = path.join(target, entry.dir, "SKILL.md");
    assert.equal(
      crypto.createHash("sha256").update(fs.readFileSync(installed)).digest("hex"),
      crypto
        .createHash("sha256")
        .update(fs.readFileSync(path.join(REPO_ROOT, ...entry.relPath.split("/"), "SKILL.md")))
        .digest("hex")
    );
  });
});

process.stdout.write(`\n${passed} passed, ${failures.length} failed\n`);
process.exitCode = failures.length ? 1 : 0;
