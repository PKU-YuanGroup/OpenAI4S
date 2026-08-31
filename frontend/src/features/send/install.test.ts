import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { isContractStub, isReady } from "../../compat/stub";
import { setRenderStoredStepImpl } from "../messages/list";
import { registerBuiltinHandlers, setFrameUpdateTurnHandler } from "../ws/handlers";
import { hasWsHandler, registerWsHandler, resetWsHandlers } from "../ws/registry";
import {
  SEND_CONTRACT_NAMES,
  installSend,
  registerSendHandlers,
  sendReady,
} from "./index";

describe("F-11 window exports and WS wiring", () => {
  beforeEach(() => {
    resetWsHandlers();
    setFrameUpdateTurnHandler(null);
    setRenderStoredStepImpl(null);
  });

  afterEach(() => {
    resetWsHandlers();
    setFrameUpdateTurnHandler(null);
    setRenderStoredStepImpl(null);
  });

  it("assigns the ten contract names as real implementations (isReady, not typeof)", () => {
    const target: Record<string, unknown> = {};
    installSend(target);
    expect([...SEND_CONTRACT_NAMES]).toEqual([
      "send",
      "buildStepCard",
      "renderAttachmentProblems",
      "renderRefProblems",
      "searchResultHttpUrl",
      "admissionSettled",
      "forgetAdmission",
      "outstandingAdmissions",
      "reconcileLastAdmission",
      "rememberAdmission",
    ]);
    for (const name of SEND_CONTRACT_NAMES) {
      expect(isReady(target[name])).toBe(true);
      expect(isContractStub(target[name])).toBe(false);
    }
    expect(sendReady(target)).toBe(true);
  });

  it("registers cards / candidate / step / plan / permission types, not the four owned elsewhere", () => {
    installSend({});
    expect(hasWsHandler("artifact_ref_problems")).toBe(true);
    expect(hasWsHandler("attachment_problems")).toBe(true);
    expect(hasWsHandler("step")).toBe(true);
    expect(hasWsHandler("step_update")).toBe(true);
    expect(hasWsHandler("plan_ready")).toBe(true);
    expect(hasWsHandler("plan_progress")).toBe(true);
    expect(hasWsHandler("await_permission")).toBe(true);
    expect(hasWsHandler("permission_resolved")).toBe(true);
    expect(hasWsHandler("candidate_ready")).toBe(true);
    expect(hasWsHandler("candidate_resolved")).toBe(true);
    expect(hasWsHandler("auto_run_terminal")).toBe(true);
    expect(hasWsHandler("frame_update")).toBe(false);
    expect(hasWsHandler("replay_begin")).toBe(false);
    expect(hasWsHandler("text_chunk")).toBe(false);
    expect(hasWsHandler("action_timeline")).toBe(false);
  });

  it("install is idempotent and does not steal frame_update from F-06", () => {
    registerBuiltinHandlers();
    expect(() => installSend({})).not.toThrow();
    expect(() => registerSendHandlers()).not.toThrow();
    expect(hasWsHandler("frame_update")).toBe(true);
    expect(() => registerWsHandler("frame_update", () => {})).toThrow(/duplicate/);
  });
});
