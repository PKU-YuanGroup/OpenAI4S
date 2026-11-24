import { useEffect, useState } from "preact/hooks";
import { copyLookup } from "../../i18n/copy";
import { api, apiErrorText } from "../../features/customize/api";
import {
  engineLabel,
  readSearchConfig,
  searchT,
  type SearchConfig,
} from "../../features/customize/search";
import { vendorKeyT } from "../customize/vendors/use-vendor-key";

// Same choices, in the same order, as `openai4s init` (search_settings.SETUP_CHOICES).
type SetupChoice = "doubao" | "tavily" | "you" | "builtin" | "parallel";
const CHOICES: readonly SetupChoice[] = ["doubao", "tavily", "you", "builtin", "parallel"];

function currentOrder(config: SearchConfig): string {
  // Usable, not merely on: a switched-on Tavily without a key never answers.
  const backups = config.engines.filter((e) => e.usable).map((e) => engineLabel(e.id));
  return (config.doubaoConfigured ? [searchT("search.doubao"), ...backups] : backups).join(" → ");
}

/**
 * The choice the current order already is: Doubao when it answers first,
 * else the first engine that can answer. The card said "Current order:
 * You.com → …" over five radios none of which was selected.
 */
export function currentSearchChoice(config: SearchConfig | null): SetupChoice | null {
  if (!config) return null;
  if (config.doubaoConfigured) return "doubao";
  const first = config.engines.find((e) => e.usable);
  return first && (CHOICES as readonly string[]).includes(first.id) ? (first.id as SetupChoice) : null;
}

const DOUBAO_KEY_COPY = copyLookup({
  zh: {
    "search.setup.doubaoKeyNote":
      "豆包搜索需要在这里保存一次 Agent Plan Key。只通过环境变量（OPENAI4S_ARK_API_KEY 或 OPENAI4S_LLM_API_KEY）提供给模型的 Key 不会自动共用。",
    "search.setup.doubaoKeyNote.share":
      "豆包搜索需要 Agent Plan Key。只通过环境变量（OPENAI4S_ARK_API_KEY 或 OPENAI4S_LLM_API_KEY）提供给模型的 Key 不会自动共用；可点「使用当前模型的 Key」共用。",
    "search.setup.needKey.share": "这个选项需要填写 Key，或点「使用当前模型的 Key」",
  },
  en: {
    "search.setup.doubaoKeyNote":
      "Doubao Search needs the Agent Plan Key saved here once. A model key given only through environment variables (OPENAI4S_ARK_API_KEY or OPENAI4S_LLM_API_KEY) is not shared automatically.",
    "search.setup.doubaoKeyNote.share":
      "Doubao Search needs an Agent Plan Key. A model key given only through environment variables (OPENAI4S_ARK_API_KEY or OPENAI4S_LLM_API_KEY) is not shared automatically; press \"Use the current model's key\" to share it.",
    "search.setup.needKey.share": "This option needs a key, or press \"Use the current model's key\"",
  },
});

/**
 * Lets the wizard's Continue apply a choice the user made but did not Apply.
 * The card sets `current` on every render; it resolves `true` when there is
 * nothing pending or the choice was saved, `false` when it was refused or a
 * required key is missing (the reason is then shown in the card).
 */
export type SearchSetupHandle = { current: (() => Promise<boolean>) | null };

type Note = { text: string; err: boolean } | null;

/**
 * First-run setup's one, optional search decision. It lives inside the
 * "environment & network" step rather than as a fifth step: the wizard keeps
 * exactly four required decisions. Doubao saves the Agent Plan key the way the
 * Network tab's card does; any other choice is `POST /search/config` with
 * `primary`, which moves that engine first (builtin: keyless engines only).
 *
 * ONB-2: Continue used to drop a pending choice, and Apply's "needs a key"
 * went to the composer status line under the modal. The outcome of an apply
 * is now said in the card, and `pendingRef` lets Continue apply it.
 */
export function SearchSetupCard({ pendingRef }: { pendingRef?: SearchSetupHandle } = {}) {
  const [config, setConfig] = useState<SearchConfig | null>(null);
  const [choice, setChoice] = useState<SetupChoice | null>(null);
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<Note>(null);
  // `GET /doubao-search/config`: the model in use is an Ark model on
  // Volcengine whose key the server can save as the Agent Plan Key.
  const [modelKeyAvailable, setModelKeyAvailable] = useState(false);

  useEffect(() => {
    let live = true;
    api("/search/config")
      .then((raw) => {
        if (live) setConfig(readSearchConfig(raw));
      })
      .catch(() => {
        /* the card stays without a current-order line */
      });
    api("/doubao-search/config")
      .then((raw) => {
        if (live) setModelKeyAvailable(raw?.model_key_available === true && raw?.key_configured !== true);
      })
      .catch(() => {
        /* no shortcut: the key can still be typed */
      });
    return () => {
      live = false;
    };
  }, []);

  useEffect(
    () => () => {
      if (pendingRef) pendingRef.current = null;
    },
    [],
  );

  const tavilyHasKey = !!config?.engines.find((e) => e.id === "tavily")?.keyConfigured;
  const current = currentSearchChoice(config);
  // Only a change has anything to apply; the current choice shows as selected.
  const pending = choice && choice !== current ? choice : null;
  const keyRequired = pending === "doubao" || (pending === "tavily" && !tavilyHasKey);
  const takesKey = pending === "doubao" || pending === "tavily" || pending === "you";
  const offerModelKey = pending === "doubao" && modelKeyAvailable;

  const apply = async (useModelKey = false): Promise<boolean> => {
    if (!pending) return true;
    if (busy) return false;
    const choice = pending;
    const value = key.trim();
    if (keyRequired && !value && !useModelKey) {
      setNote({
        text: offerModelKey ? DOUBAO_KEY_COPY("search.setup.needKey.share") : searchT("search.setup.needKey"),
        err: true,
      });
      return false;
    }
    setBusy(true);
    setNote(null);
    try {
      let next: Record<string, unknown>;
      if (choice === "doubao") {
        await api("/doubao-search/config", {
          method: "POST",
          body: JSON.stringify(useModelKey ? { use_model_key: true } : { agent_plan_key: value }),
        });
        next = await api("/search/config");
      } else {
        const body: Record<string, unknown> = { primary: choice };
        if (value && choice !== "builtin") Object.assign(body, { engine: choice, api_key: value });
        next = await api("/search/config", { method: "POST", body: JSON.stringify(body) });
      }
      setConfig(readSearchConfig(next));
      setKey("");
      setChoice(null);
      if (choice === "doubao") setModelKeyAvailable(false);
      setNote({ text: searchT("search.setup.applied"), err: false });
      return true;
    } catch (e) {
      setNote({ text: apiErrorText(e), err: true });
      return false;
    } finally {
      setBusy(false);
    }
  };

  if (pendingRef) pendingRef.current = () => apply();

  return (
    <section class="standard-readiness-card onb-search" data-onboarding-search="">
      <div class="standard-readiness-title">{searchT("search.setup.title")}</div>
      <div class="ds">{searchT("search.setup.desc")}</div>
      {config ? (
        <div class="ds" data-search-current="">
          {searchT("search.setup.current", currentOrder(config))}
        </div>
      ) : null}
      {CHOICES.map((id) => (
        <label class={"onb-choice" + (id === "doubao" ? " onb-choice-env" : "")} key={id} data-search-choice={id}>
          <input
            type="radio"
            name="onb-search"
            value={id}
            checked={(choice ?? current) === id}
            disabled={busy}
            onChange={() => {
              setChoice(id);
              setKey("");
              setNote(null);
            }}
          />
          {id === "doubao" ? (
            <span>
              <span class="onb-choice-name">{searchT("search.setup.doubao")}</span>
              <span class="onb-choice-sub">{searchT("search.setup.doubao.sub")}</span>
            </span>
          ) : (
            <span>{searchT("search.setup." + id)}</span>
          )}
        </label>
      ))}
      {pending === "doubao" ? (
        <div class="ds" data-search-doubao-note="">
          {DOUBAO_KEY_COPY(offerModelKey ? "search.setup.doubaoKeyNote.share" : "search.setup.doubaoKeyNote")}
        </div>
      ) : null}
      {pending && takesKey ? (
        <input
          class="cust-input"
          type="password"
          autocomplete="off"
          data-onboarding-search-key=""
          placeholder={
            pending === "doubao"
              ? searchT("search.setup.keyPh.doubao")
              : searchT("search.keyPh." + pending)
          }
          value={key}
          disabled={busy}
          onInput={(e) => {
            setKey((e.target as HTMLInputElement).value);
            setNote(null);
          }}
        />
      ) : null}
      {offerModelKey ? (
        <div class="ds" data-search-model-key="">
          {vendorKeyT("vendor.modelKeyHint")}
        </div>
      ) : null}
      {pending ? (
        <div class="onb-search-actions">
          {offerModelKey ? (
            <button
              type="button"
              class="outline-btn"
              data-search-use-model-key=""
              disabled={busy}
              onClick={() => apply(true).then(() => undefined)}
            >
              {vendorKeyT("vendor.useModelKey")}
            </button>
          ) : null}
          <button
            type="button"
            class="outline-btn"
            data-action="onboarding-search-apply"
            disabled={busy}
            onClick={() => void apply()}
          >
            {searchT("search.setup.apply")}
          </button>
        </div>
      ) : null}
      {note ? (
        <div
          class={note.err ? "onb-field-error" : "onb-ok"}
          role={note.err ? "alert" : "status"}
          data-search-note=""
        >
          {note.text}
        </div>
      ) : null}
    </section>
  );
}
