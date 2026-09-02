import { useEffect, useState } from "preact/hooks";
import { LANG } from "../../i18n";
import {
  apiErrorText,
  downloadDiagnosticsBundle,
  getDiagnosticsStatus,
  runDiagnosticsChecks,
} from "../../features/customize/api";
import { custTab } from "../../features/customize/actions";
import { useAlive } from "./use-timer-lease";
import { CustRow, Hdr } from "./ui";

const COPY = {
  en: {
    title: "Diagnostics",
    sub: "Read-only security posture. Full checks and a redacted support bundle run only when you ask. Nothing is uploaded.",
    requestId: "Request id",
    copy: "Copy",
    copied: "Copied",
    runChecks: "Run checks",
    download: "Download support bundle",
    models: "Models",
    network: "Network",
    compute: "Compute",
    remedy: "Open a setting that a check may point at",
    adminOnly: "Diagnostics are available to the operator only.",
    busy: "Working…",
  },
  zh: {
    title: "诊断",
    sub: "只读安全姿态。完整检查与脱敏支持包仅在你显式请求时运行，不会上传任何内容。",
    requestId: "请求 id",
    copy: "复制",
    copied: "已复制",
    runChecks: "运行检查",
    download: "下载支持包",
    models: "模型",
    network: "网络",
    compute: "计算",
    remedy: "跳转到检查可能指向的设置",
    adminOnly: "诊断仅对管理员开放。",
    busy: "进行中…",
  },
} as const;

function copy() {
  return LANG === "zh" ? COPY.zh : COPY.en;
}

function factLine(security: Record<string, unknown>): string {
  const parts: string[] = [];
  for (const key of [
    "kernel_sandbox",
    "egress",
    "secret_store_mode",
    "compute_confinement",
  ]) {
    const value = security[key];
    if (value != null && value !== "") parts.push(`${key}=${String(value)}`);
  }
  return parts.join(" · ");
}

export function DiagnosticsTab() {
  const alive = useAlive();
  const text = copy();
  const [requestId, setRequestId] = useState("");
  const [posture, setPosture] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [checks, setChecks] = useState<string>("");
  const [busy, setBusy] = useState<"checks" | "bundle" | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    void (async () => {
      try {
        const status = await getDiagnosticsStatus();
        if (!alive()) return;
        setRequestId(status.request_id);
        setPosture(factLine(status.security));
        setError(null);
      } catch (err) {
        if (!alive()) return;
        const status = (err as { status?: number } | null)?.status;
        setError(status === 403 ? text.adminOnly : apiErrorText(err));
      }
    })();
  }, [alive, text.adminOnly]);

  async function onCopy() {
    if (!requestId) return;
    try {
      await navigator.clipboard.writeText(requestId);
      if (alive()) setCopied(true);
    } catch {
      /* clipboard may be denied; the id remains visible */
    }
  }

  async function onChecks() {
    setBusy("checks");
    try {
      const result = await runDiagnosticsChecks();
      if (!alive()) return;
      if (typeof result.request_id === "string" && result.request_id) {
        setRequestId(result.request_id);
      }
      const rows = Array.isArray(result.checks) ? result.checks : [];
      setChecks(
        rows
          .map((row) => {
            const rec =
              row && typeof row === "object" ? (row as Record<string, unknown>) : {};
            return (
              `${String(rec.status || "")}  ${String(rec.name || "")}  ` +
              String(rec.detail || "")
            );
          })
          .join("\n"),
      );
      setError(null);
    } catch (err) {
      if (alive()) setError(apiErrorText(err));
    } finally {
      if (alive()) setBusy(null);
    }
  }

  async function onBundle() {
    setBusy("bundle");
    try {
      await downloadDiagnosticsBundle();
      if (alive()) setError(null);
    } catch (err) {
      if (alive()) setError(apiErrorText(err));
    } finally {
      if (alive()) setBusy(null);
    }
  }

  return (
    <div class="cust-diagnostics" data-diagnostics="1">
      <Hdr title={text.title} sub={text.sub} />
      {error ? <div class="cust-sub">{error}</div> : null}
      {posture ? (
        <CustRow name={text.title} desc={posture} />
      ) : null}
      <CustRow name={text.requestId} desc={requestId || "—"}>
        <button
          type="button"
          class="outline-btn small"
          disabled={!requestId}
          onClick={() => void onCopy()}
        >
          {copied ? text.copied : text.copy}
        </button>
      </CustRow>
      <CustRow name={text.runChecks} desc={checks}>
        <button
          type="button"
          class="outline-btn small"
          disabled={busy !== null}
          onClick={() => void onChecks()}
        >
          {busy === "checks" ? text.busy : text.runChecks}
        </button>
      </CustRow>
      <CustRow name={text.download}>
        <button
          type="button"
          class="solid-btn small"
          disabled={busy !== null}
          onClick={() => void onBundle()}
        >
          {busy === "bundle" ? text.busy : text.download}
        </button>
      </CustRow>
      <CustRow name={text.remedy}>
        <button type="button" class="outline-btn small" onClick={() => custTab("models")}>
          {text.models}
        </button>
        <button type="button" class="outline-btn small" onClick={() => custTab("network")}>
          {text.network}
        </button>
        <button type="button" class="outline-btn small" onClick={() => custTab("compute")}>
          {text.compute}
        </button>
      </CustRow>
    </div>
  );
}
