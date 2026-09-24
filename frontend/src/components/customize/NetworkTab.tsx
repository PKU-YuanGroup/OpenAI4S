import { useEffect, useState } from "preact/hooks";
import { t } from "../../i18n";
import { api } from "../../features/customize/api";
import { refreshCustTab } from "../../features/customize/actions";
import { asList, asString, hint } from "../../features/customize/host";
import {
  createTelemetryDrain,
  readTelemetryConsent,
} from "../../features/customize/telemetry";
import { useAlive } from "./use-timer-lease";
import { useOptimisticToggle, useTabRead } from "./hooks";
import { markCustomizeLoaded } from "../../features/customize/load";
import { Hdr, Pill, Toggle } from "./ui";
import { DoubaoSearchCard } from "./vendors/doubao";

export function NetworkTab() {
  const alive = useAlive();
  const [err, setErr] = useState<string | null>(null);
  const [allow, setAllow] = useState<{
    enabled: boolean | null;
    groups: Array<Record<string, unknown>>;
  }>({ enabled: null, groups: [] });
  const [doubao, setDoubao] = useState<{
    config: Record<string, unknown> | null;
    error: unknown;
  }>({ config: null, error: null });
  const [search, setSearch] = useState<Record<string, unknown>>({});
  const [searchKey, setSearchKey] = useState("");
  const [savingSearch, setSavingSearch] = useState(false);
  const network = useOptimisticToggle(
    allow.enabled,
    (on) =>
      api("/network/status", {
        method: "PUT",
        body: JSON.stringify({ enabled: on }),
      }),
    {
      done: (_on, r) => {
        hint(
          (r as Record<string, unknown>).enabled
            ? t("toast.network.enabled")
            : t("toast.network.disabled"),
        );
      },
    },
  );

  useTabRead(
    "network",
    async (current) => {
      const [d, db] = await Promise.all([
        api("/preferences/builtin-allowlist"),
        api("/doubao-search/config")
          .then((config) => ({ config, error: null as unknown }))
          .catch((error) => ({ config: {} as Record<string, unknown>, error })),
      ]);
      if (!current()) return;
      setAllow({
        enabled: !!d.enabled,
        groups: asList(d.groups) as Array<Record<string, unknown>>,
      });
      setDoubao(db);
      markCustomizeLoaded();
      try {
        const sc = await api("/search/config");
        if (current()) setSearch(sc);
      } catch {
        /* original swallowed */
      }
    },
    setErr,
  );

  if (err) return <div>{err}</div>;

  return (
    <div>
      <Hdr title={t("cust.network.title")} sub={t("cust.network.desc")} />
      <DoubaoSearchCard config={doubao.config} configError={doubao.error} />
      <div class="cust-row">
        <div class="info">
          <div class="nm">{t("cust.network.allowName")}</div>
          <div class="ds">
            {network.on ? t("cust.network.enabledDesc") : t("cust.network.disabledDesc")}
          </div>
        </div>
        <Toggle on={network.on} disabled={!network.ready} onClick={network.toggle} />
      </div>
      <div class="cust-row">
        <div class="info">
          <div class="nm">{t("cust.search.name")}</div>
          <div class="ds">
            {(search.api_key_configured ? t("cust.search.set") : t("cust.search.unset")) +
              " · " +
              (asString(search.endpoint) || "https://api.tavily.com/search")}
          </div>
          <div class="job-submit">
            <input
              class="cust-input"
              type="password"
              placeholder={t("cust.search.ph")}
              autocomplete="off"
              value={searchKey}
              onInput={(e) => setSearchKey((e.target as HTMLInputElement).value)}
            />
            <button
              type="button"
              class="solid-btn small"
              disabled={savingSearch}
              onClick={async () => {
                const k = searchKey.trim();
                if (!k) return;
                setSavingSearch(true);
                try {
                  await api("/search/config", {
                    method: "POST",
                    body: JSON.stringify({ api_key: k }),
                  });
                  hint(t("cust.search.saved"));
                  setSearchKey("");
                  refreshCustTab("network");
                } catch (e) {
                  hint((e as Error).message, true);
                } finally {
                  if (alive()) setSavingSearch(false);
                }
              }}
            >
              {t("common.save")}
            </button>
          </div>
        </div>
      </div>
      {allow.groups.map((g, i) => (
        <div class="cust-row" key={i}>
          <div class="info">
            <div class="nm">
              <span>{asString(g.name || g.label)}</span>
            </div>
            <div class="ds">
              {asList(g.domains)
                .slice(0, 12)
                .map((dm) => (
                  <Pill key={String(dm)}>{String(dm)}</Pill>
                ))}
            </div>
          </div>
        </div>
      ))}
      <TelemetryToggle alive={alive} />
    </div>
  );
}

function TelemetryToggle({ alive }: { alive: () => boolean }) {
  const [consent, setConsent] = useState<{
    enabled: boolean;
    env_locked: boolean;
  } | null>(null);
  const [on, setOn] = useState(false);

  useEffect(() => {
    void (async () => {
      try {
        const d = await api("/telemetry/consent");
        if (!alive()) return;
        const next = readTelemetryConsent(d);
        setConsent(next);
        setOn(next.enabled);
      } catch {
        /* original returns */
      }
    })();
  }, [alive]);

  if (!consent) return null;

  if (consent.env_locked) {
    return (
      <div class="cust-row">
        <div class="info">
          <div class="nm">{t("cust.telemetry.name")}</div>
          <div class="ds">{t("cust.telemetry.envlock")}</div>
        </div>
        <Toggle on={consent.enabled} disabled title={t("cust.telemetry.envlock")} onClick={() => {}} />
      </div>
    );
  }

  return (
    <LiveTelemetry initial={consent.enabled} on={on} setOn={setOn} alive={alive} />
  );
}

function LiveTelemetry({
  initial,
  on,
  setOn,
  alive,
}: {
  initial: boolean;
  on: boolean;
  setOn: (v: boolean) => void;
  alive: () => boolean;
}) {
  const [drain] = useState(() =>
    createTelemetryDrain(initial, (next) => setOn(next), { alive }),
  );
  return (
    <div class="cust-row">
      <div class="info">
        <div class="nm">{t("cust.telemetry.name")}</div>
        <div class="ds">{on ? t("cust.telemetry.on") : t("cust.telemetry.off")}</div>
      </div>
      <Toggle on={on} onClick={() => drain.onclick()} />
    </div>
  );
}
