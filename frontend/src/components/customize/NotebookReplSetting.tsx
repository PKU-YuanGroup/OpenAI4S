import { useEffect, useState } from "preact/hooks";
import { copyLookup } from "../../i18n/copy";
import { api, apiErrorText } from "../../features/customize/api";
import { hint } from "../../features/customize/host";
import { notebookOnTurnDone } from "../../features/notebook";
import { useAlive } from "./use-timer-lease";
import { useOptimisticToggle } from "./hooks";
import { Toggle } from "./ui";

/** Feature-local: the generated `i18n/en.ts` / `zh.ts` are byte-checked extracts. */
export const notebookReplCopy = copyLookup({
  zh: {
    name: "Notebook 手动单元",
    on: "已开启：可以在右侧 Notebook 里直接运行 Python/R 代码，与智能体共用同一个内核。",
    off: "已关闭：Notebook 只显示智能体运行过的代码和输出。",
    forced: "已由环境变量 OPENAI4S_NOTEBOOK_REPL 强制开启，这里不能关闭。",
    adminOnly: "只有管理员可以更改这个设置。",
    turnedOn: "已开启 Notebook 手动单元",
    turnedOff: "已关闭 Notebook 手动单元",
    failed: "无法更改 Notebook 手动单元：{0}",
  },
  en: {
    name: "Notebook manual cells",
    on: "On: you can run Python/R code in the Notebook panel; it shares the kernel with the agent.",
    off: "Off: the Notebook only shows the code the agent ran and its output.",
    forced: "Forced on by the OPENAI4S_NOTEBOOK_REPL environment variable; it cannot be turned off here.",
    adminOnly: "Only an admin can change this setting.",
    turnedOn: "Notebook manual cells turned on",
    turnedOff: "Notebook manual cells turned off",
    failed: "Could not change Notebook manual cells: {0}",
  },
});

type NotebookSettings = { repl_enabled: boolean; env_forced: boolean; can_change: boolean };

function readSettings(raw: unknown): NotebookSettings {
  const row = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
  return {
    repl_enabled: row.repl_enabled === true,
    env_forced: row.env_forced === true,
    can_change: row.can_change === true,
  };
}

/**
 * The on/off switch for manual Notebook cells (the REPL). It was reachable
 * only through `OPENAI4S_NOTEBOOK_REPL`, which the read-only Notebook never
 * mentioned. Off by default; admin-only in team mode; the variable still
 * forces it on.
 */
export function NotebookReplSetting() {
  const alive = useAlive();
  const [settings, setSettings] = useState<NotebookSettings | null>(null);
  const toggle = useOptimisticToggle(
    settings ? settings.repl_enabled : null,
    (on) =>
      api("/notebook/settings", {
        method: "PUT",
        body: JSON.stringify({ repl_enabled: on }),
      }),
    {
      done: (on) => {
        hint(notebookReplCopy(on ? "turnedOn" : "turnedOff"));
        // The open Notebook reads the switch from its kernel status.
        notebookOnTurnDone();
      },
      failed: (error) => hint(notebookReplCopy("failed", apiErrorText(error)), true),
    },
  );

  useEffect(() => {
    void (async () => {
      try {
        const raw = await api("/notebook/settings");
        if (alive()) setSettings(readSettings(raw));
      } catch {
        /* an older daemon without the switch: show nothing */
      }
    })();
  }, [alive]);

  if (!settings) return null;
  const locked = settings.env_forced || !settings.can_change;
  const desc = settings.env_forced
    ? notebookReplCopy("forced")
    : (toggle.on ? notebookReplCopy("on") : notebookReplCopy("off")) +
      (settings.can_change ? "" : " " + notebookReplCopy("adminOnly"));
  return (
    <div class="cust-row" data-setting="notebook-repl">
      <div class="info">
        <div class="nm">{notebookReplCopy("name")}</div>
        <div class="ds">{desc}</div>
      </div>
      <Toggle
        on={toggle.on}
        disabled={!toggle.ready || toggle.busy || locked}
        title={notebookReplCopy("name")}
        onClick={() => {
          if (!locked) toggle.toggle();
        }}
      />
    </div>
  );
}
