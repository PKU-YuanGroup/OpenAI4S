/**
 * Telemetry consent toggle. Port of app.js:11926-11985.
 *
 * Two variables instead of reading the DOM: `desired` is what the user asked
 * for, `confirmed` is what the server last told us. Clicks only move desired;
 * one drain loop reconciles, never with two requests in flight. Extra clicks
 * during a round trip coalesce — the last click is the one that lands.
 */
import { t } from "../../i18n";
import { api } from "./api";
import { hint } from "./host";

export type TelemetryConsent = {
  enabled: boolean;
  env_locked: boolean;
};

export function readTelemetryConsent(raw: unknown): TelemetryConsent {
  const row = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
  return {
    enabled: !!row.enabled,
    env_locked: !!row.env_locked,
  };
}

export type TelemetryDrain = {
  onclick: () => void;
  getDesired: () => boolean;
  getConfirmed: () => boolean;
  isRunning: () => boolean;
};

export function createTelemetryDrain(
  initial: boolean,
  paint: (on: boolean) => void,
  opts?: { alive?: () => boolean },
): TelemetryDrain {
  let running = false;
  let desired = initial;
  let confirmed = initial;
  const alive = opts?.alive ?? (() => true);

  const drain = async (): Promise<void> => {
    if (running) return;
    running = true;
    try {
      while (desired !== confirmed) {
        if (!alive()) return;
        const want = desired;
        try {
          const r = await api("/telemetry/consent", {
            method: "PUT",
            body: JSON.stringify({ enabled: want }),
          });
          if (!alive()) return;
          confirmed = !!r.enabled;
          if (confirmed !== want && desired === want) desired = confirmed;
          hint(confirmed ? t("toast.telemetry.on") : t("toast.telemetry.off"));
        } catch (e) {
          desired = confirmed;
          hint(t("toast.telemetry.failed", (e && (e as Error).message) || ""));
        }
        if (desired === confirmed) paint(confirmed);
      }
    } finally {
      running = false;
    }
  };

  return {
    onclick: () => {
      desired = !desired;
      paint(desired);
      void drain();
    },
    getDesired: () => desired,
    getConfirmed: () => confirmed,
    isRunning: () => running,
  };
}

/**
 * Contract export: browser_matrix.mjs calls `telemetryRow(host)` and then
 * clicks `button.toggle`. Imperative so the evaluate harness can drive it
 * without the Customize modal.
 */
export async function telemetryRow(host: Element): Promise<void> {
  let d: Record<string, unknown>;
  try {
    d = await api("/telemetry/consent");
  } catch {
    return;
  }
  const consent = readTelemetryConsent(d);
  const row = document.createElement("div");
  row.className = "cust-row";
  const info = document.createElement("div");
  info.className = "info";
  const nm = document.createElement("div");
  nm.className = "nm";
  nm.textContent = t("cust.telemetry.name");
  info.appendChild(nm);
  const ds = document.createElement("div");
  ds.className = "ds";
  ds.textContent = consent.env_locked
    ? t("cust.telemetry.envlock")
    : consent.enabled
      ? t("cust.telemetry.on")
      : t("cust.telemetry.off");
  info.appendChild(ds);
  row.appendChild(info);
  const tg = document.createElement("button");
  tg.className =
    "toggle" +
    (consent.enabled ? " on" : "") +
    (consent.env_locked ? " off" : "");
  if (consent.env_locked) {
    tg.disabled = true;
  } else {
    const paint = (on: boolean) => {
      tg.classList.toggle("on", !!on);
      ds.textContent = on ? t("cust.telemetry.on") : t("cust.telemetry.off");
    };
    const drain = createTelemetryDrain(consent.enabled, paint, {
      alive: () => host.isConnected,
    });
    tg.onclick = () => drain.onclick();
  }
  row.appendChild(tg);
  host.appendChild(row);
}
