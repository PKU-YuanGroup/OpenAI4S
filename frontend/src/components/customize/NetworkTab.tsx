import { useEffect, useState } from "preact/hooks";
import { t } from "../../i18n";
import { api } from "../../features/customize/api";
import { custTab } from "../../features/customize/actions";
import { asList, asString, hint } from "../../features/customize/host";
import {
  createTelemetryDrain,
  readTelemetryConsent,
} from "../../features/customize/telemetry";
import { useAlive } from "./use-timer-lease";
import { Hdr, Pill, Toggle } from "./ui";
import { DoubaoSearchCard } from "./vendors/doubao";

export function NetworkTab() {
  const alive = useAlive();
  const [err, setErr] = useState<string | null>(null);
  const [allow, setAllow] = useState<{
    enabled: boolean;
    groups: Array<Record<string, unknown>>;
  }>({ enabled: false, groups: [] });
  const [doubao, setDoubao] = useState<{
    config: Record<string, unknown>;
    error: unknown;
  }>({ config: {}, error: null });
  const [search, setSearch] = useState<Record<string, unknown>>({});
  const [searchKey, setSearchKey] = useState("");
  const [savingSearch, setSavingSearch] = useState(false);

  useEffect(() => {
    void (async () => {
      try {
        const [d, db] = await Promise.all([
          api("/preferences/builtin-allowlist"),
          api("/doubao-search/config")
            .then((config) => ({ config, error: null as unknown }))
            .catch((error) => ({ config: {} as Record<string, unknown>, error })),
        ]);
        if (!alive()) return;
        setAllow({
          enabled: !!d.enabled,
          groups: asList(d.groups) as Array<Record<string, unknown>>,
        });
        setDoubao(db);
        try {
          const sc = await api("/search/config");
          if (alive()) setSearch(sc);
        } catch {
          /* original swallowed */
        }
      } catch (e) {
        if (!alive()) return;
        setErr(t("versions.load.err", (e as Error).message));
      }
    })();
  }, [alive]);

  if (err) return <div>{err}</div>;

  return (
    <div>
      <Hdr title={t("cust.network.title")} sub={t("cust.network.desc")} />
      <DoubaoSearchCard config={doubao.config} configError={doubao.error} />
      <div class="cust-row">
        <div class="info">
          <div class="nm">{t("cust.network.allowName")}</div>
          <div class="ds">
            {allow.enabled ? t("cust.network.enabledDesc") : t("cust.network.disabledDesc")}
          </div>
        </div>
        <Toggle
          on={allow.enabled}
          onClick={async () => {
            const on = !allow.enabled;
            setAllow((prev) => ({ ...prev, enabled: on }));
            try {
              const r = await api("/network/status", {
                method: "PUT",
                body: JSON.stringify({ enabled: on }),
              });
              hint(r.enabled ? t("toast.network.enabled") : t("toast.network.disabled"));
            } catch {
              setAllow((prev) => ({ ...prev, enabled: !on }));
            }
          }}
        />
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
                  custTab("network");
                } catch (e) {
                  setSavingSearch(false);
                  hint((e as Error).message, true);
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
