import type { ComponentChildren } from "preact";
import { useEffect, useState } from "preact/hooks";
import { t } from "../../i18n";
import { copyFailedText, copyText } from "../../features/chrome/clipboard";
import { localClock } from "../../features/chrome/dom";
import type { StandardReadiness } from "../../features/customize/environment";
import { hint } from "../../features/customize/host";
import { ot } from "../../features/onboarding/copy";
import type { OnboardingNetwork, OnboardingStatus } from "../../features/onboarding/status";

export function CopyCommand({ item }: { item: { command: string; label: string } }) {
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 1200);
    return () => clearTimeout(timer);
  }, [copied]);
  return (
    <div class="standard-readiness-command">
      <code aria-label={item.label || undefined}>{item.command}</code>
      <button
        type="button"
        class="outline-btn small"
        onClick={async () => {
          if (await copyText(item.command)) {
            setCopied(true);
            hint(t("environment.readiness.copied"));
          } else {
            hint(copyFailedText(), true);
          }
        }}
      >
        {copied ? t("code.copied") : t("environment.readiness.copy")}
      </button>
    </div>
  );
}

/** The card's one-line state, without "standard profile" or "readiness" jargon. */
function environmentSummary(readiness: StandardReadiness): string {
  if (readiness.ready) return ot("onboarding.env.state.ready");
  if (readiness.state === "needs_setup" || readiness.state === "needs_repair") {
    return ot("onboarding.env.state." + readiness.state);
  }
  return ot("onboarding.env.state.unavailable");
}

/** A standard environment's name with what it is: "python" alone read as "your Python". */
function environmentLabel(name: string): string {
  const key = "onboarding.env.name." + name;
  const label = ot(key);
  return label === key ? name : label;
}

/**
 * The standard-profile readiness card: first-run setup shows it, and so does
 * Settings -> Compute, which adds a Refresh `action` to its head.
 *
 * An environment that does not exist yet is listed once, with what it is.
 * Its whole manifest used to be listed again as "packages missing from
 * python", which read as numpy and pandas missing from the Python the
 * sessions run on (the daemon's own, where they are installed), and nothing
 * said whether any of it mattered.
 */
export function EnvironmentCard({
  readiness,
  action,
}: {
  readiness: StandardReadiness;
  action?: ComponentChildren;
}) {
  const absent = readiness.missing_environments;
  return (
    <section class={"standard-readiness-card state-" + readiness.state}>
      <div class="standard-readiness-head">
        <div>
          <div class="standard-readiness-title">{ot("onboarding.env.title")}</div>
          <div class="standard-readiness-summary">{environmentSummary(readiness)}</div>
        </div>
        {action}
      </div>
      {readiness.ready ? null : (
        <div class="standard-readiness-explicit" data-readiness-optional="">
          {ot("onboarding.env.optional")}
        </div>
      )}
      {absent.length ? (
        <div class="standard-readiness-gap">
          <div class="standard-readiness-label">{ot("onboarding.env.missing")}</div>
          <ul>
            {absent.map((name) => (
              <li key={name}>{environmentLabel(name)}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {Object.entries(readiness.missing_packages).map(([environment, packages]) =>
        packages.length && !absent.includes(environment) ? (
          <div class="standard-readiness-gap" key={environment}>
            <div class="standard-readiness-label">
              {ot("onboarding.env.missingPackages", environment)}
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
          <div class="standard-readiness-label">{ot("onboarding.env.remediation")}</div>
          <div class="standard-readiness-explicit">{t("environment.readiness.explicitOnly")}</div>
          {readiness.remediation.commands.map((item) => (
            <CopyCommand key={item.command} item={item} />
          ))}
          <div class="standard-readiness-explicit">{ot("onboarding.env.notOnPath")}</div>
        </div>
      ) : null}
    </section>
  );
}

/** `platform.system()` as people name it. */
function systemName(platform: string): string {
  if (platform === "Darwin") return "macOS";
  return platform || "—";
}

/**
 * Network access in two parts that work differently (ENV-3): requests the app
 * makes for the agent (web search, web pages, science databases), which the
 * Settings switch and the domain limit govern, and code running in cells,
 * which the kernel sandbox blocks by default. "Allow network: yes · egress:
 * off · Contacted anyone: no" named the flags, not what they do.
 */
function NetworkCard({
  network,
  platform,
  nativeRuntime,
}: {
  network: OnboardingNetwork;
  platform: string;
  nativeRuntime: boolean;
}) {
  return (
    <section class="standard-readiness-card" data-onboarding-network="">
      <div class="standard-readiness-title">{ot("onboarding.readiness.network")}</div>
      <div class="ds" data-network="app">
        {ot(
          "onboarding.readiness.appNetwork",
          network.allow_network
            ? ot("onboarding.readiness.appNetworkOn")
            : ot("onboarding.readiness.appNetworkOff"),
        )}
      </div>
      <div class="ds" data-network="egress">
        {network.egress === "allowlist"
          ? ot("onboarding.readiness.egressOn")
          : ot("onboarding.readiness.egressOff")}
      </div>
      <div class="ds" data-network="kernel">
        {ot("onboarding.readiness.kernelNetwork")}
      </div>
      {network.contacted ? null : (
        <div class="ds">{ot("onboarding.readiness.notContacted")}</div>
      )}
      <div class="ds">
        {ot("onboarding.readiness.platform", systemName(platform))} ·{" "}
        {nativeRuntime ? ot("onboarding.readiness.runtimeYes") : ot("onboarding.readiness.runtimeNo")}
      </div>
    </section>
  );
}

/**
 * The first-run readiness step's own re-read of `GET /api/v1/onboarding`.
 *
 * `refreshing` is deliberately separate from the wizard's `busy`: this is a
 * local read, and Skip/Continue must stay usable while it waits. The action
 * doubles as the retry — a failed read keeps the previous snapshot on screen
 * and reports "not refreshed: <reason>" until a new click succeeds.
 * `refreshedAt` is the client time of the last *successful refresh*.
 */
export function ReadinessPanel({
  status,
  onRefresh,
  refreshing = false,
  refreshedAt = null,
  refreshError = "",
}: {
  status: OnboardingStatus;
  onRefresh?: () => void;
  refreshing?: boolean;
  refreshedAt?: number | null;
  refreshError?: string;
}) {
  const control = onRefresh ? (
    <button
      type="button"
      class="outline-btn small"
      disabled={refreshing}
      onClick={onRefresh}
    >
      {refreshing ? ot("onboarding.readiness.refreshing") : ot("onboarding.readiness.refresh")}
    </button>
  ) : undefined;
  let readState: ComponentChildren = null;
  if (refreshing) {
    readState = <div class="onb-refresh-state">{ot("onboarding.readiness.refreshing")}</div>;
  } else if (refreshError) {
    readState = (
      <div class="onb-refresh-state error" role="alert">
        {ot("onboarding.readiness.refreshFailed", refreshError)}
      </div>
    );
  } else if (refreshedAt != null) {
    readState = (
      <div class="onb-refresh-state">
        {ot("onboarding.readiness.lastRead", localClock(refreshedAt))}
      </div>
    );
  }
  return (
    <div class="onb-readiness">
      {status.environment ? (
        <EnvironmentCard readiness={status.environment} action={control} />
      ) : onRefresh ? (
        // The daemon reported no environment projection at all. The refresh
        // action must still exist: re-reading is how it can come back.
        <div class="onb-refresh-row">{control}</div>
      ) : null}
      {readState}
      <NetworkCard
        network={status.network}
        platform={status.platform}
        nativeRuntime={status.native_runtime_supported}
      />
    </div>
  );
}
