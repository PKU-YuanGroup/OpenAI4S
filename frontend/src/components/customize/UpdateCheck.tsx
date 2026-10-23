import { useEffect, useRef, useState } from "preact/hooks";
import { LANG } from "../../i18n";
import {
  apiErrorText,
  readUpdateStatus,
  requestUpdateCheck,
  updateRetryAfterAt,
  type UpdateCheck as UpdateCheckData,
  type UpdateStatus,
  type UpdateCheckStatus,
} from "../../features/customize/api";
import { useAlive } from "./use-timer-lease";

/**
 * U2 / §1 U1: an explicit "check for updates" card for General settings.
 *
 * The local state (`GET /updates/status`) is read when the card mounts; it is
 * a read-only projection of the daemon's own version, channel and cached
 * answer and contacts nobody. The release index is contacted only by the
 * user's click (`POST /updates/check`). There is deliberately no download,
 * install, restart, one-click upgrade or polling anywhere on this surface —
 * this version only reports and points at the maintenance instructions.
 *
 * A failed check is never rendered as "up to date": the current line becomes
 * "could not confirm" and the earlier answer is shown separately, with the
 * time it was actually read.
 */
const UPDATE_CHECK_TIMEOUT_MS = 30_000;

const COPY = {
  en: {
    title: "Check for updates",
    desc: "This version only checks for a newer release and points at the maintenance instructions; it does not download, install, restart or upgrade anything, and it never checks on its own.",
    only: "This version only provides checking and guidance.",
    check: "Check for updates",
    checking: "Checking…",
    current: "Current version",
    latest: "Latest known version",
    channel: "Install channel",
    checkedAt: "Last checked",
    cache: "Cache",
    cachedAgo: "from the local cache ({0} ago)",
    fresh: "just read from the release index",
    notChecked: "Not checked yet",
    available: "A newer version is available: {0}",
    upToDate: "This is the latest version",
    cannotConfirm: "Could not confirm whether a newer version exists",
    lastKnown: "Last known {0} ({1})",
    reason: "Reason: {0}",
    trust: "Integrity: {0} · Authorship: {1}",
    adminOnly:
      "Update checks are limited to administrators on this instance; ask an administrator to check.",
    rateLimited: "Too many checks; try again after {0}.",
    timedOut: "The check timed out, so whether a newer version exists is unknown.",
    readFailed: "Could not read the local update state: {0}",
    checkFailed: "The check failed: {0}",
    guidance: "Installation instructions",
    guidanceBody:
      "Installation and upgrades are performed by the maintenance channel of this installation. This card only reports what the configured release index says; it never downloads or runs anything.",
    unknown: "unknown",
    never: "—",
    channelVenv: "Python environment (venv)",
    channelLinuxBundle: "Linux bundle",
    channelWslBundle: "WSL bundle",
    channelSource: "Source checkout",
    channelMacosApp: "macOS app",
    channelContainer: "Container image",
    channelUnknown: "Unknown channel",
    reasonOk: "the release index answered",
    reasonOfflinePin: "the daemon is pinned offline by configuration",
    reasonNetworkDisabled: "network access is disabled for this daemon",
    reasonEgressBlocked: "the daemon's egress policy blocked the request",
    reasonSsrfBlocked: "the request was blocked by the SSRF guard",
    reasonUnreachable: "the release index could not be reached",
    reasonTimeout: "the release index did not answer in time",
    reasonHttpError: "the release index returned an error",
    reasonTooLarge: "the release index answered with more data than allowed",
    reasonMalformed: "the release index answer could not be read",
    reasonYanked: "the published release was withdrawn",
    reasonWitnessDisagreement: "the two release witnesses disagreed",
    reasonWitnessMissing: "only one release witness could be read",
    reasonBadIndex: "the configured release index is invalid",
    reasonUnparsableRunning: "the running version could not be parsed",
    reasonCode: "reason code: {0}",
  },
  zh: {
    title: "检查更新",
    desc: "本版本只检查是否有新版本，并指向维护说明；不下载、不安装、不重启、不在原位升级，也不会自行检查。",
    only: "本版本只提供检查与指引。",
    check: "检查更新",
    checking: "正在检查…",
    current: "当前版本",
    latest: "最近已知版本",
    channel: "安装渠道",
    checkedAt: "检查时间",
    cache: "缓存",
    cachedAgo: "来自本地缓存（{0}前）",
    fresh: "刚刚从发布索引读取",
    notChecked: "尚未检查",
    available: "有新版本可用：{0}",
    upToDate: "已是最新版本",
    cannotConfirm: "无法确认是否有新版本",
    lastKnown: "上次已知 {0}（{1}）",
    reason: "原因：{0}",
    trust: "完整性：{0} · 作者签名：{1}",
    adminOnly: "本实例的更新检查仅限管理员；请联系管理员检查。",
    rateLimited: "检查过于频繁；请在 {0} 之后重试。",
    timedOut: "检查超时，因此无法确认是否有新版本。",
    readFailed: "无法读取本地更新状态：{0}",
    checkFailed: "检查失败：{0}",
    guidance: "安装说明",
    guidanceBody:
      "安装与升级由本安装的维护渠道执行。本卡片只报告所配置发布索引的说法，绝不下载或运行任何东西。",
    unknown: "未知",
    never: "—",
    channelVenv: "Python 环境（venv）",
    channelLinuxBundle: "Linux 安装包",
    channelWslBundle: "WSL 安装包",
    channelSource: "源码检出",
    channelMacosApp: "macOS 应用",
    channelContainer: "容器镜像",
    channelUnknown: "未知渠道",
    reasonOk: "发布索引已答复",
    reasonOfflinePin: "该 daemon 被配置为离线固定",
    reasonNetworkDisabled: "该 daemon 禁用了网络",
    reasonEgressBlocked: "该 daemon 的外联策略阻止了请求",
    reasonSsrfBlocked: "请求被 SSRF 防护阻止",
    reasonUnreachable: "无法联系发布索引",
    reasonTimeout: "发布索引未及时答复",
    reasonHttpError: "发布索引返回错误",
    reasonTooLarge: "发布索引答复超出大小限制",
    reasonMalformed: "发布索引答复无法解析",
    reasonYanked: "已发布的版本被撤回",
    reasonWitnessDisagreement: "两个发布见证不一致",
    reasonWitnessMissing: "只能读取到一个发布见证",
    reasonBadIndex: "配置的发布索引无效",
    reasonUnparsableRunning: "无法解析当前运行的版本",
    reasonCode: "原因代码：{0}",
  },
} as const;

type Copy = { [K in keyof (typeof COPY)["en"]]: string };

const CHANNEL_COPY: Record<string, keyof Copy> = {
  venv: "channelVenv",
  "linux-bundle": "channelLinuxBundle",
  "wsl-bundle": "channelWslBundle",
  source: "channelSource",
  "macos-app": "channelMacosApp",
  container: "channelContainer",
  unknown: "channelUnknown",
};

const REASON_COPY: Record<string, keyof Copy> = {
  ok: "reasonOk",
  offline_pin: "reasonOfflinePin",
  network_disabled: "reasonNetworkDisabled",
  egress_blocked: "reasonEgressBlocked",
  ssrf_blocked: "reasonSsrfBlocked",
  unreachable: "reasonUnreachable",
  timeout: "reasonTimeout",
  http_error: "reasonHttpError",
  too_large: "reasonTooLarge",
  malformed: "reasonMalformed",
  yanked: "reasonYanked",
  witness_disagreement: "reasonWitnessDisagreement",
  witness_missing: "reasonWitnessMissing",
  bad_index: "reasonBadIndex",
  unparsable_running: "reasonUnparsableRunning",
};

type Failure = "none" | "read" | "check" | "admin" | "rate" | "timeout";

function fill(text: string, ...args: Array<string | number>): string {
  return text.replace(/\{(\d+)\}/g, (match, index) =>
    args[Number(index)] != null ? String(args[Number(index)]) : match,
  );
}

function clockText(ms: number | null | undefined): string {
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

function ageText(seconds: number | null): string {
  if (seconds == null) return "";
  const s = Math.max(0, Math.floor(seconds));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  return `${Math.floor(s / 3600)}h`;
}

function channelText(copy: Copy, id: string): string {
  const key = CHANNEL_COPY[id];
  return key ? copy[key] : id || copy.channelUnknown;
}

function reasonText(copy: Copy, reason: string): string {
  if (!reason) return "";
  const known = REASON_COPY[reason];
  if (known) return copy[known];
  return fill(copy.reasonCode, reason.slice(0, 64));
}

export function UpdateCheck() {
  const copy: Copy = LANG === "zh" ? COPY.zh : COPY.en;
  const alive = useAlive();
  const [status, setStatus] = useState<UpdateStatus | null>(null);
  const [error, setError] = useState("");
  const [failure, setFailure] = useState<Failure>("none");
  const [retryAt, setRetryAt] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const run = useRef(0);
  const abort = useRef<AbortController | null>(null);

  // Mount: read the daemon's local projection only. No release index, no POST.
  useEffect(() => {
    const mine = ++run.current;
    void (async () => {
      try {
        const next = await readUpdateStatus();
        if (!alive() || run.current !== mine) return;
        setStatus(next);
      } catch (cause) {
        if (!alive() || run.current !== mine) return;
        setError(apiErrorText(cause));
        setFailure("read");
      }
    })();
    return () => {
      run.current += 1;
      abort.current?.abort();
    };
  }, [alive]);

  const onCheck = async () => {
    if (busy) return;
    setBusy(true);
    setError("");
    setFailure("none");
    setRetryAt(null);
    const mine = ++run.current;
    const controller =
      typeof AbortController === "undefined" ? null : new AbortController();
    abort.current = controller;
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      controller?.abort();
    }, UPDATE_CHECK_TIMEOUT_MS);
    try {
      const next = await requestUpdateCheck(controller?.signal);
      if (!alive() || run.current !== mine) return;
      setStatus(next);
    } catch (cause) {
      if (!alive() || run.current !== mine) return;
      const httpStatus = Number((cause as { status?: unknown } | null)?.status);
      if (httpStatus === 403) {
        setFailure("admin");
        setError(apiErrorText(cause));
      } else if (httpStatus === 429) {
        setFailure("rate");
        setRetryAt(updateRetryAfterAt(cause));
      } else if (timedOut) {
        setFailure("timeout");
      } else {
        setFailure("check");
        setError(apiErrorText(cause));
      }
    } finally {
      clearTimeout(timer);
      if (alive() && run.current === mine) setBusy(false);
    }
  };

  const check: UpdateCheckData | null = status?.check ?? null;
  const failed = failure !== "none";
  // A failed check may not leave a claim standing: the "current" line becomes
  // "could not confirm", and the previous answer is shown separately below.
  const currentStatus: UpdateCheckStatus | null = failed
    ? "unknown"
    : check
      ? check.status
      : null;
  const previous = failed
    ? check?.previous ??
      (check
        ? {
            status: check.status,
            latest: check.latest || check.running,
            checked_at_ms: check.checked_at_ms,
          }
        : null)
    : check?.previous ?? null;

  const currentVersion = check?.running || copy.unknown;
  const channel = channelText(copy, status?.channelId || "");
  const cacheLine = check
    ? check.cached
      ? fill(copy.cachedAgo, ageText(check.age_s))
      : copy.fresh
    : copy.notChecked;
  const checkedLine = check?.checked_at_ms ? clockText(check.checked_at_ms) : copy.never;

  const resultLine = !check
    ? copy.notChecked
    : currentStatus === "update_available"
      ? fill(copy.available, check.latest || copy.unknown)
      : currentStatus === "up_to_date"
        ? copy.upToDate
        : copy.cannotConfirm;
  // Only the *current* answer's reason. After a failed check the old check's
  // reason would describe a different attempt.
  const reasonLine =
    !failed && check && currentStatus === "unknown" && check.reason
      ? fill(copy.reason, reasonText(copy, check.reason))
      : "";
  const failureLine =
    failure === "admin"
      ? copy.adminOnly
      : failure === "rate"
        ? fill(copy.rateLimited, retryAt ? clockText(retryAt) : copy.unknown)
        : failure === "timeout"
          ? copy.timedOut
          : failure === "read"
            ? fill(copy.readFailed, error || copy.unknown)
            : failure === "check"
              ? fill(copy.checkFailed, error || copy.unknown)
              : "";
  const previousLine = previous
    ? fill(
        copy.lastKnown,
        previous.latest || copy.unknown,
        clockText(previous.checked_at_ms) || copy.never,
      )
    : "";
  const trustLine =
    check?.trust && (check.trust.integrity || check.trust.authorship)
      ? fill(
          copy.trust,
          check.trust.integrity || copy.unknown,
          check.trust.authorship || copy.unknown,
        )
      : "";
  const state = failed
    ? "unknown"
    : currentStatus === "update_available"
      ? "available"
      : currentStatus === "up_to_date"
        ? "up_to_date"
        : check
          ? "unknown"
          : "never";

  return (
    <div
      class="cust-row update-check"
      data-update-card="1"
      data-update-state={state}
      data-update-failure={failure}
    >
      <div class="info">
        <div class="nm">{copy.title}</div>
        <div class="ds">{copy.desc}</div>
        <div class="ds update-check-meta">
          {copy.current}: {currentVersion} · {copy.latest}: {check?.latest || copy.unknown} ·{" "}
          {copy.channel}: {channel}
        </div>
        <div class="ds">
          {copy.checkedAt}: {checkedLine} · {copy.cache}: {cacheLine}
        </div>
        <div class={"ds update-check-result state-" + state}>{resultLine}</div>
        {reasonLine ? <div class="ds update-check-reason">{reasonLine}</div> : null}
        {previousLine ? <div class="ds update-check-previous">{previousLine}</div> : null}
        {failureLine ? <div class="ds update-check-error">{failureLine}</div> : null}
        {trustLine ? <div class="ds update-check-trust">{trustLine}</div> : null}
        <div class="ds update-check-note">{copy.only}</div>
        <details class="update-check-guidance">
          <summary>{copy.guidance}</summary>
          <p class="ds">{copy.guidanceBody}</p>
        </details>
      </div>
      <button
        type="button"
        class="outline-btn small"
        disabled={busy}
        data-action="update-check"
        onClick={() => void onCheck()}
      >
        {busy ? copy.checking : copy.check}
      </button>
    </div>
  );
}
