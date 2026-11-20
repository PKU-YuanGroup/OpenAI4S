import { t } from "../../i18n";
import { openCustomize } from "../../features/customize";
import {
  chooseComposerModel,
  composerSelection,
  credentialMissing,
  modelT,
  type ComposerModel,
} from "../../features/customize/models";
import { models } from "../../stores/customize";

/**
 * Beside `#model-select` while the next message would be refused for want of
 * an API key. The selector names a default model either way, which read as
 * "ready" on a daemon nobody had configured; this says so before the first
 * send, on the home page and in a session alike, and opens Settings → Models.
 */
export function ModelKeyNotice() {
  if (!credentialMissing.value) return null;
  return (
    <button
      type="button"
      class="nb-model-nokey"
      title={modelT("model.nokey.title")}
      onClick={() => void openCustomize("models")}
    >
      {modelT("model.nokey")}
    </button>
  );
}

/**
 * `#model-select` in the composer, rendered from the Customize stores that
 * `loadModels()` fills. The shell used to render a bare `<select>` that only a
 * `window.loadModels` bridge could fill, and nothing assigned one: the control
 * was a chevron with zero options.
 *
 * It shows the model the open session is pinned to, not the server default:
 * a session runs on its own pin, so showing the default there named a model
 * the conversation was not using. A pin no entry can stand for (a deleted
 * profile, an earlier revision of a listed one) is a disabled placeholder.
 * A change re-pins that session (and makes the choice the default for new
 * ones) through `chooseComposerModel`.
 */
export function ModelSelect() {
  const list = models.value as ComposerModel[];
  const { value, placeholder } = composerSelection();
  return (
    <select
      id="model-select"
      data-i18n-title="composer.model"
      title="模型"
      value={list.length ? value : ""}
      onChange={(event) => {
        const chosen = (event.currentTarget as HTMLSelectElement).value;
        if (chosen) void chooseComposerModel(chosen);
      }}
    >
      {list.length ? (
        [
          placeholder ? (
            <option key="" value="" disabled>
              {placeholder}
            </option>
          ) : null,
          ...list.map((entry) => (
            <option key={entry.id} value={entry.id} title={entry.description || undefined}>
              {entry.name || entry.id}
            </option>
          )),
        ]
      ) : (
        <option value="">{t("models.none")}</option>
      )}
    </select>
  );
}
