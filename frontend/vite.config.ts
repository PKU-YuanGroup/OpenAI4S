import preact from "@preact/preset-vite";
import { existsSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import type { Plugin } from "vite";
import { defineConfig } from "vitest/config";

const frontendRoot = dirname(fileURLToPath(import.meta.url));
const outDir = join(frontendRoot, "../openai4s/server/webui/dist");

const DOC_NAMES = new Set(["README.md", "README_zh.md"]);
// Match a <script> opening tag that has no src= before the closing '>'.
const INLINE_SCRIPT = /<script\b(?![^>]*\bsrc\s*=)[^>]*>/i;

function listDirect(dir: string): { files: string[]; dirs: string[] } {
  const files: string[] = [];
  const dirs: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (DOC_NAMES.has(entry.name)) continue;
    if (entry.isDirectory()) dirs.push(entry.name);
    else files.push(entry.name);
  }
  files.sort();
  dirs.sort();
  return { files, dirs };
}

function fileTable(files: string[], cellEn: string, cellZh: string): {
  en: string;
  zh: string;
} {
  const headerEn = "| File | Responsibility |\n| --- | --- |";
  const headerZh = "| 文件 | 职责 |\n| --- | --- |";
  const enRows = files.map((name) => `| \`${name}\` | ${cellEn} |`).join("\n");
  const zhRows = files.map((name) => `| \`${name}\` | ${cellZh} |`).join("\n");
  return {
    en: enRows ? `${headerEn}\n${enRows}` : headerEn,
    zh: zhRows ? `${headerZh}\n${zhRows}` : headerZh,
  };
}

function dirTable(dirs: string[], cellEn: string, cellZh: string): {
  en: string;
  zh: string;
} {
  const headerEn = "| Directory | Responsibility |\n| --- | --- |";
  const headerZh = "| 目录 | 职责 |\n| --- | --- |";
  const enRows = dirs.map((name) => `| \`${name}/\` | ${cellEn} |`).join("\n");
  const zhRows = dirs.map((name) => `| \`${name}/\` | ${cellZh} |`).join("\n");
  return {
    en: enRows ? `${headerEn}\n${enRows}` : headerEn,
    zh: zhRows ? `${headerZh}\n${zhRows}` : headerZh,
  };
}

function writeDistReadmes(dir: string, titleEn: string, titleZh: string): void {
  const { files, dirs } = listDirect(dir);
  const filesTable = fileTable(
    files,
    "Vite build output. Do not edit by hand; rebuild from `frontend/`.",
    "Vite 构建产物。不要手改；在 `frontend/` 里重新 build。",
  );
  const dirsTable = dirTable(
    dirs,
    "Hashed chunks emitted by Vite.",
    "Vite 打出的带哈希分块。",
  );
  const subEn = dirs.length
    ? `\n## Subdirectories\n\n${dirsTable.en}\n`
    : "";
  const subZh = dirs.length
    ? `\n## 子目录\n\n${dirsTable.zh}\n`
    : "";
  const english = `# ${titleEn}

[中文说明](README_zh.md)

Committed output of \`frontend/\` (\`npm run build\`). The gateway serves this tree at \`/static/dist/\`. It is also the default SPA shell at \`/\` and at workbench deep links; \`OPENAI4S_WEBUI=legacy\` is the escape hatch that serves \`webui/index.html\` instead. Every script is an external \`src=\` file so CSP \`script-src 'self'\` holds.

## Files

${filesTable.en}
${subEn}`;
  const chinese = `# ${titleZh}

[English](README.md)

\`frontend/\`（\`npm run build\`）提交进来的构建产物。Gateway 在 \`/static/dist/\` 提供这棵树。它也是 \`/\` 与工作台深链的默认 SPA 外壳；\`OPENAI4S_WEBUI=legacy\` 是改发 \`webui/index.html\` 的逃生舱。脚本全部是带 \`src=\` 的外链文件，CSP \`script-src 'self'\` 不需要放行内联脚本。

## 文件

${filesTable.zh}
${subZh}`;
  writeFileSync(join(dir, "README.md"), english.endsWith("\n") ? english : `${english}\n`);
  writeFileSync(
    join(dir, "README_zh.md"),
    chinese.endsWith("\n") ? chinese : `${chinese}\n`,
  );
}

function scanHtmlForInlineScripts(dir: string): void {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) {
      scanHtmlForInlineScripts(path);
      continue;
    }
    if (!entry.name.endsWith(".html")) continue;
    const html = readFileSync(path, "utf8");
    if (INLINE_SCRIPT.test(html)) {
      throw new Error(
        `inline <script> in ${path} violates CSP script-src 'self'`,
      );
    }
  }
}

function cspBuildGuard(): Plugin {
  return {
    name: "openai4s-csp-no-inline-scripts",
    configResolved(config) {
      for (const plugin of config.plugins) {
        const name = plugin.name;
        if (name === "vite:legacy" || name.startsWith("vite:legacy-")) {
          throw new Error(
            `${name} is forbidden: @vitejs/plugin-legacy injects inline scripts and breaks CSP script-src 'self'`,
          );
        }
      }
      const preload = config.build.modulePreload;
      if (preload !== false && preload.polyfill !== false) {
        throw new Error(
          'build.modulePreload.polyfill must be false so Vite emits external <link rel="modulepreload"> instead of an inline polyfill script',
        );
      }
    },
    closeBundle() {
      if (!existsSync(outDir)) {
        throw new Error(`build output missing: ${outDir}`);
      }
      scanHtmlForInlineScripts(outDir);
      writeDistReadmes(outDir, "Workbench build output", "Workbench 构建产物");
      const assetsDir = join(outDir, "assets");
      if (existsSync(assetsDir)) {
        writeDistReadmes(assetsDir, "Workbench hashed assets", "Workbench 哈希资源");
      }
    },
  };
}

export default defineConfig({
  plugins: [
    preact(),
    // Do not add @vitejs/plugin-legacy. It injects inline scripts.
    cspBuildGuard(),
  ],
  base: "/static/dist/",
  build: {
    outDir,
    emptyOutDir: true,
    assetsInlineLimit: 0,
    target: "es2022",
    modulePreload: {
      // External <link rel="modulepreload"> only; no inline polyfill script.
      polyfill: false,
    },
    rollupOptions: {
      output: {
        inlineDynamicImports: false,
      },
    },
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": { target: "http://127.0.0.1:8760", changeOrigin: true },
      "/ws": { target: "ws://127.0.0.1:8760", ws: true },
      "/static": { target: "http://127.0.0.1:8760" },
    },
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
  },
});
