import { useEffect } from "preact/hooks";
import { t } from "../../i18n";
import { closeCust, custTab } from "../../features/customize/actions";
import {
  customizeGeneration,
  customizeOpen,
  customizeTab,
  nestedEditor,
} from "../../features/customize/state";
import { CUST_TABS, CUST_TAB_I18N } from "../../features/customize/tabs";
import { Icon } from "./icons";
import { GeneralTab } from "./GeneralTab";
import { SkillsTab } from "./SkillsTab";
import { SpecialistsTab } from "./SpecialistsTab";
import { ConnectorsTab } from "./ConnectorsTab";
import { ComputeTab } from "./ComputeTab";
import { PermissionsTab } from "./PermissionsTab";
import { NetworkTab } from "./NetworkTab";
import { MemoryTab } from "./MemoryTab";
import { ModelsTab } from "./ModelsTab";
import { NestedEditor } from "./NestedEditor";
import "./customize.css";

function ActiveTab() {
  switch (customizeTab.value) {
    case "general":
      return <GeneralTab />;
    case "skills":
      return <SkillsTab />;
    case "specialists":
      return <SpecialistsTab />;
    case "connectors":
      return <ConnectorsTab />;
    case "compute":
      return <ComputeTab />;
    case "permissions":
      return <PermissionsTab />;
    case "network":
      return <NetworkTab />;
    case "memory":
      return <MemoryTab />;
    case "models":
      return <ModelsTab />;
  }
}

export function Customize() {
  const open = customizeOpen.value;
  const tab = customizeTab.value;
  const gen = customizeGeneration.value;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (!customizeOpen.value) return;
      if (nestedEditor.value) {
        nestedEditor.value = null;
        e.preventDefault();
        return;
      }
      e.preventDefault();
      closeCust();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div
      id="cust"
      class={"modal" + (open ? "" : " hidden")}
      role="dialog"
      aria-modal="true"
      aria-label={t("common.settings")}
      onClick={(e) => {
        if ((e.target as HTMLElement).id === "cust") closeCust();
      }}
    >
      <div class="modal-box cust-box">
        <div class="modal-head">
          <span data-i18n="common.settings">{t("common.settings")}</span>
          <button
            id="cust-close"
            class="icon-ghost"
            data-icon="x"
            data-icon-size="16"
            aria-label="Close"
            type="button"
            onClick={() => closeCust()}
          >
            <Icon name="x" size={16} />
          </button>
        </div>
        <div class="cust-body">
          <nav class="cust-tabs" role="tablist" aria-label="Settings sections">
            {CUST_TABS.map((id) => (
              <button
                key={id}
                type="button"
                class={"cust-tab" + (id === tab ? " active" : "")}
                data-tab={id}
                role="tab"
                aria-selected={id === tab ? "true" : "false"}
                data-i18n={CUST_TAB_I18N[id]}
                onClick={() => custTab(id)}
              >
                {t(CUST_TAB_I18N[id])}
              </button>
            ))}
          </nav>
          <div id="cust-content" class="cust-content" role="tabpanel">
            {open ? <ActiveTab key={gen} /> : null}
          </div>
        </div>
        {nestedEditor.value ? <NestedEditor /> : null}
      </div>
    </div>
  );
}
