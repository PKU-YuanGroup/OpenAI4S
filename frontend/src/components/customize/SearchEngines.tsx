import { useState } from "preact/hooks";
import { api, apiErrorText } from "../../features/customize/api";
import { hint } from "../../features/customize/host";
import {
  canSwitchOff,
  engineConfigBody,
  engineDescription,
  engineLabel,
  moved,
  readSearchConfig,
  searchT,
  type SearchConfig,
  type SearchEngine,
  type SearchEngineId,
} from "../../features/customize/search";
import { Icon } from "./icons";
import { Toggle } from "./ui";
import { useAlive } from "./use-timer-lease";

/**
 * The search-engine list on Settings → Network: Doubao pinned on top, the
 * backup engines below it reorderable (drag, or the arrow buttons) and
 * switchable. Every change is one `POST /search/config`; the server re-checks
 * the "one usable engine stays on" rule this list already enforces.
 */
export function SearchEngines({
  raw,
  onSaved,
}: {
  raw: Record<string, unknown>;
  onSaved: (next: Record<string, unknown>) => void;
}) {
  const alive = useAlive();
  const config = readSearchConfig(raw);
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState<SearchEngineId | null>(null);

  const send = async (body: Record<string, unknown>, done: string) => {
    setBusy(true);
    try {
      // POST, the verb Customize's config-write list (`api.ts`) already knows.
      const next = await api("/search/config", { method: "POST", body: JSON.stringify(body) });
      onSaved(next);
      hint(done);
      return true;
    } catch (e) {
      hint(apiErrorText(e), true);
      return false;
    } finally {
      if (alive()) setBusy(false);
    }
  };
  const reorder = (order: SearchEngineId[]) =>
    void send(engineConfigBody(config, { order }), searchT("search.saved"));

  if (!config.engines.length) return null;
  const order = config.engines.map((e) => e.id);
  return (
    <section class="search-engines" data-search-engines="">
      <div class="search-engines-head">
        <div class="nm">{searchT("search.title")}</div>
        <div class="ds">{searchT("search.desc")}</div>
      </div>
      <div class="cust-row search-engine-row locked" data-engine="doubao">
        <span class="search-engine-handle">
          <Icon name="lock" size={14} />
        </span>
        <div class="info">
          <div class="nm">{searchT("search.doubao")}</div>
          <div class="ds">
            {searchT(config.doubaoConfigured ? "search.doubao.on" : "search.doubao.off")}
          </div>
        </div>
      </div>
      {config.engines.map((engine, index) => (
        <EngineRow
          key={engine.id}
          engine={engine}
          index={index}
          last={index === config.engines.length - 1}
          config={config}
          busy={busy}
          dragging={dragging === engine.id}
          onMove={(delta) => reorder(moved(order, engine.id, delta))}
          onToggle={() =>
            void send(engineConfigBody(config, { toggle: engine.id }), searchT("search.saved"))
          }
          onSaveKey={(key) =>
            send({ engine: engine.id, api_key: key }, searchT("search.keySaved"))
          }
          onDragStart={() => setDragging(engine.id)}
          onDragEnd={() => setDragging(null)}
          onDrop={() => {
            if (dragging && dragging !== engine.id) {
              reorder(moved(order, dragging, index - order.indexOf(dragging)));
            }
            setDragging(null);
          }}
        />
      ))}
    </section>
  );
}

function EngineRow({
  engine,
  index,
  last,
  config,
  busy,
  dragging,
  onMove,
  onToggle,
  onSaveKey,
  onDragStart,
  onDragEnd,
  onDrop,
}: {
  engine: SearchEngine;
  index: number;
  last: boolean;
  config: SearchConfig;
  busy: boolean;
  dragging: boolean;
  onMove: (delta: number) => void;
  onToggle: () => void;
  onSaveKey: (key: string) => Promise<boolean>;
  onDragStart: () => void;
  onDragEnd: () => void;
  onDrop: () => void;
}) {
  const [key, setKey] = useState("");
  const locked = engine.enabled && !canSwitchOff(config, engine.id);
  const saveKey = async () => {
    const value = key.trim();
    if (!value) return;
    if (await onSaveKey(value)) setKey("");
  };
  return (
    <div
      class={"cust-row search-engine-row" + (dragging ? " dragging" : "")}
      data-engine={engine.id}
      draggable={!busy}
      onDragStart={(e) => {
        e.dataTransfer?.setData("text/plain", engine.id);
        onDragStart();
      }}
      onDragEnd={onDragEnd}
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => {
        e.preventDefault();
        onDrop();
      }}
    >
      <span class="search-engine-handle" title={searchT("search.drag")} aria-hidden="true">
        ≡
      </span>
      <div class="info">
        <div class="nm">
          {index + 1}. {engineLabel(engine.id)}
        </div>
        <div class="ds">{engineDescription(engine)}</div>
        {engine.acceptsKey ? (
          <div class="job-submit">
            <input
              class="cust-input"
              type="password"
              autocomplete="off"
              data-engine-key={engine.id}
              placeholder={searchT("search.keyPh." + engine.id)}
              value={key}
              onInput={(e) => setKey((e.target as HTMLInputElement).value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  void saveKey();
                }
              }}
            />
            <button
              type="button"
              class="solid-btn small"
              disabled={busy || !key.trim()}
              onClick={() => void saveKey()}
            >
              {searchT("search.saveKey")}
            </button>
          </div>
        ) : null}
      </div>
      <div class="search-engine-actions">
        <button
          type="button"
          class="outline-btn small"
          data-action="search-engine-up"
          aria-label={searchT("search.up")}
          title={searchT("search.up")}
          disabled={busy || index === 0}
          onClick={() => onMove(-1)}
        >
          ↑
        </button>
        <button
          type="button"
          class="outline-btn small"
          data-action="search-engine-down"
          aria-label={searchT("search.down")}
          title={searchT("search.down")}
          disabled={busy || last}
          onClick={() => onMove(1)}
        >
          ↓
        </button>
        <Toggle
          on={engine.enabled}
          disabled={busy || locked}
          title={locked ? searchT("search.lastUsable") : engineLabel(engine.id)}
          onClick={onToggle}
        />
      </div>
    </div>
  );
}
