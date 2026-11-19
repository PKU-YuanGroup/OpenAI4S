import { useEffect, useState } from "preact/hooks";
import { copyLookup } from "../../i18n/copy";
import { api, apiErrorText } from "../../features/customize/api";
import { hint } from "../../features/customize/host";
import {
  engineLabel,
  readSearchConfig,
  searchT,
  type SearchConfig,
} from "../../features/customize/search";

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
      "豆包搜索需要在这里保存一次 Agent Plan Key。只通过环境变量（OPENAI4S_ARK_API_KEY 或 OPENAI4S_LLM_API_KEY）提供给模型的 Key 不会自动用于搜索，即使它们是同一个套餐 Key。",
  },
  en: {
    "search.setup.doubaoKeyNote":
      "Doubao search needs the Agent Plan key saved here once. A model key given only through environment variables (OPENAI4S_ARK_API_KEY or OPENAI4S_LLM_API_KEY) is not used for search automatically, even when it is the same plan key.",
  },
});

/**
 * First-run setup's one, optional search decision. It lives inside the
 * "environment & network" step rather than as a fifth step: the wizard keeps
 * exactly four required decisions. Doubao saves the Agent Plan key the way the
 * Network tab's card does; any other choice is `POST /search/config` with
 * `primary`, which moves that engine first (builtin: keyless engines only).
 */
export function SearchSetupCard() {
  const [config, setConfig] = useState<SearchConfig | null>(null);
  const [choice, setChoice] = useState<SetupChoice | null>(null);
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let live = true;
    api("/search/config")
      .then((raw) => {
        if (live) setConfig(readSearchConfig(raw));
      })
      .catch(() => {
        /* the card stays without a current-order line */
      });
    return () => {
      live = false;
    };
  }, []);

  const tavilyHasKey = !!config?.engines.find((e) => e.id === "tavily")?.keyConfigured;
  const current = currentSearchChoice(config);
  // Only a change has anything to apply; the current choice shows as selected.
  const pending = choice && choice !== current ? choice : null;
  const keyRequired = pending === "doubao" || (pending === "tavily" && !tavilyHasKey);
  const takesKey = pending === "doubao" || pending === "tavily" || pending === "you";

  const apply = async () => {
    if (!pending) return;
    const choice = pending;
    const value = key.trim();
    if (keyRequired && !value) {
      hint(searchT("search.setup.needKey"), true);
      return;
    }
    setBusy(true);
    try {
      let next: Record<string, unknown>;
      if (choice === "doubao") {
        await api("/doubao-search/config", {
          method: "POST",
          body: JSON.stringify({ agent_plan_key: value }),
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
      hint(searchT("search.setup.applied"));
    } catch (e) {
      hint(apiErrorText(e), true);
    } finally {
      setBusy(false);
    }
  };

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
        <label class="onb-choice" key={id}>
          <input
            type="radio"
            name="onb-search"
            value={id}
            checked={(choice ?? current) === id}
            disabled={busy}
            onChange={() => {
              setChoice(id);
              setKey("");
            }}
          />
          <span>{searchT("search.setup." + id)}</span>
        </label>
      ))}
      {pending === "doubao" ? (
        <div class="ds" data-search-doubao-note="">
          {DOUBAO_KEY_COPY("search.setup.doubaoKeyNote")}
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
          onInput={(e) => setKey((e.target as HTMLInputElement).value)}
        />
      ) : null}
      {pending ? (
        <button
          type="button"
          class="outline-btn"
          data-action="onboarding-search-apply"
          disabled={busy}
          onClick={() => void apply()}
        >
          {searchT("search.setup.apply")}
        </button>
      ) : null}
    </section>
  );
}
