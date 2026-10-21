/**
 * Local-model discovery sanitizer and protocol catalogue.
 * Port of app.js:12064-12150. Probe/readiness stay on the existing routes
 * (`GET /model-profiles` readiness is local-only; `POST .../probe` spends
 * quota). Capability-receipt badges read the B-04 additive field when present.
 */
import { effect } from "@preact/signals";
import { publicText } from "../scrub/scrub";
import { t, tOptional } from "../../i18n";
import { copyLookup, type CopyTable } from "../../i18n/copy";
import { isReady } from "../../compat/stub";
import { defaultModel, defaultModelName, models } from "../../stores/customize";
import { currentId } from "../../stores/session";
import { field } from "../../stores/signal-field";
import { running } from "../../stores/stream";
import { api, apiErrorText } from "./api";

export const LOCAL_MODEL_KINDS = new Set([
  "ollama",
  "lm_studio",
  "vllm",
  "llama_cpp",
]);

export function loopbackModelBase(value: unknown): string {
  const text = publicText(value, 600);
  try {
    const parsed = new URL(text);
    const host = parsed.hostname.toLowerCase();
    const safeHost = host === "127.0.0.1" || host === "::1" || host === "[::1]";
    return ["http:", "https:"].includes(parsed.protocol) &&
      safeHost &&
      !parsed.username &&
      !parsed.password &&
      !parsed.search &&
      !parsed.hash
      ? parsed.toString().replace(/\/$/, "")
      : "";
  } catch {
    return "";
  }
}

export type LocalEndpoint = {
  kind: string;
  label: string;
  provider: "chatgpt" | "ollama";
  base_url: string;
  models: string[];
  default_model: string;
  requires_api_key: false;
};

export type LocalDiscovery = {
  endpoints: LocalEndpoint[];
  probed: number;
  mutated_settings: false;
};

export function sanitizeLocalModelDiscovery(payload: unknown): LocalDiscovery {
  const source =
    payload && typeof payload === "object" ? (payload as Record<string, unknown>) : {};
  const endpoints: LocalEndpoint[] = [];
  const rawList = Array.isArray(source.endpoints) ? source.endpoints : [];
  rawList.slice(0, 20).forEach((raw) => {
    if (!raw || typeof raw !== "object") return;
    const row = raw as Record<string, unknown>;
    const kind = publicText(row.kind, 32);
    const provider = kind === "ollama" ? "ollama" : "chatgpt";
    const baseUrl = loopbackModelBase(row.base_url);
    if (
      !LOCAL_MODEL_KINDS.has(kind) ||
      !baseUrl ||
      row.local !== true ||
      (row.provider !== provider && row.provider !== "chatgpt")
    ) {
      return;
    }
    const models: string[] = [];
    (Array.isArray(row.models) ? row.models : []).slice(0, 500).forEach((value) => {
      if (typeof value !== "string") return;
      const model = publicText(value, 512);
      if (model && !models.includes(model)) models.push(model);
    });
    endpoints.push({
      kind,
      label: publicText(row.label, 80) || kind,
      provider,
      base_url: baseUrl,
      models,
      default_model: models.includes(String(row.default_model || ""))
        ? String(row.default_model)
        : models[0] || "",
      requires_api_key: false,
    });
  });
  return {
    endpoints,
    probed: Math.max(0, Math.min(20, Number(source.probed) || 0)),
    mutated_settings: false,
  };
}

const PROTOCOL_LABEL_KEYS: Record<string, string> = {
  chatgpt: "cust.models.protocol.openai",
  claude: "cust.models.protocol.anthropic",
  ark: "cust.models.protocol.ark",
  gemini: "cust.models.protocol.gemini",
  openai_responses: "cust.models.protocol.openaiResponses",
};

export type ProtocolOption = { value: string; label: string };

export function modelProtocolOptions(served: unknown): ProtocolOption[] {
  const ids: string[] = [];
  (Array.isArray(served) ? served : []).forEach((value) => {
    const id = typeof value === "string" ? value.trim().slice(0, 64) : "";
    if (id && !ids.includes(id)) ids.push(id);
  });
  const list = ids.length ? ids : ["chatgpt", "claude", "ark"];
  return list.map((id) => ({
    value: id,
    label: id === "ollama"
      ? modelT("model.ollama")
      : tOptional(PROTOCOL_LABEL_KEYS[id] || "") || id,
  }));
}

export type Evidence = "true" | "false" | "unknown";

export type CapabilityReceipt = {
  native_tool_call: Evidence;
  streaming: Evidence;
  stale: boolean;
  native_completion: boolean;
  reachable: boolean;
  detail?: string;
};

function asEvidence(value: unknown): Evidence {
  if (value === true || value === "true") return "true";
  if (value === false || value === "false") return "false";
  return "unknown";
}

export function readCapabilityReceipt(raw: unknown): CapabilityReceipt | null {
  if (!raw || typeof raw !== "object") return null;
  const row = raw as Record<string, unknown>;
  if (row.native_tool_call == null && row.streaming == null) return null;
  return {
    native_tool_call: asEvidence(row.native_tool_call),
    streaming: asEvidence(row.streaming),
    stale: row.stale === true,
    native_completion: row.native_completion === true,
    reachable: row.reachable === true,
  };
}

export function protocolLabelOf(
  protocols: ProtocolOption[],
  provider: unknown,
): string {
  const id = typeof provider === "string" ? provider : "";
  const match = protocols.find((item) => item.value === id);
  return match ? match.label : id;
}

/** One `#model-select` entry, as `GET /models` lists it. */
export type ComposerModel = {
  id: string;
  name: string;
  description: string;
  model: string;
  /** The profile revision choosing this entry pins now; 0 for the live entry. */
  revision: number;
};

/**
 * Not `publicText`: these values go back to the server as `model_id` and into
 * `frames.model`, and its credential-shape redaction rewrites legitimate model
 * ids -- `ark-code-latest` matches its `ark-<8+ chars>` pattern and would be
 * sent as "[redacted]". `GET /models` carries no credential to redact.
 */
function entryText(value: unknown): string {
  return typeof value === "string" ? value.trim().slice(0, 512) : "";
}

function readComposerModels(payload: Record<string, unknown>): ComposerModel[] {
  const groups =
    payload.models && typeof payload.models === "object"
      ? Object.values(payload.models as Record<string, unknown>)
      : [];
  const list: ComposerModel[] = [];
  groups.forEach((group) => {
    (Array.isArray(group) ? group : []).forEach((raw) => {
      if (!raw || typeof raw !== "object") return;
      const row = raw as Record<string, unknown>;
      const id = entryText(row.id);
      if (!id || list.some((entry) => entry.id === id)) return;
      const revision = Number(row.revision);
      list.push({
        id,
        name: entryText(row.name) || id,
        description: entryText(row.description),
        model: entryText(row.model),
        revision: Number.isInteger(revision) && revision > 0 ? revision : 0,
      });
    });
  });
  return list;
}

/**
 * The model name for an entry. The option value is a `profile_id` for saved
 * profiles, and session creation sends this display-only name as `model`:
 * sending the id there would store a profile id in `frames.model`.
 */
export function composerModelName(id: unknown): string {
  const key = typeof id === "string" ? id : "";
  const entry = (models.value as ComposerModel[]).find((item) => item.id === key);
  return (entry && (entry.model || entry.name)) || key;
}

/**
 * Port of app.js `loadModels`: fill the composer selector's stores from
 * `GET /models`. It was bridged to a `window.loadModels` nothing assigned, so
 * the selector stayed empty, every new frame recorded `model: null`, and the
 * post-profile-change refreshes in Customize did nothing.
 */
export async function loadModels(): Promise<void> {
  const choice = choiceSeq;
  try {
    const payload = await api("/models");
    if (choice !== choiceSeq) return;
    const list = readComposerModels(payload);
    models.value = list;
    const wanted = entryText(payload.default_model_id);
    // An unlisted default (e.g. the id of a since-deleted profile) falls back
    // to the first entry, and that is not an arbitrary pick: `models_payload`
    // always lists the daemon's live model (`llm_model`, else `cfg.llm.model`)
    // first, which is the model `resolve_llm_config` runs an unpinned session
    // on. A `model` sent from it (session creation, plan approve/resume/revise)
    // restates the server's default rather than overriding it. app.js kept the
    // unlisted id instead, and so sent a deleted profile's id as `model`.
    const chosen = list.some((entry) => entry.id === wanted) ? wanted : list[0]?.id || null;
    defaultModel.value = chosen;
    defaultModelName.value = chosen ? composerModelName(chosen) : null;
  } catch {
    if (choice === choiceSeq) models.value = [];
  }
}


/**
 * The model configuration the open session is pinned to, as `GET /frames/{id}`
 * reports it. `profileId: ""` means the session was read and is not pinned yet
 * (its first send binds whatever is the default then); `null`, or a pin for
 * another frame, means nothing is known for the session on screen.
 */
export type SessionModelPin = { frameId: string; profileId: string; revision: number };

export const sessionModelPin = field((): SessionModelPin | null => null);

/** The last pin seen per session, so reopening one shows it before the read lands. */
const knownPins = new Map<string, SessionModelPin>();

/**
 * Bumped by every read and every local write of the pin. A read applies its
 * answer only if nothing touched the pin after it started: `GET /frames/{id}`
 * is queued behind the burst of reads opening a session, and a slow one used
 * to land after the user's own switch and put the old pin back.
 */
let pinVersion = 0;

function setPin(pin: SessionModelPin | null): void {
  pinVersion += 1;
  if (pin) knownPins.set(pin.frameId, pin);
  sessionModelPin.value = pin;
  if (pin?.profileId) refreshListFor(pin);
}

/** Pins the list was already reloaded for, so a truly deleted profile asks once. */
const listRefreshedFor = new Set<string>();

/**
 * A pin naming a profile the list does not have, or a newer revision than the
 * list says, is first evidence that the list is stale (another tab, an admin)
 * -- not that the profile is gone. Reload it once before saying so.
 */
function refreshListFor(pin: SessionModelPin): void {
  const list = models.peek() as ComposerModel[];
  // Nothing loaded yet: boot's own load is what fills it, not evidence of staleness.
  if (!list.length) return;
  const entry = list.find((item) => item.id === pin.profileId);
  if (entry && !(pin.revision && entry.revision && pin.revision > entry.revision)) return;
  const key = `${pin.profileId}@${pin.revision}`;
  if (listRefreshedFor.has(key)) return;
  listRefreshedFor.add(key);
  void loadModels();
}

function readPin(frameId: string, raw: Record<string, unknown> | null | undefined): SessionModelPin {
  const revision = Number(raw?.model_profile_revision);
  return {
    frameId,
    profileId: entryText(raw?.model_profile_id),
    revision: Number.isInteger(revision) && revision > 0 ? revision : 0,
  };
}

/** Read the open session's pin. A late answer, or one overtaken by a switch, is dropped. */
export async function loadSessionModelPin(frameId: string | null): Promise<void> {
  if (!frameId) {
    setPin(null);
    return;
  }
  if (sessionModelPin.peek()?.frameId !== frameId) setPin(knownPins.get(frameId) || null);
  const version = ++pinVersion;
  try {
    const frame = await api(`/frames/${encodeURIComponent(frameId)}`);
    // `{}` is the route's answer for a frame it does not know: nothing learned.
    if (version !== pinVersion || currentId.peek() !== frameId || !frame.id) return;
    setPin(readPin(frameId, frame));
  } catch {
    // Unreadable (offline, or a session this member cannot see): keep what is
    // shown, which falls back to the default when nothing is known.
  }
}

/**
 * Record what `POST /frames/{id}/model-binding` answered, from either caller
 * (this selector, or the send path's re-bind prompt).
 */
/** A mark taken before a send, so its 202 can tell whether a choice came after. */
export function composerChoiceMark(): number {
  return choiceSeq;
}

/**
 * The pair a sent message was admitted under (the 202's `model_binding`). It
 * is how the composer learns the pin a first send writes -- and the server's
 * answer at admission, so it is taken unless the user chose a model after the
 * send started (`mark`): that choice is newer, and its own answer stands.
 */
export function noteAdmittedModelBinding(frameId: string, binding: unknown, mark: number): void {
  if (mark !== choiceSeq) return;
  noteSessionModelBinding(frameId, { binding });
}

/** Tests: forget every session's cached pin and any queued re-pin. */
export function resetSessionModelState(): void {
  knownPins.clear();
  listRefreshedFor.clear();
  modelWriteChain = Promise.resolve();
  pendingChoices.clear();
  choiceSeq += 1;
  stopPinWatch?.();
}

export function noteSessionModelBinding(frameId: string, answer: unknown): void {
  const raw =
    answer && typeof answer === "object" ? (answer as { binding?: unknown }).binding : null;
  if (!raw || typeof raw !== "object") return;
  const pin = readPin(frameId, raw as Record<string, unknown>);
  knownPins.set(frameId, pin);
  if (currentId.peek() === frameId) setPin(pin);
}

let stopPinWatch: (() => void) | null = null;

/** Follow the open session: each newly opened session's pin is read. Idempotent. */
export function watchSessionModelPin(): () => void {
  if (stopPinWatch) return stopPinWatch;
  let last: string | null | undefined;
  const dispose = effect(() => {
    const frameId = currentId.value;
    if (frameId === last) return;
    last = frameId;
    void loadSessionModelPin(frameId);
  });
  stopPinWatch = () => {
    dispose();
    stopPinWatch = null;
  };
  return stopPinWatch;
}

/** What the selector shows for the session on screen. */
export type ComposerSelection = { value: string; placeholder: string | null };

/**
 * The open session's own pin when it has one, else the server default -- which
 * is also what an unsent session's first send will bind. A pin the list cannot
 * show as one of its entries (a deleted profile, or an earlier revision of a
 * listed one) is shown as a disabled placeholder saying so: showing the entry
 * would name a configuration the session does not run, and re-choosing an
 * already-selected option fires no change, so it could not be moved off it.
 */
export function composerSelection(): ComposerSelection {
  const list = models.value as ComposerModel[];
  const fallback = typeof defaultModel.value === "string" ? defaultModel.value : "";
  const frameId = currentId.value;
  const pin = sessionModelPin.value;
  if (!frameId || !pin || pin.frameId !== frameId || !pin.profileId) {
    return { value: fallback, placeholder: null };
  }
  const entry = list.find((item) => item.id === pin.profileId);
  if (!entry) return { value: "", placeholder: modelT("model.session.unavailable") };
  if (pin.revision && entry.revision && pin.revision < entry.revision) {
    return { value: "", placeholder: modelT("model.session.earlier", entry.name || entry.id) };
  }
  return { value: entry.id, placeholder: null };
}

const MODEL_COPY: CopyTable = {
  en: {
    "model.ollama": "Ollama (cloud or local)",
    "model.session.unavailable": "Model no longer available",
    "model.session.earlier": "{0} (earlier configuration)",
    "model.session.switched": "This session now uses {0}",
    "model.session.switchedNextTurn":
      "This session uses {0} from your next message; the turn already running keeps its model",
    "model.session.switchFailed": "Could not switch this session's model: {0}",
    "model.session.pending": "The model change is still saving. Please send again once it finishes.",
    "model.default.adminOnly": "Only an admin can change the default model",
  },
  zh: {
    "model.ollama": "Ollama（云端或本地）",
    "model.session.unavailable": "原模型已不可用",
    "model.session.earlier": "{0}（旧配置）",
    "model.session.switched": "该会话已切换到 {0}",
    "model.session.switchedNextTurn": "该会话将从你的下一条消息起使用 {0}；正在运行的这一轮保持原模型",
    "model.session.switchFailed": "未能切换该会话的模型：{0}",
    "model.session.pending": "模型切换仍在保存，请完成后再发送。",
    "model.default.adminOnly": "只有管理员可以修改默认模型",
  },
};

export const modelT = copyLookup(MODEL_COPY);

type Bridged = { hint?: (message: string, err?: boolean) => void; openCust?: (tab?: string) => void };

function bridged(): Bridged {
  return ((globalThis as { window?: unknown }).window || {}) as Bridged;
}

function hint(message: string, err = false): void {
  const fn = bridged().hint;
  if (isReady(fn)) fn(message, err);
}

function errorCode(error: unknown): string {
  const code = (error as { code?: unknown } | null)?.code;
  return typeof code === "string" ? code : "";
}

/** Only the latest choice may finish: a quick second pick supersedes the first. */
let choiceSeq = 0;
const pendingChoices = new Set<number>();

/** An optimistic selection is not yet the configuration admission will use. */
export function composerModelChoicePending(): boolean {
  return pendingChoices.size > 0;
}

/**
 * The server default only: the stores move at once, a refusal puts them back
 * -- unless a newer choice has moved them since. A profile id activates that
 * profile, which rewrites the live model, i.e. the list's first entry: the
 * list is reloaded so that entry names what the daemon now runs, instead of a
 * model id the next pick would be refused for (`model_selection_stale`).
 */
async function chooseDefaultModel(id: string, seq: number): Promise<boolean> {
  const previous = defaultModel.value;
  const previousName = defaultModelName.value;
  defaultModel.value = id;
  defaultModelName.value = composerModelName(id);
  try {
    await queueModelWrite(() =>
      api("/models/default", { method: "PUT", body: JSON.stringify({ model_id: id }) }),
    );
  } catch (error) {
    if (seq === choiceSeq) {
      defaultModel.value = previous;
      defaultModelName.value = previousName;
    }
    return Promise.reject(error);
  }
  if (seq === choiceSeq) void loadModels();
  return true;
}

/**
 * Re-pins and default writes share one queue. Concurrent defaults could
 * otherwise finish in reverse order, even while the selector showed the
 * newest choice. A slow request must not be overtaken after a timeout: its
 * server-side write is still capable of undoing a newer choice.
 */
let modelWriteChain: Promise<unknown> = Promise.resolve();

function queueModelWrite(write: () => Promise<Record<string, unknown>>): Promise<Record<string, unknown>> {
  const sent = modelWriteChain.then(write);
  modelWriteChain = sent.catch(() => undefined);
  return sent;
}

/**
 * Choose an entry. With a session open, that session is re-pinned to it first
 * (`POST /frames/{id}/model-binding {model_id}`): a session runs on the
 * configuration it is pinned to, so `PUT /models/default` alone -- all this
 * used to do -- changed what new sessions bind and nothing in the
 * conversation on screen. It then becomes the server default too
 * (`PUT /models/default`; a profile id activates that profile), so new
 * conversations start on it.
 *
 * A refused re-pin changes nothing, the default included. A refused default
 * (in team mode only an admin may set it) leaves the session's switch in
 * place: that one was the session owner's to make.
 */
export async function chooseComposerModel(id: string): Promise<boolean> {
  const seq = ++choiceSeq;
  pendingChoices.add(seq);
  try {
    return await applyComposerModel(id, seq);
  } finally {
    pendingChoices.delete(seq);
  }
}

async function applyComposerModel(id: string, seq: number): Promise<boolean> {
  const frameId = currentId.peek();
  if (!frameId) {
    try {
      return await chooseDefaultModel(id, seq);
    } catch (error) {
      if (seq === choiceSeq && errorCode(error) === "admin_only") {
        hint(modelT("model.default.adminOnly"));
      }
      return false;
    }
  }
  const shown = sessionModelPin.peek();
  const previous = shown?.frameId === frameId ? shown : knownPins.get(frameId) || null;
  setPin({ frameId, profileId: id, revision: 0 });
  let answer: Record<string, unknown>;
  try {
    answer = await queueModelWrite(() =>
      api(`/frames/${encodeURIComponent(frameId)}/model-binding`, {
        method: "POST",
        body: JSON.stringify({ model_id: id }),
      }),
    );
  } catch (error) {
    if (seq !== choiceSeq) return false;
    if (currentId.peek() === frameId) {
      if (!previous) knownPins.delete(frameId);
      setPin(previous);
      // What is shown was a guess (the pin before this choice, which may
      // itself have been an unconfirmed one): ask the server what it is.
      void loadSessionModelPin(frameId);
      hint(modelT("model.session.switchFailed", apiErrorText(error)), true);
      const code = errorCode(error);
      if (code === "model_profile_not_found" || code === "model_selection_stale") {
        void loadModels();
      } else if (code === "model_profile_needs_key") {
        const open = bridged().openCust;
        if (isReady(open)) open("models");
      }
    }
    return false;
  }
  if (seq !== choiceSeq) return false;
  noteSessionModelBinding(frameId, answer);
  if (currentId.peek() === frameId) {
    const list = models.value as ComposerModel[];
    const bound = sessionModelPin.peek()?.profileId || id;
    const name = (list.find((entry) => entry.id === bound) || list.find((entry) => entry.id === id))?.name || id;
    hint(modelT(running.peek() ? "model.session.switchedNextTurn" : "model.session.switched", name));
  }
  try {
    await chooseDefaultModel(id, seq);
  } catch {
    // Most often a member in team mode: the default is an admin's. The
    // session's own switch, which was theirs to make, stands.
  }
  return true;
}

export { t };
