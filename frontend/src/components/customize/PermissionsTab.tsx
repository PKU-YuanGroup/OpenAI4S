import { useEffect, useState } from "preact/hooks";
import { t } from "../../i18n";
import { api, apiErrorText } from "../../features/customize/api";
import { custTab } from "../../features/customize/actions";
import { confirmAction, hint } from "../../features/customize/host";
import { currentId } from "../../stores/session";
import { useAlive } from "./use-timer-lease";
import { markCustomizeFailed, markCustomizeLoaded } from "../../features/customize/load";
import { Hdr, IconGhost, Note } from "./ui";

type ScopeMeta = { scope: string; scope_id: string; label: string };
type Rule = { rule_id: string; tool: string; pattern: string; decision: string };

function DecSelect({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <select
      class="perm-dec"
      value={value}
      onChange={(e) => onChange((e.target as HTMLSelectElement).value)}
    >
      <option value="allow">{t("perm.btn.allow")}</option>
      <option value="ask">{t("cust.perm.decision.ask")}</option>
      <option value="deny">{t("perm.btn.deny")}</option>
    </select>
  );
}

function ResetRow() {
  return (
    <div class="cust-row">
      <div class="info">
        <div class="nm">{t("cust.perm.resetName")}</div>
        <div class="ds">{t("cust.perm.resetDesc")}</div>
      </div>
      <button
        type="button"
        class="outline-btn small"
        onClick={async () => {
          if (!confirmAction(t("cust.perm.resetConfirm"))) return;
          try {
            await api("/permissions/reset", { method: "POST" });
            custTab("permissions");
            hint(t("toast.perm.resetDone"));
          } catch (e) {
            hint(t("toast.failed", apiErrorText(e)), true);
          }
        }}
      >
        {t("cust.perm.resetBtn")}
      </button>
    </div>
  );
}

function AddRow({ g }: { g: ScopeMeta }) {
  const [tool, setTool] = useState("");
  const [pat, setPat] = useState("*");
  const [dec, setDec] = useState("ask");
  return (
    <div class="perm-rule perm-add">
      <input
        class="perm-in"
        placeholder={t("cust.perm.toolPlaceholder")}
        value={tool}
        onInput={(e) => setTool((e.target as HTMLInputElement).value)}
      />
      <input
        class="perm-in"
        placeholder={t("cust.perm.patternPlaceholder")}
        value={pat}
        onInput={(e) => setPat((e.target as HTMLInputElement).value)}
      />
      <DecSelect value={dec} onChange={setDec} />
      <button
        type="button"
        class="outline-btn small"
        onClick={async () => {
          if (!tool.trim()) {
            hint(t("toast.perm.enterTool"), true);
            return;
          }
          try {
            await api("/permissions", {
              method: "POST",
              body: JSON.stringify({
                scope: g.scope,
                scope_id: g.scope_id,
                tool: tool.trim(),
                pattern: pat.trim() || "*",
                decision: dec,
              }),
            });
            custTab("permissions");
          } catch (e) {
            hint(t("toast.addFailed", apiErrorText(e)), true);
          }
        }}
      >
        {t("common.add")}
      </button>
    </div>
  );
}

export function PermissionsTab() {
  const alive = useAlive();
  const [note, setNote] = useState<string | null>(null);
  const [data, setData] = useState<Record<string, unknown> | null>(null);

  useEffect(() => {
    if (!currentId.value) {
      setNote(t("cust.perm.noSessionNote"));
      markCustomizeLoaded();
      return;
    }
    void (async () => {
      try {
        const next = await api(`/frames/${currentId.value}/permissions`);
        if (!alive()) return;
        setData(next);
        markCustomizeLoaded();
      } catch (e) {
        if (!alive()) return;
        const message = t("versions.load.err", (e as Error).message);
        setNote(message);
        markCustomizeFailed(message);
      }
    })();
  }, [alive]);

  const meta: Record<string, ScopeMeta> | null = data
    ? {
        global: { scope: "global", scope_id: "", label: t("cust.perm.scope.global") },
        project: {
          scope: "project",
          scope_id: String(data.project_id || ""),
          label: t("cust.perm.scope.project"),
        },
        conversation: {
          scope: "conversation",
          scope_id: String(data.root_frame_id || ""),
          label: t("cust.perm.scope.conversation"),
        },
      }
    : null;

  return (
    <div>
      <Hdr title={t("cust.perm.title")} sub={t("cust.perm.desc")} />
      {note ? <Note>{note}</Note> : null}
      {meta
        ? (["conversation", "project", "global"] as const).map((k) => {
            const g = meta[k]!;
            const rules = ((data!.rules as Record<string, Rule[]> | undefined) || {})[
              k
            ] || [];
            return (
              <div class="perm-sec" key={k}>
                <div class="perm-sec-h">{g.label}</div>
                {!rules.length ? <Note>{t("cust.perm.noRules")}</Note> : null}
                {rules.map((r) => (
                  <div class="perm-rule" key={r.rule_id}>
                    <span class="perm-rtool">{r.tool}</span>
                    <span class="perm-rpat mono">{r.pattern}</span>
                    <DecSelect
                      value={r.decision}
                      onChange={async (v) => {
                        try {
                          await api("/permissions", {
                            method: "POST",
                            body: JSON.stringify({
                              scope: g.scope,
                              scope_id: g.scope_id,
                              tool: r.tool,
                              pattern: r.pattern,
                              decision: v,
                            }),
                          });
                          hint(t("toast.perm.ruleUpdated"));
                        } catch (e) {
                          hint(t("toast.perm.updateFailed", apiErrorText(e)), true);
                        }
                      }}
                    />
                    <IconGhost
                      name="trash-2"
                      title={t("common.delete")}
                      onClick={async () => {
                        try {
                          await api(`/permissions/${r.rule_id}`, { method: "DELETE" });
                          custTab("permissions");
                        } catch (e) {
                          hint(t("toast.deleteFailed", apiErrorText(e)), true);
                        }
                      }}
                    />
                  </div>
                ))}
                <AddRow g={g} />
              </div>
            );
          })
        : null}
      <ResetRow />
    </div>
  );
}
