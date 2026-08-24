/**
 * A gzip + POSIX-tar reader with no dependencies.
 *
 * The repository's core is stdlib-only on purpose; this CLI keeps the same
 * promise on the Node side, because the alternative -- pulling `tar` and its
 * transitive tree into a command whose whole job is to download and unpack
 * code onto a user's machine -- widens exactly the surface an installer should
 * be narrowing.
 *
 * Extraction is the security boundary here: the archive is remote input, so
 * every member path is validated against the destination root before a byte is
 * written, and every entry type other than "regular file" and "directory" is
 * refused rather than approximated. A symlink inside the archive is the classic
 * way out of an extraction root, and this reader has no code that could follow
 * one.
 */

import zlib from "node:zlib";

const BLOCK = 512;

function cstr(buf, offset, length) {
  const slice = buf.subarray(offset, offset + length);
  const end = slice.indexOf(0);
  return slice.toString("utf8", 0, end === -1 ? slice.length : end);
}

function octal(buf, offset, length) {
  const text = cstr(buf, offset, length).trim();
  if (!text) return 0;
  const value = parseInt(text, 8);
  return Number.isFinite(value) ? value : 0;
}

/**
 * The header checksum is the only integrity signal a bare tar carries. It does
 * not authenticate anything -- it catches a truncated or corrupted transfer,
 * which is the failure this reader would otherwise turn into a confusing
 * "unknown entry type" further down.
 */
function checksumOk(header) {
  const declared = octal(header, 148, 8);
  let unsigned = 0;
  let signed = 0;
  for (let i = 0; i < BLOCK; i += 1) {
    const byte = i >= 148 && i < 156 ? 0x20 : header[i];
    unsigned += byte;
    signed += byte > 127 ? byte - 256 : byte;
  }
  return declared === unsigned || declared === signed;
}

function paxRecords(data) {
  // "%d %s=%s\n", where the leading number is the length of the whole record.
  const records = {};
  let offset = 0;
  while (offset < data.length) {
    const space = data.indexOf(0x20, offset);
    if (space === -1) break;
    const length = parseInt(data.toString("utf8", offset, space), 10);
    if (!Number.isFinite(length) || length <= 0 || offset + length > data.length) break;
    const body = data.toString("utf8", space + 1, offset + length).replace(/\n$/, "");
    const eq = body.indexOf("=");
    if (eq > 0) records[body.slice(0, eq)] = body.slice(eq + 1);
    offset += length;
  }
  return records;
}

/**
 * Yield `{ path, type, mode, data }` for every member of an uncompressed tar.
 *
 * `type` is normalised to "file" or "dir"; anything else is yielded as its raw
 * typeflag so the caller decides whether to refuse the archive or skip the
 * entry. Deciding that here would hide a hardlink farm behind a silent skip.
 */
export function* readTar(buf) {
  let offset = 0;
  let pendingLongName = null;
  let pendingPaxPath = null;

  while (offset + BLOCK <= buf.length) {
    const header = buf.subarray(offset, offset + BLOCK);
    let empty = true;
    for (let i = 0; i < BLOCK; i += 1) {
      if (header[i] !== 0) {
        empty = false;
        break;
      }
    }
    if (empty) return; // two zero blocks end the archive; one is enough to stop
    if (!checksumOk(header)) {
      throw new Error(`corrupt tar header at byte ${offset} (checksum mismatch)`);
    }

    const size = octal(header, 124, 12);
    const typeflag = String.fromCharCode(header[156] || 0x30);
    const mode = octal(header, 100, 8);
    offset += BLOCK;
    const data = buf.subarray(offset, offset + size);
    offset += Math.ceil(size / BLOCK) * BLOCK;

    if (typeflag === "L" || typeflag === "K") {
      // GNU long name / long link name: the next header's name is in `data`.
      if (typeflag === "L") pendingLongName = cstr(data, 0, data.length);
      continue;
    }
    if (typeflag === "x" || typeflag === "X") {
      const record = paxRecords(data);
      if (record.path) pendingPaxPath = record.path;
      continue;
    }
    if (typeflag === "g") continue; // global pax header: not per-entry state

    const name = cstr(header, 0, 100);
    const prefix = cstr(header, 345, 155);
    let path = pendingPaxPath || pendingLongName || (prefix ? `${prefix}/${name}` : name);
    pendingPaxPath = null;
    pendingLongName = null;

    let kind = typeflag;
    if (typeflag === "0" || typeflag === "\0" || typeflag === "7") kind = "file";
    else if (typeflag === "5") kind = "dir";
    if (kind === "file" && path.endsWith("/")) kind = "dir";

    yield { path, type: kind, mode, data };
  }
}

export function gunzip(buf) {
  return zlib.gunzipSync(buf, { maxOutputLength: 512 * 1024 * 1024 });
}

/**
 * Reject a member path that could write outside the extraction root.
 *
 * Returns the sanitised relative POSIX path, or null when the entry must be
 * skipped (the archive's own top-level directory once `strip` has eaten it).
 * Throws when the path is hostile rather than merely uninteresting: a caller
 * that quietly skipped those would extract a partial tree and report success.
 */
export function safeRelativePath(rawPath, strip = 0) {
  if (rawPath.includes("\0")) throw new Error(`tar entry path contains NUL: ${rawPath}`);
  const normalised = rawPath.replace(/\\/g, "/");
  if (normalised.startsWith("/")) throw new Error(`absolute tar entry path: ${rawPath}`);
  if (/^[A-Za-z]:/.test(normalised)) throw new Error(`drive-qualified tar entry path: ${rawPath}`);

  const parts = normalised.split("/").filter((part) => part !== "" && part !== ".");
  for (const part of parts) {
    if (part === "..") throw new Error(`tar entry escapes the archive root: ${rawPath}`);
  }
  if (parts.length <= strip) return null;
  return parts.slice(strip).join("/");
}
