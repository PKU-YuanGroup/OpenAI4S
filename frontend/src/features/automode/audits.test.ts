/**
 * The Audit view reads `GET /auto-audits` and nothing else: a kind filter,
 * server cursor paging that stops when `has_more` is false, the contract's
 * error codes, and no field outside the allowlist on screen.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const dom = vi.hoisted(() => ({ nodes: {} as Record<string, unknown> }));
vi.mock("../sessions/dom", async () => {
  const { fakeEl } = await import("./testing");
  return { $: (selector: string) => dom.nodes[selector] ?? null, el: fakeEl };
});
vi.mock("../chrome/modal", () => ({
  openModalEl: (node: { classList: { remove: (name: string) => void } } | null) => node?.classList.remove("hidden"),
  closeModalEl: (node: { classList: { add: (name: string) => void } } | null) => node?.classList.add("hidden"),
}));

import { setLang } from "../../i18n";
import { _openGen, currentId } from "../../stores/session";
import { resetStoreFields } from "../../stores/signal-field";
import {
  AUDIT_PAGE_SIZE,
  autoModeAuditsContextChanged,
  autoModeAuditsOpen,
  openAutoModeAudits,
  resetAutoModeAudits,
  scheduleAutoModeAuditsRefresh,
} from "./audits";
import { FakeEl, auditPageBody, auditRowBody, deferred, json, routeFetch, settle, type FetchCall } from "./testing";

let calls: FetchCall[];
let respond: (url: URL) => Response | Promise<Response>;

function body(): FakeEl {
  return dom.nodes["#modal-body"] as FakeEl;
}

function shownIds(): string[] {
  return body()
    .allByClass("am-audit")
    .map((node) => node.dataset.auditId!);
}

function filterButton(kind: string): FakeEl {
  return body().find((node) => node.dataset.kind === kind && node.classList.contains("am-filter"))!;
}

beforeEach(async () => {
  resetStoreFields();
  resetAutoModeAudits();
  dom.nodes = {
    "#modal": new FakeEl("div", "modal hidden"),
    "#modal-title": new FakeEl("span"),
    "#modal-body": new FakeEl("div"),
    "#modal-download": new FakeEl("a"),
  };
  respond = () => json(auditPageBody([]));
  calls = routeFetch((url) => respond(url));
  currentId.value = "f-root";
  await setLang("en");
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("paging", () => {
  it("reads newest first and walks the server cursor until has_more is false", async () => {
    respond = (url) => {
      const before = url.searchParams.get("before");
      if (!before) return json(auditPageBody([auditRowBody(1), auditRowBody(2)], { next_before: "98", has_more: true }));
      if (before === "98") return json(auditPageBody([auditRowBody(2), auditRowBody(3)], { next_before: "97", has_more: true }));
      return json(auditPageBody([auditRowBody(4)]));
    };
    await openAutoModeAudits();
    expect((dom.nodes["#modal-title"] as FakeEl).textContent).toBe("Audit");
    expect(shownIds()).toEqual(["audit-1", "audit-2"]);
    body().byClass("am-more")!.click();
    await settle();
    // A row the next page repeats is not shown twice; order is the server's.
    expect(shownIds()).toEqual(["audit-1", "audit-2", "audit-3"]);
    body().byClass("am-more")!.click();
    await settle();
    expect(shownIds()).toEqual(["audit-1", "audit-2", "audit-3", "audit-4"]);
    expect(body().byClass("am-more")).toBeNull();
    expect(calls.map((call) => [call.method, call.path])).toEqual([
      ["GET", `/api/v1/frames/f-root/auto-audits?limit=${AUDIT_PAGE_SIZE}`],
      ["GET", `/api/v1/frames/f-root/auto-audits?limit=${AUDIT_PAGE_SIZE}&before=98`],
      ["GET", `/api/v1/frames/f-root/auto-audits?limit=${AUDIT_PAGE_SIZE}&before=97`],
    ]);
  });

  it("filters by subject kind and pages within that kind", async () => {
    respond = (url) =>
      json(
        auditPageBody([auditRowBody(5, { subject_kind: "permission_review" })], {
          subject_kind: url.searchParams.get("subject_kind"),
          next_before: url.searchParams.get("before") ? null : "95",
          has_more: !url.searchParams.get("before"),
        }),
      );
    await openAutoModeAudits();
    filterButton("permission_review").click();
    await settle();
    expect(filterButton("permission_review").getAttribute("aria-pressed")).toBe("true");
    expect(filterButton("all").getAttribute("aria-pressed")).toBe("false");
    body().byClass("am-more")!.click();
    await settle();
    expect(calls.map((call) => call.path)).toEqual([
      `/api/v1/frames/f-root/auto-audits?limit=${AUDIT_PAGE_SIZE}`,
      `/api/v1/frames/f-root/auto-audits?limit=${AUDIT_PAGE_SIZE}&subject_kind=permission_review`,
      `/api/v1/frames/f-root/auto-audits?limit=${AUDIT_PAGE_SIZE}&subject_kind=permission_review&before=95`,
    ]);
  });

  it("drops a page answer that a filter change superseded", async () => {
    const slow = deferred<Response>();
    respond = (url) => (url.searchParams.get("subject_kind") ? json(auditPageBody([auditRowBody(9, { subject_kind: "permission_review" })])) : slow.promise);
    const opening = openAutoModeAudits();
    filterButton("permission_review").click();
    await settle();
    slow.resolve(json(auditPageBody([auditRowBody(1)])));
    await opening;
    await settle();
    expect(shownIds()).toEqual(["audit-9"]);
  });

  it("drops a next page that a refresh superseded", async () => {
    vi.useFakeTimers();
    const slowPage = deferred<Response>();
    let first = 0;
    respond = (url) => {
      if (url.searchParams.get("before")) return slowPage.promise;
      first += 1;
      return json(auditPageBody([auditRowBody(first === 1 ? 1 : 7)], { next_before: "90", has_more: true }));
    };
    await openAutoModeAudits();
    body().byClass("am-more")!.click();
    scheduleAutoModeAuditsRefresh();
    await vi.advanceTimersByTimeAsync(80);
    await settle();
    slowPage.resolve(json(auditPageBody([auditRowBody(2)])));
    await settle();
    expect(shownIds()).toEqual(["audit-7"]);
  });
});

describe("what a row shows", () => {
  it("shows the public summary and allowlisted facts, never a prompt or an authorization", async () => {
    respond = () =>
      json(
        auditPageBody([
          {
            ...auditRowBody(1, {
              rationale_summary: "Bounded summary of the reasoning.",
              findings: [{ finding_id: "finding-1", fingerprint: "fp-1", severity: "major", category: "evidence", status: "open", claim: "Recompute the fit.", cell_ids: ["cell-3"], prompt: "SECRET-FINDING-PROMPT" }],
            }),
            prompt: "SECRET-PROMPT",
            system_prompt: "SECRET-SYSTEM-PROMPT",
            hidden_rationale: "SECRET-HIDDEN",
            permission_request: { body: "SECRET-PERMISSION-BODY" },
            authorization: "SECRET-AUTHORIZATION",
            capability: "SECRET-CAPABILITY",
          },
        ]),
      );
    await openAutoModeAudits();
    const text = body().texts();
    expect(text).toEqual(expect.arrayContaining([
      "Result review",
      "Audit 1 summary.",
      "Candidate evidence snapshot",
      "completed",
      "pass",
      "#99",
      "Recompute the fit.",
      "cell-3",
      "Summary",
      "Bounded summary of the reasoning.",
      "a".repeat(64),
    ]));
    expect(body().textContent).not.toMatch(/SECRET/);
  });

  it("falls back to kind and status when there is no public summary", async () => {
    respond = () => json(auditPageBody([auditRowBody(1, { public_summary: undefined, status: "started" })]));
    await openAutoModeAudits();
    expect(body().byClass("am-audit-summary")!.textContent).toBe("Result review · started");
  });

  it("says there are no audits on an empty page", async () => {
    await openAutoModeAudits();
    expect(body().texts()).toContain("No audits");
  });

  it("uses the Chinese copy", async () => {
    await setLang("zh");
    respond = () => json(auditPageBody([auditRowBody(1, { subject_kind: "permission_review" })], { next_before: "99", has_more: true }));
    await openAutoModeAudits();
    expect((dom.nodes["#modal-title"] as FakeEl).textContent).toBe("审计");
    expect(body().texts()).toEqual(expect.arrayContaining(["全部类型", "结果审核", "权限审核", "审批动作", "加载更多"]));
  });
});

describe("errors", () => {
  it("resets a rejected kind to both kinds once, without resending it", async () => {
    respond = (url) =>
      url.searchParams.get("subject_kind")
        ? json({ error: "bad kind", code: "invalid_subject_kind" }, 400)
        : json(auditPageBody([auditRowBody(1)]));
    await openAutoModeAudits();
    filterButton("result_review").click();
    await vi.waitFor(() => expect(shownIds()).toEqual(["audit-1"]));
    expect(filterButton("all").getAttribute("aria-pressed")).toBe("true");
    expect(calls.map((call) => call.path)).toEqual([
      `/api/v1/frames/f-root/auto-audits?limit=${AUDIT_PAGE_SIZE}`,
      `/api/v1/frames/f-root/auto-audits?limit=${AUDIT_PAGE_SIZE}&subject_kind=result_review`,
      `/api/v1/frames/f-root/auto-audits?limit=${AUDIT_PAGE_SIZE}`,
    ]);
    expect(shownIds()).toEqual(["audit-1"]);
  });

  it("marks a rejected cursor unavailable for that request and does not walk it again", async () => {
    respond = (url) =>
      url.searchParams.get("before")
        ? json({ error: "bad cursor", code: "invalid_cursor" }, 400)
        : json(auditPageBody([auditRowBody(1)], { next_before: "99", has_more: true }));
    await openAutoModeAudits();
    body().byClass("am-more")!.click();
    await settle();
    expect(shownIds()).toEqual(["audit-1"]);
    expect(body().byClass("am-page-error")!.textContent).toBe("Audits unavailable");
    expect(body().byClass("am-more")).toBeNull();
    expect(calls.filter((call) => call.path.includes("before="))).toHaveLength(1);
  });

  it.each([
    [json({ error: "storage", code: "auto_mode_storage_unavailable" }, 503), "Audits unavailable"],
    [json({ error: "frame not found", code: "frame_not_found" }, 404), "Session not found"],
    [json({ error: "bad limit", code: "invalid_limit" }, 400), "Audits unavailable"],
    [json(auditPageBody([], { schema_version: 2 })), "Audits unavailable"],
  ])("shows %o as %s, with a retry that reads again", async (response, text) => {
    let first = true;
    respond = () => {
      if (first) {
        first = false;
        return response as Response;
      }
      return json(auditPageBody([auditRowBody(1)]));
    };
    await openAutoModeAudits();
    expect(body().texts()).toContain(text);
    body().byClass("am-retry")!.click();
    await settle();
    expect(shownIds()).toEqual(["audit-1"]);
  });
});

describe("refresh and reopen", () => {
  it("re-reads the first page for a matching audit event, and not for another kind", async () => {
    vi.useFakeTimers();
    respond = (url) => json(auditPageBody([auditRowBody(1, { subject_kind: url.searchParams.get("subject_kind") || "result_review" })]));
    await openAutoModeAudits();
    filterButton("permission_review").click();
    await vi.advanceTimersByTimeAsync(0);
    const before = calls.length;
    scheduleAutoModeAuditsRefresh("result_review");
    await vi.advanceTimersByTimeAsync(80);
    expect(calls.length).toBe(before);
    scheduleAutoModeAuditsRefresh("permission_review");
    scheduleAutoModeAuditsRefresh();
    await vi.advanceTimersByTimeAsync(80);
    expect(calls.length).toBe(before + 1);
    expect(calls.at(-1)!.path).toBe(`/api/v1/frames/f-root/auto-audits?limit=${AUDIT_PAGE_SIZE}&subject_kind=permission_review`);
  });

  it("does nothing once the modal belongs to someone else", async () => {
    vi.useFakeTimers();
    await openAutoModeAudits();
    (dom.nodes["#modal"] as FakeEl).classList.add("hidden");
    expect(autoModeAuditsOpen()).toBe(false);
    const before = calls.length;
    scheduleAutoModeAuditsRefresh();
    await vi.advanceTimersByTimeAsync(80);
    expect(calls.length).toBe(before);
  });

  it("re-reads under a new opening of the same conversation and closes for another one", async () => {
    await openAutoModeAudits();
    _openGen.value += 1;
    autoModeAuditsContextChanged();
    await settle();
    expect(calls).toHaveLength(2);
    currentId.value = "f-other";
    autoModeAuditsContextChanged();
    expect((dom.nodes["#modal"] as FakeEl).classList.contains("hidden")).toBe(true);
  });

  it("only ever reads", async () => {
    respond = () => json(auditPageBody([auditRowBody(1)], { next_before: "99", has_more: true }));
    await openAutoModeAudits();
    filterButton("result_review").click();
    await settle();
    body().byClass("am-more")!.click();
    await settle();
    expect(calls.length).toBeGreaterThan(2);
    expect(calls.every((call) => call.method === "GET" && call.path.startsWith("/api/v1/frames/f-root/auto-audits?"))).toBe(true);
  });
});
