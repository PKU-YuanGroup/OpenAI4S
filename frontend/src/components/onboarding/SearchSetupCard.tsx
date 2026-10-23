import { useEffect, useState } from "preact/hooks";
import { api, apiErrorText } from "../../features/customize/api";
import { hint } from "../../features/customize/host";
import {
  engineLabel,
  readSearchConfig,
  searchT,
  type SearchConfig,
} from "../../features/customize/search";

type SetupChoice = "doubao" | "tavily" | "you" | "builtin";
const CHOICES: readonly SetupChoice[] = ["doubao", "tavily", "you", "builtin"];

function currentOrder(config: SearchConfig): string {
  // Usable, not merely on: a switched-on Tavily without a key never answers.
  const backups = config.engines.filter((e) => e.usable).map((e) => engineLabel(e.id));
  return (config.doubaoConfigured ? [searchT("search.doubao"), ...backups] : backups).join(" → ");
}

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
  const keyRequired = choice === "doubao" || (choice === "tavily" && !tavilyHasKey);
  const takesKey = choice === "doubao" || choice === "tavily" || choice === "you";

  const apply = async () => {
    if (!choice) return;
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
            checked={choice === id}
            disabled={busy}
            onChange={() => {
              setChoice(id);
              setKey("");
            }}
          />
          <span>{searchT("search.setup." + id)}</span>
        </label>
      ))}
      {choice && takesKey ? (
        <input
          class="cust-input"
          type="password"
          autocomplete="off"
          data-onboarding-search-key=""
          placeholder={
            choice === "doubao"
              ? searchT("search.setup.keyPh.doubao")
              : searchT("search.keyPh." + choice)
          }
          value={key}
          disabled={busy}
          onInput={(e) => setKey((e.target as HTMLInputElement).value)}
        />
      ) : null}
      {choice ? (
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
