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
  markCustomizeLoaded,
  markCustomizeTimedOut,
} from "../../features/customize/load";
import { settingsReadOnly } from "../../features/customize/roles";
import {
  customizeGeneration,
  customizeLoad,
  customizeNotice,
  customizeOpen,
  customizeTab,
} from "../../features/customize/state";
import {
  CUST_TABS,
  CUST_TAB_I18N,
  OPERATOR_TABS,
  custTabLabel,
  type CustTab,
} from "../../features/customize/tabs";
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
import { ExperimentsTab } from "./ExperimentsTab";
import { DiagnosticsTab } from "./DiagnosticsTab";
import { UpdateCheck } from "./UpdateCheck";
import "./customize.css";

/**
 * A page whose sections report their own state: the pane's first-load
 * annotation settles on mount. Experiments and Diagnostics were sections of
 * General, which settled it for them.
 */
function Settled({ children }: { children: ComponentChildren }) {
  useEffect(() => {
    markCustomizeLoaded();
  }, []);
  return <>{children}</>;
}

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
    case "experiments":
      return (
        <Settled>
          <ExperimentsTab />
        </Settled>
      );
    case "diagnostics":
      return (
        <Settled>
          <DiagnosticsTab />
          <UpdateCheck />
        </Settled>
      );
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

/**
 * What the last action in Settings reported (`hint()` → `customizeNotice`).
 * `hint()` alone writes under the composer, behind this modal, so a refused
 * save looked like nothing happened (TEAM-23).
 */
function Notice() {
  const notice = customizeNotice.value;
  if (!notice) return null;
  return (
    <div
      class={"cust-notice" + (notice.error ? " err" : "")}
      role={notice.error ? "alert" : "status"}
      data-cust-notice=""
    >
      <span>{notice.text}</span>
      <button
        type="button"
        class="icon-ghost"
        aria-label={t("common.close")}
        title={t("common.close")}
        onClick={() => {
          customizeNotice.value = null;
        }}
      >
        <Icon name="x" size={14} />
      </button>
    </div>
  );
}

export function Customize() {
  const open = customizeOpen.value;
  const tab = customizeTab.value;
  const gen = customizeGeneration.value;
  // A team member does not get the operator's pages (both are 403 for them).
  const member = settingsReadOnly();
  const tabs = CUST_TABS.filter((id) => !member || !OPERATOR_TABS.has(id));

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
            {tabs.map((id) => (
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
                {custTabLabel(id)}
              </button>
            ))}
          </nav>
          <Content open={open}>
            {open ? <LoadStatus key={`status-${gen}`} tab={tab} generation={gen} /> : null}
            {open ? <ActiveTab key={gen} /> : null}
          </Content>
        </div>
        {open ? <Notice /> : null}
        <NestedEditor />
      </div>
    </div>
  );
}
