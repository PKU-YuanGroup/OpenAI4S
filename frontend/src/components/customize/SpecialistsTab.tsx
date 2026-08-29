import { useEffect, useState } from "preact/hooks";
import { t } from "../../i18n";
import { api, apiErrorText } from "../../features/customize/api";
import { custTab } from "../../features/customize/actions";
import { nestedEditor } from "../../features/customize/state";
import {
  asList,
  asString,
  confirmAction,
  hint,
} from "../../features/customize/host";
import { useAlive } from "./use-timer-lease";
import { Hdr, IconGhost, Pill, Subhead, Toggle } from "./ui";

export function SpecialistsTab() {
  const alive = useAlive();
  const [err, setErr] = useState<string | null>(null);
  const [builtin, setBuiltin] = useState<Record<string, unknown>[]>([]);
  const [custom, setCustom] = useState<Record<string, unknown>[]>([]);

  useEffect(() => {
    void (async () => {
      try {
        const d = await api("/specialists");
        if (!alive()) return;
        setBuiltin(asList(d.builtin) as Record<string, unknown>[]);
        setCustom(asList(d.specialists) as Record<string, unknown>[]);
      } catch (e) {
        if (!alive()) return;
        setErr(t("versions.load.err", (e as Error).message));
      }
    })();
  }, [alive]);

  if (err) return <div>{err}</div>;

  return (
    <div>
      <Hdr title={t("cust.tab.specialists")} sub={t("cust.specialists.desc")} />
      <div class="cust-row">
        <div class="info">
          <div class="nm">{t("cust.specialists.yours")}</div>
          <div class="cust-actrow">
            <button
              type="button"
              class="outline-btn small"
              onClick={() => {
                nestedEditor.value = { kind: "specialist", name: null };
              }}
            >
              {t("cust.specialists.newBtn")}
            </button>
          </div>
        </div>
      </div>
      {custom.map((s) => (
        <div class="cust-row" key={asString(s.name)}>
          <div class="info">
            <div class="nm">
              <span>{asString(s.name)}</span> <Pill>custom</Pill>
            </div>
            <div class="ds">{asString(s.description)}</div>
          </div>
          <IconGhost
            name="pencil"
            title={t("common.edit")}
            onClick={() => {
              nestedEditor.value = { kind: "specialist", name: asString(s.name) };
            }}
          />
          <IconGhost
            name="trash-2"
            title={t("common.delete")}
            onClick={async () => {
              if (!confirmAction(t("cust.specialists.deleteConfirm", asString(s.name))))
                return;
              try {
                await api(`/specialists/${encodeURIComponent(asString(s.name))}`, {
                  method: "DELETE",
                });
                custTab("specialists");
              } catch (e) {
                hint(t("toast.deleteFailed", apiErrorText(e)), true);
              }
            }}
          />
        </div>
      ))}
      <Subhead>{t("cust.specialists.builtinRoles")}</Subhead>
      {builtin.map((ag) => (
        <BuiltinRow key={asString(ag.name)} ag={ag} />
      ))}
    </div>
  );
}

function BuiltinRow({ ag }: { ag: Record<string, unknown> }) {
  const [on, setOn] = useState(ag.enabled !== false);
  return (
    <div class="cust-row">
      <div class="info">
        <div class="nm">
          <span>{asString(ag.name)}</span> <Pill>{asString(ag.mode, "agent")}</Pill>
          {ag.supportsPlanMode ? (
            <>
              {" "}
              <Pill>plan</Pill>
            </>
          ) : null}
        </div>
        <div class="ds">{asString(ag.description)}</div>
      </div>
      <Toggle
        on={on}
        onClick={async () => {
          const next = !on;
          setOn(next);
          try {
            await api(`/agents/${encodeURIComponent(asString(ag.name))}/enabled`, {
              method: "PUT",
              body: JSON.stringify({ enabled: next }),
            });
          } catch {
            setOn(!next);
          }
        }}
      />
    </div>
  );
}
