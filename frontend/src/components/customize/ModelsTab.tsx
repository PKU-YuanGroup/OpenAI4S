import { useEffect, useState } from "preact/hooks";
import { LANG, t } from "../../i18n";
import { publicModelId, publicText } from "../../features/scrub/scrub";
import { api, apiErrorText } from "../../features/customize/api";
import { custTab } from "../../features/customize/actions";
import { defaultModel } from "../../stores/customize";
import {
  asList,
  asString,
  confirmAction,
  hint,
  loadModels,
  refreshKeyBanner,
} from "../../features/customize/host";
import {
  loopbackModelBase,
  modelProtocolOptions,
  protocolLabelOf,
  readCapabilityReceipt,
  sanitizeLocalModelDiscovery,
  type CapabilityReceipt,
  type LocalDiscovery,
  type ProtocolOption,
} from "../../features/customize/models";
import { CapabilityBadges } from "../onboarding/CapabilityBadges";
import { useAlive } from "./use-timer-lease";
import { markCustomizeFailed, markCustomizeLoaded } from "../../features/customize/load";
import { Empty, Hdr, IconGhost, Pill, Subhead } from "./ui";
import { VolcenginePanel } from "./vendors/volcengine";

type Profile = Record<string, unknown>;

/**
 * UI-LIVE-06 copy. Feature-local on purpose: `i18n/en.ts` / `zh.ts` are
 * generated extracts of the legacy dictionary and are byte-checked.
 */
const COPY = {
  en: {
    liveSource: "In use now · from the daemon's environment or saved settings, not a saved profile",
    liveNoProfiles: "No saved profiles. The active model above comes from the environment or saved settings.",
    envKey: "🔑 Key from environment",
  },
  zh: {
    liveSource: "当前在用 · 来自守护进程的环境变量或已保存的设置，不是已保存的配置档",
    liveNoProfiles: "还没有已保存的配置档。上方正在使用的模型来自环境变量或已保存的设置。",
    envKey: "🔑 密钥来自环境变量",
  },
} as const;

function copy(key: keyof (typeof COPY)["en"]): string {
  return (LANG === "zh" ? COPY.zh : COPY.en)[key];
}

/**
 * The key half of a profile row. `has_api_key` is only "holds a key of its
 * own", so a profile dispatched under the daemon's environment key for the same
 * provider read "No key" beside a `ready` card; `credential_source` says which.
 */
export function profileKeyLabel(p: Record<string, unknown>): string {
  if (p.has_api_key) return t("cust.models.hasKey");
  if (p.credential_source === "environment") return copy("envKey");
  if (p.credential_source === "local" || loopbackModelBase(p.base_url)) {
    return t("cust.models.local.keyless");
  }
  return t("cust.models.noKey");
}

type LiveModel = { provider: string; model: string; baseUrl: string; hasKey: boolean };

/**
 * What `GET /config/llm` says the daemon runs on. Never carries key material.
 * The model id and protocol go through `publicModelId`, not `publicText`: the
 * generic credential regex rendered `ark-code-latest` as "[redacted]" (the
 * hazard `features/customize/models.ts` `entryText` already names).
 */
function readLiveModel(raw: unknown): LiveModel | null {
  if (!raw || typeof raw !== "object") return null;
  const row = raw as Record<string, unknown>;
  const model = publicModelId(asString(row.model), 200);
  const provider = publicModelId(asString(row.provider), 64);
  if (!model) return null;
  return {
    provider,
    model,
    baseUrl: publicText(asString(row.base_url), 300).trim(),
    hasKey: row.has_api_key === true,
  };
}

export function ModelsTab() {
  const alive = useAlive();
  const [err, setErr] = useState<string | null>(null);
  const [data, setData] = useState<{
    profiles: Profile[];
    active_id: string;
    protocols: unknown[];
  }>({ profiles: [], active_id: "", protocols: [] });
  const [live, setLive] = useState<LiveModel | null>(null);
  const [discovery, setDiscovery] = useState<LocalDiscovery | null>(null);
  const [scanning, setScanning] = useState(false);
  const [scanErr, setScanErr] = useState<string | null>(null);
  const [editing, setEditing] = useState<Profile | null>(null);
  const [name, setName] = useState("");
  const [provider, setProvider] = useState("chatgpt");
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    void (async () => {
      try {
        // The live configuration alongside the saved profiles. An install
        // driven by `.env` has no profiles at all, and this tab then said "No
        // models configured yet" while every turn ran on the environment's
        // model. Best-effort: an unreadable config must not hide the profiles.
        const [next, conf] = await Promise.all([
          api("/model-profiles"),
          api("/config/llm").catch(() => null),
        ]);
        if (!alive()) return;
        setData({
          profiles: asList(next.profiles) as Profile[],
          active_id: asString(next.active_id),
          protocols: asList(next.protocols),
        });
        setLive(readLiveModel(conf));
        markCustomizeLoaded();
      } catch (e) {
        if (!alive()) return;
        const message = t("versions.load.err", (e as Error).message);
        setErr(message);
        markCustomizeFailed(message);
      }
    })();
  }, [alive]);

  const protocols: ProtocolOption[] = modelProtocolOptions(data.protocols);
  const protocolIds = new Set(protocols.map((item) => item.value));

  const resetForm = () => {
    setEditing(null);
    setName("");
    setProvider("chatgpt");
    setBaseUrl("");
    setModel("");
    setApiKey("");
  };

  const startEdit = (p: Profile) => {
    setEditing(p);
    setName(asString(p.name));
    setProvider(protocolIds.has(asString(p.provider)) ? asString(p.provider) : asString(p.provider));
    setBaseUrl(asString(p.base_url));
    setModel(asString(p.model));
    setApiKey("");
  };

  const runLocalScan = async (force: boolean) => {
    setScanning(true);
    setScanErr(null);
    try {
      const result = sanitizeLocalModelDiscovery(
        await api("/model-endpoints/discover" + (force ? "?force=1" : "")),
      );
      if (alive()) setDiscovery(result);
    } catch (error) {
      if (alive())
        setScanErr(
          t("cust.models.local.error", publicText((error as Error).message, 240)),
        );
    } finally {
      if (alive()) setScanning(false);
    }
  };

  if (err) {
    return (
      <div>
        <Hdr title={t("cust.tab.models")} sub={t("cust.models.subtitle2")} />
        <Empty>{err}</Empty>
      </div>
    );
  }

  return (
    <div>
      <Hdr title={t("cust.tab.models")} sub={t("cust.models.subtitle2")} />
      <Subhead>{t("cust.volc.title")}</Subhead>
      <VolcenginePanel />
      <Subhead>{t("cust.models.local.title")}</Subhead>
      <div class="cust-sub">{t("cust.models.local.desc")}</div>
      <div class="form-actions">
        <button
          type="button"
          class="outline-btn small"
          disabled={scanning}
          onClick={() => void runLocalScan(true)}
        >
          {scanning ? t("cust.models.local.scanning") : t("cust.models.local.scan")}
        </button>
      </div>
      <div class="local-model-results">
        {scanErr ? <div class="timeline-error">{scanErr}</div> : null}
        {scanning && !discovery ? (
          <Empty>{t("cust.models.local.scanning")}</Empty>
        ) : discovery ? (
          discovery.endpoints.length ? (
            discovery.endpoints.map((endpoint) => (
              <LocalEndpointRow
                key={endpoint.base_url}
                endpoint={endpoint}
                profiles={data.profiles}
              />
            ))
          ) : (
            <Empty>{t("cust.models.local.none")}</Empty>
          )
        ) : (
          <Empty>{t("cust.models.local.idle")}</Empty>
        )}
      </div>
      <div class="cust-subhead">
        {editing
          ? t("cust.models.editHeading", asString(editing.name || editing.id))
          : t("cust.models.addHeading")}
      </div>
      <div class="skill-form">
        <label class="skill-lbl">{t("cust.connectors.namePlaceholder")}</label>
        <input
          class="cust-input"
          placeholder={t("cust.models.namePlaceholder")}
          value={name}
          onInput={(e) => setName((e.target as HTMLInputElement).value)}
        />
        <label class="skill-lbl">{t("cust.models.label.protocol")}</label>
        <select
          class="cust-input"
          value={provider}
          onChange={(e) => setProvider((e.target as HTMLSelectElement).value)}
        >
          {protocols.map((p) => (
            <option value={p.value} key={p.value}>
              {p.label}
            </option>
          ))}
          {editing && !protocolIds.has(asString(editing.provider)) ? (
            <option value={asString(editing.provider)} disabled data-legacy="true">
              {asString(editing.provider) || "—"}
            </option>
          ) : null}
        </select>
        <label class="skill-lbl">Base URL</label>
        <input
          class="cust-input"
          placeholder={t("cust.models.baseUrlPlaceholder")}
          value={baseUrl}
          onInput={(e) => setBaseUrl((e.target as HTMLInputElement).value)}
        />
        <label class="skill-lbl">{t("label.model")}</label>
        <input
          class="cust-input"
          placeholder={t("cust.models.modelPlaceholder2")}
          value={model}
          onInput={(e) => setModel((e.target as HTMLInputElement).value)}
        />
        <label class="skill-lbl">API Key</label>
        <input
          class="cust-input"
          type="password"
          placeholder={
            editing
              ? editing.has_api_key
                ? t("cust.models.keyPlaceholderSet")
                : t("cust.models.keyPlaceholderUnset")
              : "API Key"
          }
          autocomplete="off"
          value={apiKey}
          onInput={(e) => setApiKey((e.target as HTMLInputElement).value)}
        />
        <div class="form-actions">
          <button
            type="button"
            class="solid-btn"
            disabled={saving}
            onClick={async () => {
              const nm = name.trim();
              if (!nm) {
                hint(t("toast.specialist.enterName"), true);
                return;
              }
              setSaving(true);
              const body: Record<string, unknown> = {
                name: nm,
                base_url: baseUrl.trim(),
                model: model.trim(),
              };
              if (protocolIds.has(provider)) body.provider = provider;
              if (apiKey) body.api_key = apiKey;
              try {
                if (editing) {
                  await api(`/model-profiles/${editing.id}`, {
                    method: "PATCH",
                    body: JSON.stringify(body),
                  });
                  hint(t("toast.models.updated", nm));
                } else {
                  await api("/model-profiles", {
                    method: "POST",
                    body: JSON.stringify(body),
                  });
                  hint(t("toast.models.added", nm));
                }
                if (editing && editing.id === data.active_id) {
                  await refreshKeyBanner();
                  await loadModels();
                }
                custTab("models");
              } catch (e) {
                setSaving(false);
                hint(t("artifact.save.err", apiErrorText(e)), true);
              }
            }}
          >
            {saving
              ? t("common.saving")
              : editing
                ? t("cust.models.updateBtn")
                : t("cust.models.addBtn")}
          </button>
          {editing ? (
            <button type="button" class="outline-btn small" onClick={resetForm}>
              {t("cust.models.cancelEdit")}
            </button>
          ) : null}
        </div>
      </div>
      <Subhead>{t("cust.models.configuredHeading")}</Subhead>
      {live && !data.active_id ? liveModelRow(live, protocols) : null}
      {!data.profiles.length ? (
        <Empty>{live && !data.active_id ? copy("liveNoProfiles") : t("cust.models.empty2")}</Empty>
      ) : (
        data.profiles.map((p) => (
          <ProfileRow
            key={asString(p.id)}
            p={p}
            activeId={data.active_id}
            protocols={protocols}
            onEdit={() => startEdit(p)}
          />
        ))
      )}
    </div>
  );
}

/**
 * The configuration turns run on when no saved profile is active. A plain
 * render helper rather than a component: it holds no state of its own.
 */
function liveModelRow(live: LiveModel, protocols: ProtocolOption[]) {
  const bits: string[] = [];
  if (live.provider) bits.push(protocolLabelOf(protocols, live.provider));
  bits.push(live.model);
  bits.push(
    live.hasKey
      ? t("cust.models.hasKey")
      : loopbackModelBase(live.baseUrl)
        ? t("cust.models.local.keyless")
        : t("cust.models.noKey"),
  );
  return (
    <div class="cust-row prof-row" data-live-model="true">
      <div class="info">
        <div class="nm">
          <span>{live.model}</span> <Pill>{t("cust.models.activePill")}</Pill>
        </div>
        <div class="ds">
          {bits.join(" · ") + (live.baseUrl ? "  ·  " + live.baseUrl : "")}
        </div>
        <div class="ds">{copy("liveSource")}</div>
      </div>
    </div>
  );
}

function LocalEndpointRow({
  endpoint,
  profiles,
}: {
  endpoint: {
    label: string;
    base_url: string;
    models: string[];
    default_model: string;
  };
  profiles: Profile[];
}) {
  const [model, setModel] = useState(endpoint.default_model);
  const configured = profiles.some(
    (profile) =>
      loopbackModelBase(profile.base_url) === endpoint.base_url && profile.model === model,
  );
  return (
    <div class="cust-row local-model-row">
      <div class="info">
        <div class="nm">{endpoint.label}</div>
        <div class="ds">
          {endpoint.base_url + " · " + t("cust.models.local.models", endpoint.models.length)}
        </div>
      </div>
      <select
        class="cust-input local-model-select"
        value={model}
        onChange={(e) => setModel((e.target as HTMLSelectElement).value)}
      >
        {!endpoint.models.length ? (
          <option value="">{t("models.none")}</option>
        ) : (
          endpoint.models.map((m) => (
            <option value={m} key={m}>
              {m}
            </option>
          ))
        )}
      </select>
      <button
        type="button"
        class="outline-btn small"
        disabled={configured || !model}
        onClick={async () => {
          const next = publicText(model, 512);
          if (!next || configured) return;
          try {
            await api("/model-profiles", {
              method: "POST",
              body: JSON.stringify({
                name: endpoint.label + " · " + next,
                provider: "chatgpt",
                base_url: endpoint.base_url,
                model: next,
              }),
            });
            hint(t("cust.models.local.added", next));
            custTab("models");
          } catch (error) {
            hint(t("artifact.save.err", publicText((error as Error).message, 240)), true);
          }
        }}
      >
        {configured ? t("cust.models.local.configured") : t("cust.models.local.add")}
      </button>
    </div>
  );
}

function ProfileRow({
  p,
  activeId,
  protocols,
  onEdit,
}: {
  p: Profile;
  activeId: string;
  protocols: ProtocolOption[];
  onEdit: () => void;
}) {
  const isActive = p.id === activeId;
  const rd = (p.readiness && typeof p.readiness === "object"
    ? p.readiness
    : {}) as Record<string, unknown>;
  const [probe, setProbe] = useState<string | null>(null);
  const [probeClass, setProbeClass] = useState("ds prof-probe");
  const [probeReason, setProbeReason] = useState("");
  const [receipt, setReceipt] = useState<CapabilityReceipt | null>(
    readCapabilityReceipt(p.capability_receipt),
  );
  const [testing, setTesting] = useState(false);
  const bits: string[] = [];
  if (p.provider) bits.push(protocolLabelOf(protocols, p.provider));
  if (p.model) bits.push(asString(p.model));
  bits.push(profileKeyLabel(p));
  return (
    <div class="cust-row prof-row">
      <div class="info">
        <div class="nm">
          <span>{asString(p.name || p.id)}</span>
          {isActive ? (
            <>
              {" "}
              <Pill>{t("cust.models.activePill")}</Pill>
            </>
          ) : null}
        </div>
        <div class="ds">
          {bits.join(" · ") + (p.base_url ? "  ·  " + asString(p.base_url) : "")}
        </div>
        {rd.state && rd.state !== "ready" ? (
          <div class="ds prof-warn">{publicText(rd.detail || rd.state, 200)}</div>
        ) : null}
        <CapabilityBadges receipt={receipt} unknownReason={probeReason} />
        {probe != null ? <div class={probeClass}>{probe}</div> : null}
      </div>
      {!isActive ? (
        <button
          type="button"
          class="outline-btn small"
          onClick={async () => {
            try {
              await api(`/model-profiles/${p.id}/activate`, { method: "POST" });
              hint(t("toast.models.switched", asString(p.name || p.id)));
              defaultModel.value = p.model || defaultModel.value;
              await loadModels();
              await refreshKeyBanner();
              custTab("models");
            } catch (e) {
              hint(t("toast.switchFailed", apiErrorText(e)), true);
            }
          }}
        >
          {t("cust.models.setActive")}
        </button>
      ) : (
        <div class="col-spacer" />
      )}
      <button
        type="button"
        class="outline-btn small"
        disabled={testing}
        onClick={async () => {
          setTesting(true);
          setProbe(t("cust.models.testing"));
          setProbeClass("ds prof-probe");
          try {
            const r = await api(`/model-profiles/${encodeURIComponent(asString(p.id))}/probe`, {
              method: "POST",
            });
            const detail = publicText(r.detail, 240);
            setProbeClass("ds prof-probe " + (r.reachable ? "ok" : "bad"));
            setProbe(
              (r.reachable ? t("cust.models.reachable") : t("cust.models.unreachable")) +
                (detail ? " — " + detail : ""),
            );
            setProbeReason(r.reachable ? "" : detail);
            const next = readCapabilityReceipt(r.capability_receipt);
            if (next) setReceipt(next);
          } catch (e) {
            setProbeClass("ds prof-probe bad");
            const text = apiErrorText(e);
            setProbe(text);
            setProbeReason(text);
          } finally {
            setTesting(false);
          }
        }}
      >
        {t("cust.models.test")}
      </button>
      <button type="button" class="outline-btn small" onClick={onEdit}>
        {t("common.edit")}
      </button>
      <IconGhost
        name="trash-2"
        title={t("common.delete")}
        size={14}
        onClick={async () => {
          if (!confirmAction(t("model.delete.confirm", asString(p.name || p.id)))) return;
          try {
            await api(`/model-profiles/${p.id}`, { method: "DELETE" });
            hint(t("toast.deleted"));
            if (isActive) {
              await refreshKeyBanner();
              await loadModels();
            }
            custTab("models");
          } catch (e) {
            hint(t("toast.deleteFailed", apiErrorText(e)), true);
          }
        }}
      />
    </div>
  );
}
