import { signal } from "@preact/signals";
import * as api from "./api";
import { labT } from "./copy";
import type { Capability, Command, CommandRequest, CreateRequest, DetailResult, Device, ExportResult, Observation, ReplayEntry, Run } from "./types";

type Intent = { kind: "create"; body: CreateRequest } | { kind: "execute"; runId: string; body: CommandRequest };
export type Pending = { intent: Intent; sending: boolean; error: string; confirmed?: boolean };
export type ReplayState = { runId: string; loading: boolean; error: string; entries: ReplayEntry[]; index: number };
export type ExportState = { runId: string; loading: boolean; error: string; result: ExportResult | null };
export type LabState = {
  rootId: string | null; generation: number; devices: Device[]; runs: Run[];
  selectedRunId: string | null; detail: DetailResult | null;
  sequences: Record<string, number>; loading: boolean; error: string; pending: Pending | null; stopping: boolean; querying: string | null;
  /** False until this scope's first index read lands, so an unread scope never shows "no devices". */
  loaded: boolean; replay: ReplayState | null; exported: ExportState | null;
};
const empty = (rootId: string | null, generation: number): LabState => ({
  rootId, generation, devices: [], runs: [], selectedRunId: null, detail: null,
  sequences: {}, loading: false, error: "", pending: null, stopping: false, querying: null, loaded: false, replay: null, exported: null,
});
const message = (error: unknown): string => error instanceof Error ? error.message : labT("unknown");
const newKey = (): string => "ui-" + crypto.randomUUID();

/** Whether a failed request can never succeed by resending its key. A structured 4xx is
 * refused before dispatch. A create refused as provider_unavailable/adapter_mismatch has
 * already ended its run as failed, and a same-key retry only returns that failed run. */
function definitive(intent: Intent, error: unknown): boolean {
  const { status, code } = (error ?? {}) as { status?: number; code?: string };
  if (status !== undefined && status >= 400 && status < 500) return true;
  return intent.kind === "create" && status === 503 && (code === "provider_unavailable" || code === "adapter_mismatch");
}

/** Revisions only count applied actions. Counts and timestamps also protect refusals and stops. */
function older(candidate: Run, previous: Run | undefined): boolean {
  if (!previous || previous.run_id !== candidate.run_id) return false;
  return candidate.revision < previous.revision || candidate.updated_at < previous.updated_at ||
    candidate.command_count < previous.command_count ||
    (["ended", "failed"].includes(previous.status) && candidate.status !== previous.status);
}

function newestObservation(current: Observation | null | undefined, incoming: Observation | null): Observation | null {
  if (!incoming || incoming.channels.some((c) => c.value && typeof c.value === "object" &&
      !Array.isArray(c.value) && "truncated" in c.value)) return current || null;
  if (current?.run_id === incoming.run_id && current.sequence >= incoming.sequence) return current;
  return incoming;
}

export function endCapability(state: LabState): Capability | undefined {
  return state.detail?.descriptor.capabilities.find((cap) => cap.terminal && cap.operation === "end_experiment" && Object.keys(cap.parameters).length === 0);
}
export function canEnd(state: LabState): boolean {
  return !!state.rootId && !!endCapability(state) && state.detail?.run.status === "ready" && !state.pending && !state.stopping && !state.querying && !state.detail.commands.some((c) => c.state === "outcome_unknown");
}
export function canStop(state: LabState): boolean {
  return !!state.rootId && !!state.detail && !state.stopping && !["ended", "failed"].includes(state.detail.run.status);
}

/** Associate only recorded identities, retaining refusals that have no observation. */
function replayEntries(commands: Command[], observations: Observation[]): ReplayEntry[] {
  const used = new Set<string>();
  const entries: ReplayEntry[] = observations.filter((o) => !o.command_id).map((observation) => {
    used.add(observation.observation_id);
    return { command: null, observation };
  });
  for (const command of commands) {
    const observation = observations.find((o) => !used.has(o.observation_id) &&
      (command.observation_id ? o.observation_id === command.observation_id : o.command_id === command.command_id)) || null;
    if (observation) used.add(observation.observation_id);
    entries.push({ command, observation });
  }
  for (const observation of observations) {
    if (!used.has(observation.observation_id)) entries.push({ command: null, observation });
  }
  return entries;
}

export class LabController {
  readonly state = signal<LabState>(empty(null, 0));
  private epoch = 0;
  private read = 0;
  private selection = 0;
  private activeRead: { epoch: number; dirty: boolean; promise: Promise<void> } | null = null;
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

  refresh(): Promise<void> {
    if (!this.state.value.rootId) return Promise.resolve();
    if (this.activeRead?.epoch === this.epoch) {
      this.activeRead.dirty = true;
      return this.activeRead.promise;
    }
    const flight = { epoch: this.epoch, dirty: false, promise: Promise.resolve() };
    this.activeRead = flight;
    flight.promise = (async () => {
      do {
        flight.dirty = false;
        await this.readSnapshot();
      } while (flight.dirty && flight.epoch === this.epoch);
    })().finally(() => { if (this.activeRead === flight) this.activeRead = null; });
    return flight.promise;
  }

  private async readSnapshot(): Promise<void> {
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
      if (selected !== previous.selectedRunId) {
        this.selection++;
        this.patch({ replay: null, exported: null });
      }
      this.patch({ devices: index.devices, runs, selectedRunId: selected, error: "", loaded: true,
        detail: selected === previous.selectedRunId ? previous.detail : null });
      if (!selected) { this.patch({ detail: null }); return; }
      const [detail, observations] = await Promise.all([api.getRun(root, selected), api.listObservations(root, selected, -1, 200, false)]);
      if (!valid() || this.state.value.selectedRunId !== selected || detail.run.run_id !== selected) return;
      const known = this.state.value.detail?.run || this.state.value.runs.find((r) => r.run_id === selected);
      if (older(detail.run, known)) return;
      const retained = this.retained.get(root);
      if (retained && retained.intent.kind === "execute" && retained.intent.runId === selected &&
          detail.commands.some((c) => c.idempotency_key === retained.intent.body.idempotency_key)) {
        // A confirmed command record supersedes transport uncertainty. Unknown outcomes
        // now offer reconcile, never an execute retry, even with the old key.
        retained.confirmed = true;
        if (!retained.sending) {
          this.retained.delete(root);
          this.patch({ pending: null });
        }
      }
      detail.observation = newestObservation(this.state.value.detail?.observation, detail.observation);
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
    this.selection++;
    this.patch({ selectedRunId: runId, detail: null, replay: null, exported: null });
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

  async end(): Promise<void> {
    const state = this.state.value;
    if (!canEnd(state)) return;
    const cap = endCapability(state)!;
    await this.submit({ kind: "execute", runId: state.detail!.run.run_id, body: {
      operation: cap.operation, source: cap.source, target: cap.target, parameters: {},
      expected_revision: state.detail!.run.revision, idempotency_key: this.key(),
    } });
  }

  async exportRun(includeEvaluation = false): Promise<void> {
    const { rootId: root, selectedRunId: run, detail, exported } = this.state.value;
    if (!root || !run || detail?.run.run_id !== run || exported?.loading) return;
    const epoch = this.epoch, selection = this.selection;
    const valid = () => this.matches(root, epoch) && this.selection === selection && this.state.value.selectedRunId === run;
    this.patch({ exported: { runId: run, loading: true, error: "", result: exported?.result || null } });
    try {
      const result = await api.exportRun(root, run, includeEvaluation);
      if (!valid()) return;
      if (result.run_id !== run || result.include_evaluation !== includeEvaluation || result.artifacts.some((artifact) =>
        !artifact.artifact_id || !artifact.version_id || !includeEvaluation && artifact.kind === "simulation_ground_truth")) throw new Error(labT("invalidResult"));
      this.patch({ exported: { runId: run, loading: false, error: "", result } });
    } catch (error) {
      if (valid()) this.patch({ exported: { runId: run, loading: false, error: message(error), result: exported?.result || null } });
    }
  }

  async loadReplay(): Promise<void> {
    const { rootId: root, selectedRunId: run, detail, replay } = this.state.value;
    if (!root || !run || detail?.run.run_id !== run || replay?.loading) return;
    const epoch = this.epoch, selection = this.selection;
    const valid = () => this.matches(root, epoch) && this.selection === selection && this.state.value.selectedRunId === run;
    this.patch({ replay: { runId: run, loading: true, error: "", entries: [], index: 0 } });
    try {
      const readCommands = async (): Promise<Command[]> => {
        const rows: Command[] = [];
        let after = 0;
        while (valid()) {
          const page = await api.listCommands(root, run, after, 200);
          if (!valid()) return [];
          if (!page.commands.length) return rows;
          if (!Number.isInteger(page.next_after_seq) || page.next_after_seq <= after || page.commands.some((c) => c.run_id !== run || c.seq <= after)) throw new Error(labT("invalidReplay"));
          rows.push(...page.commands); after = page.next_after_seq;
        }
        return [];
      };
      const readObservations = async (): Promise<Observation[]> => {
        const rows: Observation[] = [];
        let after = -1;
        while (valid()) {
          const page = await api.listObservations(root, run, after, 200, true);
          if (!valid()) return [];
          if (!page.observations.length) return rows;
          if (!Number.isInteger(page.next_after_sequence) || page.next_after_sequence <= after || page.observations.some((o) => o.run_id !== run || o.sequence <= after)) throw new Error(labT("invalidReplay"));
          rows.push(...page.observations); after = page.next_after_sequence;
        }
        return [];
      };
      const [commands, observations] = await Promise.all([readCommands(), readObservations()]);
      if (!valid()) return;
      const entries = replayEntries(commands.sort((a, b) => a.seq - b.seq), observations.sort((a, b) => a.sequence - b.sequence));
      this.patch({ replay: { runId: run, loading: false, error: "", entries, index: 0 } });
    } catch (error) {
      if (valid()) this.patch({ replay: { runId: run, loading: false, error: message(error), entries: [], index: 0 } });
    }
  }

  selectReplay(index: number): void {
    const replay = this.state.value.replay;
    if (!replay || replay.loading || replay.runId !== this.state.value.selectedRunId || !Number.isInteger(index) || index < 0 || index >= replay.entries.length) return;
    this.patch({ replay: { ...replay, index } });
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
    const saved: Pending = existing || { intent: structuredClone(intent), sending: false, error: "" };
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
            this.selection++;
            this.patch({ replay: null, exported: null });
            this.patch({ runs, selectedRunId: result.run.run_id, detail: { ...result, observation: newestObservation(null, result.observation), commands: [] } });
          } else if ("command" in result && this.state.value.detail?.run.run_id === result.run.run_id) {
            const detail = this.state.value.detail;
            const commands = result.command ? [...detail.commands.filter((c) => c.command_id !== result.command!.command_id), result.command]
              .sort((a, b) => a.seq - b.seq) : detail.commands;
            this.patch({ runs, detail: { ...detail, run: result.run, observation: newestObservation(detail.observation, result.observation), commands } });
          }
        }
      }
      void this.refresh();
    } catch (error) {
      saved.sending = false; saved.error = message(error);
      // Network errors, timeouts and other 5xx leave the outcome uncertain: keep the key.
      if (saved.confirmed || definitive(saved.intent, error)) this.retained.delete(root);
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
        this.patch({ detail: { ...current, run: result.run, commands, observation: newestObservation(current.observation, result.observation) },
          runs: this.state.value.runs.map((r) => r.run_id === run ? result.run : r) });
      }
      void this.refresh();
    } catch (error) { if (this.matches(root, epoch)) this.patch({ error: message(error) }); }
    finally { if (this.matches(root, epoch)) this.patch({ querying: null }); }
  }

  async stop(): Promise<void> {
    const { rootId: root, selectedRunId: run, detail, stopping } = this.state.value;
    if (!root || !run || !detail || stopping || !canStop(this.state.value)) return;
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
      void this.refresh();
    } catch (error) { if (this.matches(root, epoch)) this.patch({ error: message(error) }); }
    finally { if (this.matches(root, epoch)) this.patch({ stopping: false }); }
  }
}
export const lab = new LabController();
export const labConnected = signal(false);
