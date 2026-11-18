/**
 * A refused Host call is told apart from a failed one, and from a Stop that
 * cancelled it while it waited for approval.
 */
import { beforeEach, describe, expect, it } from "vitest";
import { setLang } from "../../i18n/runtime";
import { resetStoreFields } from "../../stores/signal-field";
import { permCards } from "../../stores/stream";
import { declinedCell, declinedMeta, declinedReason, declinedStep } from "./declined";

beforeEach(async () => {
  resetStoreFields();
  await setLang("zh");
});

describe("declined", () => {
  it("reads a step's refusal: denied, stopped, timed out, refused by a rule", () => {
    expect(declinedStep({ error: "Permission denied: denied by user" })).toEqual({ kind: "you", reason: "" });
    expect(declinedStep({ error: "Permission denied: turn cancelled" })).toEqual({ kind: "stopped", reason: "" });
    expect(declinedStep({ error: "Permission denied: approval timed out" })).toEqual({
      kind: "timedOut",
      reason: "",
    });
    expect(declinedStep({ error: "Permission denied: blocked by a standing 'deny' permission rule" })).toEqual({
      kind: "rule",
      reason: "blocked by a standing 'deny' permission rule",
    });
    expect(declinedStep({ error: "ZeroDivisionError: division by zero" })).toBeNull();
    expect(declinedStep({})).toBeNull();
    expect(declinedStep(null)).toBeNull();
  });

  it("a reason typed on a card in this view is the user's own denial", () => {
    permCards.value = Object.assign(Object.create(null), {
      "perm-1": { resolved: true, allowed: false, resolution: { message: "不需要定义工具。" } },
    });
    const declined = declinedStep({ error: "Permission denied: 不需要定义工具。" })!;
    expect(declined.kind).toBe("you");
    expect(declinedMeta(declined)).toBe("你拒绝了 · 未执行");
    expect(declinedReason(declined)).toBe("原因：不需要定义工具");
  });

  it("reads only the line a Cell actually raised, and never an OS PermissionError", () => {
    const cell = (last: string) => `Traceback (most recent call last):\n  File "<kernel:1>", line 1\n${last}`;
    expect(declinedCell(cell("RuntimeError: bash: Permission denied: 不需要; command was not executed"))).toEqual({
      kind: "rule",
      reason: "不需要",
    });
    expect(declinedCell(cell("RuntimeError: host.web_fetch error: Permission denied: turn cancelled"))?.kind).toBe(
      "stopped",
    );
    expect(declinedCell(cell("PermissionError: [Errno 13] Permission denied: '/etc/shadow'"))).toBeNull();
    // A refusal the cell caught, followed by a real failure, is a failure.
    expect(
      declinedCell(
        "RuntimeError: bash: Permission denied: denied by user; command was not executed\n" +
          "During handling of the above exception, another exception occurred:\nValueError: x",
      ),
    ).toBeNull();
    expect(declinedCell("")).toBeNull();
  });

  it("speaks the UI language", async () => {
    await setLang("en");
    expect(declinedMeta({ kind: "stopped", reason: "" })).toBe("Stopped with the turn · not run");
    expect(declinedMeta({ kind: "you", reason: "" })).toBe("You declined · not run");
    expect(declinedReason({ kind: "rule", reason: "policy" })).toBe("Reason: policy");
  });
});
