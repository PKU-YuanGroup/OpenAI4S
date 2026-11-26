import { useEffect, useRef, useState } from "preact/hooks";
import { LANG } from "../../i18n";
import {
  downloadDiagnosticsBundle,
  getDiagnosticsStatus,
  runDiagnosticsChecks,
} from "../../features/customize/api";
import { copyFailedText, copyText } from "../../features/chrome/clipboard";
import { custTab } from "../../features/customize/actions";
import { hint } from "../../features/customize/host";
import {
  customizeGeneration,
  diagnosticsAttempt,
  diagnosticsConfigRevision,
  diagnosticsFailure,
  diagnosticsRequest,
  diagnosticsResult,
  type DiagnosticsFailure,
} from "../../features/customize/state";
import { useAlive } from "./use-timer-lease";
import { CustRow, Hdr } from "./ui";

const COPY = {
  en: {
    title: "Diagnostics",
    sub: "Read-only security posture. Full checks and a redacted support bundle run only when you ask. Nothing is uploaded.",
    requestId: "Request id", copy: "Copy", copied: "Copied", runChecks: "Run checks",
    download: "Download support bundle", models: "Models", network: "Network", compute: "Compute",
    adminOnly: "Diagnostics are available to the operator only.", busy: "Working…",
    ok: "OK", warn: "Warning", fail: "Failed", remedy: "Next step", facts: "Supporting facts",
    factsLimit: "Showing the first 20 facts; each value is limited to 500 characters.",
    received: "Received this time", previous: "Previous results", stale: "Configuration changed — run checks again.",
    invalid: "The diagnostics response is incomplete or invalid. Run checks again.",
    failed: "The diagnostics request failed.", timeout: "The diagnostics request timed out. Run checks again.",
    noChecks: "No check items were returned.",
    sandbox: "Kernel sandbox", egress: "Network egress", secrets: "Credential store", knobs: "Environment variables",
  },
  zh: {
    title: "诊断",
    sub: "只读安全姿态。完整检查与脱敏支持包仅在你显式请求时运行，不会上传任何内容。",
    requestId: "请求 id", copy: "复制", copied: "已复制", runChecks: "运行检查",
    download: "下载支持包", models: "模型", network: "网络", compute: "计算",
    adminOnly: "诊断仅对管理员开放。", busy: "进行中…",
    ok: "正常", warn: "警告", fail: "失败", remedy: "下一步", facts: "支持事实",
    factsLimit: "最多展示前 20 项事实，每项文本最多 500 个字符。",
    received: "本次获取时间", previous: "上次结果", stale: "配置已改变，需要重新检查。",
    invalid: "诊断响应不完整或格式错误，请重新检查。", failed: "诊断请求失败。",
    timeout: "诊断请求超时，请重新检查。", noChecks: "没有返回检查项。",
    sandbox: "内核沙箱", egress: "网络出口", secrets: "凭证存储", knobs: "环境变量",
  },
} as const;

/**
 * SET-3: the resolved posture (`security.resolved`) in words. The row used to
 * echo the environment ("kernel_sandbox=(default) · egress=(default) …");
 * those knobs are now the secondary line.
 */
const POSTURE_WORDS: Record<"en" | "zh", Record<string, string>> = {
  en: {
    "sandbox.auto": "Automatic", "sandbox.enforce": "Required", "sandbox.off": "Off",
    "sandbox.invalid": "Setting not recognised; the kernel will not start",
    "backend.seatbelt": "macOS Seatbelt", "backend.bubblewrap": "bubblewrap",
    "backend.none": "no sandbox tool found",
    "selfTest.not_run": "its self-test runs with Run checks",
    "selfTest.failed": "its self-test failed on this machine",
    "egress.off": "Not restricted", "egress.allowlist": "Only domains on the allowlist",
    "store.macos-keychain": "macOS Keychain", "store.secret-service": "system keyring (Secret Service)",
    "store.windows-dpapi": "Windows account protection", "store.env-injection": "environment variables",
    "store.plaintext-db": "the database, unencrypted", "store.memory": "memory only (not kept)",
    "store.none": "none available", "store.not_resolved": "Not used yet",
    "store.secure": "secure", "store.insecure": "not encrypted",
    "storeMode.auto": "chosen automatically", "storeMode.keychain": "keychain only",
    "storeMode.env": "environment only", "storeMode.plaintext": "plaintext chosen",
    "storeMode.invalid": "setting not recognised",
  },
  zh: {
    "sandbox.auto": "自动", "sandbox.enforce": "必须启用", "sandbox.off": "已关闭",
    "sandbox.invalid": "设置无法识别，内核将无法启动",
    "backend.seatbelt": "macOS Seatbelt", "backend.bubblewrap": "bubblewrap",
    "backend.none": "没有找到沙箱工具",
    "selfTest.not_run": "自检在「运行检查」时进行",
    "selfTest.failed": "这台机器上的自检未通过",
    "egress.off": "不限制", "egress.allowlist": "只允许白名单里的域名",
    "store.macos-keychain": "macOS 钥匙串", "store.secret-service": "系统密钥环（Secret Service）",
    "store.windows-dpapi": "Windows 账户保护", "store.env-injection": "环境变量",
    "store.plaintext-db": "数据库（未加密）", "store.memory": "仅内存（不保留）",
    "store.none": "没有可用的存储", "store.not_resolved": "尚未使用",
    "store.secure": "安全", "store.insecure": "未加密",
    "storeMode.auto": "自动选择", "storeMode.keychain": "仅钥匙串",
    "storeMode.env": "仅环境变量", "storeMode.plaintext": "已选择明文",
    "storeMode.invalid": "设置无法识别",
  },
};

type PostureRow = { key: string; name: string; desc: string };

function resolvedRows(security: Record<string, unknown>, lang: "en" | "zh"): PostureRow[] {
  const words = POSTURE_WORDS[lang];
  const text = lang === "zh" ? COPY.zh : COPY.en;
  const resolved = security.resolved && typeof security.resolved === "object"
    ? security.resolved as Record<string, unknown> : null;
  if (!resolved) return [];
  const part = (name: string): Record<string, unknown> => {
    const value = resolved[name];
    return value && typeof value === "object" ? value as Record<string, unknown> : {};
  };
  const word = (key: string) => words[key];
  const rows: PostureRow[] = [];
  const sandbox = part("kernel_sandbox");
  const mode = word("sandbox." + String(sandbox.mode));
  if (mode) {
    const bits = [mode];
    if (sandbox.mode === "auto" || sandbox.mode === "enforce") {
      const backend = word("backend." + String(sandbox.backend));
      if (backend) bits.push(backend);
      const selfTest = sandbox.backend !== "none" ? word("selfTest." + String(sandbox.self_test)) : "";
      if (selfTest) bits.push(selfTest);
    }
    rows.push({ key: "kernel_sandbox", name: text.sandbox, desc: bits.join(" · ") });
  }
  const egress = word("egress." + String(part("egress").mode));
  if (egress) rows.push({ key: "egress", name: text.egress, desc: egress });
  const store = part("secret_store");
  const backend = word("store." + String(store.backend));
  if (backend) {
    const note = store.backend === "not_resolved"
      ? word("storeMode." + String(store.mode))
      : typeof store.secure === "boolean" ? word(store.secure ? "store.secure" : "store.insecure") : "";
    const desc = note ? (lang === "zh" ? `${backend}（${note}）` : `${backend} (${note})`) : backend;
    rows.push({ key: "secret_store", name: text.secrets, desc });
  }
  return rows;
}

// doctor._CHECKS names and the existing Customize destinations. Response URLs
// and remedy commands are ordinary text and never decide navigation or actions.
const SETTINGS = new Map<string, "models" | "network" | "compute">([
  ["model", "models"], ["connectors", "network"], ["remote", "compute"], ["runtime", "compute"],
]);
const clip = (value: string) => value.length > 500 ? value.slice(0, 499) + "…" : value;

function failure(error: unknown): DiagnosticsFailure {
  const err = error && typeof error === "object"
    ? error as { message?: unknown; status?: unknown; code?: unknown; requestId?: unknown; name?: unknown }
    : {};
  return {
    message: typeof err.message === "string" ? clip(err.message) : "",
    status: typeof err.status === "number" ? err.status : 0,
    code: err.name === "TimeoutError" ? "timeout" : typeof err.code === "string" ? err.code : "",
    requestId: typeof err.requestId === "string" ? clip(err.requestId) : "",
  };
}

function factLine(security: Record<string, unknown>): string {
  return ["kernel_sandbox", "egress", "secret_store_mode", "compute_confinement"]
    .flatMap((key) => {
      const value = security[key];
      return typeof value === "string" || typeof value === "boolean"
        ? [`${key}=${clip(String(value))}`] : [];
    }).join(" · ");
}

function factText(value: unknown): string {
  return clip(typeof value === "string" ? value : JSON.stringify(value) ?? "");
}

export function DiagnosticsTab() {
  const alive = useAlive();
  const generation = useRef(customizeGeneration.value);
  const text = LANG === "zh" ? COPY.zh : COPY.en;
  const [passiveId, setPassiveId] = useState("");
  const [posture, setPosture] = useState("");
  const [passiveError, setPassiveError] = useState<DiagnosticsFailure | null>(null);
  const [bundleError, setBundleError] = useState<DiagnosticsFailure | null>(null);
  const [copied, setCopied] = useState(false);
  // SET-3: the passive read's `security`, for the resolved posture rows.
  const [security, setSecurity] = useState<Record<string, unknown> | null>(null);
  const current = () => alive() && generation.current === customizeGeneration.value;
  const snapshot = diagnosticsResult.value;
  const busy = diagnosticsRequest.value?.kind;
  const stale = snapshot !== null && snapshot.configRevision !== diagnosticsConfigRevision.value;
  const previous = snapshot !== null && (stale || snapshot.attempt !== diagnosticsAttempt.value);
  const error = diagnosticsFailure.value || bundleError || passiveError;
  const requestId = error?.requestId || snapshot?.report.request_id || passiveId;
  const errorText = error && (error.status === 401 || error.status === 403 ? text.adminOnly
    : error.code === "invalid_response" ? text.invalid
      : error.code === "timeout" ? text.timeout
        : error.code === "config_changed" ? text.stale : error.message || text.failed);

  useEffect(() => {
    let active = true;
    void (async () => {
      try {
        const status = await getDiagnosticsStatus();
        if (!active || !current()) return;
        setPassiveId(status.request_id);
        setPosture(factLine(status.security));
        setSecurity(status.security);
        setPassiveError(null);
      } catch (err) {
        if (active && current()) setPassiveError(failure(err));
      }
    })();
    return () => { active = false; };
  }, [alive]);

  async function onCopy() {
    if (!requestId) return;
    // The shared write: over plain http there is no navigator.clipboard, and
    // the selection copy it falls back to still works there.
    const copiedNow = await copyText(requestId);
    if (!current()) return;
    if (copiedNow) setCopied(true);
    else hint(copyFailedText(), true);
  }

  async function onChecks() {
    if (!current() || diagnosticsRequest.value) return;
    const owner = { kind: "checks" as const };
    diagnosticsRequest.value = owner;
    const attempt = ++diagnosticsAttempt.value;
    const configRevision = diagnosticsConfigRevision.value;
    diagnosticsFailure.value = null;
    try {
      const report = await runDiagnosticsChecks();
      if (!current() || diagnosticsRequest.value !== owner) return;
      if (configRevision !== diagnosticsConfigRevision.value) {
        diagnosticsFailure.value = { message: "", status: 0, code: "config_changed", requestId: report.request_id };
        return;
      }
      diagnosticsResult.value = { report, receivedAt: Date.now(), configRevision, attempt };
    } catch (err) {
      if (current() && diagnosticsRequest.value === owner) diagnosticsFailure.value = failure(err);
    } finally {
      if (diagnosticsRequest.value === owner) diagnosticsRequest.value = null;
    }
  }

  async function onBundle() {
    if (!current() || diagnosticsRequest.value) return;
    const owner = { kind: "bundle" as const };
    diagnosticsRequest.value = owner;
    try {
      await downloadDiagnosticsBundle();
      if (current()) setBundleError(null);
    } catch (err) {
      if (current()) setBundleError(failure(err));
    } finally {
      if (diagnosticsRequest.value === owner) diagnosticsRequest.value = null;
    }
  }

  return (
    <div class="cust-diagnostics" data-diagnostics="1">
      <Hdr title={text.title} sub={text.sub} />
      {errorText ? <div class="cust-sub" role="alert" data-diagnostics-error="1">{errorText}</div> : null}
      {security ? resolvedRows(security, LANG === "zh" ? "zh" : "en").map((row) => (
        <div key={row.key} class="cust-row" data-diagnostics-posture={row.key}>
          <div class="info">
            <div class="nm">{row.name}</div>
            <div class="ds">{row.desc}</div>
          </div>
        </div>
      )) : null}
      {posture ? (
        <div class="cust-row" data-diagnostics-knobs="1">
          <div class="info">
            <div class="nm">{text.knobs}</div>
            <div class="ds">{posture}</div>
          </div>
        </div>
      ) : null}
      <CustRow name={text.requestId} desc={requestId || "—"}>
        <button type="button" class="outline-btn small" disabled={!requestId} onClick={() => void onCopy()}>
          {copied ? text.copied : text.copy}
        </button>
      </CustRow>
      <CustRow name={text.runChecks}>
        <button type="button" class="outline-btn small" data-diagnostics-run="1"
          disabled={!!busy} onClick={() => void onChecks()}>
          {busy === "checks" ? text.busy : text.runChecks}
        </button>
      </CustRow>
      {snapshot ? (
        <section data-diagnostics-results={previous ? "previous" : "current"} aria-label={text.title}>
          <div class="cust-sub" role="status">
            {previous ? text.previous + " · " : ""}{text.received}: {" "}
            <time dateTime={new Date(snapshot.receivedAt).toISOString()}>{new Date(snapshot.receivedAt).toLocaleString(LANG === "zh" ? "zh-CN" : "en-US")}</time>
            {stale ? <p data-diagnostics-stale="1">{text.stale}</p> : null}
          </div>
          {snapshot.report.checks.length === 0 ? <p class="cust-sub">{text.noChecks}</p> : null}
          {snapshot.report.checks.map((row, index) => {
            const target = SETTINGS.get(row.name);
            const facts = Object.entries(row.facts || {});
            return (
              <article key={index} class="cust-row" data-diagnostic-check={row.name} data-diagnostic-status={row.status}>
                <div class="info">
                  <div class="nm"><strong>{row.name}</strong> · <span>{text[row.status]}</span></div>
                  <p class="cust-sub" data-diagnostic-detail="1" style={{ whiteSpace: "pre-wrap" }}>{row.detail}</p>
                  {row.remedy ? <p class="cust-sub" data-diagnostic-remedy="1" style={{ whiteSpace: "pre-wrap" }}><strong>{text.remedy}: </strong>{row.remedy}</p> : null}
                  {facts.length ? <details data-diagnostic-facts="1">
                    <summary>{text.facts}</summary>
                    <p class="cust-sub">{text.factsLimit}</p>
                    <dl>{facts.slice(0, 20).map(([key, value]) => (
                      <div key={key}><dt>{clip(key)}</dt><dd style={{ whiteSpace: "pre-wrap" }}>{factText(value)}</dd></div>
                    ))}</dl>
                  </details> : null}
                  {target && row.status !== "ok" ? <button type="button" class="outline-btn small"
                    data-diagnostic-setting={target} onClick={() => custTab(target)}>{text[target]}</button> : null}
                </div>
              </article>
            );
          })}
        </section>
      ) : null}
      <CustRow name={text.download}>
        <button type="button" class="solid-btn small" data-diagnostics-bundle="1"
          disabled={!!busy} onClick={() => void onBundle()}>
          {busy === "bundle" ? text.busy : text.download}
        </button>
      </CustRow>
    </div>
  );
}
