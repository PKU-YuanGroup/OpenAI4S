/**
 * "Export as Markdown" (TEAM-12): a record a colleague can read outside the app.
 *
 * It was one `## Assistant` heading per progress line, no code, no output,
 * and figure links to `/api/v1/artifacts/…`, which resolve nowhere else. Now
 * one section per turn, each executed cell's source and (bounded) output in
 * the order it ran, image artifacts embedded as data URIs under the cell that
 * wrote them, and in-app links reduced to their text.
 */

import { copyLookup } from "../../i18n/copy";

export const markdownT = copyLookup({
  en: {
    user: "User",
    assistant: "Assistant",
    cell: "Code · cell {0}",
    background: " (background task)",
    output: "Output",
    error: "Error",
    more: "… {0} more characters not shown",
    artifacts: "Artifacts",
    tooLarge: "{0} — not embedded ({1}); it is in the session's artifacts",
  },
  zh: {
    user: "用户",
    assistant: "助手",
    cell: "代码 · 单元 {0}",
    background: "（后台任务）",
    output: "输出",
    error: "错误",
    more: "…… 另有 {0} 个字符未显示",
    artifacts: "产物",
    tooLarge: "{0} — 未内嵌（{1}），见会话产物",
  },
});

export type MarkdownMessage = { role?: string; content?: unknown; created_at?: unknown };
export type MarkdownCell = {
  producing_cell_id?: string;
  cell_index?: number | null;
  kernel_id?: string | null;
  language?: string | null;
  source?: string | null;
  code?: string | null;
  stdout?: string | null;
  stderr?: string | null;
  error?: string | null;
  visibility?: string | null;
  created_at?: unknown;
  files_written?: unknown;
  figures?: unknown;
};
export type MarkdownImage = { filename: string; dataUri?: string; skipped?: string };

export const OUTPUT_CHARS = 3000;

function when(value: unknown): number {
  if (typeof value === "number") return value < 1e12 ? value * 1000 : value;
  if (typeof value === "string" && value) {
    const parsed = /^\d+$/.test(value) ? Number(value) : Date.parse(value);
    return Number.isNaN(parsed) ? 0 : parsed < 1e12 ? parsed * 1000 : parsed;
  }
  return 0;
}

function text(content: unknown): string {
  return Array.isArray(content)
    ? (content as Array<{ text?: string }>).map((block) => block.text || "").join("")
    : String(content || "");
}

/** `[name](/api/…)` and `![name](/api/…)` resolve only inside the app: keep the name. */
export function portableLinks(markdown: string): string {
  return markdown.replace(/!?\[([^\]\n]+)\]\((\/[^)\s]*)\)/g, "$1");
}

function fence(body: string, lang = ""): string {
  const longest = Math.max(2, ...(body.match(/`+/g) || []).map((run) => run.length));
  const ticks = "`".repeat(longest + 1);
  return `${ticks}${lang}\n${body.replace(/\n+$/, "")}\n${ticks}\n\n`;
}

function bounded(value: string): string {
  if (value.length <= OUTPUT_CHARS) return value;
  return value.slice(0, OUTPUT_CHARS) + "\n" + markdownT("more", value.length - OUTPUT_CHARS);
}

function names(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

export function buildSessionMarkdown(input: {
  title: string;
  truncatedNote?: string | null;
  messages: MarkdownMessage[];
  cells: MarkdownCell[];
  images: MarkdownImage[];
  otherArtifacts: Array<{ filename: string; content_type?: string | null }>;
}): string {
  let md = `# ${input.title}\n\n`;
  if (input.truncatedNote) md += `> ${input.truncatedNote}\n\n`;
  const images = new Map(input.images.map((image) => [image.filename, image]));
  const placed = new Set<string>();
  type Item = { at: number; order: number; kind: "message" | "cell"; value: MarkdownMessage | MarkdownCell };
  const items: Item[] = [
    ...input.messages.map((value, order) => ({ at: when(value.created_at), order, kind: "message" as const, value })),
    ...input.cells
      .filter((cell) => cell.visibility !== "system")
      .map((value, order) => ({ at: when(value.created_at), order: order + 0.5, kind: "cell" as const, value })),
  ];
  // Stable on time; a row with no time keeps its own list order.
  items.sort((a, b) => (a.at && b.at ? a.at - b.at : 0) || a.order - b.order);
  let lastRole = "";
  let count = 0;
  for (const item of items) {
    if (item.kind === "message") {
      const message = item.value as MarkdownMessage;
      const body = portableLinks(text(message.content)).trim();
      if (!body) continue;
      const role = message.role === "user" ? "user" : "assistant";
      // One heading per turn: the assistant's progress lines and its answer
      // are one section, not one heading each.
      if (role !== lastRole || role === "user") {
        md += `## ${role === "user" ? "🧑 " + markdownT("user") : "🤖 " + markdownT("assistant")}\n\n`;
      }
      md += body + "\n\n";
      lastRole = role;
      continue;
    }
    const cell = item.value as MarkdownCell;
    const source = String(cell.source ?? cell.code ?? "");
    if (!source.trim()) continue;
    // The code belongs to the assistant's section of this turn.
    if (lastRole !== "assistant") md += `## 🤖 ${markdownT("assistant")}\n\n`;
    lastRole = "assistant";
    count += 1;
    const background = String(cell.kernel_id || "").includes("background");
    md += `### ${markdownT("cell", count)}${background ? markdownT("background") : ""}\n\n`;
    md += fence(source, cell.language === "r" ? "r" : "python");
    const output = [cell.stdout, cell.stderr].filter(Boolean).join("\n");
    if (output.trim()) md += `${markdownT("output")}:\n\n` + fence(bounded(output));
    if (cell.error) md += `${markdownT("error")}:\n\n` + fence(bounded(String(cell.error)));
    for (const name of [...names(cell.figures), ...names(cell.files_written)]) {
      const image = images.get(name);
      if (!image || placed.has(name)) continue;
      placed.add(name);
      md += image.dataUri ? `![${name}](${image.dataUri})\n\n` : `- ${markdownT("tooLarge", name, image.skipped || "")}\n\n`;
    }
  }
  const rest = [
    ...input.images.filter((image) => !placed.has(image.filename)),
    ...input.otherArtifacts.map((item) => ({ filename: item.filename, content_type: item.content_type })),
  ];
  if (rest.length) {
    md += `## ${markdownT("artifacts")}\n\n`;
    for (const item of rest) {
      const image = images.get(item.filename);
      if (image && image.dataUri) md += `### ${item.filename}\n\n![${item.filename}](${image.dataUri})\n\n`;
      else if (image) md += `- ${markdownT("tooLarge", item.filename, image.skipped || "")}\n`;
      else md += `- ${item.filename}${"content_type" in item && item.content_type ? ` (${item.content_type})` : ""}\n`;
    }
    md += "\n";
  }
  return md;
}

const IMAGE_TYPES: Record<string, string> = {
  png: "image/png",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  gif: "image/gif",
  svg: "image/svg+xml",
  webp: "image/webp",
};
export const EMBED_BYTES = 4 * 1024 * 1024;
export const EMBED_TOTAL = 16 * 1024 * 1024;

export function imageType(filename: string, contentType?: string | null): string | null {
  if (contentType && /^image\/(png|jpeg|gif|svg\+xml|webp)$/.test(contentType)) return contentType;
  const ext = (filename.split(".").pop() || "").toLowerCase();
  return IMAGE_TYPES[ext] || null;
}

function base64(bytes: Uint8Array): string {
  let binary = "";
  for (let at = 0; at < bytes.length; at += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(at, at + 0x8000));
  }
  return btoa(binary);
}

/** Each image artifact as a data URI, within the per-image and total budgets. */
export async function embedImages(
  rows: Array<{ id: string; filename?: string | null; content_type?: string | null; size_bytes?: number | null }>,
  read: (id: string) => Promise<ArrayBuffer | null>,
  sizeLabel: (n: number) => string,
): Promise<MarkdownImage[]> {
  const out: MarkdownImage[] = [];
  let total = 0;
  for (const row of rows) {
    const filename = String(row.filename || "");
    const mime = filename ? imageType(filename, row.content_type) : null;
    if (!mime) continue;
    const declared = Number(row.size_bytes || 0);
    if (declared > EMBED_BYTES || total + declared > EMBED_TOTAL) {
      out.push({ filename, skipped: sizeLabel(declared) });
      continue;
    }
    const data = await read(row.id).catch(() => null);
    if (!data || data.byteLength > EMBED_BYTES || total + data.byteLength > EMBED_TOTAL) {
      out.push({ filename, skipped: sizeLabel(data ? data.byteLength : declared) });
      continue;
    }
    total += data.byteLength;
    out.push({ filename, dataUri: `data:${mime};base64,${base64(new Uint8Array(data))}` });
  }
  return out;
}
