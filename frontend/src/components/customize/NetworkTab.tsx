import { useEffect, useState } from "preact/hooks";
import { t } from "../../i18n";
import { copyLookup } from "../../i18n/copy";
import { api } from "../../features/customize/api";
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
import { OllamaWebCard } from "./vendors/ollama";
import { SearchEngines } from "./SearchEngines";

/**
 * The allowlist section's words: what the domain groups are, when they apply,
 * and the group names, which the server sends in English.
 */
const nt = copyLookup({
  zh: {
    "allowlist.title": "出网白名单（按用途分组的域名）",
    "allowlist.off":
      "这些分组是出网白名单。当前未启用（OPENAI4S_EGRESS=off，默认），所以智能体的联网请求不受这些分组限制。设置 OPENAI4S_EGRESS=allowlist 后，只有这些域名和你批准过的域名可以访问，其他域名需要你在审批卡里放行。",
    "allowlist.on":
      "出网白名单已启用（OPENAI4S_EGRESS=allowlist）：智能体的联网请求只能访问下列分组里的域名，以及你批准过的域名；访问其他域名会弹出审批卡。",
    "allowlist.skills":
      "另外，加载带联网清单的技能（如 literature-review）会把那个会话进一步限定到技能声明的域名，可在「技能」页查看。",
    "allowlist.granted": "已批准的域名：{0}",
    "allowlist.more": "还有 {0} 个",
    "group.Scientific databases": "科学数据库",
    "group.Literature & preprints": "文献与预印本",
    "group.Package indexes": "软件包索引",
    "group.Data repositories": "数据仓库",
    "group.Professional datasets": "专业数据集",
    "group.Web search": "网页搜索",
  },
  en: {
    "allowlist.title": "Outbound allowlist (domains grouped by purpose)",
    "allowlist.off":
      "These groups are the outbound allowlist. It is not enforced now (OPENAI4S_EGRESS=off, the default), so the agent's network requests are not limited to them. With OPENAI4S_EGRESS=allowlist, only these domains and the ones you approved are reachable; any other domain asks you on an approval card.",
    "allowlist.on":
      "The outbound allowlist is enforced (OPENAI4S_EGRESS=allowlist): the agent's network requests can reach only the domains in these groups and the ones you approved; any other domain asks you on an approval card.",
    "allowlist.skills":
      "A Skill with a network manifest (such as literature-review) further limits the session that loads it to the domains it declares; see the Skills tab.",
    "allowlist.granted": "Approved domains: {0}",
    "allowlist.more": "{0} more",
  },
});

/** A group's name in the UI language; the server's English name otherwise. */
export function allowlistGroupName(name: string): string {
  const key = "group." + name;
  const label = nt(key);
  return label === key ? name : label;
}

const GROUP_PILLS = 12;

export function NetworkTab() {
  const alive = useAlive();
  const [err, setErr] = useState<string | null>(null);
  const [allow, setAllow] = useState<{
    enabled: boolean | null;
    groups: Array<Record<string, unknown>>;
    mode: string;
    granted: string[];
  }>({ enabled: null, groups: [], mode: "off", granted: [] });
  const [doubao, setDoubao] = useState<{
    config: Record<string, unknown> | null;
    error: unknown;
  }>({ config: null, error: null });
  const [ollama, setOllama] = useState<{
    config: Record<string, unknown> | null;
    error: unknown;
  }>({ config: null, error: null });
  const [search, setSearch] = useState<Record<string, unknown>>({});
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
      const [d, db, ol] = await Promise.all([
        api("/preferences/builtin-allowlist"),
        api("/doubao-search/config")
          .then((config) => ({ config, error: null as unknown }))
          .catch((error) => ({ config: {} as Record<string, unknown>, error })),
        api("/ollama-web/config")
          .then((config) => ({ config, error: null as unknown }))
          .catch((error) => ({ config: {} as Record<string, unknown>, error })),
      ]);
      if (!current()) return;
      setAllow({
        enabled: !!d.enabled,
        groups: asList(d.groups) as Array<Record<string, unknown>>,
        mode: asString(d.egress_mode) || "off",
        granted: asList(d.granted).map((item) => String(item)),
      });
      setDoubao(db);
      setOllama(ol);
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
      <OllamaWebCard config={ollama.config} configError={ollama.error} />
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
      <SearchEngines raw={search} onSaved={setSearch} />
      {allow.groups.length ? (
        <div class="search-engines-head allowlist-head" data-egress-mode={allow.mode}>
          <div class="nm">{nt("allowlist.title")}</div>
          <div class="ds">
            {nt(allow.mode === "allowlist" ? "allowlist.on" : "allowlist.off")}
          </div>
          <div class="ds">{nt("allowlist.skills")}</div>
          {allow.granted.length ? (
            <div class="ds">{nt("allowlist.granted", allow.granted.join(", "))}</div>
          ) : null}
        </div>
      ) : null}
      {allow.groups.map((g, i) => {
        const domains = asList(g.domains).map((dm) => String(dm));
        const hidden = domains.length - GROUP_PILLS;
        return (
          <div class="cust-row" key={i}>
            <div class="info">
              <div class="nm">
                <span>{allowlistGroupName(asString(g.name || g.label))}</span>
              </div>
              <div class="ds" title={domains.join(", ")}>
                {domains.slice(0, GROUP_PILLS).map((dm) => (
                  <Pill key={dm}>{dm}</Pill>
                ))}
                {hidden > 0 ? <Pill>{nt("allowlist.more", hidden)}</Pill> : null}
              </div>
            </div>
          </div>
        );
      })}
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
