import type { ComponentChildren } from "preact";
import { useEffect } from "preact/hooks";
import { t } from "../../i18n";
import { closeCust, custTab } from "../../features/customize/actions";
import {
  backdropClicked,
  followCustomizeDom,
  installCustomizeEscape,
  notePress,
  onCustomizeKeydown,
} from "../../features/customize/dismiss";
import {
  CUST_LOAD_TIMEOUT_MS,
  markCustomizeTimedOut,
} from "../../features/customize/load";
import {
  customizeGeneration,
  customizeLoad,
  customizeOpen,
  customizeTab,
} from "../../features/customize/state";
import { CUST_TABS, CUST_TAB_I18N, type CustTab } from "../../features/customize/tabs";
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

/**
 * `#cust-content`. The only part of the modal that follows the load state
 * (`aria-busy`); Customize itself does not read it, so a load settling does
 * not re-render the tab below.
 */
function Content({ open, children }: { open: boolean; children: ComponentChildren }) {
  const load = customizeLoad.value;
  const busy = open && load.generation === customizeGeneration.value && load.state === "loading";
  return (
    <div id="cust-content" class="cust-content" role="tabpanel" aria-busy={busy ? "true" : "false"}>
      {children}
    </div>
  );
}

/**
 * The bounded-load annotation above the tab body. "Loading…" while the tab's
 * first fetch is pending; after CUST_LOAD_TIMEOUT_MS with no answer, or when
 * the fetch failed, a Retry that reloads the tab (a new generation). The tab
 * body stays mounted underneath: a control the user is already using is
 * annotated, never discarded.
 */
function LoadStatus({ tab, generation }: { tab: CustTab; generation: number }) {
  const load = customizeLoad.value;
  useEffect(() => {
    const timer = setTimeout(() => markCustomizeTimedOut(generation), CUST_LOAD_TIMEOUT_MS);
    return () => clearTimeout(timer);
  }, [generation]);
  if (load.generation !== generation || load.state === "ready") return null;
  if (load.state === "loading") {
    return (
      <div class="cust-load-status" role="status" aria-live="polite">
        {t("common.loading")}
      </div>
    );
  }
  return (
    <div class="cust-load-status" role="alert">
      {load.state === "timeout" && load.error ? (
        <div class="timeline-error">{load.error}</div>
      ) : null}
      <div class="form-actions">
        <button type="button" class="outline-btn small" onClick={() => custTab(tab)}>
          {t("common.retry")}
        </button>
      </div>
    </div>
  );
}

export function Customize() {
  const open = customizeOpen.value;
  const tab = customizeTab.value;
  const gen = customizeGeneration.value;

  useEffect(() => {
    installCustomizeEscape();
    document.addEventListener("keydown", onCustomizeKeydown);
    const modal = document.getElementById("cust");
    const unfollow = modal ? followCustomizeDom(modal) : () => {};
    return () => {
      document.removeEventListener("keydown", onCustomizeKeydown);
      unfollow();
    };
  }, []);

  return (
    <div
      id="cust"
      class={"modal" + (open ? "" : " hidden")}
      role="dialog"
      aria-modal="true"
      aria-label={t("common.settings")}
      onPointerDown={notePress}
      onClick={(e) => {
        if (backdropClicked(e)) closeCust();
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
          <Content open={open}>
            {open ? <LoadStatus key={`status-${gen}`} tab={tab} generation={gen} /> : null}
            {open ? <ActiveTab key={gen} /> : null}
          </Content>
        </div>
        <NestedEditor />
      </div>
    </div>
  );
}
