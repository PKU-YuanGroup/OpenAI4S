/**
 * Settings → Compute: which interpreter each language runs in, and what a
 * package install did.
 *
 * The tab showed one row ("python kernel · Ready") and nothing about R, and
 * an install changed a package count three seconds later without saying what
 * was installed, which version, where, or whether a kernel had to restart.
 * The rows are read from `runtimes` on the environment listing
 * (`GET /environments`, `GET /frames/{id}/environments`); a path never
 * appears, only the environment's name and where it comes from.
 */
import { copyLookup, type CopyTable } from "../../i18n/copy";

const COPY: CopyTable = {
  en: {
    "rt.title": "Runtime environments",
    "rt.desc.session": "What the open session's Python and R code runs in.",
    "rt.desc.default": "What a new session's Python and R code runs in.",
    "rt.kind.builtin": "built-in",
    "rt.kind.managed": "managed",
    "rt.kind.local": "local",
    "rt.systemR": "R installed on this computer",
    "rt.r.chosen": "chosen for this session",
    "rt.r.default": "default",
    "rt.r.none": "no R found",
    "rt.missing.python": "No Python environment is available.",
    "rt.missing.r":
      "No R was found. Run `openai4s setup` to build the managed Python + R environments.",
    "rt.r.packages": "Key packages: {0}.",
    "rt.r.pkgOk": "{0} loads",
    "rt.r.pkgMissing": "{0} is not available",
    "rt.r.unchecked": "Could not check which R packages load.",
    "rt.r.managedHint":
      "The managed R environment built by `openai4s setup` includes them; OpenAI4S does not install R packages itself.",
    "rt.packages.name": "Common Python packages in the built-in environment",
    "rt.packages.installing": "installing…",
    "rt.packages.detail": "{0} of {1} installed: {2}",
    "rt.packages.missing": "Not installed: {0}",
    "rt.install.hint":
      "Installs into the built-in Python environment ({0}). To build the managed Python + R environments, run `openai4s setup`.",
    "rt.install.done": "Installed into the built-in environment ({0}): {1}.",
    "rt.install.doneNoVersion": "Installed into the built-in environment ({0}): {1} (version not reported).",
    "rt.install.restarted": "This session's kernel was restarted, so the packages can be imported now; earlier variables are gone.",
    "rt.install.restartFailed": "The kernel could not be restarted. Restart it from the Notebook before importing the new packages.",
    "rt.install.restartNeeded": "A kernel that is already running must be restarted to see them; a kernel started from now on has them.",
    "rt.install.otherEnv": "This session runs in the {0} environment, which does not see packages installed here.",
    "rt.install.failed": "Install failed: {0}",
  },
  zh: {
    "rt.title": "运行环境",
    "rt.desc.session": "当前会话的 Python 和 R 代码实际运行在这些环境里。",
    "rt.desc.default": "新会话的 Python 和 R 代码默认运行在这些环境里。",
    "rt.kind.builtin": "内置",
    "rt.kind.managed": "托管",
    "rt.kind.local": "本机",
    "rt.systemR": "这台电脑上安装的 R",
    "rt.r.chosen": "本会话所选",
    "rt.r.default": "默认",
    "rt.r.none": "未找到 R",
    "rt.missing.python": "没有可用的 Python 环境。",
    "rt.missing.r": "没有找到 R。运行 `openai4s setup` 构建托管的 Python + R 环境。",
    "rt.r.packages": "关键包：{0}。",
    "rt.r.pkgOk": "{0} 可用",
    "rt.r.pkgMissing": "{0} 不可用",
    "rt.r.unchecked": "无法检测 R 包是否可用。",
    "rt.r.managedHint": "`openai4s setup` 构建的托管 R 环境自带这些包；OpenAI4S 本身不安装 R 包。",
    "rt.packages.name": "内置环境里的常用 Python 包",
    "rt.packages.installing": "安装中…",
    "rt.packages.detail": "已安装 {0} / {1} 个：{2}",
    "rt.packages.missing": "未安装：{0}",
    "rt.install.hint": "安装到内置 Python 环境（{0}）。要构建托管的 Python + R 环境，请运行 `openai4s setup`。",
    "rt.install.done": "已安装到内置环境（{0}）：{1}。",
    "rt.install.doneNoVersion": "已安装到内置环境（{0}）：{1}（未返回版本号）。",
    "rt.install.restarted": "本会话的内核已重启，现在就能导入新装的包；之前的变量已清空。",
    "rt.install.restartFailed": "内核重启失败。请先在 Notebook 里重启内核，再导入新装的包。",
    "rt.install.restartNeeded": "已经在运行的内核需要重启后才能用到它们；之后启动的内核直接可用。",
    "rt.install.otherEnv": "当前会话运行在 {0} 环境里，用不到装在这里的包。",
    "rt.install.failed": "安装失败：{0}",
  },
};

export const runtimeT = copyLookup(COPY);

export type RuntimeRow = {
  available: boolean;
  environment: string;
  kind: string;
  version: string;
  selected?: boolean;
  checked?: boolean;
  packages?: Array<{ name: string; available: boolean }>;
};
export type Runtimes = { python: RuntimeRow; r: RuntimeRow };

const str = (value: unknown): string => (typeof value === "string" ? value : "");

function runtimeRow(value: unknown): RuntimeRow | null {
  if (!value || typeof value !== "object") return null;
  const row = value as Record<string, unknown>;
  const packages = Array.isArray(row.packages)
    ? row.packages
        .filter((p): p is Record<string, unknown> => !!p && typeof p === "object")
        .map((p) => ({ name: str(p.name), available: p.available === true }))
        .filter((p) => p.name)
    : [];
  return {
    available: row.available === true,
    environment: str(row.environment),
    kind: str(row.kind),
    version: str(row.version),
    selected: row.selected === true,
    checked: row.checked === true,
    packages,
  };
}

/** `runtimes` from an environment listing, or null when the daemon sent none. */
export function readRuntimes(listing: unknown): Runtimes | null {
  const runtimes =
    listing && typeof listing === "object" ? (listing as { runtimes?: unknown }).runtimes : null;
  if (!runtimes || typeof runtimes !== "object") return null;
  const python = runtimeRow((runtimes as Record<string, unknown>).python);
  const r = runtimeRow((runtimes as Record<string, unknown>).r);
  return python && r ? { python, r } : null;
}

export function kindLabel(kind: string): string {
  return kind === "builtin" || kind === "managed" || kind === "local"
    ? runtimeT("rt.kind." + kind)
    : "";
}

/** "base · built-in · Python 3.13.1": name, where it comes from, version. */
export function runtimeSummary(language: "python" | "r", row: RuntimeRow): string {
  if (!row.available) return runtimeT("rt.missing." + language);
  const title = language === "r" ? "R" : "Python";
  const name = row.environment || (language === "r" ? runtimeT("rt.systemR") : "");
  return [name, kindLabel(row.kind), row.version ? title + " " + row.version : ""]
    .filter(Boolean)
    .join(" · ");
}

/** Whether R's key packages load, and where to get them when they do not. */
export function rPackagesNote(row: RuntimeRow): string {
  if (!row.available) return "";
  if (!row.checked) return runtimeT("rt.r.unchecked");
  const packages = row.packages || [];
  const note = runtimeT(
    "rt.r.packages",
    packages
      .map((p) => runtimeT(p.available ? "rt.r.pkgOk" : "rt.r.pkgMissing", p.name))
      .join(" · "),
  );
  const missing = packages.some((p) => !p.available);
  return missing && row.kind !== "managed" ? note + " " + runtimeT("rt.r.managedHint") : note;
}

/** What "default" stands for in the runtime menu's R group. */
export function rDefaultLabel(row: RuntimeRow | null): string {
  const fallback = runtimeT("rt.r.default");
  if (!row || row.selected) return fallback;
  return fallback + " → " + (row.available ? runtimeSummary("r", row) : runtimeT("rt.r.none"));
}

type InstallResult = {
  ok?: unknown;
  installed?: unknown;
  installed_versions?: unknown;
  failed?: unknown;
  restarted?: unknown;
  restart_error?: unknown;
};

export type InstallOutcome = { ok: boolean; lines: string[] };

/**
 * What an install did, in words: what was installed at which version, into
 * which environment, and what a running kernel has to do to see it.
 */
export function installOutcome(
  result: InstallResult,
  context: { builtinEnv: string; sessionOpen: boolean; sessionEnv: string; fallbackError: string },
): InstallOutcome {
  if (result.ok !== true) {
    const failed = Array.isArray(result.failed) ? (result.failed[0] as { error?: unknown }) : null;
    return {
      ok: false,
      lines: [runtimeT("rt.install.failed", str(failed && failed.error) || context.fallbackError)],
    };
  }
  // Keyed by project name: the requirement as typed may carry extras or a
  // version clause (`pandas[excel]>=2`), the answer names the project.
  const versions = new Map<string, string>();
  if (Array.isArray(result.installed_versions)) {
    for (const entry of result.installed_versions as Array<Record<string, unknown>>) {
      if (entry && str(entry.name) && str(entry.version)) {
        versions.set(str(entry.name).toLowerCase(), str(entry.name) + " " + str(entry.version));
      }
    }
  }
  const requested = Array.isArray(result.installed) ? result.installed.map(String) : [];
  let versioned = false;
  const names = requested.map((requirement) => {
    const project = (/^[A-Za-z0-9][A-Za-z0-9._-]*/.exec(requirement) || [""])[0].toLowerCase();
    const known = versions.get(project);
    if (known) versioned = true;
    return known || requirement;
  });
  const lines = [
    runtimeT(
      versioned ? "rt.install.done" : "rt.install.doneNoVersion",
      context.builtinEnv,
      names.join(", "),
    ),
  ];
  if (context.sessionOpen && context.sessionEnv && context.sessionEnv !== context.builtinEnv) {
    lines.push(runtimeT("rt.install.otherEnv", context.sessionEnv));
  } else if (result.restarted === true) {
    lines.push(runtimeT("rt.install.restarted"));
  } else if (result.restart_error) {
    lines.push(runtimeT("rt.install.restartFailed"));
  } else {
    lines.push(runtimeT("rt.install.restartNeeded"));
  }
  return { ok: true, lines };
}
