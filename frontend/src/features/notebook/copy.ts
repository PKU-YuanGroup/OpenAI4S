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
  },
};

export const nbT = copyLookup(COPY);
