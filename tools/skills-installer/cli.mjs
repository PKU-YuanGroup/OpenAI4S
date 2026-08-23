#!/usr/bin/env node
/**
 * `openai4s-skills` -- install OpenAI4S's bundled Skills onto this machine.
 *
 *   npx openai4s-skills list
 *   npx openai4s-skills install --all
 *   npx openai4s-skills install alphafold2 boltz --target claude
 *   npx github:PKU-YuanGroup/OpenAI4S install --all
 *
 * A Skill here is a recipe -- prose plus code plus the operational knowledge
 * needed to run it -- so "installing" one is copying a directory. The work this
 * command does that a `cp -r` does not: it finds the tree (locally or from
 * github.com), refuses archive members that would write outside the target,
 * records what it wrote so uninstall is exact, and declines to overwrite a
 * Skill the user has edited.
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { buildCatalog, flatten, select } from "./catalog.mjs";
import {
  MANIFEST_NAME,
  TARGETS,
  defaultTarget,
  installSkills,
  readManifest,
  resolveTargets,
  uninstallSkills,
} from "./install.mjs";
import { DEFAULT_REF, DEFAULT_REPO, resolveSource } from "./source.mjs";

const MODULE_DIR = path.dirname(fileURLToPath(import.meta.url));
const MIN_NODE_MAJOR = 18;

const USAGE = `openai4s-skills — install the OpenAI4S Skill library locally

USAGE
  npx openai4s-skills <command> [options]

COMMANDS
  list                    List available Skills (curated by default)
  install [name...]       Install named Skills, or --all
  uninstall [name...]     Remove Skills this command installed, or --all
  installed               Show what is installed in a target
  help                    Show this message

SELECTION
  --all                   Every curated Skill (add --with-collections for the rest)
  --collection <id>       Every member of one collection, e.g. bioskills
  --with-collections      Let --all include collection members too

TARGET
  --target <name>         claude | claude-project | openai4s   (repeatable, comma-separated)
  --dir <path>            An explicit directory instead of a named target

SOURCE
  --repo <owner/name>     Default ${DEFAULT_REPO}
  --ref <ref>             Branch, tag, or commit SHA. Default ${DEFAULT_REF}
  --offline               Refuse to download; use a local checkout only
  --remote                Download even when a local checkout is available
                          (implied by --repo / --ref)
  --refresh               Re-download even if the cache has this ref

OTHER
  --force                 Overwrite / remove even when locally modified
  --dry-run               Report what would happen and write nothing
  --json                  Machine-readable output (list, installed)
  --quiet                 Only errors and results

The default target is chosen by detection, not preference: ${defaultTarget()} on
this machine. The resolved absolute path is always printed before any write.
`;

function parseArgs(argv) {
  const options = {
    command: null,
    names: [],
    all: false,
    collection: null,
    withCollections: false,
    targets: [],
    dir: null,
    repo: DEFAULT_REPO,
    ref: DEFAULT_REF,
    offline: false,
    remote: false,
    refresh: false,
    force: false,
    dryRun: false,
    json: false,
    quiet: false,
  };
  const takesValue = new Set(["--collection", "--target", "--dir", "--repo", "--ref"]);

  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    const next = () => {
      const value = argv[i + 1];
      if (value === undefined || value.startsWith("--")) {
        throw new Error(`${arg} needs a value`);
      }
      i += 1;
      return value;
    };
    if (takesValue.has(arg)) {
      const value = next();
      if (arg === "--collection") options.collection = value;
      else if (arg === "--target") options.targets.push(...value.split(",").filter(Boolean));
      else if (arg === "--dir") options.dir = value;
      else if (arg === "--repo") {
        options.repo = value;
        // An explicitly named source must not be silently answered by whatever
        // checkout this file happens to sit in.
        options.remote = true;
      } else if (arg === "--ref") {
        options.ref = value;
        options.remote = true;
      }
      continue;
    }
    switch (arg) {
      case "--all":
        options.all = true;
        break;
      case "--with-collections":
        options.withCollections = true;
        break;
      case "--offline":
        options.offline = true;
        options.remote = false;
        break;
      case "--remote":
        options.remote = true;
        break;
      case "--refresh":
        options.refresh = true;
        break;
      case "--force":
        options.force = true;
        break;
      case "--dry-run":
        options.dryRun = true;
        break;
      case "--json":
        options.json = true;
        break;
      case "--quiet":
      case "-q":
        options.quiet = true;
        break;
      case "-h":
      case "--help":
        options.command = "help";
        break;
      default:
        if (arg.startsWith("-")) throw new Error(`unknown option ${arg}`);
        if (options.command === null) options.command = arg;
        else options.names.push(arg);
    }
  }
  return options;
}

function chooseEntries(catalog, options) {
  if (options.collection) {
    const collection = catalog.collections.find((c) => c.id === options.collection);
    if (!collection) {
      const known = catalog.collections.map((c) => c.id).join(", ") || "none";
      throw new Error(`unknown collection ${options.collection}; available: ${known}`);
    }
    return collection.members;
  }
  if (options.all) {
    return options.withCollections ? flatten(catalog) : catalog.skills;
  }
  if (!options.names.length) {
    throw new Error("name at least one Skill, or pass --all / --collection <id>");
  }
  const { chosen, unknown } = select(catalog, options.names);
  if (unknown.length) {
    throw new Error(`unknown Skill(s): ${unknown.join(", ")}`);
  }
  return chosen;
}

async function commandList(options, out) {
  const { rootDir, provenance } = await resolveSource({
    repo: options.repo,
    ref: options.ref,
    offline: options.offline,
    preferLocal: !options.remote,
    refresh: options.refresh,
    moduleDir: MODULE_DIR,
    log: options.quiet ? () => {} : (message) => out(`… ${message}`),
  });
  const catalog = buildCatalog(rootDir);

  if (options.json) {
    process.stdout.write(
      `${JSON.stringify({ source: provenance, ...catalog }, null, 2)}\n`
    );
    return 0;
  }

  const entries = options.collection || options.withCollections
    ? chooseEntries(catalog, { ...options, all: !options.collection })
    : catalog.skills;

  for (const entry of entries) {
    const summary = entry.description ? entry.description.slice(0, 96) : "";
    out(`  ${entry.dir.padEnd(34)} ${summary}`);
  }
  out("");
  out(`${catalog.skills.length} curated Skills.`);
  for (const collection of catalog.collections) {
    out(
      `Collection ${collection.id}: ${collection.members.length} members ` +
        `(install with --collection ${collection.id})`
    );
  }
  return 0;
}

async function commandInstall(options, out) {
  const targets = resolveTargets(options);
  const { rootDir, provenance } = await resolveSource({
    repo: options.repo,
    ref: options.ref,
    offline: options.offline,
    preferLocal: !options.remote,
    refresh: options.refresh,
    moduleDir: MODULE_DIR,
    log: options.quiet ? () => {} : (message) => out(`… ${message}`),
  });
  const catalog = buildCatalog(rootDir);
  const entries = chooseEntries(catalog, options);

  let failures = 0;
  for (const target of targets) {
    out(`${options.dryRun ? "dry run → " : ""}${entries.length} Skill(s) → ${target.path}`);
    const results = installSkills({
      sourceRoot: rootDir,
      targetDir: target.path,
      entries,
      provenance,
      force: options.force,
      dryRun: options.dryRun,
    });
    const tally = new Map();
    for (const result of results) {
      tally.set(result.action, (tally.get(result.action) || 0) + 1);
      if (result.action === "skipped" || result.action === "failed") {
        failures += result.action === "failed" ? 1 : 0;
        out(`  ${result.action}: ${result.entry.dir} — ${result.reason}`);
      }
    }
    out(`  ${[...tally].map(([action, count]) => `${action} ${count}`).join(", ")}`);
    if (!options.dryRun) out(`  manifest: ${path.join(target.path, MANIFEST_NAME)}`);
  }
  return failures ? 1 : 0;
}

async function commandUninstall(options, out) {
  const targets = resolveTargets(options);
  let failures = 0;
  for (const target of targets) {
    const manifest = readManifest(target.path);
    const names = options.all ? Object.keys(manifest.installs) : options.names;
    if (!names.length) {
      out(`nothing recorded in ${target.path}`);
      continue;
    }
    out(`${options.dryRun ? "dry run → " : ""}removing ${names.length} Skill(s) from ${target.path}`);
    for (const result of uninstallSkills({
      targetDir: target.path,
      names,
      force: options.force,
      dryRun: options.dryRun,
    })) {
      if (result.action !== "removed" && result.action !== "would-remove") {
        failures += 1;
        out(`  ${result.action}: ${result.name} — ${result.reason}`);
      } else {
        out(`  ${result.action}: ${result.name}`);
      }
    }
  }
  return failures ? 1 : 0;
}

function commandInstalled(options, out) {
  const targets = resolveTargets(options);
  const report = targets.map((target) => {
    const manifest = readManifest(target.path);
    return {
      target: target.name,
      path: target.path,
      exists: fs.existsSync(target.path),
      installs: Object.entries(manifest.installs).map(([dir, record]) => ({
        dir,
        name: record.name,
        collection: record.collection,
        installed_at: record.installed_at,
        files: Object.keys(record.files || {}).length,
        source: record.source,
      })),
    };
  });
  if (options.json) {
    process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
    return 0;
  }
  for (const entry of report) {
    out(`${entry.path}${entry.exists ? "" : "  (does not exist)"}`);
    if (!entry.installs.length) out("  nothing installed by openai4s-skills");
    for (const install of entry.installs) {
      out(
        `  ${install.dir.padEnd(34)} ${String(install.files).padStart(4)} files  ` +
          `${install.installed_at || "?"}`
      );
    }
  }
  return 0;
}

async function main(argv) {
  const major = Number(process.versions.node.split(".")[0]);
  if (!Number.isFinite(major) || major < MIN_NODE_MAJOR) {
    process.stderr.write(
      `openai4s-skills needs Node ${MIN_NODE_MAJOR}+ (found ${process.versions.node})\n`
    );
    return 1;
  }

  let options;
  try {
    options = parseArgs(argv);
  } catch (error) {
    process.stderr.write(`openai4s-skills: ${error.message}\n\n${USAGE}`);
    return 2;
  }

  const out = options.quiet ? () => {} : (line) => process.stdout.write(`${line}\n`);

  try {
    switch (options.command) {
      case "list":
        return await commandList(options, out);
      case "install":
        return await commandInstall(options, out);
      case "uninstall":
        return await commandUninstall(options, out);
      case "installed":
        return commandInstalled(options, out);
      case null:
      case "help":
        process.stdout.write(USAGE);
        return 0;
      default:
        process.stderr.write(`openai4s-skills: unknown command ${options.command}\n\n${USAGE}`);
        return 2;
    }
  } catch (error) {
    process.stderr.write(`openai4s-skills: ${error.message}\n`);
    return 1;
  }
}

process.exitCode = await main(process.argv.slice(2));
