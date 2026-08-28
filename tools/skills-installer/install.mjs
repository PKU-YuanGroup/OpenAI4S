/**
 * Copy Skills into a target directory, and take them back out again.
 *
 * The manifest (`.openai4s-skills.json`, written beside the installed Skills)
 * is what makes uninstall safe: it records a SHA-256 for every file this
 * command wrote, so removal can tell "a file we installed and nobody touched"
 * from "a file the user edited" from "a directory that was already here". An
 * installer that just `rm -rf`'d a name it recognised would delete a Skill the
 * user had spent an afternoon adapting, and report success.
 *
 * The same hashes make overwrite refuse by default: an install over a modified
 * Skill stops and names the files rather than silently winning.
 */

import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

export const MANIFEST_NAME = ".openai4s-skills.json";
const MANIFEST_VERSION = 1;

/**
 * Where a Skill tree can be installed.
 *
 * `openai4s` mirrors `Config.data_dir / "user-skills"`, including the
 * `OPENAI4S_DATA_DIR` override, so a Skill installed here is the one the daemon
 * loads. Note the precedence the loader applies: a bundled Skill of the same
 * name wins and stays read-only, so installing a *bundled* Skill into this
 * target is a no-op for an OpenAI4S user who already ships every one.
 */
export const TARGETS = {
  openai4s: () =>
    path.join(
      process.env.OPENAI4S_DATA_DIR || path.join(os.homedir(), ".openai4s"),
      "user-skills"
    ),
  claude: () => path.join(os.homedir(), ".claude", "skills"),
  "claude-project": () => path.join(process.cwd(), ".claude", "skills"),
};

export function resolveTargets({ targets, dir }) {
  if (dir) return [{ name: "dir", path: path.resolve(dir) }];
  const names = targets && targets.length ? targets : [defaultTarget()];
  return names.map((name) => {
    const resolve = TARGETS[name];
    if (!resolve) {
      throw new Error(
        `unknown --target ${name}; choose from ${Object.keys(TARGETS).join(", ")} or pass --dir`
      );
    }
    return { name, path: resolve() };
  });
}

/**
 * Pick a target when the user named none.
 *
 * Detection, not preference: an existing `~/.claude` means the machine runs
 * Claude Code and these Skills are useful there, while an OpenAI4S install
 * already carries every bundled Skill and gains nothing from a copy. The
 * resolved absolute path is always printed before anything is written, so a
 * wrong guess is visible rather than surprising.
 */
export function defaultTarget() {
  if (fs.existsSync(path.join(os.homedir(), ".claude"))) return "claude";
  return "openai4s";
}

function sha256(file) {
  return crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex");
}

function walk(dir, base = dir) {
  const out = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...walk(full, base));
    else if (entry.isFile()) out.push(path.relative(base, full).split(path.sep).join("/"));
  }
  return out.sort();
}

export function readManifest(targetDir) {
  const file = path.join(targetDir, MANIFEST_NAME);
  if (!fs.existsSync(file)) return { version: MANIFEST_VERSION, installs: {} };
  try {
    const parsed = JSON.parse(fs.readFileSync(file, "utf8"));
    if (!parsed || typeof parsed !== "object" || typeof parsed.installs !== "object") {
      return { version: MANIFEST_VERSION, installs: {} };
    }
    return { version: parsed.version || MANIFEST_VERSION, installs: parsed.installs };
  } catch {
    return { version: MANIFEST_VERSION, installs: {} };
  }
}

export function writeManifest(targetDir, manifest) {
  fs.mkdirSync(targetDir, { recursive: true });
  fs.writeFileSync(
    path.join(targetDir, MANIFEST_NAME),
    `${JSON.stringify({ ...manifest, version: MANIFEST_VERSION }, null, 2)}\n`
  );
}

/**
 * Classify what already sits at `<targetDir>/<name>`.
 *
 * "modified" is the answer that matters: it is the difference between a copy
 * this command may replace and a Skill the user has changed.
 */
export function inspectExisting(targetDir, name, manifest) {
  const dest = path.join(targetDir, name);
  if (!fs.existsSync(dest)) return { state: "absent", modified: [] };
  const record = manifest.installs[name];
  if (!record) return { state: "foreign", modified: [] };
  const modified = [];
  for (const [rel, digest] of Object.entries(record.files || {})) {
    const file = path.join(dest, rel);
    if (!fs.existsSync(file) || sha256(file) !== digest) modified.push(rel);
  }
  for (const rel of walk(dest)) {
    if (!(rel in (record.files || {}))) modified.push(rel);
  }
  return { state: modified.length ? "modified" : "managed", modified: [...new Set(modified)] };
}

function copyTree(sourceDir, destDir) {
  const files = {};
  for (const rel of walk(sourceDir)) {
    const from = path.join(sourceDir, rel);
    const to = path.join(destDir, rel);
    fs.mkdirSync(path.dirname(to), { recursive: true });
    fs.copyFileSync(from, to);
    files[rel] = sha256(to);
  }
  return files;
}

/**
 * Install `entries` (catalogue rows) from `sourceRoot` into `targetDir`.
 *
 * Returns a per-entry outcome rather than throwing on the first conflict: a
 * user installing 561 recipes wants the twelve that collided named, not the
 * first one plus an aborted run.
 */
export function installSkills({
  sourceRoot,
  targetDir,
  entries,
  provenance,
  force = false,
  dryRun = false,
}) {
  const manifest = readManifest(targetDir);
  const results = [];

  for (const entry of entries) {
    const sourceDir = path.join(sourceRoot, ...entry.relPath.split("/"));
    if (!fs.existsSync(path.join(sourceDir, "SKILL.md"))) {
      results.push({ entry, action: "failed", reason: "source is missing SKILL.md" });
      continue;
    }
    const existing = inspectExisting(targetDir, entry.dir, manifest);
    if ((existing.state === "foreign" || existing.state === "modified") && !force) {
      results.push({
        entry,
        action: "skipped",
        reason:
          existing.state === "foreign"
            ? "a directory of that name is already there and was not installed by this command"
            : `locally modified (${existing.modified.slice(0, 3).join(", ")}${
                existing.modified.length > 3 ? ", …" : ""
              })`,
      });
      continue;
    }
    if (dryRun) {
      results.push({ entry, action: existing.state === "absent" ? "would-install" : "would-replace" });
      continue;
    }
    const dest = path.join(targetDir, entry.dir);
    fs.rmSync(dest, { recursive: true, force: true });
    fs.mkdirSync(dest, { recursive: true });
    const files = copyTree(sourceDir, dest);
    manifest.installs[entry.dir] = {
      name: entry.name,
      collection: entry.collection,
      source: provenance,
      installed_at: new Date().toISOString(),
      files,
    };
    results.push({ entry, action: existing.state === "absent" ? "installed" : "replaced" });
  }

  if (!dryRun) writeManifest(targetDir, manifest);
  return results;
}

function pruneEmpty(dir, stopAt) {
  let current = dir;
  while (current.startsWith(stopAt) && current !== stopAt) {
    let entries;
    try {
      entries = fs.readdirSync(current);
    } catch {
      return;
    }
    if (entries.length) return;
    fs.rmdirSync(current);
    current = path.dirname(current);
  }
}

/** Remove installed Skills, leaving anything this command did not write. */
export function uninstallSkills({ targetDir, names, force = false, dryRun = false }) {
  const manifest = readManifest(targetDir);
  const results = [];

  for (const name of names) {
    const record = manifest.installs[name];
    const dest = path.join(targetDir, name);
    if (!record) {
      results.push({ name, action: "skipped", reason: "not recorded in this target's manifest" });
      continue;
    }
    const existing = inspectExisting(targetDir, name, manifest);
    if (existing.state === "modified" && !force) {
      results.push({
        name,
        action: "skipped",
        reason: `locally modified (${existing.modified.slice(0, 3).join(", ")}); pass --force to remove anyway`,
      });
      continue;
    }
    if (dryRun) {
      results.push({ name, action: "would-remove" });
      continue;
    }
    for (const rel of Object.keys(record.files || {})) {
      const file = path.join(dest, rel);
      fs.rmSync(file, { force: true });
      pruneEmpty(path.dirname(file), dest);
    }
    if (force) fs.rmSync(dest, { recursive: true, force: true });
    else pruneEmpty(dest, targetDir);
    delete manifest.installs[name];
    results.push({ name, action: "removed" });
  }

  if (!dryRun) writeManifest(targetDir, manifest);
  return results;
}
