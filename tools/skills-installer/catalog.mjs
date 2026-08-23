/**
 * Discover Skills in a `skills/` tree and read enough of each one to list it.
 *
 * The discovery rule is the loader's, restated rather than guessed at
 * (`openai4s/skills_loader/loader.py`): a directory is a Skill when it holds a
 * `SKILL.md`, and a directory is a *collection* when it holds a
 * `COLLECTION.json` and its members sit one level below. Nothing here hardcodes
 * a directory name, so a new bundled collection is discovered by its marker the
 * same way the Python loader discovers it.
 *
 * The frontmatter reader deliberately understands one YAML subset -- top-level
 * scalars and folded (`>`) or literal (`|`) blocks -- and no more. A Skill's
 * catalogue entry needs `name`, `description`, and `license`; inventing a full
 * YAML parser to read three keys would be a second, divergent definition of a
 * format the Python loader already owns.
 */

import fs from "node:fs";
import path from "node:path";

export const SKILL_MARKER = "SKILL.md";
export const COLLECTION_MARKER = "COLLECTION.json";

function readDirs(dir) {
  let entries;
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch {
    return [];
  }
  return entries
    .filter((entry) => entry.isDirectory() && !entry.name.startsWith("."))
    .map((entry) => entry.name)
    .sort();
}

/** Parse the leading `---` frontmatter block of a SKILL.md. */
export function parseFrontmatter(text) {
  const lines = text.split(/\r?\n/);
  if (lines[0]?.trim() !== "---") return {};
  const fields = {};
  let key = null;
  let folded = null;
  let buffer = [];

  const flush = () => {
    if (key && folded) {
      fields[key] = buffer.join(folded === ">" ? " " : "\n").trim();
    }
    key = null;
    folded = null;
    buffer = [];
  };

  for (let i = 1; i < lines.length; i += 1) {
    const line = lines[i];
    if (line.trim() === "---") {
      flush();
      break;
    }
    if (folded && (line.startsWith("  ") || line.trim() === "")) {
      buffer.push(line.trim());
      continue;
    }
    flush();
    const match = /^([A-Za-z0-9_-]+):\s*(.*)$/.exec(line);
    if (!match) continue;
    const [, field, rest] = match;
    if (rest === ">" || rest === "|" || rest === ">-" || rest === "|-") {
      key = field;
      folded = rest[0];
      buffer = [];
    } else if (rest !== "") {
      fields[field] = rest.replace(/^["']|["']$/g, "");
    }
  }
  flush();
  return fields;
}

function describe(dir, dirName) {
  const skillPath = path.join(dir, SKILL_MARKER);
  let fields = {};
  try {
    fields = parseFrontmatter(fs.readFileSync(skillPath, "utf8"));
  } catch {
    fields = {};
  }
  const description = (fields.description || "").replace(/\s+/g, " ").trim();
  return {
    name: fields.name || dirName,
    dir: dirName,
    description,
    license: fields.license || null,
    category: fields.category || null,
  };
}

/**
 * Build the catalogue for a checkout root (the directory that CONTAINS
 * `skills/`).
 *
 * Returns `{ skills, collections }`, where a collection carries its own
 * members. Members are addressable by their directory name exactly like a
 * top-level Skill, so `install bio-alignment-alignment-io` works without the
 * caller knowing which collection it came from.
 */
export function buildCatalog(rootDir) {
  const skillsDir = path.join(rootDir, "skills");
  if (!fs.existsSync(skillsDir)) {
    throw new Error(`no skills/ directory under ${rootDir}`);
  }

  const skills = [];
  const collections = [];

  for (const childName of readDirs(skillsDir)) {
    const child = path.join(skillsDir, childName);
    if (fs.existsSync(path.join(child, COLLECTION_MARKER))) {
      let marker = {};
      try {
        marker = JSON.parse(fs.readFileSync(path.join(child, COLLECTION_MARKER), "utf8"));
      } catch {
        marker = {};
      }
      const members = [];
      for (const memberName of readDirs(child)) {
        const member = path.join(child, memberName);
        if (fs.existsSync(path.join(member, SKILL_MARKER))) {
          members.push({
            ...describe(member, memberName),
            collection: marker.id || childName,
            relPath: path.posix.join("skills", childName, memberName),
          });
        }
      }
      collections.push({
        id: marker.id || childName,
        dir: childName,
        relPath: path.posix.join("skills", childName),
        members,
      });
      continue;
    }
    if (fs.existsSync(path.join(child, SKILL_MARKER))) {
      skills.push({
        ...describe(child, childName),
        collection: null,
        relPath: path.posix.join("skills", childName),
      });
    }
  }

  return { skills, collections };
}

/** Every installable entry, curated Skills first, then collection members. */
export function flatten(catalog) {
  return [...catalog.skills, ...catalog.collections.flatMap((c) => c.members)];
}

/** Resolve requested names against the catalogue, reporting what is unknown. */
export function select(catalog, names) {
  const all = flatten(catalog);
  // Directory names are unique; frontmatter names are not guaranteed to be,
  // and the two namespaces overlap (a bioskills member directory
  // `bio-alignment-alignment-io` declares `name: bio-alignment-io`). Names
  // first, directories second, so a directory name always resolves to its own
  // directory rather than to whichever entry happened to claim it as a name.
  const byKey = new Map();
  for (const entry of all) byKey.set(entry.name, entry);
  for (const entry of all) byKey.set(entry.dir, entry);
  const chosen = [];
  const unknown = [];
  const seen = new Set();
  for (const requested of names) {
    const entry = byKey.get(requested);
    if (!entry) {
      unknown.push(requested);
      continue;
    }
    if (seen.has(entry.relPath)) continue;
    seen.add(entry.relPath);
    chosen.push(entry);
  }
  return { chosen, unknown };
}
