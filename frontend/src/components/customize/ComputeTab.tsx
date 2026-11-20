import { useEffect, useRef, useState } from "preact/hooks";
import { LANG, t } from "../../i18n";
import { copyLookup } from "../../i18n/copy";
import { api, ApiError, apiErrorText } from "../../features/customize/api";
import { isTeamMember } from "../../features/chrome/team-identity";
import { refreshCustTab } from "../../features/customize/actions";
import { nestedEditor } from "../../features/customize/state";
import {
  asList,
  asString,
  confirmAction,
  dropEnvSnapshots,
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
import { ActionStatus, CustRow, Hdr, InfoRow, type ActionNote } from "./ui";

/** The read-only check `POST /compute/remote` runs (gateway `_probe_remote_gpu`). */
export function remoteProbeCommand(alias: string): string {
  return `ssh -o ConnectTimeout=8 -o BatchMode=yes ${alias} nvidia-smi --query-gpu=name --format=csv,noheader`;
}

/** Where the compute providers that exist today are described. */
export const COMPUTE_GUIDE_URL = "https://github.com/PKU-YuanGroup/OpenAI4S/blob/main/docs/compute.md";

/** Feature-local: the generated `i18n/en.ts` / `zh.ts` are byte-checked extracts. */
const computeCopy = copyLookup({
  zh: {
    "compute.memberNote":
      "远程算力、安装依赖包和后台作业由本服务器的管理员管理；这里显示的是本机环境的只读信息。",
    // SCI-18: the generated copy offered "Modal/SSH remote compute"; Modal does
    // not exist, and nothing hands back a CPU approximation for GPU work.
    "compute.gpuNone":
      "本机没有 NVIDIA GPU。需要 GPU 的技能在本机会失败；内置的结构预测与突变打分在没有远程 GPU 时直接报错，不会用近似结果代替。",
    "compute.gpuNoneSetup": "可在下方添加带 GPU 的 SSH 主机，或使用 NVIDIA NIM。",
    "compute.otherTitle": "其他远程算力",
    "compute.nim":
      "NVIDIA NIM：代理可以把作业发到 NVIDIA 托管的 NIM 服务，或本机 GPU 上的 NIM 容器。需要 Docker，以及守护进程环境里的 NVIDIA_API_KEY（托管）或 NGC_API_KEY（容器）；此页无需设置。",
    "compute.slurm": "Slurm 集群：用 `openai4s cluster` 命令提交并跟踪批处理作业（submit、list、cancel、logs）。",
    "compute.guide": "算力文档（docs/compute.md）",
    // SCI-19: what Add does before it is clicked.
    "remote.desc": "把 ~/.ssh/config 里的主机用作远程 GPU 算力。在检查之前，OpenAI4S 并不知道哪台主机有 GPU。",
    "remote.addWhat": "「添加」只对主机做一次只读检查：{0}。不会安装任何东西。",
    "remote.addNeeds":
      "主机需要能免密码登录（密钥或 ssh-agent），并有带 nvidia-smi 的 NVIDIA GPU。检查失败的主机仍会列出，标为不可达。",
    "remote.addLater":
      "模型服务要等任务需要时才部署：代理会在主机上执行可见的 ssh 步骤，每一步都和其他命令一样受你的权限规则约束。主机列表保存在 OpenAI4S 数据目录的 remote_compute.json 中；「移除」只是在这里忘掉该主机，不会改动主机本身。",
    "remote.addedUnreachable":
      "已添加 {0}，但检查没有拿到 GPU 列表（没有应答、需要输入密码，或没有 nvidia-smi）。它会标为不可达；如果它不是 GPU 主机，可以移除。",
    "remote.host": "<主机>",
    // M-27: where a job runs and where its output goes, which nothing said.
    "jobs.desc":
      "在本机以守护进程的用户身份、于 OpenAI4S 数据目录的 compute-jobs 文件夹中后台运行较长的 bash 命令或 Python 脚本，可查看输出、可取消。作业不属于任何项目或会话：输出（最后 200 KB）只显示在这里，不会存为项目文件，守护进程停止时也会丢失。要让结果随项目保存，请在会话里运行。",
  },
  en: {
    "compute.memberNote":
      "Remote compute, package installs and daemon jobs are managed by this server's administrator. This page shows the local environment, read-only.",
    "compute.gpuNone":
      "None on this machine. Skills that need an NVIDIA GPU fail here, and the built-in structure prediction and mutation scoring refuse without a remote GPU rather than return an approximation.",
    "compute.gpuNoneSetup": "Add an SSH host with a GPU below, or use NVIDIA NIM.",
    "compute.otherTitle": "Other remote compute",
    "compute.nim":
      "NVIDIA NIM: the agent can send jobs to NVIDIA's hosted NIM service or to a NIM container on a local GPU. It needs Docker and, in the daemon's environment, NVIDIA_API_KEY (hosted) or NGC_API_KEY (container); there is nothing to set up on this page.",
    "compute.slurm":
      "Slurm clusters: submit and follow batch jobs with the `openai4s cluster` command (submit, list, cancel, logs).",
    "compute.guide": "Compute guide (docs/compute.md)",
    // SCI-19: what Add does before it is clicked.
    "remote.desc":
      "Use a host from your ~/.ssh/config as remote GPU compute. OpenAI4S does not know which hosts have a GPU until it checks one.",
    "remote.addWhat": "Add checks the host once, read-only: {0}. Nothing is installed.",
    "remote.addNeeds":
      "The host needs a login that works without a password prompt (a key or ssh-agent) and an NVIDIA GPU with nvidia-smi. A host that fails the check is still listed, marked unreachable.",
    "remote.addLater":
      "Model services are set up later, only when a task needs one: the agent runs visible ssh steps on the host, each going through your permission rules like any other command. The host list is kept in remote_compute.json in the OpenAI4S data directory; Remove forgets a host here and does not change it.",
    "remote.addedUnreachable":
      "Added {0}, but the check got no GPU list (no answer, a password prompt, or no nvidia-smi). It is listed as unreachable; remove it if it is not a GPU host.",
    "remote.host": "<host>",
    // M-27: where a job runs and where its output goes, which nothing said.
    "jobs.desc":
      "Run a long bash command or Python script in the background on this machine, as the daemon's user, in the compute-jobs folder of the OpenAI4S data directory; watch its output and cancel it. A job belongs to no project or session: its output (the last 200 KB) is shown here only, is not saved as a project file, and is lost if the daemon stops. To keep results with a project, run the work from a session.",
  },
});

export function ComputeTab() {
  const alive = useAlive();
  const lease = useTimerLease();
  const [err, setErr] = useState<string | null>(null);
  const [gpu, setGpu] = useState<Record<string, unknown>>({ available: false });
  const [host, setHost] = useState<Record<string, unknown>>({});
  const [envs, setEnvs] = useState<Record<string, unknown>[]>([]);
  const [remote, setRemote] = useState<Record<string, unknown> | null>(null);
  const [jobs, setJobs] = useState<Record<string, unknown>[]>([]);
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
  const [adding, setAdding] = useState(false);
  // SCI-04: what adding/removing a host and submitting/cancelling a job
  // answered, beside those controls rather than on the session status line.
  const [remoteNote, setRemoteNote] = useState<ActionNote>(null);
  const [jobNote, setJobNote] = useState<ActionNote>(null);
  // Team mode: the SSH host list, remote registration, package installs and
  // daemon jobs are the operator's (403 `admin_only`). A member's tab is the
  // local facts, and it does not ask for what it will be refused.
  const [operatorOnly, setOperatorOnly] = useState(false);
  // One poll chain: each read supersedes the one before it and owns the next
  // tick. Submit and Cancel read too, and every read used to arm a timer of
  // its own, so N chains polled at once and answered out of order.
  const jobPoll = useRef<{ seq: number; timer: ReturnType<typeof setTimeout> | 0 }>({
    seq: 0,
    timer: 0,
  });

  const loadJobs = async () => {
    const poll = jobPoll.current;
    const seq = ++poll.seq;
    clearLeaseTimeout(lease, poll.timer);
    poll.timer = 0;
    let d: Record<string, unknown>;
    try {
      d = await api("/compute/jobs");
    } catch {
      d = { jobs: [] };
    }
    if (!alive() || seq !== poll.seq) return;
    const list = asList(d.jobs) as Record<string, unknown>[];
    setJobs(list);
    const anyRunning = list.some(
      (j) => j.status === "running" || j.status === "queued",
    );
    if (anyRunning) {
      poll.timer = scheduleTimeout(
        lease,
        () => {
          poll.timer = 0;
          void loadJobs();
        },
        1500,
      );
      _jobPoll.value = poll.timer;
    }
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
      if (isTeamMember()) return;
      try {
        const info = await api("/compute/remote");
        if (current()) setRemote(info);
      } catch (e) {
        if (e instanceof ApiError && e.status === 403 && e.code === "admin_only") {
          if (current()) setOperatorOnly(true);
          return;
        }
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

  const member = operatorOnly || isTeamMember();
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
            : computeCopy("compute.gpuNone") +
              (member ? "" : (LANG === "en" ? " " : "") + computeCopy("compute.gpuNoneSetup"))
        }
      />
      {remote ? (
        <>
          <div class="cust-row">
            <div class="info">
              <div class="nm">{t("cust.remote.title")}</div>
              <div class="ds">{computeCopy("remote.desc")}</div>
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
                  if (!(await confirmAction(t("cust.remote.confirmRemove", h.alias)))) return;
                  try {
                    await api("/compute/remote/" + encodeURIComponent(asString(h.alias)), {
                      method: "DELETE",
                    });
                    setRemoteNote(null);
                    refreshCustTab("compute");
                  } catch (e) {
                    setRemoteNote({ text: apiErrorText(e), err: true });
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
                  // SCI-19 / SCI-05: nothing to add until a host is picked.
                  disabled={!alias || adding}
                  onClick={async () => {
                    if (!alias || adding) return;
                    setAdding(true);
                    setRemoteNote(null);
                    try {
                      const r = await api("/compute/remote", {
                        method: "POST",
                        body: JSON.stringify({ alias }),
                      });
                      setRemoteNote({
                        text: r.reachable
                          ? t("cust.remote.added", alias, r.gpus || "")
                          : computeCopy("remote.addedUnreachable", alias),
                        err: !r.reachable,
                      });
                      setAlias("");
                      refreshCustTab("compute");
                    } catch (e) {
                      setRemoteNote({ text: apiErrorText(e), err: true });
                    }
                    setAdding(false);
                  }}
                >
                  {adding ? t("cust.remote.testing") : t("common.add")}
                </button>
              </div>
              <div class="ds remote-add-what" data-remote-add-what="">
                <span class="rt-line">
                  {computeCopy(
                    "remote.addWhat",
                    remoteProbeCommand(alias || computeCopy("remote.host")),
                  )}
                </span>
                <span class="rt-line">{computeCopy("remote.addNeeds")}</span>
                <span class="rt-line">{computeCopy("remote.addLater")}</span>
              </div>
              <ActionStatus note={remoteNote} />
            </div>
          </div>
        </>
      ) : null}
      {member ? null : (
        <CustRow
          name={computeCopy("compute.otherTitle")}
          desc={
            <>
              <span class="rt-line">{computeCopy("compute.nim")}</span>
              <span class="rt-line">{computeCopy("compute.slurm")}</span>
              <a class="rt-line" href={COMPUTE_GUIDE_URL} target="_blank" rel="noopener noreferrer">
                {computeCopy("compute.guide")}
              </a>
            </>
          }
        />
      )}
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
      {member ? (
        <div class="cust-row" data-compute-member-note="">
          <div class="info">
            <div class="ds">{computeCopy("compute.memberNote")}</div>
          </div>
        </div>
      ) : null}
      {member ? null : (
      <>
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
              // SCI-05: an empty Install or Run did nothing visible; now it cannot be pressed.
              disabled={installing || !pkg.trim()}
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
                  if (r.ok) {
                    dropEnvSnapshots();
                    setPkg("");
                  }
                  refreshCustTab("compute");
                } catch (e) {
                  const message = runtimeT("rt.install.failed", apiErrorText(e));
                  if (alive()) setInstalled({ ok: false, lines: [message] });
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
      <Hdr title={t("cust.jobs.title")} sub={computeCopy("jobs.desc")} />
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
              disabled={jobBusy || !jobCmd.trim()}
              onClick={async () => {
                const command = jobCmd.trim();
                if (!command) return;
                setJobBusy(true);
                setJobNote(null);
                try {
                  await api("/compute/jobs", {
                    method: "POST",
                    body: JSON.stringify({ command, kind: jobKind }),
                  });
                  setJobCmd("");
                  await loadJobs();
                } catch (e) {
                  setJobNote({ text: t("toast.submitFailed", apiErrorText(e)), err: true });
                }
                setJobBusy(false);
              }}
            >
              {t("cust.jobs.runBtn")}
            </button>
          </div>
          <ActionStatus note={jobNote} />
        </div>
      </div>
      <div class="job-list">
        {!jobs.length ? (
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
              {j.status === "running" || j.status === "queued" ? (
                <button
                  type="button"
                  class="outline-btn small"
                  onClick={async () => {
                    // A failed cancel is a non-2xx now (404 gone, 500 still
                    // running): say so, and re-read either way so the row
                    // shows the job's real state rather than a stale Cancel.
                    setJobNote(null);
                    try {
                      await api(`/compute/jobs/${j.id}/cancel`, { method: "POST" });
                    } catch (e) {
                      setJobNote({ text: t("toast.failed", apiErrorText(e)), err: true });
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
      </>
      )}
    </div>
  );
}
