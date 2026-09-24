import type { ComponentChildren } from "preact";
import { useEffect, useState } from "preact/hooks";
import { t } from "../../i18n";
import { copyFailedText, copyText } from "../../features/chrome/clipboard";
import {
  standardReadinessStateText,
  type StandardReadiness,
} from "../../features/customize/environment";
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

/**
 * The standard-profile readiness card: first-run setup shows it, and so does
 * Settings -> Compute, which adds a Refresh `action` to its head.
 */
export function EnvironmentCard({
  readiness,
  action,
}: {
  readiness: StandardReadiness;
  action?: ComponentChildren;
}) {
  return (
    <section class={"standard-readiness-card state-" + readiness.state}>
      <div class="standard-readiness-head">
        <div>
          <div class="standard-readiness-title">{t("environment.readiness.cardTitle")}</div>
          <div class="standard-readiness-summary">{standardReadinessStateText(readiness)}</div>
        </div>
        {action}
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
          <div class="standard-readiness-explicit">{t("environment.readiness.explicitOnly")}</div>
          {readiness.remediation.commands.map((item) => (
            <CopyCommand key={item.command} item={item} />
          ))}
        </div>
      ) : null}
    </section>
  );
}

function NetworkCard({
  network,
  platform,
  nativeRuntime,
}: {
  network: OnboardingNetwork;
  platform: string;
  nativeRuntime: boolean;
}) {
  const yn = (value: boolean) => (value ? ot("onboarding.readiness.yes") : ot("onboarding.readiness.no"));
  return (
    <section class="standard-readiness-card">
      <div class="standard-readiness-title">{ot("onboarding.readiness.network")}</div>
      <div class="ds">{ot("onboarding.readiness.allowNetwork", yn(network.allow_network))}</div>
      <div class="ds">{ot("onboarding.readiness.egress", network.egress)}</div>
      <div class="ds">{ot("onboarding.readiness.contacted", yn(network.contacted))}</div>
      <div class="ds">{ot("onboarding.readiness.platform", platform || "—")}</div>
      <div class="ds">
        {nativeRuntime ? ot("onboarding.readiness.runtimeYes") : ot("onboarding.readiness.runtimeNo")}
      </div>
    </section>
  );
}

export function ReadinessPanel({ status }: { status: OnboardingStatus }) {
  return (
    <div class="onb-readiness">
      {status.environment ? <EnvironmentCard readiness={status.environment} /> : null}
      <NetworkCard
        network={status.network}
        platform={status.platform}
        nativeRuntime={status.native_runtime_supported}
      />
    </div>
  );
}
