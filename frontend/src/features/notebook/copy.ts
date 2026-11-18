import { copyLookup, type CopyTable } from "../../i18n/copy";

/**
 * Notebook strings the generated `i18n/en.ts` / `zh.ts` extract does not
 * carry. New Notebook copy goes here, never into the generated dictionaries.
 */
const COPY: CopyTable = {
  zh: {
    "nb.crash.notice": "内核进程在这个单元完成前退出了（{0}）。之前单元里的变量都已丢失；下一个单元会在新的内核里运行。",
    "nb.crash.oom": "退出码 {0}，通常表示内存不足",
    "nb.crash.exitCode": "退出码 {0}",
    "nb.crash.signal": "被信号 {0} 终止",
    "nb.crash.unknown": "退出状态未知",
    "nb.generation.divider": "从这里开始是一个新的内核：之前单元定义的变量在这个内核里不存在。",
    "nb.output.stdout": "输出",
    "nb.output.stderr": "标准错误（stderr）",
    "nb.output.long": "{0} · {1} 行",
    "nb.interrupt.label": "中断",
    "nb.interrupt.title": "中断正在运行的单元（相当于 KeyboardInterrupt，内核和变量保留）",
    "nb.kernel.action.restart": "正在重启…",
    "nb.kernel.action.stop": "正在停止…",
    "nb.kernel.action.start": "正在启动…",
    "nb.kernel.restartWaits": "智能体正在使用内核，重启会在它当前的执行结束后进行。",
    "nb.repl.keys": "Tab 缩进 · Shift+Tab 取消缩进 · ↑/↓ 调出之前的输入 · Shift/Ctrl/⌘+Enter 运行 · 按 Esc 后 Tab 可离开输入框",
    "nb.action.edit": "编辑为新单元",
    "nb.action.editTitle": "把这段代码放进下面的输入框，修改后作为新单元运行",
    "nb.status.notStarted": "{0} · 未启动 — 第一次运行代码时启动",
    "nb.status.stopped": "{0} · 已停止 — 内存中的变量已释放，再次运行代码会启动新的内核",
    "nb.status.exited": "{0} · 已退出 — 下一次运行代码会启动新的内核",
    "nb.kernel.notStarted": "未启动",
    "nb.readonly.hint": "这是只读的执行记录：显示智能体运行过的代码和输出。要自己运行代码，请在 设置 → 通用 中开启「Notebook 手动单元」。",
    "nb.readonly.openSettings": "打开设置",
  },
  en: {
    "nb.crash.notice":
      "The kernel process exited before this cell finished ({0}). Variables from earlier cells are lost; the next cell runs in a fresh kernel.",
    "nb.crash.oom": "exit code {0}, which usually means it ran out of memory",
    "nb.crash.exitCode": "exit code {0}",
    "nb.crash.signal": "killed by signal {0}",
    "nb.crash.unknown": "exit status unknown",
    "nb.generation.divider": "A new kernel starts here: variables defined by earlier cells do not exist in it.",
    "nb.output.stdout": "Output",
    "nb.output.stderr": "stderr",
    "nb.output.long": "{0} · {1} lines",
    "nb.interrupt.label": "Interrupt",
    "nb.interrupt.title": "Interrupt the running cell (a KeyboardInterrupt; the kernel and its variables are kept)",
    "nb.kernel.action.restart": "Restarting…",
    "nb.kernel.action.stop": "Stopping…",
    "nb.kernel.action.start": "Starting…",
    "nb.kernel.restartWaits": "The agent is using the kernel; the restart runs when its current execution ends.",
    "nb.repl.keys": "Tab indents · Shift+Tab dedents · ↑/↓ recall earlier input · Shift/Ctrl/⌘+Enter runs · Esc then Tab leaves the box",
    "nb.action.edit": "Edit as new cell",
    "nb.action.editTitle": "Put this code in the input below to change it and run it as a new cell",
    "nb.status.notStarted": "{0} · not started — it starts with the first cell",
    "nb.status.stopped": "{0} · stopped — in-memory variables were released; running code starts a fresh kernel",
    "nb.status.exited": "{0} · exited — the next cell starts a fresh kernel",
    "nb.kernel.notStarted": "Not started",
    "nb.readonly.hint": "This is a read-only record of the code the agent ran and its output. To run code yourself, turn on “Notebook manual cells” in Settings → General.",
    "nb.readonly.openSettings": "Open Settings",
  },
};

export const nbT = copyLookup(COPY);
