import { useState } from "preact/hooks";
import { t } from "../../i18n";
import { api, apiErrorText } from "../../features/customize/api";
import { refreshCustTab } from "../../features/customize/actions";
import { asList, asString, customizeProject, hint } from "../../features/customize/host";
import { MEMORY_BLOCKS, memScopeIds, memScopeLabel } from "../../features/customize/memory";
import { useAlive } from "./use-timer-lease";
import { useOptimisticToggle, useTabRead } from "./hooks";
import { Empty, Hdr, IconGhost, Pill, Subhead, Toggle } from "./ui";

/**
 * A scope's name. Its own component: the project list it reads changes far
 * more often than anything else on this tab.
 */
function ScopeName({ pid }: { pid: string }) {
  return <>{memScopeLabel(pid)}</>;
}

function InjectedInto({ pid }: { pid: string }) {
  return <>{t("cust.memory.injectedInto", memScopeLabel(pid))}</>;
}

export function MemoryTab() {
  const alive = useAlive();
  const [err, setErr] = useState<string | null>(null);
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [memories, setMemories] = useState<Record<string, unknown>[]>([]);
  const [cats, setCats] = useState<Record<string, unknown>[]>([]);
  const [ctx, setCtx] = useState<Record<string, unknown> | null>(null);
  const scopes = memScopeIds(customizeProject.value);
  const active = scopes[scopes.length - 1] || "global";
  const [block, setBlock] = useState("user");
  const [scope, setScope] = useState(active);
  const [content, setContent] = useState("");
  const [saving, setSaving] = useState(false);
  const memory = useOptimisticToggle(
    enabled,
    (on) =>
      api("/memory/enabled", {
        method: "PUT",
        body: JSON.stringify({ enabled: on }),
      }),
    { done: (on) => hint(on ? t("toast.memory.enabled") : t("toast.memory.disabled")) },
  );

  useTabRead(
    "memory",
    async (current) => {
      const m = await api("/memory/enabled");
      const mem = await api("/memory?project_id=all").catch(() => ({ memories: [] }));
      const cat = await api("/memory/categories?project_id=all").catch(() => ({
        categories: [],
      }));
      const context = await api(
        `/memory/context?project_id=${encodeURIComponent(active)}`,
      ).catch(() => null);
      if (!current()) return;
      setEnabled(!!m.enabled);
      setMemories(asList(mem.memories) as Record<string, unknown>[]);
      setCats(asList(cat.categories) as Record<string, unknown>[]);
      setCtx(context);
      setScope(active);
    },
    setErr,
    [active],
  );

  if (err) return <div>{err}</div>;

  const groups: Record<string, Record<string, unknown>[]> = {};
  memories.forEach((x) => {
    const b = asString(x.block, "general");
    (groups[b] = groups[b] || []).push(x);
  });

  return (
    <div>
      <Hdr title={t("cust.memory.title")} sub={t("cust.memory.desc")} />
      <div class="cust-row">
        <div class="info">
          <div class="nm">{t("cust.memory.enableName")}</div>
          <div class="ds">
            {memory.on ? t("cust.memory.enabledDesc") : t("cust.memory.disabledDesc")}
          </div>
        </div>
        <Toggle on={memory.on} disabled={!memory.ready} onClick={memory.toggle} />
      </div>
      <div class="cust-row">
        <div class="info">
          <div class="nm">{t("cust.memory.addName")}</div>
          <div class="job-submit">
            <select
              class="cust-input"
              style={{ flex: "0 0 150px" }}
              title={t("cust.memory.scopeName")}
              value={scope}
              onChange={(e) => setScope((e.target as HTMLSelectElement).value)}
            >
              {scopes.map((id) => (
                <option value={id} key={id}>
                  <ScopeName pid={id} />
                </option>
              ))}
            </select>
            <select
              class="cust-input"
              style={{ flex: "0 0 120px" }}
              value={block}
              onChange={(e) => setBlock((e.target as HTMLSelectElement).value)}
            >
              {MEMORY_BLOCKS.map((k) => (
                <option value={k} key={k}>
                  {k}
                </option>
              ))}
            </select>
            <input
              class="cust-input"
              placeholder={t("cust.memory.contentPlaceholder")}
              value={content}
              onInput={(e) => setContent((e.target as HTMLInputElement).value)}
            />
            <button
              type="button"
              class="solid-btn small"
              disabled={saving}
              onClick={async () => {
                const v = content.trim();
                if (!v || saving) return;
                setSaving(true);
                try {
                  await api("/memory", {
                    method: "POST",
                    body: JSON.stringify({
                      content: v,
                      block,
                      project_id: scope,
                    }),
                  });
                  setContent("");
                  refreshCustTab("memory");
                } catch (e) {
                  hint(t("artifact.save.err", apiErrorText(e)), true);
                } finally {
                  if (alive()) setSaving(false);
                }
              }}
            >
              {t("common.save")}
            </button>
          </div>
        </div>
      </div>
      {ctx ? (
        <div class="cust-row">
          <div class="info">
            <div class="nm">
              <InjectedInto pid={active} />
            </div>
            <div class="ds">
              {t(
                "cust.memory.injectedCounts",
                String(ctx.included_count || 0),
                String(asList(ctx.omitted).length),
                String(ctx.inherited_count || 0),
                String(ctx.overridden_count || 0),
              )}
            </div>
          </div>
        </div>
      ) : null}
      {cats.length ? (
        <div class="cust-row">
          <div class="info">
            <div class="nm">{t("cust.memory.categories")}</div>
            <div class="ds">
              {cats.map((k, i) => (
                <Pill key={i}>
                  {asString(k.block, "general") + " · " + String(k.count)}
                </Pill>
              ))}
            </div>
          </div>
        </div>
      ) : null}
      {Object.keys(groups)
        .sort()
        .map((b) => (
          <div key={b}>
            <Subhead>{b}</Subhead>
            {(groups[b] || []).map((x) => (
              <div class="cust-row" key={asString(x.memory_id)}>
                <div class="info">
                  <div class="ds">{asString(x.content)}</div>
                  <div class="ds">
                    <Pill>
                      <ScopeName pid={asString(x.project_id)} />
                    </Pill>
                    {x.updated_at ? <Pill>{t("cust.memory.edited")}</Pill> : null}
                  </div>
                </div>
                <IconGhost
                  name="pencil"
                  title={t("common.edit")}
                  size={14}
                  onClick={async () => {
                    const next = window.prompt(t("cust.memory.editPrompt"), asString(x.content));
                    if (next === null) return;
                    const value = String(next).trim();
                    if (!value || value === asString(x.content)) return;
                    try {
                      await api(
                        `/memory/${x.memory_id}?project_id=${encodeURIComponent(asString(x.project_id, "global"))}`,
                        { method: "PATCH", body: JSON.stringify({ content: value }) },
                      );
                      refreshCustTab("memory");
                    } catch (e) {
                      hint(apiErrorText(e), true);
                    }
                  }}
                />
                <IconGhost
                  name="trash-2"
                  title={t("common.delete")}
                  size={14}
                  onClick={async () => {
                    try {
                      await api(
                        `/memory/${x.memory_id}?project_id=${encodeURIComponent(asString(x.project_id, "global"))}`,
                        { method: "DELETE" },
                      );
                      refreshCustTab("memory");
                    } catch (e) {
                      hint(apiErrorText(e), true);
                    }
                  }}
                />
              </div>
            ))}
          </div>
        ))}
      {!memories.length ? <Empty>{t("cust.memory.empty")}</Empty> : null}
    </div>
  );
}
