import { useState } from "preact/hooks";
import { LANG, t } from "../../i18n";
import { api, apiErrorText } from "../../features/customize/api";
import { refreshCustTab } from "../../features/customize/actions";
import { nestedEditor } from "../../features/customize/state";
import { DATAPRO_CONNECTOR_ID } from "../../features/customize/vendors";
import { connectorDescription } from "../../features/customize/copy";
import {
  asList,
  asString,
  confirmAction,
} from "../../features/customize/host";
import { markCustomizeLoaded } from "../../features/customize/load";
import { useOptimisticToggle, useTabRead } from "./hooks";
import { ActionStatus, Hdr, IconGhost, Pill, Subhead, Toggle, type ActionNote } from "./ui";
import { DataProCard } from "./vendors/datapro";

export function ConnectorsTab() {
  const [err, setErr] = useState<string | null>(null);
  const [conns, setConns] = useState<Record<string, unknown>[]>([]);
  const [directory, setDirectory] = useState<Record<string, unknown>[]>([]);
  const [datapro, setDatapro] = useState<{
    config: Record<string, unknown> | null;
    error: unknown;
  }>({ config: null, error: null });
  const [name, setName] = useState("");
  const [cmd, setCmd] = useState("");
  // SCI-04: adding from the directory and adding a custom server answer here.
  const [dirNote, setDirNote] = useState<ActionNote>(null);
  const [customNote, setCustomNote] = useState<ActionNote>(null);

  useTabRead(
    "connectors",
    async (current) => {
      const [d, dp] = await Promise.all([
        api("/connectors"),
        api("/datapro/config")
          .then((config) => ({ config, error: null as unknown }))
          .catch((error) => ({ config: {} as Record<string, unknown>, error })),
      ]);
      if (!current()) return;
      setConns(asList(d.connectors) as Record<string, unknown>[]);
      setDatapro(dp);
      markCustomizeLoaded();
      try {
        const dir = await api("/connectors/directory");
        if (!current()) return;
        setDirectory(asList(dir.directory) as Record<string, unknown>[]);
      } catch {
        if (!current()) return;
        setDirectory([]);
      }
    },
    setErr,
  );

  if (err) return <div>{err}</div>;

  return (
    <div>
      <Hdr title={t("cust.tab.connectors")} sub={t("cust.connectors.desc")} />
      <DataProCard config={datapro.config} configError={datapro.error} />
      {conns
        .filter((k) => k.connector_id !== DATAPRO_CONNECTOR_ID)
        .map((k) => (
          <ConnectorRow
            key={asString(k.connector_id)}
            k={k}
            catalog={directory.find((item) => item.id === k.connector_id)?.description}
          />
        ))}
      <Subhead>{t("cust.connectors.fromDirectory")}</Subhead>
      <ActionStatus note={dirNote} />
      {directory.map((item) => {
        if (
          item.id === DATAPRO_CONNECTOR_ID ||
          conns.some((k) => k.connector_id === item.id)
        ) {
          return null;
        }
        return (
          <div class="cust-row" key={asString(item.id)}>
            <div class="info">
              <div class="nm">{asString(item.name)}</div>
              <div class="ds">{connectorDescription(item.id, item.description)}</div>
            </div>
            <button
              type="button"
              class="outline-btn small"
              onClick={async () => {
                try {
                  const request: Record<string, unknown> = {
                    connector_id: item.id,
                    name: item.name,
                    description: item.description,
                    command: item.command,
                  };
                  if (item.args) request.args = item.args;
                  if (item.env) request.env = item.env;
                  await api("/connectors", {
                    method: "POST",
                    body: JSON.stringify(request),
                  });
                  setDirNote({ text: t("toast.connectors.added", asString(item.name)) });
                  refreshCustTab("connectors");
                } catch (e) {
                  setDirNote({ text: t("toast.addFailed", apiErrorText(e)), err: true });
                }
              }}
            >
              {t("common.add")}
            </button>
          </div>
        );
      })}
      <div class="cust-row">
        <div class="info">
          <div class="nm">{t("cust.connectors.customAddName")}</div>
          <div class="job-submit">
            <input
              class="cust-input"
              placeholder={t("cust.connectors.namePlaceholder")}
              style={{ flex: "0 0 120px" }}
              value={name}
              onInput={(e) => setName((e.target as HTMLInputElement).value)}
            />
            <input
              class="cust-input"
              placeholder={t("cust.connectors.cmdPlaceholder")}
              value={cmd}
              onInput={(e) => setCmd((e.target as HTMLInputElement).value)}
            />
            <button
              type="button"
              class="solid-btn small"
              // Both fields are required; an empty Add used to do nothing visible.
              disabled={!name.trim() || !cmd.trim()}
              onClick={async () => {
                const nm = name.trim();
                const command = cmd.trim();
                if (!nm || !command) return;
                try {
                  await api("/connectors", {
                    method: "POST",
                    body: JSON.stringify({ name: nm, command: command.split(/\s+/) }),
                  });
                  setName("");
                  setCmd("");
                  setCustomNote({ text: t("toast.connectors.added", nm) });
                  refreshCustTab("connectors");
                } catch (e) {
                  setCustomNote({ text: t("toast.addFailed", apiErrorText(e)), err: true });
                }
              }}
            >
              {t("common.add")}
            </button>
          </div>
          <ActionStatus note={customNote} />
        </div>
      </div>
    </div>
  );
}

export function ConnectorRow({ k, catalog }: { k: Record<string, unknown>; catalog?: unknown }) {
  const enabled = useOptimisticToggle(!!k.enabled, (on) =>
    api(`/connectors/${k.connector_id}/enabled`, {
      method: "PUT",
      body: JSON.stringify({ enabled: on }),
    }),
  );
  const [testing, setTesting] = useState(false);
  // SCI-04: a test's answer (the tools it found, or why it failed) in the row.
  const [note, setNote] = useState<ActionNote>(null);
  return (
    <div class="cust-row">
      <div class="info">
        <div class="nm">
          <span>{asString(k.name)}</span> <Pill>{asString(k.connector_id)}</Pill>
        </div>
        <div class="ds">{connectorDescription(k.connector_id, k.description, catalog)}</div>
        <ActionStatus note={note} />
      </div>
      <IconGhost
        name="pencil"
        title={t("common.edit")}
        onClick={() => {
          nestedEditor.value = { kind: "connector", connector: k };
        }}
      />
      <button
        type="button"
        class="outline-btn small"
        disabled={testing}
        onClick={async () => {
          setTesting(true);
          setNote(null);
          try {
            const r = await api(`/connectors/${k.connector_id}/probe`, { method: "POST" });
            const tools = asList(r.tools) as Array<{ name?: string }>;
            setNote(
              r.ok
                ? { text: t("toast.connectors.probeOk", tools.map((x) => x.name).join(LANG === "en" ? ", " : "、")) }
                : { text: t("toast.failed", asString(r.error)), err: true },
            );
          } catch (e) {
            setNote({ text: t("toast.connectors.testFailed", apiErrorText(e)), err: true });
          }
          setTesting(false);
        }}
      >
        {testing ? t("cust.connectors.testing") : t("cust.connectors.test")}
      </button>
      <Toggle on={enabled.on} label={asString(k.name)} onClick={enabled.toggle} />
      <IconGhost
        name="trash-2"
        title={t("common.delete")}
        onClick={async () => {
          if (!(await confirmAction(t("cust.connectors.deleteConfirm", asString(k.name))))) return;
          try {
            await api(`/connectors/${k.connector_id}`, { method: "DELETE" });
            refreshCustTab("connectors");
          } catch (e) {
            setNote({ text: t("toast.deleteFailed", apiErrorText(e)), err: true });
          }
        }}
      />
    </div>
  );
}
