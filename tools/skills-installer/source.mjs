/**
 * Resolve the Skill tree this command installs from.
 *
 * Two sources, in this order:
 *
 *   1. A checkout that already contains `skills/`. `npx github:PKU-YuanGroup/OpenAI4S`
 *      clones the repository before running this file, so that case needs no
 *      network at all and the installed bytes are the ones npm just fetched.
 *   2. The published source tarball at codeload.github.com, extracted into a
 *      cache directory. This is the path `npx openai4s-skills` takes, because
 *      the npm package deliberately does not carry 24 MB of Skills.
 *
 * What the manifest records about a download is what was actually verified:
 * the ref asked for, the URL, and the SHA-256 of the bytes received. It does
 * not record a commit SHA, because resolving a branch to a commit would be a
 * second request whose answer this code never checks against the tarball it
 * unpacked. Pass `--ref <commit-sha>` for an install that is reproducible by
 * construction.
 */

import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { gunzip, readTar, safeRelativePath } from "./targz.mjs";

export const DEFAULT_REPO = "PKU-YuanGroup/OpenAI4S";
export const DEFAULT_REF = "main";

const MAX_TARBALL_BYTES = 256 * 1024 * 1024;

/**
 * Candidate tarball URLs for a ref, most likely first.
 *
 * A ref is a commit SHA, a fully qualified `refs/...`, a branch, or a tag, and
 * codeload needs to be told which. Guessing once and reporting a 404 as "no
 * such repository" would be wrong for every tag; trying the branch form and
 * then the tag form answers the question the user actually asked.
 */
export function tarballUrls(repo, ref) {
  const base = `https://codeload.github.com/${repo}/tar.gz`;
  if (/^[0-9a-f]{7,40}$/i.test(ref) || ref.startsWith("refs/")) return [`${base}/${ref}`];
  return [`${base}/refs/heads/${ref}`, `${base}/refs/tags/${ref}`];
}

/** Walk up from this file looking for a checkout that has `skills/`. */
export function findLocalCheckout(startDir) {
  let dir = startDir;
  for (let depth = 0; depth < 8; depth += 1) {
    if (fs.existsSync(path.join(dir, "skills", "README.md"))) return dir;
    const parent = path.dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  return null;
}

export function cacheRoot() {
  const base =
    process.env.OPENAI4S_SKILLS_CACHE ||
    path.join(os.homedir(), ".cache", "openai4s-skills");
  return base;
}

async function download(url, { onProgress } = {}) {
  const response = await fetch(url, {
    redirect: "follow",
    headers: { "user-agent": "openai4s-skills (+https://github.com/PKU-YuanGroup/OpenAI4S)" },
  });
  if (!response.ok) {
    throw new Error(`${url} -> HTTP ${response.status} ${response.statusText}`);
  }
  const chunks = [];
  let received = 0;
  for await (const chunk of response.body) {
    received += chunk.length;
    if (received > MAX_TARBALL_BYTES) {
      throw new Error(
        `refusing a source tarball over ${Math.round(MAX_TARBALL_BYTES / 1048576)} MB`
      );
    }
    chunks.push(chunk);
    if (onProgress) onProgress(received);
  }
  return Buffer.concat(chunks);
}

/**
 * Extract only `skills/**` out of the repository tarball.
 *
 * Everything else in the archive is skipped without being written, so a source
 * tree the installer has no business unpacking never touches the disk. Entry
 * types other than file and directory abort the extraction: an installer that
 * silently skipped a symlink would report a complete tree it had not produced.
 */
function extractSkills(tarBuffer, destDir) {
  let files = 0;
  for (const entry of readTar(tarBuffer)) {
    const rel = safeRelativePath(entry.path, 1);
    if (rel === null) continue;
    if (rel !== "skills" && !rel.startsWith("skills/")) continue;
    if (entry.type !== "file" && entry.type !== "dir") {
      throw new Error(`unsupported tar entry type ${JSON.stringify(entry.type)} for ${rel}`);
    }
    const target = path.join(destDir, rel);
    if (entry.type === "dir") {
      fs.mkdirSync(target, { recursive: true });
      continue;
    }
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.writeFileSync(target, entry.data, { mode: entry.mode & 0o111 ? 0o755 : 0o644 });
    files += 1;
  }
  if (files === 0) throw new Error("the source tarball contained no skills/ files");
  return files;
}

/**
 * Return `{ rootDir, provenance }` for the Skill tree to install from.
 *
 * `rootDir` is the directory that CONTAINS `skills/`, which is what
 * `buildCatalog` expects.
 */
export async function resolveSource({
  repo = DEFAULT_REPO,
  ref = DEFAULT_REF,
  preferLocal = true,
  offline = false,
  refresh = false,
  log = () => {},
  moduleDir,
} = {}) {
  if (preferLocal) {
    const checkout = findLocalCheckout(moduleDir || process.cwd());
    if (checkout) {
      return {
        rootDir: checkout,
        provenance: { kind: "checkout", path: checkout, repo, ref: null, tarball_sha256: null },
      };
    }
  }
  if (offline) {
    throw new Error(
      "--offline was requested but no local checkout containing skills/ was found"
    );
  }

  const candidates = tarballUrls(repo, ref);
  const slug = `${repo.replace(/[^A-Za-z0-9]+/g, "-")}-${ref.replace(/[^A-Za-z0-9]+/g, "-")}`;
  const dest = path.join(cacheRoot(), slug);
  const stamp = path.join(dest, ".source.json");

  if (!refresh && fs.existsSync(stamp)) {
    try {
      const cached = JSON.parse(fs.readFileSync(stamp, "utf8"));
      log(`using cached source ${dest}`);
      return { rootDir: dest, provenance: cached };
    } catch {
      // a corrupt stamp means re-download, not a crash
    }
  }

  let tarball = null;
  let url = null;
  const attempts = [];
  for (const candidate of candidates) {
    log(`downloading ${candidate}`);
    try {
      tarball = await download(candidate);
      url = candidate;
      break;
    } catch (error) {
      attempts.push(`${candidate}: ${error.message}`);
    }
  }
  if (tarball === null) {
    throw new Error(`could not fetch ${repo}@${ref}\n  ${attempts.join("\n  ")}`);
  }
  const digest = crypto.createHash("sha256").update(tarball).digest("hex");
  fs.rmSync(dest, { recursive: true, force: true });
  fs.mkdirSync(dest, { recursive: true });
  const files = extractSkills(gunzip(tarball), dest);
  const provenance = {
    kind: "tarball",
    repo,
    ref,
    url,
    tarball_sha256: digest,
    tarball_bytes: tarball.length,
    skill_files: files,
    downloaded_at: new Date().toISOString(),
  };
  fs.writeFileSync(stamp, `${JSON.stringify(provenance, null, 2)}\n`);
  log(`extracted ${files} files to ${dest}`);
  return { rootDir: dest, provenance };
}
