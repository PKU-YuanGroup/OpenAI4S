import { useEffect, useState } from "preact/hooks";
import { t } from "../../i18n";
import { api, apiErrorText } from "../../features/customize/api";
import { custTab } from "../../features/customize/actions";
import { nestedEditor } from "../../features/customize/state";
import {
  asList,
  asString,
  confirmAction,
  dropEnvSnapshots,
  hint,
} from "../../features/customize/host";
import { currentId } from "../../stores/session";
import {
  environmentStatus,
  standardProfileReadiness,
  _environmentStatusPromise,
  _environmentStatusRefreshFailed,
} from "../../stores/customize";
import { _jobPoll } from "../../stores/ui";
import {
  sanitizeStandardProfileReadiness,
  standardReadinessStateText,
  type StandardReadiness,
} from "../../features/customize/environment";
import { scheduleTimeout } from "../../features/customize/timers";
import { useAlive, useTimerLease } from "./use-timer-lease";
import { markCustomizeFailed, markCustomizeLoaded } from "../../features/customize/load";
import { Hdr, InfoRow } from "./ui";

async function refreshEnvironmentStatus(): Promise<Record<string, unknown> | null> {
  if (_environmentStatusPromise.value) {
    return _environmentStatusPromise.value as Promise<Record<string, unknown> | null>;
  }
  const pending = (async () => {
    try {
      const payload = await api("/environments/status");
      _environmentStatusRefreshFailed.value = false;
      environmentStatus.value = payload && typeof payload === "object" ? payload : null;
      standardProfileReadiness.value = sanitizeStandardProfileReadiness(
        payload.standard_profile_readiness,
      );
    } catch {
      _environmentStatusRefreshFailed.value = true;
      const prev = standardProfileReadiness.value as StandardReadiness | null;
      if (prev && prev.enabled === true) {
        standardProfileReadiness.value = {
          ...prev,
          ready: false,
          state: "unavailable",
          reason: "status_refresh_failed",
        };
      }
    }
    return environmentStatus.value as Record<string, unknown> | null;
  })();
  _environmentStatusPromise.value = pending;
  try {
    return await pending;
  } finally {
    _environmentStatusPromise.value = null;
  }
}

function ReadinessCard({
  readiness,
  lease,
  alive,
}: {
  readiness: StandardReadiness;
  lease: ReturnType<typeof useTimerLease>;
  alive: () => boolean;
}) {
  return (
    <section class={"standard-readiness-card state-" + readiness.state}>
      <div class="standard-readiness-head">
        <div>
          <div class="standard-readiness-title">{t("environment.readiness.cardTitle")}</div>
          <div class="standard-readiness-summary">
            {standardReadinessStateText(readiness)}
          </div>
        </div>
        <button
          type="button"
          class="outline-btn small"
          onClick={() => custTab("compute")}
        >
          {t("environment.readiness.refresh")}
        </button>
      </div>
      {readiness.missing_environments.length ? (
        <div class="standard-readiness-gap">
          <div class="standard-readiness-label">
            {t("environment.readiness.missingEnvironments")}
          </div>
          <ul>
            {readiness.missing_environments.map((name) => (
              <li key={name}>{name}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {Object.entries(readiness.missing_packages).map(([environment, packages]) =>
        packages.length ? (
          <div class="standard-readiness-gap" key={environment}>
            <div class="standard-readiness-label">
              {t("environment.readiness.missingPackages", environment)}
            </div>
            <ul class="standard-readiness-packages">
              {packages.map((packageName) => (
                <li key={packageName}>{packageName}</li>
              ))}
            </ul>
          </div>
        ) : null,
      )}
      {readiness.remediation &&
      readiness.remediation.requires_explicit_action &&
      readiness.remediation.commands.length ? (
        <div class="standard-readiness-remediation">
          <div class="standard-readiness-label">{t("environment.readiness.remediation")}</div>
          <div class="standard-readiness-explicit">
            {t("environment.readiness.explicitOnly")}
          </div>
          {readiness.remediation.commands.map((item) => (
            <CopyCommand
              key={item.command}
              item={item}
              lease={lease}
              alive={alive}
            />
          ))}
        </div>
      ) : null}
    </section>
  );
}

function CopyCommand({
  item,
  lease,
  alive,
}: {
  item: { command: string; label: string };
  lease: ReturnType<typeof useTimerLease>;
  alive: () => boolean;
}) {
  const [copied, setCopied] = useState(false);
  return (
    <div class="standard-readiness-command">
      <code aria-label={item.label || undefined}>{item.command}</code>
      <button
        type="button"
        class="outline-btn small"
        onClick={async () => {
          try {
            if (!navigator.clipboard || !navigator.clipboard.writeText) {
              throw new Error("clipboard unavailable");
            }
            await navigator.clipboard.writeText(item.command);
            setCopied(true);
            hint(t("environment.readiness.copied"));
            scheduleTimeout(
              lease,
              () => {
                if (alive()) setCopied(false);
              },
              1200,
            );
          } catch {
            hint(t("nb.action.failed"), true);
          }
        }}
      >
        {copied ? t("code.copied") : t("environment.readiness.copy")}
      </button>
    </div>
  );
}

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
  const [jobKind, setJobKind] = useState("bash");
  const [jobCmd, setJobCmd] = useState("");
  const [jobBusy, setJobBusy] = useState(false);
  const [alias, setAlias] = useState("");

  const loadJobs = async () => {
    let d: Record<string, unknown>;
    try {
      d = await api("/compute/jobs");
    } catch {
      d = { jobs: [] };
    }
    if (!alive()) return;
    const list = asList(d.jobs) as Record<string, unknown>[];
    setJobs(list);
    const anyRunning = list.some(
      (j) => j.status === "running" || j.status === "queued",
    );
    if (anyRunning) {
      const handle = scheduleTimeout(
        lease,
        () => {
          void loadJobs();
        },
        1500,
      );
      _jobPoll.value = handle;
    }
  };

  useEffect(() => {
    void (async () => {
      try {
        const [g, env, h] = await Promise.all([
          api("/compute/gpu").catch(() => ({ available: false })),
          refreshEnvironmentStatus().then((status) => status || { environments: [] }),
          api("/compute/local/hostinfo").catch(() => ({})),
        ]);
        if (!alive()) return;
        setGpu(g);
        setHost(h);
        setEnvs(asList(env && env.environments) as Record<string, unknown>[]);
        markCustomizeLoaded();
        try {
          const info = await api("/compute/remote");
          if (alive()) setRemote(info);
        } catch {
          /* original swallowed */
        }
        await loadJobs();
      } catch (e) {
        if (!alive()) return;
        const message = t("versions.load.err", (e as Error).message);
        setErr(message);
        markCustomizeFailed(message);
      }
    })();
    return () => {
      _jobPoll.value = null;
    };
  }, [alive, lease]);

  if (err) return <div>{err}</div>;

  const readiness = standardProfileReadiness.value as StandardReadiness | null;
  const hosts = remote ? (asList(remote.hosts) as Record<string, unknown>[]) : [];
  const taken = new Set(hosts.map((h) => h.alias));
  const avail = (remote ? asList(remote.available_aliases) : []).filter(
    (a) => !taken.has(a),
  );

  return (
    <div>
      <Hdr title={t("cust.compute.title")} sub={t("cust.compute.desc")} />
      {readiness && readiness.enabled ? (
        <ReadinessCard readiness={readiness} lease={lease} alive={alive} />
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
                    custTab("compute");
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
                      custTab("compute");
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
      {envs.map((e) => {
        const inst = asList(e.packages).filter(
          (p) => (p as Record<string, unknown>).installed,
        ) as Record<string, unknown>[];
        return (
          <InfoRow
            key={asString(e.language)}
            name={t(
              "cust.compute.kernelLabel",
              e.language,
              e.status === "installing"
                ? t("cust.compute.kernelInstalling")
                : t("cust.compute.kernelReady"),
            )}
            detail={t(
              "cust.compute.preinstalledDetail",
              e.package_count,
              inst
                .slice(0, 18)
                .map((p) => p.name)
                .join("、") + (inst.length > 18 ? " …" : ""),
            )}
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
                try {
                  const r = currentId.value
                    ? await api(`/frames/${currentId.value}/kernel/install`, {
                        method: "POST",
                        body: JSON.stringify({ packages: pkgs, restart: true }),
                      })
                    : await api(`/kernel/install`, {
                        method: "POST",
                        body: JSON.stringify({ packages: pkgs }),
                      });
                  hint(
                    r.ok
                      ? t(
                          "step.env.installed",
                          asList(r.installed).join("、") +
                            (r.restarted ? t("cust.compute.kernelRestarted") : ""),
                        )
                      : t(
                          "toast.compute.installFailed",
                          (asList(r.failed)[0] as { error?: string } | undefined)?.error ||
                            t("toast.compute.installSeeLogs"),
                        ),
                  );
                  if (r.ok) dropEnvSnapshots();
                  custTab("compute");
                } catch (e) {
                  hint(t("toast.compute.installFailed", apiErrorText(e)), true);
                }
                setInstalling(false);
              }}
            >
              {installing ? t("cust.compute.installingBtn") : t("cust.compute.installBtn")}
            </button>
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
                    try {
                      await api(`/compute/jobs/${j.id}/cancel`, { method: "POST" });
                      await loadJobs();
                    } catch {
                      /* original swallowed */
                    }
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
