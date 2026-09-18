import { t } from "../../i18n";
import { chooseComposerModel, type ComposerModel } from "../../features/customize/models";
import { defaultModel, models } from "../../stores/customize";

/**
 * `#model-select` in the composer, rendered from the Customize stores that
 * `loadModels()` fills. The shell used to render a bare `<select>` that only a
 * `window.loadModels` bridge could fill, and nothing assigned one: the control
 * was a chevron with zero options.
 */
export function ModelSelect() {
  const list = models.value as ComposerModel[];
  const selected = typeof defaultModel.value === "string" ? defaultModel.value : "";
  return (
    <select
      id="model-select"
      data-i18n-title="composer.model"
      title="模型"
      value={list.length ? selected : ""}
      onChange={(event) => {
        const value = (event.currentTarget as HTMLSelectElement).value;
        if (value) void chooseComposerModel(value);
      }}
    >
      {list.length ? (
        list.map((entry) => (
          <option key={entry.id} value={entry.id} title={entry.description || undefined}>
            {entry.name || entry.id}
          </option>
        ))
      ) : (
        <option value="">{t("models.none")}</option>
      )}
    </select>
  );
}
