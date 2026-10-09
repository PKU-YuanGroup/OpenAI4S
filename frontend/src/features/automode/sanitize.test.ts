/**
 * The sanitizer is the allowlist: only the fields docs/auto-mode.md names
 * leave it, an envelope this client does not understand becomes "Status
 * unavailable", and a later payload that adds a prompt or an authorization
 * still cannot reach the renderer.
 */
import { describe, expect, it } from "vitest";
import { sanitizeAuditPage, sanitizeAuditRow, sanitizeAutoModeView } from "./sanitize";
import { auditPageBody, auditRowBody, autoModeBody, meter, runBody, selectionBody, usage } from "./testing";

const SECRETS = {
  prompt: "SECRET-PROMPT you are the guardian",
  system_prompt: "SECRET-SYSTEM",
  assessment_prompt: "SECRET-ASSESSMENT-PROMPT",
  hidden_rationale: "SECRET-HIDDEN-RATIONALE",
  permission_request: { body: "SECRET-PERMISSION-BODY" },
  request_body: "SECRET-REQUEST-BODY",
  authorization: "SECRET-AUTHORIZATION",
  authorization_token: "SECRET-TOKEN",
  capability: { grant: "SECRET-CAPABILITY" },
  rationale: "SECRET-RAW-RATIONALE",
  assessment: { rationale: "SECRET-NESTED", public_summary: "nested summary is not a row field" },
};

describe("the status envelope", () => {
  it("keeps the documented fields of a real GET body", () => {
    const view = sanitizeAutoModeView(
      autoModeBody({
        run: runBody({ status: "reviewing", candidate_digest: "d".repeat(64), evidence_snapshot_sha256: "e".repeat(64) }),
        last_event_id: "auto-event-1",
        last_event_ordinal: 7,
      }),
    );
    expect(view).not.toBeNull();
    expect(view!.selection).toEqual(selectionBody());
    expect(view!.deployment).toEqual({ explicit: false, explicit_fields: [] });
    expect(view!.budgets.max_extra_cells).toBe(30);
    expect(view!.run!.status).toBe("reviewing");
    expect(view!.run!.legacy).toBe(false);
    expect(view!.run!.budget_usage.max_extra_cells).toEqual({
      limit: 30, used: 0, reserved: 0, remaining: 30, exhausted: false, authority: "auto_budget",
    });
    expect(view!.run!.circuit).toEqual({ state: "closed", reason: null });
    expect(view!.run!.digests).toEqual([
      ["candidate_digest", "d".repeat(64)],
      ["evidence_snapshot_sha256", "e".repeat(64)],
    ]);
    expect(view!.last_event_ordinal).toBe(7);
  });

  it.each([
    ["an unknown schema version", { schema_version: 2 }],
    ["a missing schema version", { schema_version: undefined }],
    ["an unknown preset", { selection: selectionBody({ preset: "turbo" }) }],
    ["an unknown source", { selection: selectionBody({ source: "magic" }) }],
    ["a non-integer revision", { selection: selectionBody({ revision: "2" }) }],
    ["a third disabled reason", { writable: false, disabled_reason: "maintenance" }],
    ["writable with a disabled reason", { writable: true, disabled_reason: "import_quarantine" }],
    ["unwritable with no reason", { writable: false, disabled_reason: null }],
    ["writable storage that is off", { feature_enabled: false, writable: true }],
    ["storage on yet disabled by the flag", { feature_enabled: true, writable: false, disabled_reason: "stage2_feature_disabled" }],
    ["an unknown run status", { run: runBody({ status: "exploding" }) }],
    ["no run key at all", { run: undefined }],
    ["no budgets object", { budgets: null }],
  ])("refuses %s rather than guessing a layout", (_label, overrides) => {
    const body = autoModeBody(overrides as Record<string, unknown>);
    for (const [key, value] of Object.entries(overrides)) if (value === undefined) delete body[key];
    expect(sanitizeAutoModeView(body)).toBeNull();
  });

  it("accepts quarantine winning over a disabled stage flag", () => {
    const view = sanitizeAutoModeView(
      autoModeBody({ feature_enabled: false, writable: false, disabled_reason: "import_quarantine" }),
    );
    expect(view?.disabled_reason).toBe("import_quarantine");
  });

  it("treats a run with no budget projection as unknown usage, not zero", () => {
    const legacy = sanitizeAutoModeView(autoModeBody({ run: runBody({ legacy: true, budget_usage: {}, circuit: { state: "closed" } }) }));
    expect(legacy!.run!.legacy).toBe(true);
    expect(legacy!.run!.budget_usage).toEqual({});
    expect(legacy!.run!.circuit).toBeNull();
    const unmarked = runBody();
    delete unmarked.legacy;
    expect(sanitizeAutoModeView(autoModeBody({ run: unmarked }))!.run!.legacy).toBe(true);
  });

  it("drops a meter it cannot read instead of inventing its numbers", () => {
    const view = sanitizeAutoModeView(
      autoModeBody({
        run: runBody({
          budget_usage: usage({
            max_extra_cells: { limit: 30, used: "25", reserved: 0, remaining: 5, exhausted: false },
            max_repair_rounds: meter(2, 2),
          }),
        }),
      }),
    );
    expect(view!.run!.budget_usage.max_extra_cells).toBeUndefined();
    expect(view!.run!.budget_usage.max_repair_rounds).toMatchObject({ used: 2, exhausted: true });
  });

  it("copies no field outside the allowlist", () => {
    const view = sanitizeAutoModeView({ ...autoModeBody({ run: { ...runBody(), ...SECRETS } }), ...SECRETS });
    const encoded = JSON.stringify(view);
    expect(encoded).not.toMatch(/SECRET/);
    expect(Object.keys(view!).sort()).toEqual([
      "branch_id", "budgets", "deployment", "disabled_reason", "feature_enabled", "last_event_id",
      "last_event_ordinal", "root_frame_id", "run", "schema_version", "selection", "writable",
    ]);
  });

  it("keeps only the deployment fields the contract names", () => {
    const view = sanitizeAutoModeView(
      autoModeBody({ deployment: { explicit: true, explicit_fields: ["preset", "OPENAI4S_LLM_API_KEY", "preset"] } }),
    );
    expect(view!.deployment.explicit_fields).toEqual(["preset"]);
  });
});

describe("the audit page", () => {
  it("keeps the documented row fields and drops everything else", () => {
    const page = sanitizeAuditPage(
      auditPageBody([
        {
          ...auditRowBody(1, {
            rationale_summary: "Bounded rationale summary.",
            decision: "deny",
            round: 1,
            attempt: 2,
            finding_count: 1,
            reviewer_profile_id: "scientific-reviewer",
            profile_revision: 7,
            findings: [
              {
                finding_id: "finding-1",
                fingerprint: "stable-1",
                severity: "major",
                category: "evidence",
                status: "open",
                claim: "Needs an independent recomputation.",
                evidence_refs: ["cell-1"],
                version_ids: ["v-1"],
                artifact_ids: [],
                cell_ids: ["cell-1"],
                ...SECRETS,
              },
            ],
          }),
          ...SECRETS,
        },
      ]),
    );
    expect(page).not.toBeNull();
    const row = page!.audits[0]!;
    expect(row.public_summary).toBe("Audit 1 summary.");
    expect(row.rationale_summary).toBe("Bounded rationale summary.");
    expect(row.findings[0]).toEqual({
      finding_id: "finding-1",
      fingerprint: "stable-1",
      severity: "major",
      category: "evidence",
      status: "open",
      claim: "Needs an independent recomputation.",
      evidence_refs: ["cell-1"],
      version_ids: ["v-1"],
      artifact_ids: [],
      cell_ids: ["cell-1"],
    });
    expect(JSON.stringify(page)).not.toMatch(/SECRET|nested summary/);
  });

  it("drops a row whose subject pairing is not one of the two valid ones", () => {
    expect(sanitizeAuditRow(auditRowBody(1, { subject_entity_kind: "approval_action" }))).toBeNull();
    expect(sanitizeAuditRow(auditRowBody(1, { subject_kind: "guardian_review" }))).toBeNull();
    expect(sanitizeAuditRow(auditRowBody(1, { audit_id: "" }))).toBeNull();
  });

  it("believes has_more only together with a cursor", () => {
    expect(sanitizeAuditPage(auditPageBody([], { has_more: true, next_before: null }))!.has_more).toBe(false);
    expect(sanitizeAuditPage(auditPageBody([], { has_more: true, next_before: "42" }))).toMatchObject({
      has_more: true,
      next_before: "42",
    });
    expect(sanitizeAuditPage(auditPageBody([], { has_more: false, next_before: "42" }))!.has_more).toBe(false);
  });

  it("refuses an envelope it does not understand", () => {
    expect(sanitizeAuditPage(auditPageBody([], { schema_version: 2 }))).toBeNull();
    expect(sanitizeAuditPage(auditPageBody([], { subject_kind: "guardian" }))).toBeNull();
    expect(sanitizeAuditPage({ schema_version: 1, root_frame_id: "f", branch_id: "f", audits: "none" })).toBeNull();
  });

  it("scrubs a credential-shaped string out of model-written text", () => {
    const row = sanitizeAuditRow(auditRowBody(1, { public_summary: "Leaked sk-abcdefghijk here." }));
    expect(row!.public_summary).toBe("Leaked [redacted] here.");
  });
});
