import { useEffect, useState } from "preact/hooks";
import { t } from "../../i18n";
import { api, apiErrorText } from "../../features/customize/api";
import { custTab } from "../../features/customize/actions";
import { nestedEditor } from "../../features/customize/state";
import { DATAPRO_CONNECTOR_ID } from "../../features/customize/vendors";
import {
  asList,
  asString,
  confirmAction,
  hint,
} from "../../features/customize/host";
import { useAlive } from "./use-timer-lease";
import { Hdr, IconGhost, Pill, Subhead, Toggle } from "./ui";
import { DataProCard } from "./vendors/datapro";

export function ConnectorsTab() {
  const alive = useAlive();
  const [err, setErr] = useState<string | null>(null);
  const [conns, setConns] = useState<Record<string, unknown>[]>([]);
  const [directory, setDirectory] = useState<Record<string, unknown>[]>([]);
  const [datapro, setDatapro] = useState<{
    config: Record<string, unknown>;
    error: unknown;
  }>({ config: {}, error: null });
  const [name, setName] = useState("");
  const [cmd, setCmd] = useState("");

  useEffect(() => {
    void (async () => {
      try {
        const [d, dp] = await Promise.all([
          api("/connectors"),
          api("/datapro/config")
            .then((config) => ({ config, error: null as unknown }))
            .catch((error) => ({ config: {} as Record<string, unknown>, error })),
        ]);
        if (!alive()) return;
        setConns(asList(d.connectors) as Record<string, unknown>[]);
        setDatapro(dp);
        try {
          const dir = await api("/connectors/directory");
          if (!alive()) return;
          setDirectory(asList(dir.directory) as Record<string, unknown>[]);
        } catch {
          if (!alive()) return;
          setDirectory([]);
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
      <Hdr title={t("cust.tab.connectors")} sub={t("cust.connectors.desc")} />
      <DataProCard config={datapro.config} configError={datapro.error} />
      {conns
        .filter((k) => k.connector_id !== DATAPRO_CONNECTOR_ID)
        .map((k) => (
          <ConnectorRow key={asString(k.connector_id)} k={k} />
        ))}
      <Subhead>{t("cust.connectors.fromDirectory")}</Subhead>
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
              <div class="ds">{asString(item.description)}</div>
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
                  hint(t("toast.connectors.added", asString(item.name)));
                  custTab("connectors");
                } catch (e) {
                  hint(t("toast.addFailed", apiErrorText(e)), true);
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
                  custTab("connectors");
                } catch (e) {
                  hint(t("toast.addFailed", apiErrorText(e)), true);
                }
              }}
            >
              {t("common.add")}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function ConnectorRow({ k }: { k: Record<string, unknown> }) {
  const [on, setOn] = useState(!!k.enabled);
  const [testing, setTesting] = useState(false);
  return (
    <div class="cust-row">
      <div class="info">
        <div class="nm">
          <span>{asString(k.name)}</span> <Pill>{asString(k.connector_id)}</Pill>
        </div>
        <div class="ds">{asString(k.description)}</div>
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
          try {
            const r = await api(`/connectors/${k.connector_id}/probe`, { method: "POST" });
            const tools = asList(r.tools) as Array<{ name?: string }>;
            hint(
              r.ok
                ? t("toast.connectors.probeOk", tools.map((x) => x.name).join("、"))
                : t("toast.failed", asString(r.error)),
            );
          } catch (e) {
            hint(t("toast.connectors.testFailed", apiErrorText(e)), true);
          }
          setTesting(false);
        }}
      >
        {testing ? t("cust.connectors.testing") : t("cust.connectors.test")}
      </button>
      <Toggle
        on={on}
        onClick={async () => {
          const next = !on;
          setOn(next);
          try {
            await api(`/connectors/${k.connector_id}/enabled`, {
              method: "PUT",
              body: JSON.stringify({ enabled: next }),
            });
          } catch {
            setOn(!next);
          }
        }}
      />
      <IconGhost
        name="trash-2"
        title={t("common.delete")}
        onClick={async () => {
          if (!confirmAction(t("cust.connectors.deleteConfirm", asString(k.name)))) return;
          try {
            await api(`/connectors/${k.connector_id}`, { method: "DELETE" });
            custTab("connectors");
          } catch {
            /* original swallowed */
          }
        }}
      />
    </div>
  );
}
