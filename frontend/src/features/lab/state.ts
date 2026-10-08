import { signal } from "@preact/signals";
import * as api from "./api";
import { labT } from "./copy";
import type { Capability, CommandRequest, CreateRequest, DetailResult, Device, Run } from "./types";

type Intent = { kind: "create"; body: CreateRequest } | { kind: "execute"; runId: string; body: CommandRequest };
export type Pending = { intent: Intent; sending: boolean; error: string };
export type LabState = {
  rootId: string | null; generation: number; devices: Device[]; runs: Run[];
  selectedRunId: string | null; detail: DetailResult | null;
  sequences: Record<string, number>; loading: boolean; error: string; pending: Pending | null; stopping: boolean; querying: string | null;
};
const empty = (rootId: string | null, generation: number): LabState => ({
  rootId, generation, devices: [], runs: [], selectedRunId: null, detail: null,
  sequences: {}, loading: false, error: "", pending: null, stopping: false, querying: null,
});
const message = (error: unknown): string => error instanceof Error ? error.message : labT("unknown");
const newKey = (): string => "ui-" + crypto.randomUUID();

/** Revisions only count applied actions. Counts and timestamps also protect refusals and stops. */
function older(candidate: Run, previous: Run | undefined): boolean {
  if (!previous || previous.run_id !== candidate.run_id) return false;
  return candidate.revision < previous.revision || candidate.updated_at < previous.updated_at ||
    candidate.command_count < previous.command_count ||
    (["ended", "failed"].includes(previous.status) && candidate.status !== previous.status);
}

export class LabController {
  readonly state = signal<LabState>(empty(null, 0));
  private epoch = 0;
  private read = 0;
  // Uncertain requests survive leaving/reopening a session within this page. They are
  // never shown under another root, and only an explicit same-request retry sends them.
  private retained = new Map<string, Pending>();
  constructor(private key: () => string = newKey) {}

  scope(rootId: string | null, generation: number, force = false): void {
    const s = this.state.value;
    if (!force && s.rootId === rootId && s.generation === generation) return;
    this.epoch++; this.read++;
    this.state.value = { ...empty(rootId, generation), pending: rootId ? this.retained.get(rootId) || null : null };
  }
  private patch(next: Partial<LabState>): void { this.state.value = { ...this.state.value, ...next }; }
  private matches(root: string, epoch: number): boolean {
    return this.epoch === epoch && this.state.value.rootId === root;
  }

  async refresh(): Promise<void> {
    const root = this.state.value.rootId;
    if (!root) return;
    const epoch = this.epoch, read = ++this.read;
    const valid = () => this.matches(root, epoch) && read === this.read;
    this.patch({ loading: true });
    try {
      const index = await api.listLab(root);
      if (!valid()) return;
      const previous = this.state.value;
      const runs = index.runs.map((run) => {
        const known = previous.runs.find((r) => r.run_id === run.run_id);
        return older(run, known) ? known! : run;
      });
      const selected = previous.selectedRunId && runs.some((r) => r.run_id === previous.selectedRunId)
        ? previous.selectedRunId : runs[0]?.run_id || null;
      this.patch({ devices: index.devices, runs, selectedRunId: selected, error: "",
        detail: selected === previous.selectedRunId ? previous.detail : null });
      if (!selected) { this.patch({ detail: null }); return; }
      const [detail, observations] = await Promise.all([api.getRun(root, selected), api.listObservations(root, selected, -1, 200, false)]);
      if (!valid() || this.state.value.selectedRunId !== selected || detail.run.run_id !== selected) return;
      const known = this.state.value.detail?.run || this.state.value.runs.find((r) => r.run_id === selected);
      if (older(detail.run, known)) return;
      const retained = this.retained.get(root);
      if (retained && !retained.sending && retained.intent.kind === "execute" &&
          detail.commands.some((c) => c.idempotency_key === retained.intent.body.idempotency_key)) {
        // A confirmed command record supersedes transport uncertainty. Unknown outcomes
        // now offer reconcile, never an execute retry, even with the old key.
        this.retained.delete(root);
        this.patch({ pending: null });
      }
      this.patch({ detail, sequences: Object.fromEntries(observations.observations.filter((o) => o.run_id === selected).map((o) => [o.observation_id, o.sequence])), runs: this.state.value.runs.map((r) => r.run_id === selected ? detail.run : r) });
    } catch (error) {
      if (valid()) this.patch({ error: message(error) });
    } finally {
      if (valid()) this.patch({ loading: false });
    }
  }

  async select(runId: string): Promise<void> {
    if (!this.state.value.runs.some((r) => r.run_id === runId)) return;
    this.read++;
    this.patch({ selectedRunId: runId, detail: null });
    await this.refresh();
  }

  async create(device: Device, profile: string, seed?: number): Promise<void> {
    if (device.available !== true || !device.profiles.includes(profile) || this.state.value.pending) return;
    if (seed !== undefined && (!Number.isInteger(seed) || seed < 0 || seed > 4294967295)) {
      this.patch({ error: labT("invalidSeed") }); return;
    }
    const body: CreateRequest = { device_id: device.device_id, profile, idempotency_key: this.key() };
    if (seed !== undefined) body.seed = seed;
    await this.submit({ kind: "create", body });
  }

  async execute(capability: Capability, values: Record<string, number>): Promise<void> {
    const { detail, pending, stopping, querying } = this.state.value;
    if (!detail || detail.run.status !== "ready" || pending || stopping || querying) return;
    // Resolve by identity against this run's immutable descriptor, never trust a stale form.
    const cap = detail.descriptor.capabilities.find((c) => c.capability_id === capability.capability_id);
    if (!cap || cap.terminal) return;
    const parameters: CommandRequest["parameters"] = {};
    for (const [name, spec] of Object.entries(cap.parameters)) {
      const value = values[name];
      if (value === undefined || !spec.allowed.includes(value)) return;
      parameters[name] = { value, unit: spec.unit };
    }
    await this.submit({ kind: "execute", runId: detail.run.run_id, body: {
      operation: cap.operation, source: cap.source, target: cap.target, parameters,
      expected_revision: detail.run.revision, idempotency_key: this.key(),
    } });
  }

  async retry(): Promise<void> {
    const pending = this.state.value.pending;
    if (pending && !pending.sending) await this.submit(pending.intent);
  }

  private async submit(intent: Intent): Promise<void> {
    const root = this.state.value.rootId;
    if (!root) return;
    const existing = this.retained.get(root);
    if (existing?.sending || (existing && existing.intent !== intent)) return;
    // Freeze the submitted body; neither refreshed revision nor edited controls can alter retries.
    const saved = existing || { intent: structuredClone(intent), sending: false, error: "" };
    saved.sending = true; saved.error = "";
    this.retained.set(root, saved);
    const epoch = this.epoch, selected = this.state.value.selectedRunId;
    this.read++; this.patch({ pending: { ...saved }, loading: false, error: "" });
    try {
      const sent = saved.intent;
      const result = sent.kind === "create" ? await api.createRun(root, sent.body)
        : await api.executeCommand(root, sent.runId, sent.body);
      this.retained.delete(root);
      if (!this.matches(root, epoch)) return;
      this.read++;
      this.patch({ pending: null });
      if (this.state.value.selectedRunId === selected) {
        const known = this.state.value.runs.find((r) => r.run_id === result.run.run_id);
        if (!older(result.run, known)) {
          const runs = [result.run, ...this.state.value.runs.filter((r) => r.run_id !== result.run.run_id)];
          if (sent.kind === "create" && "descriptor" in result) {
            this.patch({ runs, selectedRunId: result.run.run_id, detail: { ...result, commands: [] } });
          } else if ("command" in result && this.state.value.detail?.run.run_id === result.run.run_id) {
            const detail = this.state.value.detail;
            const commands = result.command ? [...detail.commands.filter((c) => c.command_id !== result.command!.command_id), result.command]
              .sort((a, b) => a.seq - b.seq) : detail.commands;
            this.patch({ runs, detail: { ...detail, run: result.run, observation: result.observation, commands } });
          }
        }
      }
      await this.refresh();
    } catch (error) {
      saved.sending = false; saved.error = message(error);
      // A structured 4xx is a confirmed refusal before dispatch, unlike network/5xx errors.
      const status = (error as { status?: number })?.status;
      if (status && status >= 400 && status < 500) this.retained.delete(root);
      if (this.matches(root, epoch)) this.patch({ pending: this.retained.has(root) ? { ...saved } : null, error: saved.error });
    } finally {
      saved.sending = false;
      // An in-flight intent may have been reattached by navigation while the old call completed.
      if (this.state.value.rootId === root && !this.matches(root, epoch)) {
        this.patch({ pending: this.retained.has(root) ? { ...saved } : null });
        void this.refresh();
      }
    }
  }

  async reconcile(commandId: string): Promise<void> {
    const { rootId: root, selectedRunId: run, detail, querying } = this.state.value;
    if (!root || !run || querying || !detail?.commands.some((c) => c.command_id === commandId && c.state === "outcome_unknown")) return;
    const epoch = this.epoch;
    this.read++; this.patch({ querying: commandId, loading: false, error: "" });
    try {
      const result = await api.reconcileCommand(root, run, commandId);
      if (!this.matches(root, epoch) || this.state.value.selectedRunId !== run) return;
      this.read++;
      const current = this.state.value.detail;
      if (current && result.run.run_id === run && !older(result.run, current.run)) {
        const commands = result.command ? current.commands.map((c) => c.command_id === commandId ? result.command! : c) : current.commands;
        this.patch({ detail: { ...current, run: result.run, commands, observation: result.observation },
          runs: this.state.value.runs.map((r) => r.run_id === run ? result.run : r) });
      }
      await this.refresh();
    } catch (error) { if (this.matches(root, epoch)) this.patch({ error: message(error) }); }
    finally { if (this.matches(root, epoch)) this.patch({ querying: null }); }
  }

  async stop(): Promise<void> {
    const { rootId: root, selectedRunId: run, detail, stopping } = this.state.value;
    if (!root || !run || !detail || stopping || ["ended", "failed"].includes(detail.run.status)) return;
    const epoch = this.epoch;
    this.read++; this.patch({ stopping: true, loading: false, error: "" });
    try {
      const result = await api.stopRun(root, run);
      if (!this.matches(root, epoch) || this.state.value.selectedRunId !== run) return;
      this.read++;
      const current = this.state.value.detail;
      if (current && result.run.run_id === run && !older(result.run, current.run)) {
        this.patch({ detail: { ...current, run: result.run }, runs: this.state.value.runs.map((r) => r.run_id === run ? result.run : r) });
      }
      await this.refresh();
    } catch (error) { if (this.matches(root, epoch)) this.patch({ error: message(error) }); }
    finally { if (this.matches(root, epoch)) this.patch({ stopping: false }); }
  }
}
export const lab = new LabController();
export const labConnected = signal(false);
