import { useEffect, useRef, useState } from "preact/hooks";
import { copyText } from "../../features/chrome/clipboard";
import {
  apiErrorText,
  checkForUpdates,
  getUpdateStatus,
  isUpdateAdminOnly,
  updateRetryAfterSeconds,
  type UpdateCheck as UpdateCheckData,
  type UpdateStatus,
} from "../../features/customize/api";
import { updateT } from "../../features/customize/update";
import { useAlive } from "./use-timer-lease";

/**
 * U2 / PLAN §10 U2, appendix B.2: a "Version and updates" row for General
 * settings. Mount reads only the local `GET /updates/status` (zero external
 * traffic); "Check for updates" posts `{refresh: false}` and "Force a fresh
 * check" posts `{refresh: true}` — the only calls that may reach the release
 * index. There is no download, install, restart, one-click upgrade or polling
 * anywhere: this version only checks and points at the manual instructions.
 *
 * A failed check never leaves an "up to date" claim standing; an `unknown`
 * answer shows its reason code and, when the daemon carried one forward, the
 * last known answer with the time it was actually checked. Trust is digest
 * agreement only — the authorship line is always "not verified".
 */
const UPDATE_CHECK_TIMEOUT_MS = 30_000;

type Failure = "none" | "read" | "check" | "admin" | "rate" | "timeout";

const REASONS = new Set([
  "ok",
  "offline_pin",
  "network_disabled",
  "egress_blocked",
  "ssrf_blocked",
  "unreachable",
  "timeout",
  "http_error",
  "too_large",
  "malformed",
  "yanked",
  "witness_disagreement",
  "witness_missing",
  "bad_index",
  "unparsable_running",
  "unknown",
]);

const CHANNELS = new Set([
  "venv",
  "linux-bundle",
  "wsl-bundle",
  "source",
  "macos-app",
  "container",
  "unknown",
]);

function clockText(ms: number): string {
  if (!ms) return "";
  const date = new Date(ms);
  const pad = (value: number) => String(value).padStart(2, "0");
  return (
    date.getFullYear() +
    "-" +
    pad(date.getMonth() + 1) +
    "-" +
    pad(date.getDate()) +
    " " +
    pad(date.getHours()) +
    ":" +
    pad(date.getMinutes())
  );
}

function ageText(seconds: number): string {
  const value = Math.max(0, Math.floor(seconds));
  if (value < 60) return updateT("update.seconds", value);
  if (value < 3600) return `${Math.floor(value / 60)}m`;
  return `${Math.floor(value / 3600)}h`;
}

function channelLabel(id: string): string {
  return CHANNELS.has(id) ? updateT("update.channel." + id) : updateT("update.channel.unknown");
}

function channelGuide(id: string): string {
  return CHANNELS.has(id)
    ? updateT("update.channelGuide." + id)
    : updateT("update.channelGuide.unknown");
}

function reasonText(reason: string): string {
  return REASONS.has(reason)
    ? updateT("update.reason." + reason)
    : updateT("update.unknownCode", reason);
}

export function UpdateCheck() {
  const alive = useAlive();
  const [status, setStatus] = useState<UpdateStatus | null>(null);
  const [failure, setFailure] = useState<Failure>("none");
  const [error, setError] = useState("");
  const [retrySeconds, setRetrySeconds] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState<number | null>(null);
  const runSeq = useRef(0);
  const abortRef = useRef<AbortController | null>(null);
  // A synchronous guard: two clicks in one tick both see the pre-render state,
  // so `busy` alone would let a double click send two POSTs.
  const busyRef = useRef(false);

  // Mount: one local status read. No release-index request happens here.
  useEffect(() => {
    const mine = ++runSeq.current;
    void (async () => {
      try {
        const next = await getUpdateStatus();
        if (!alive() || runSeq.current !== mine) return;
        if (!next) {
          setFailure("read");
          setError(updateT("update.malformed"));
          return;
        }
        setStatus(next);
      } catch (cause) {
        if (!alive() || runSeq.current !== mine) return;
        if (isUpdateAdminOnly(cause)) {
          setFailure("admin");
          return;
        }
        setFailure("read");
        setError(apiErrorText(cause));
      }
    })();
    return () => {
      runSeq.current += 1;
      abortRef.current?.abort();
    };
  }, [alive]);

  const onCheck = async (refresh: boolean) => {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setFailure("none");
    setError("");
    setRetrySeconds(null);
    const mine = ++runSeq.current;
    const controller =
      typeof AbortController === "undefined" ? null : new AbortController();
    abortRef.current = controller;
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      controller?.abort();
    }, UPDATE_CHECK_TIMEOUT_MS);
    try {
      const next = await checkForUpdates(refresh, controller?.signal);
      if (!alive() || runSeq.current !== mine) return;
      if (!next) {
        setFailure("check");
        setError(updateT("update.malformed"));
        return;
      }
      setStatus(next);
    } catch (cause) {
      if (!alive() || runSeq.current !== mine) return;
      const httpStatus = Number((cause as { status?: unknown } | null)?.status);
      if (isUpdateAdminOnly(cause)) {
        setFailure("admin");
      } else if (httpStatus === 429) {
        setFailure("rate");
        setRetrySeconds(updateRetryAfterSeconds(cause));
      } else if (timedOut) {
        setFailure("timeout");
      } else {
        setFailure("check");
        setError(apiErrorText(cause));
      }
    } finally {
      clearTimeout(timer);
      busyRef.current = false;
      if (alive() && runSeq.current === mine) setBusy(false);
    }
  };

  const copyCommand = async (command: string, index: number) => {
    if (!(await copyText(command))) return;
    if (!alive()) return;
    setCopied(index);
    setTimeout(() => {
      if (alive()) setCopied((current) => (current === index ? null : current));
    }, 1200);
  };

  const check: UpdateCheckData | null = status?.check ?? null;
  const failed = failure !== "none";
  const adminRefused = failure === "admin";
  // A failure never leaves a positive claim standing: only `up_to_date` may say
  // "this is the latest version", and never while this attempt failed.
  const currentStatus = failed ? "unknown" : check ? check.status : null;
  const previous =
    failed && check
      ? check.previous ?? {
          status: check.status,
          latest: check.latest,
          checked_at_ms: check.checked_at_ms,
        }
      : check?.previous ?? null;

  const resultText = adminRefused
    ? updateT("update.adminOnly")
    : failure === "rate"
      ? updateT(
          "update.rateLimited",
          retrySeconds != null ? ageText(retrySeconds) : updateT("update.unknown"),
        )
      : failure === "timeout"
        ? updateT("update.timedOut")
        : failure === "check"
          ? updateT("update.checkFailed", error || updateT("update.unknown"))
          : failure === "read"
            ? updateT("update.readFailed", error || updateT("update.unknown"))
            : !check
              ? updateT("update.notChecked")
              : currentStatus === "update_available"
                ? updateT("update.available", check.latest || updateT("update.unknown"))
                : currentStatus === "up_to_date"
                  ? updateT("update.upToDate")
                  : updateT("update.unknown");
  const reasonLine =
    !failed && check && check.status === "unknown" && check.reason
      ? updateT("update.reason", reasonText(check.reason))
      : "";
  const previousLine = previous
    ? updateT(
        "update.previous",
        previous.latest || updateT("update.unknown"),
        previous.checked_at_ms ? clockText(previous.checked_at_ms) : updateT("update.unknown"),
      )
    : "";
  const trustLine =
    check?.trust
      ? `${updateT("update.trust." + check.trust.integrity)} · ${updateT("update.authorship")}`
      : "";
  const channelId = status?.channel.id || "unknown";
  const current = status?.running || "—";
  const latest = check?.latest || "—";
  const checkedLine = check?.checked_at_ms
    ? clockText(check.checked_at_ms)
    : updateT("update.notChecked");
  const cacheLine = check
    ? check.cached
      ? updateT("update.cachedAgo", ageText(check.age_s))
      : updateT("update.noCache")
    : updateT("update.notChecked");
  const state = failed ? "unknown" : !check ? "never" : check.status;
  const refusalCommands = status?.channel.refusal_command || [];

  return (
    <div
      class="cust-row update-check"
      data-update-card="1"
      data-update-state={state}
      data-update-failure={failure}
    >
      <div class="info">
        <div class="nm">{updateT("update.title")}</div>
        <div class="ds">{updateT("update.desc")}</div>
        <div class="ds update-check-meta">
          {updateT("update.current")}: {current} · {updateT("update.channel")}:{" "}
          {channelLabel(channelId)} · {updateT("update.latest")}: {latest}
        </div>
        <div class="ds">
          {updateT("update.checkedAt")}: {checkedLine} · {updateT("update.cache")}: {cacheLine}
        </div>
        <div class={"ds update-check-result state-" + state}>{resultText}</div>
        {reasonLine ? <div class="ds update-check-reason">{reasonLine}</div> : null}
        {previousLine ? <div class="ds update-check-previous">{previousLine}</div> : null}
        {trustLine ? <div class="ds update-check-trust">{trustLine}</div> : null}
        {refusalCommands.length ? (
          <div class="ds update-check-refusal">
            <div>{updateT("update.refusal")}</div>
            <div class="ds">{updateT("update.manualStep")}</div>
            {refusalCommands.map((command, index) => (
              <div class="standard-readiness-command" key={command}>
                <code aria-label={updateT("update.manualStep")}>{command}</code>
                <button
                  type="button"
                  class="outline-btn small"
                  onClick={() => void copyCommand(command, index)}
                >
                  {copied === index ? updateT("update.copied") : updateT("update.copy")}
                </button>
              </div>
            ))}
          </div>
        ) : null}
        <div class="ds update-check-note">{updateT("update.only")}</div>
        <details class="update-check-guidance">
          <summary>{updateT("update.guidance")}</summary>
          <p class="ds">{updateT("update.guidanceBody")}</p>
          <p class="ds">{channelGuide(channelId)}</p>
        </details>
      </div>
      {adminRefused ? null : (
        <div class="update-check-actions">
          <button
            type="button"
            class="outline-btn small"
            data-action="update-check"
            disabled={busy}
            onClick={() => void onCheck(false)}
          >
            {busy ? updateT("update.checking") : updateT("update.check")}
          </button>
          <button
            type="button"
            class="outline-btn small"
            data-action="update-force"
            disabled={busy}
            onClick={() => void onCheck(true)}
          >
            {updateT("update.force")}
          </button>
        </div>
      )}
    </div>
  );
}
