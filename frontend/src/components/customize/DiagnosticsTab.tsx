import { useEffect, useRef, useState } from "preact/hooks";
import { LANG } from "../../i18n";
import {
  downloadDiagnosticsBundle,
  getDiagnosticsStatus,
  runDiagnosticsChecks,
} from "../../features/customize/api";
import { custTab } from "../../features/customize/actions";
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
  },
} as const;

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
        setPassiveError(null);
      } catch (err) {
        if (active && current()) setPassiveError(failure(err));
      }
    })();
    return () => { active = false; };
  }, [alive]);

  async function onCopy() {
    if (!requestId) return;
    try {
      await navigator.clipboard.writeText(requestId);
      if (current()) setCopied(true);
    } catch {
      /* clipboard may be denied; the id remains visible */
    }
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
      {posture ? <CustRow name={text.title} desc={posture} /> : null}
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
