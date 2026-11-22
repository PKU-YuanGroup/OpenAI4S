import { useEffect, useRef, useState } from "preact/hooks";
import { t } from "../../i18n";
import { copyLookup } from "../../i18n/copy";
import { api, apiErrorText } from "../../features/customize/api";
import { refreshCustTab } from "../../features/customize/actions";
import { nestedEditor } from "../../features/customize/state";
import {
  asList,
  asString,
  confirmAction,
  dropEnvSnapshots,
  hint,
} from "../../features/customize/host";
import { currentId } from "../../stores/session";
import { standardProfileReadiness } from "../../stores/customize";
import { _jobPoll } from "../../stores/ui";
import type { StandardReadiness } from "../../features/customize/environment";
import {
  installOutcome,
  readRuntimes,
  rPackagesNote,
  runtimeSummary,
  runtimeT,
  type InstallOutcome,
} from "../../features/customize/runtimes";
import { refreshEnvironmentStatus } from "../../features/send/environment";
import { clearLeaseTimeout, scheduleTimeout } from "../../features/customize/timers";
import { useAlive, useTimerLease } from "./use-timer-lease";
import { useTabRead } from "./hooks";
import { markCustomizeLoaded } from "../../features/customize/load";
import { EnvironmentCard } from "../onboarding/ReadinessPanel";
import { CustRow, Hdr, InfoRow } from "./ui";

/** Feature-local: the generated `i18n/en.ts` / `zh.ts` are byte-checked extracts. */
const jobsT = copyLookup({
  zh: {
    "jobsRead.loading": "正在读取任务列表…",
    "jobsRead.unavailable": "任务列表暂不可用：{0}",
    "jobsRead.stale": "无法刷新任务列表：{0}",
    "jobsRead.lastConfirmed": "上次确认 {0}",
    "jobsRead.retryIn": "{0} 秒后自动重读。",
    "jobsRead.retry": "重试",
    "jobsRead.checking": "正在读取…",
    "jobsRead.confirmFirst": "状态未确认，重新读取后才能取消。",
    "jobsRead.badReply": "返回的不是任务列表",
  },
  en: {
    "jobsRead.loading": "Loading jobs…",
    "jobsRead.unavailable": "The job list is unavailable: {0}",
    "jobsRead.stale": "Could not refresh the job list: {0}",
    "jobsRead.lastConfirmed": "Last confirmed {0}",
    "jobsRead.retryIn": "Checking again in {0} s.",
    "jobsRead.retry": "Retry",
    "jobsRead.checking": "Checking…",
    "jobsRead.confirmFirst": "Status not confirmed; read it again before cancelling.",
    "jobsRead.badReply": "the reply was not a job list",
  },
});

/**
 * What the job list shows: `loading` before the first answer, `confirmed`
 * once a read succeeded, `stale` when a later read failed (the last
 * confirmed rows stay, stamped), `unavailable` when no read has succeeded.
 */
type JobsRead = {
  phase: "loading" | "confirmed" | "stale" | "unavailable";
  confirmedAt: number | null;
  reason: string;
  /** Seconds until the automatic re-read; 0 when none is scheduled. */
  retryIn: number;
  reading: boolean;
};

/**
 * A failed read while the last confirmed list had a running or queued job is
 * read again after 2 s, then 4 s; the third consecutive failure leaves it to
 * Retry. Only the GET is ever repeated: submit and cancel stay explicit.
 */
const JOB_RETRY_MS = [2000, 4000];

const isActiveJob = (j: Record<string, unknown>) =>
  j.status === "running" || j.status === "queued";

/** The `jobs` array of a reply, or null when the reply is not a job list. */
function jobList(reply: Record<string, unknown>): Record<string, unknown>[] | null {
  const list: unknown = reply.jobs;
  if (!Array.isArray(list)) return null;
  const ok = list.every((j: unknown) => {
    const row = j && typeof j === "object" && !Array.isArray(j) ? (j as Record<string, unknown>) : null;
    return !!row && typeof row.id === "string" && !!row.id && typeof row.status === "string";
  });
  return ok ? (list as Record<string, unknown>[]) : null;
}

const pad2 = (n: number) => String(n).padStart(2, "0");
const clock = (at: number | null) => {
  if (at == null) return "";
  const d = new Date(at);
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
};

export function ComputeTab() {
  const alive = useAlive();
  const lease = useTimerLease();
  const [err, setErr] = useState<string | null>(null);
  const [gpu, setGpu] = useState<Record<string, unknown>>({ available: false });
  const [host, setHost] = useState<Record<string, unknown>>({});
  const [envs, setEnvs] = useState<Record<string, unknown>[]>([]);
  const [remote, setRemote] = useState<Record<string, unknown> | null>(null);
  const [jobs, setJobs] = useState<Record<string, unknown>[]>([]);
  const [jobsRead, setJobsRead] = useState<JobsRead>({
    phase: "loading",
    confirmedAt: null,
    reason: "",
    retryIn: 0,
    reading: false,
  });
  const [pkg, setPkg] = useState("");
  const [installing, setInstalling] = useState(false);
  // The environment listing (`runtimes`: what Python and R resolve to) for the
  // open session, or the defaults when none is open.
  const [listing, setListing] = useState<Record<string, unknown> | null>(null);
  const [listedFor, setListedFor] = useState<string | null>(null);
  const [installed, setInstalled] = useState<InstallOutcome | null>(null);
  const [jobKind, setJobKind] = useState("bash");
  const [jobCmd, setJobCmd] = useState("");
  const [jobBusy, setJobBusy] = useState(false);
  const [alias, setAlias] = useState("");
  // One poll chain: each read supersedes the one before it and owns the next
  // tick. Submit and Cancel read too, and every read used to arm a timer of
  // its own, so N chains polled at once and answered out of order.
  // `reading` is the seq of the read in flight (0: none), `failures` the
  // consecutive failed reads, `active` whether the last confirmed list had a
  // running or queued job.
  const jobPoll = useRef<{
    seq: number;
    timer: ReturnType<typeof setTimeout> | 0;
    reading: number;
    failures: number;
    active: boolean;
  }>({ seq: 0, timer: 0, reading: 0, failures: 0, active: false });

  const armJobPoll = (ms: number) => {
    const poll = jobPoll.current;
    poll.timer = scheduleTimeout(
      lease,
      () => {
        poll.timer = 0;
        void loadJobs();
      },
      ms,
    );
    _jobPoll.value = poll.timer;
  };

  // A failed read used to become an empty list and stop the poll: a short
  // 503 looked like every job had gone, and so had their Output buttons.
  const loadJobs = async () => {
    const poll = jobPoll.current;
    const seq = ++poll.seq;
    clearLeaseTimeout(lease, poll.timer);
    poll.timer = 0;
    poll.reading = seq;
    setJobsRead((r) => ({ ...r, retryIn: 0, reading: true }));
    let list: Record<string, unknown>[] | null = null;
    let reason = "";
    try {
      list = jobList(await api("/compute/jobs"));
      if (!list) reason = jobsT("jobsRead.badReply");
    } catch (e) {
      reason = apiErrorText(e);
    }
    if (!alive() || seq !== poll.seq) return;
    poll.reading = 0;
    if (list) {
      poll.failures = 0;
      poll.active = list.some(isActiveJob);
      setJobs(list);
      setJobsRead({ phase: "confirmed", confirmedAt: Date.now(), reason: "", retryIn: 0, reading: false });
      if (poll.active) armJobPoll(1500);
      return;
    }
    poll.failures += 1;
    const delay = poll.active ? JOB_RETRY_MS[poll.failures - 1] || 0 : 0;
    setJobsRead((r) => ({
      phase: r.confirmedAt == null ? "unavailable" : "stale",
      confirmedAt: r.confirmedAt,
      reason,
      retryIn: delay / 1000,
      reading: false,
    }));
    if (delay) armJobPoll(delay);
  };

  /** Retry: a click while a read is in flight joins that read. */
  const retryJobs = () => {
    if (jobPoll.current.reading) return;
    void loadJobs();
  };

  useTabRead(
    "compute",
    async (current) => {
      const session = currentId.value;
      const [g, env, h, list] = await Promise.all([
        api("/compute/gpu").catch(() => ({ available: false })),
        refreshEnvironmentStatus().then(
          (status) => (status as Record<string, unknown> | null) || { environments: [] },
        ),
        api("/compute/local/hostinfo").catch(() => ({})),
        api(session ? `/frames/${session}/environments` : "/environments").catch(() => null),
      ]);
      if (!current()) return;
      setGpu(g);
      setHost(h);
      setEnvs(asList(env && env.environments) as Record<string, unknown>[]);
      setListing(list);
      setListedFor(session);
      markCustomizeLoaded();
      try {
        const info = await api("/compute/remote");
        if (current()) setRemote(info);
      } catch {
        /* original swallowed */
      }
      if (current()) await loadJobs();
    },
    setErr,
  );
  useEffect(
    () => () => {
      _jobPoll.value = null;
    },
    [],
  );

  if (err) return <div>{err}</div>;

  const readiness = standardProfileReadiness.value as StandardReadiness | null;
  const hosts = remote ? (asList(remote.hosts) as Record<string, unknown>[]) : [];
  const taken = new Set(hosts.map((h) => h.alias));
  const avail = (remote ? asList(remote.available_aliases) : []).filter(
    (a) => !taken.has(a),
  );
  const runtimes = readRuntimes(listing);
  // The daemon's own interpreter is what this tab installs into; discovery
  // lists it as the one built-in environment that is not a conda env.
  const builtinEnv =
    asString(
      (asList(listing && listing.environments) as Record<string, unknown>[]).find(
        (e) => e.builtin === true && e.is_conda === false,
      )?.name,
    ) || "base";

  return (
    <div>
      <Hdr title={t("cust.compute.title")} sub={t("cust.compute.desc")} />
      {readiness && readiness.enabled ? (
        <EnvironmentCard
          readiness={readiness}
          action={
            <button
              type="button"
              class="outline-btn small"
              onClick={() => refreshCustTab("compute")}
            >
              {t("environment.readiness.refresh")}
            </button>
          }
        />
      ) : null}
      <InfoRow
        name={t("cust.compute.host")}
        detail={t(
          "cust.compute.hostDetail",
          host.python || "?",
          host.machine || "",
          host.cpu_count || "?",
          host.ram_gb || "?",
          host.disk_free_gb || "?",
        )}
      />
      <InfoRow
        name="GPU"
        detail={
          gpu.available
            ? asString(gpu.gpu_name) || t("cust.compute.gpuAvailable")
            : t("cust.compute.gpuUnavailable")
        }
      />
      {remote ? (
        <>
          <div class="cust-row">
            <div class="info">
              <div class="nm">{t("cust.remote.title")}</div>
              <div class="ds">{t("cust.remote.desc")}</div>
            </div>
          </div>
          {hosts.map((h) => (
            <div class="cust-row" key={asString(h.alias)}>
              <div class="info">
                <div class="nm">
                  {(h.reachable ? "🟢 " : "🔴 ") + asString(h.label || h.alias)}
                  <span style={{ opacity: 0.55, fontWeight: 400 }}>
                    {" · " + asString(h.provider)}
                  </span>
                </div>
                <div class="ds">
                  {asString(h.gpus) || (h.reachable ? "" : t("cust.remote.unreachable"))}
                  <br />
                  {asList(h.capabilities).length ? (
                    <>
                      {t("cust.remote.services") + " "}
                      {asList(h.capabilities).map((cp, i) => {
                        const c = cp as Record<string, unknown>;
                        return (
                          <span
                            key={i}
                            style={{
                              display: "inline-block",
                              padding: "1px 7px",
                              margin: "3px 4px 0 0",
                              borderRadius: "8px",
                              background: "rgba(127,127,127,.18)",
                              fontSize: "11px",
                            }}
                          >
                            {asString(c.name) +
                              (c.engine ? " · " + asString(c.engine) : "") +
                              (c.verified ? " ✓" : "")}
                          </span>
                        );
                      })}
                    </>
                  ) : (
                    <span style={{ opacity: 0.6 }}>{t("cust.remote.noservices")}</span>
                  )}
                </div>
              </div>
              <button
                type="button"
                class="outline-btn small"
                onClick={async () => {
                  if (!confirmAction(t("cust.remote.confirmRemove", h.alias))) return;
                  try {
                    await api("/compute/remote/" + encodeURIComponent(asString(h.alias)), {
                      method: "DELETE",
                    });
                    refreshCustTab("compute");
                  } catch (e) {
                    hint((e as Error).message, true);
                  }
                }}
              >
                {t("common.remove")}
              </button>
            </div>
          ))}
          <div class="cust-row">
            <div class="info">
              <div class="nm">{t("cust.remote.addName")}</div>
              <div class="ds job-submit">
                <select
                  class="cust-input"
                  value={alias}
                  onChange={(e) => setAlias((e.target as HTMLSelectElement).value)}
                >
                  <option value="">
                    {avail.length ? t("cust.remote.pickAlias") : t("cust.remote.noAlias")}
                  </option>
                  {avail.map((a) => (
                    <option value={String(a)} key={String(a)}>
                      {String(a)}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  class="solid-btn small"
                  onClick={async () => {
                    if (!alias) return;
                    try {
                      const r = await api("/compute/remote", {
                        method: "POST",
                        body: JSON.stringify({ alias }),
                      });
                      hint(
                        r.reachable
                          ? t("cust.remote.added", alias, r.gpus || "")
                          : t("cust.remote.addedUnreachable", alias),
                      );
                      setAlias("");
                      refreshCustTab("compute");
                    } catch (e) {
                      hint((e as Error).message, true);
                    }
                  }}
                >
                  {t("common.add")}
                </button>
              </div>
            </div>
          </div>
        </>
      ) : null}
      {runtimes ? (
        <>
          <InfoRow
            name={runtimeT("rt.title")}
            detail={runtimeT(listedFor ? "rt.desc.session" : "rt.desc.default")}
          />
          <InfoRow name="Python" detail={runtimeSummary("python", runtimes.python)} />
          <CustRow
            name={
              "R" +
              (runtimes.r.available
                ? " · " + runtimeT(runtimes.r.selected ? "rt.r.chosen" : "rt.r.default")
                : "")
            }
            desc={
              <>
                <span class="rt-line">{runtimeSummary("r", runtimes.r)}</span>
                {rPackagesNote(runtimes.r) ? (
                  <span class="rt-line">{rPackagesNote(runtimes.r)}</span>
                ) : null}
              </>
            }
          />
        </>
      ) : null}
      {envs.map((e) => {
        // The packages the daemon tracks in its own interpreter, with the
        // versions actually installed. This row used to be the whole of the
        // tab's environment view, labelled "scientific/networking packages"
        // whether or not a networking package was among them.
        const tracked = asList(e.packages) as Record<string, unknown>[];
        const inst = tracked.filter((p) => p.installed);
        const absent = tracked.filter((p) => !p.installed);
        const names = (rows: Record<string, unknown>[], versions: boolean) =>
          rows
            .slice(0, 18)
            .map((p) => asString(p.name) + (versions && p.version ? " " + asString(p.version) : ""))
            .join(", ") + (rows.length > 18 ? " …" : "");
        return (
          <CustRow
            key={asString(e.language)}
            name={
              runtimeT("rt.packages.name") +
              (e.status === "installing" ? " · " + runtimeT("rt.packages.installing") : "")
            }
            desc={
              <>
                <span class="rt-line">
                  {runtimeT("rt.packages.detail", inst.length, tracked.length, names(inst, true))}
                </span>
                {absent.length ? (
                  <span class="rt-line">
                    {runtimeT("rt.packages.missing", names(absent, false))}
                  </span>
                ) : null}
              </>
            }
          />
        );
      })}
      <div class="cust-row">
        <div class="info">
          <div class="nm">{t("cust.compute.installExtraName")}</div>
          <div class="ds">
            <input
              class="cust-input"
              placeholder={t("cust.compute.installPlaceholder")}
              value={pkg}
              onInput={(e) => setPkg((e.target as HTMLInputElement).value)}
            />
            <button
              type="button"
              class="outline-btn small"
              disabled={installing}
              onClick={async () => {
                const pkgs = pkg.trim().split(/\s+/).filter(Boolean);
                if (!pkgs.length) return;
                setInstalling(true);
                setInstalled(null);
                const session = currentId.value;
                try {
                  const r = session
                    ? await api(`/frames/${session}/kernel/install`, {
                        method: "POST",
                        body: JSON.stringify({ packages: pkgs, restart: true }),
                      })
                    : await api(`/kernel/install`, {
                        method: "POST",
                        body: JSON.stringify({ packages: pkgs }),
                      });
                  // Said in the tab and kept there: the toast alone was gone
                  // before the package count below had moved.
                  const outcome = installOutcome(r, {
                    builtinEnv,
                    sessionOpen: !!session,
                    sessionEnv: session === listedFor && runtimes ? runtimes.python.environment : "",
                    fallbackError: t("toast.compute.installSeeLogs"),
                  });
                  if (alive()) setInstalled(outcome);
                  hint(outcome.lines[0]!, !outcome.ok);
                  if (r.ok) {
                    dropEnvSnapshots();
                    setPkg("");
                  }
                  refreshCustTab("compute");
                } catch (e) {
                  const message = runtimeT("rt.install.failed", apiErrorText(e));
                  if (alive()) setInstalled({ ok: false, lines: [message] });
                  hint(message, true);
                }
                setInstalling(false);
              }}
            >
              {installing ? t("cust.compute.installingBtn") : t("cust.compute.installBtn")}
            </button>
            <span class="rt-line">{runtimeT("rt.install.hint", builtinEnv)}</span>
            {installed ? (
              <span class={"rt-line rt-install " + (installed.ok ? "ok" : "err")} role="status">
                {installed.lines.join(" ")}
              </span>
            ) : null}
          </div>
        </div>
      </div>
      <Hdr title={t("cust.jobs.title")} sub={t("cust.jobs.desc")} />
      <div class="cust-row">
        <div class="info">
          <div class="nm">{t("cust.jobs.submitName")}</div>
          <div class="job-submit">
            <select
              class="cust-input"
              style={{ flex: "0 0 92px" }}
              value={jobKind}
              onChange={(e) => setJobKind((e.target as HTMLSelectElement).value)}
            >
              <option value="bash">bash</option>
              <option value="python">python</option>
            </select>
            <input
              class="cust-input"
              placeholder={t("cust.jobs.cmdPlaceholder")}
              value={jobCmd}
              onInput={(e) => setJobCmd((e.target as HTMLInputElement).value)}
            />
            <button
              type="button"
              class="solid-btn small"
              disabled={jobBusy}
              onClick={async () => {
                const command = jobCmd.trim();
                if (!command) return;
                setJobBusy(true);
                try {
                  await api("/compute/jobs", {
                    method: "POST",
                    body: JSON.stringify({ command, kind: jobKind }),
                  });
                  setJobCmd("");
                  await loadJobs();
                } catch (e) {
                  hint(t("toast.submitFailed", apiErrorText(e)), true);
                }
                setJobBusy(false);
              }}
            >
              {t("cust.jobs.runBtn")}
            </button>
          </div>
        </div>
      </div>
      <div class="job-list">
        {jobsRead.phase === "stale" || jobsRead.phase === "unavailable" ? (
          <div class="cust-row" role="status">
            <div class="info">
              <div class="nm">
                {jobsT(
                  jobsRead.phase === "stale" ? "jobsRead.stale" : "jobsRead.unavailable",
                  jobsRead.reason,
                )}
              </div>
              <div class="ds">
                {[
                  jobsRead.phase === "stale"
                    ? jobsT("jobsRead.lastConfirmed", clock(jobsRead.confirmedAt))
                    : "",
                  jobsRead.retryIn ? jobsT("jobsRead.retryIn", jobsRead.retryIn) : "",
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </div>
            </div>
            <button
              type="button"
              class="outline-btn small"
              disabled={jobsRead.reading}
              onClick={retryJobs}
            >
              {jobsRead.reading ? jobsT("jobsRead.checking") : jobsT("jobsRead.retry")}
            </button>
          </div>
        ) : null}
        {jobsRead.phase === "loading" ? (
          <div class="dock-empty">{jobsT("jobsRead.loading")}</div>
        ) : jobsRead.phase === "unavailable" ? null : !jobs.length ? (
          <div class="dock-empty">{t("cust.jobs.empty")}</div>
        ) : (
          jobs.map((j) => (
            <div class="cust-row" key={asString(j.id)}>
              <div class="info">
                <div class="nm">
                  <span class={"job-badge " + asString(j.status)}>{asString(j.status)}</span>{" "}
                  <span class="job-cmd">
                    {(asString(j.kind) + "  " + asString(j.command)).slice(0, 80)}
                  </span>
                </div>
                <div class="ds">
                  {(j.duration_s != null ? j.duration_s + "s" : "") +
                    (j.exit_code != null ? " · exit " + j.exit_code : "") +
                    (j.truncated
                      ? " " + t("cust.jobs.dropped", (j.dropped_bytes || 0).toLocaleString())
                      : "")}
                  {jobsRead.phase === "stale" && isActiveJob(j)
                    ? " · " + jobsT("jobsRead.confirmFirst")
                    : ""}
                </div>
              </div>
              <button
                type="button"
                class="outline-btn small"
                onClick={() => {
                  nestedEditor.value = { kind: "job", id: asString(j.id) };
                }}
              >
                {t("cust.jobs.viewOutput")}
              </button>
              {/* Cancel only on a state this read confirmed: a stale row is read again first. */}
              {jobsRead.phase === "confirmed" && isActiveJob(j) ? (
                <button
                  type="button"
                  class="outline-btn small"
                  onClick={async () => {
                    // A failed cancel is a non-2xx now (404 gone, 500 still
                    // running): say so, and re-read either way so the row
                    // shows the job's real state rather than a stale Cancel.
                    try {
                      await api(`/compute/jobs/${j.id}/cancel`, { method: "POST" });
                    } catch (e) {
                      hint(t("toast.failed", apiErrorText(e)), true);
                    }
                    await loadJobs();
                  }}
                >
                  {t("common.cancel")}
                </button>
              ) : null}
            </div>
          ))
        )}
      </div>
    </div>
  );
}
