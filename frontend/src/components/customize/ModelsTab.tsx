import { useEffect, useState } from "preact/hooks";
import { t } from "../../i18n";
import { publicText } from "../../features/scrub/scrub";
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
import { Empty, Hdr, IconGhost, Pill, Subhead } from "./ui";
import { VolcenginePanel } from "./vendors/volcengine";

type Profile = Record<string, unknown>;

export function ModelsTab() {
  const alive = useAlive();
  const [err, setErr] = useState<string | null>(null);
  const [data, setData] = useState<{
    profiles: Profile[];
    active_id: string;
    protocols: unknown[];
  }>({ profiles: [], active_id: "", protocols: [] });
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
        const next = await api("/model-profiles");
        if (!alive()) return;
        setData({
          profiles: asList(next.profiles) as Profile[],
          active_id: asString(next.active_id),
          protocols: asList(next.protocols),
        });
      } catch (e) {
        if (!alive()) return;
        setErr(t("versions.load.err", (e as Error).message));
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
      {!data.profiles.length ? (
        <Empty>{t("cust.models.empty2")}</Empty>
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
  bits.push(
    p.has_api_key
      ? t("cust.models.hasKey")
      : loopbackModelBase(p.base_url)
        ? t("cust.models.local.keyless")
        : t("cust.models.noKey"),
  );
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
