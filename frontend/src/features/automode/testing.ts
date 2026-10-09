/**
 * Test support for this lane's Vitest files; nothing in the app imports it.
 *
 * Vitest runs in `node`, with no DOM. The lane builds nodes through
 * `sessions/dom.el()`, so the tests mock that one function with `fakeEl` and
 * read what was painted back through `texts()` / `find()`. Only the surface
 * the lane uses is modelled.
 *
 * The response builders mirror what the real `AutoModeService` returned for a
 * seeded SQLite store (see tests/test_auto_mode_browser_fixture.py, which
 * pins those shapes against the production service).
 */

import { vi } from "vitest";

type FakeEvent = { stopPropagation: () => void; preventDefault: () => void };

export class FakeEl {
  tagName: string;
  className = "";
  id = "";
  type = "";
  title = "";
  disabled = false;
  open = false;
  /** `undefined` reads as attached; set `false` to model a closed menu. */
  isConnected: boolean | undefined = undefined;
  dataset: Record<string, string> = {};
  style: Record<string, string> = {};
  children: FakeEl[] = [];
  parent: FakeEl | null = null;
  onclick: ((event: FakeEvent) => void) | null = null;
  private text = "";
  private attrs = new Map<string, string>();

  classList = {
    add: (...names: string[]) => {
      const set = new Set(this.className.split(/\s+/).filter(Boolean));
      names.forEach((name) => set.add(name));
      this.className = [...set].join(" ");
    },
    remove: (...names: string[]) => {
      this.className = this.className
        .split(/\s+/)
        .filter((name) => name && !names.includes(name))
        .join(" ");
    },
    contains: (name: string) => this.className.split(/\s+/).includes(name),
    toggle: (name: string, force?: boolean) => {
      const on = force === undefined ? !this.classList.contains(name) : force;
      if (on) this.classList.add(name);
      else this.classList.remove(name);
      return on;
    },
  };

  constructor(tag: string, className?: string | null, text?: string | null) {
    this.tagName = tag.toUpperCase();
    if (className) this.className = className;
    if (text != null) this.text = String(text);
  }

  get textContent(): string {
    return this.text + this.children.map((child) => child.textContent).join("");
  }

  set textContent(value: string) {
    this.children.forEach((child) => (child.parent = null));
    this.children = [];
    this.text = String(value);
  }

  set innerHTML(_value: string) {
    this.textContent = "";
  }

  get firstChild(): FakeEl | null {
    return this.children[0] || null;
  }

  appendChild<T extends FakeEl>(child: T): T {
    child.parent = this;
    this.children.push(child);
    return child;
  }

  remove(): void {
    if (this.parent) this.parent.children = this.parent.children.filter((node) => node !== this);
    this.parent = null;
  }

  setAttribute(name: string, value: string): void {
    this.attrs.set(name, String(value));
  }

  getAttribute(name: string): string | null {
    return this.attrs.has(name) ? this.attrs.get(name)! : null;
  }

  hasAttribute(name: string): boolean {
    return this.attrs.has(name);
  }

  removeAttribute(name: string): void {
    this.attrs.delete(name);
  }

  click(): void {
    if (this.disabled) return;
    this.onclick?.({ stopPropagation: () => {}, preventDefault: () => {} });
  }

  /** Every node below this one, depth first. */
  all(): FakeEl[] {
    return this.children.flatMap((child) => [child, ...child.all()]);
  }

  find(match: (node: FakeEl) => boolean): FakeEl | null {
    return this.all().find(match) || null;
  }

  findAll(match: (node: FakeEl) => boolean): FakeEl[] {
    return this.all().filter(match);
  }

  byClass(name: string): FakeEl | null {
    return this.find((node) => node.classList.contains(name));
  }

  allByClass(name: string): FakeEl[] {
    return this.findAll((node) => node.classList.contains(name));
  }

  /** The text of every node that carries text of its own, in document order. */
  texts(): string[] {
    const own = this.text ? [this.text] : [];
    return [...own, ...this.children.flatMap((child) => child.texts())];
  }
}

export function fakeEl(tag: string, className?: string | null, text?: string | null): FakeEl {
  return new FakeEl(tag, className, text);
}

export const BUDGETS = {
  max_review_rounds: 2,
  max_repair_rounds: 2,
  repair_turns_per_round: 12,
  max_extra_cells: 30,
  wall_time_s: 900,
  extra_token_multiplier: 1.5,
  repeated_finding_limit: 2,
  same_action_no_delta_limit: 3,
  no_progress_turn_limit: 5,
  guardian_timeout_s: 90,
  guardian_consecutive_denial_limit: 3,
  guardian_window_size: 50,
  guardian_window_denial_limit: 10,
};

const GUARDIAN_FIELDS = new Set([
  "guardian_timeout_s",
  "guardian_consecutive_denial_limit",
  "guardian_window_size",
  "guardian_window_denial_limit",
]);

type Body = Record<string, unknown>;

/** One `budget_usage` meter the way `AutoBudgetAdmission.project_usage` builds it. */
export function meter(limit: number, used: number, extra: Body = {}): Body {
  const reserved = typeof extra.reserved === "number" ? extra.reserved : 0;
  const remaining = Math.max(0, limit - used - reserved);
  return { limit, used, reserved, remaining, exhausted: remaining <= 0, authority: "auto_budget", ...extra };
}

/** All thirteen meters at zero use; the token ceiling unfrozen, as for a fresh run. */
export function usage(overrides: Record<string, Body> = {}): Body {
  const out: Body = {};
  for (const [field, limit] of Object.entries(BUDGETS)) {
    if (field === "extra_token_multiplier") {
      out[field] = { limit: 0, used: 0, reserved: 0, remaining: 0, exhausted: false, authority: "auto_budget" };
    } else {
      out[field] = meter(limit, 0, { authority: GUARDIAN_FIELDS.has(field) ? "guardian" : "auto_budget" });
    }
  }
  return { ...out, ...overrides };
}

export function autoModeBody(overrides: Body = {}): Body {
  return {
    schema_version: 1,
    feature_enabled: true,
    writable: true,
    disabled_reason: null,
    root_frame_id: "f-root",
    branch_id: "f-root",
    selection: {
      preset: "off",
      result_review_mode: "off",
      approvals_reviewer: "user",
      source: "built_in_defaults",
      explicit: false,
      revision: 0,
      source_revision: 0,
    },
    deployment: { explicit: false, explicit_fields: [] },
    budgets: { ...BUDGETS },
    run: null,
    last_event_id: null,
    last_event_ordinal: 0,
    ...overrides,
  };
}

export function selectionBody(overrides: Body = {}): Body {
  return {
    preset: "off",
    result_review_mode: "off",
    approvals_reviewer: "user",
    source: "built_in_defaults",
    explicit: false,
    revision: 0,
    source_revision: 0,
    ...overrides,
  };
}

export function runBody(overrides: Body = {}): Body {
  return {
    run_id: "run-1",
    root_frame_id: "f-root",
    branch_id: "f-root",
    turn_id: "turn-1",
    execution_id: "exec-1",
    mode: "auto_fix",
    status: "running",
    recovery_required: true,
    started_at: 1791543998042,
    updated_at: 1791543998047,
    last_event_id: "auto-event-1",
    last_event_ordinal: 3,
    budgets: { ...BUDGETS },
    legacy: false,
    budget_usage: usage(),
    circuit: { state: "closed", reason: null, last_delta_cursor: null },
    ...overrides,
  };
}

export function auditRowBody(index: number, overrides: Body = {}): Body {
  const kind = (overrides.subject_kind as string) || "result_review";
  return {
    audit_id: `audit-${index}`,
    run_id: "run-1",
    root_frame_id: "f-root",
    branch_id: "f-root",
    turn_id: "turn-1",
    execution_id: "exec-1",
    subject_kind: kind,
    subject_entity_kind: kind === "result_review" ? "candidate_evidence_snapshot" : "approval_action",
    subject_entity_id: kind === "result_review" ? "cand-1" : `decision-${index}`,
    status: "completed",
    verdict: kind === "result_review" ? "pass" : undefined,
    outcome: kind === "permission_review" ? "denied" : undefined,
    risk: kind === "permission_review" ? "high" : undefined,
    audit_request_digest: "a".repeat(64),
    assessment_digest: "b".repeat(64),
    action_digest: kind === "permission_review" ? "c".repeat(64) : undefined,
    public_summary: `Audit ${index} summary.`,
    created_at: 1791543998000 + index,
    completed_at: 1791543998100 + index,
    event_ordinal: 100 - index,
    ...overrides,
  };
}

export function auditPageBody(audits: Body[], overrides: Body = {}): Body {
  return {
    schema_version: 1,
    root_frame_id: "f-root",
    branch_id: "f-root",
    subject_kind: null,
    audits,
    next_before: null,
    has_more: false,
    ...overrides,
  };
}

/**
 * A response with only what `sessions/api.api()` reads. Not undici's
 * `Response`: its `text()` settles after a Node-version-dependent number of
 * ticks, which made "N microtasks later" assertions pass locally and race in CI.
 */
export function json(body: unknown, status = 200): Response {
  const text = JSON.stringify(body);
  return { ok: status >= 200 && status < 300, status, text: () => Promise.resolve(text) } as unknown as Response;
}

export type FetchCall = { path: string; method: string; body: unknown };

/** Stub `fetch`; every call is recorded with its method so a test can prove the UI only read. */
export function routeFetch(handler: (url: URL, call: FetchCall) => Response | Promise<Response>): FetchCall[] {
  const calls: FetchCall[] = [];
  vi.stubGlobal("fetch", async (input: unknown, init: RequestInit = {}) => {
    const url = new URL(String(input), "http://127.0.0.1");
    const call = { path: url.pathname + url.search, method: String(init.method || "GET").toUpperCase(), body: init.body };
    calls.push(call);
    return handler(url, call);
  });
  return calls;
}

export function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void; reject: (error: unknown) => void } {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

/** Let every queued microtask and resolved fetch settle. */
export async function settle(rounds = 20): Promise<void> {
  for (let i = 0; i < rounds; i += 1) await Promise.resolve();
}
