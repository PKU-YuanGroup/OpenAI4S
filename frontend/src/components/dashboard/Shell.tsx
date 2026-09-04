import { useEffect } from "preact/hooks";
import { finishArtifactsBoot } from "../../features/artifacts/boot";
import { bindWorkbench } from "../../features/sessions/boot";
import "./dashboard.css";

export function Shell() {
  useEffect(() => {
    void finishArtifactsBoot(bindWorkbench());
  }, []);

  return (
    <>
      <div id="conn-banner" role="status" aria-live="polite" hidden />
      <a class="skip-link" href="#messages">
        Skip to content
      </a>
      <div id="dashboard" class="dashboard">
        <header class="dash-head">
          <div class="dash-brand">
            <div class="wordmark">
              <span class="wm-title">OpenAI4S</span>
              <span class="wm-beta">Beta</span>
            </div>
            <span id="dash-running-count" class="running-count hidden" />
          </div>
          <div class="dash-actions">
            <div class="lang-seg" role="group" aria-label="Language / 语言">
              <button class="lang-btn" data-lang="zh" type="button">
                中文
              </button>
              <button class="lang-btn" data-lang="en" type="button">
                EN
              </button>
            </div>
            <button
              id="dash-theme"
              class="icon-ghost lg"
              data-i18n-title="theme.toggle"
              title="主题"
              data-icon="moon"
              data-icon-size="20"
              aria-label="Theme"
              type="button"
            />
            <button
              id="dash-settings"
              class="icon-ghost lg"
              data-i18n-title="common.settings"
              title="设置"
              data-icon="settings"
              data-icon-size="20"
              type="button"
            />
            <button id="dash-import-session" class="outline-btn" type="button">
              <span class="ic" data-icon="cloud-upload" data-icon-size="16" />
              <span data-i18n="sessionPackage.import">Import session</span>
            </button>
            <input
              id="session-package-input"
              type="file"
              accept=".zip,.openai4s-session.zip,application/vnd.openai4s.session+zip"
              style="display:none"
            />
            <button id="dash-new-project" class="outline-btn" type="button">
              <span class="ic" data-icon="plus" data-icon-size="16" />
              <span data-i18n="palette.action.newProject">New project</span>
            </button>
          </div>
        </header>
        <section id="dash-running" class="dash-running hidden" />
        <div class="dash-grid">
          <section class="dash-col">
            <div class="col-title">
              <span class="ci" data-icon="box" data-icon-size="18" />{" "}
              <span data-i18n="dash.col.projects">Projects</span>
            </div>
            <input
              id="dash-project-search"
              type="search"
              data-i18n-ph="palette.action.search"
              placeholder="Search"
              autocomplete="off"
              spellcheck={false}
              aria-controls="dash-projects"
              style="width:100%;margin:0 0 8px;box-sizing:border-box"
            />
            <div id="dash-projects" class="dash-card" />
          </section>
          <section class="dash-col sessions">
            <div class="col-title">
              <span class="ci" data-icon="clock" data-icon-size="18" />{" "}
              <span data-i18n="dash.col.recentSessions">Recent sessions</span>
            </div>
            <div id="dash-sessions" class="dash-card" />
          </section>
        </div>
      </div>

      <div id="workspace" class="workspace hidden">
        <aside id="sidebar">
          <div class="side-top">
            <button
              id="back-home"
              class="icon-ghost"
              data-i18n-title="palette.action.backHome"
              title="返回主页"
              data-icon="arrow-left"
              data-icon-size="20"
              type="button"
            />
            <button id="proj-btn" class="proj-name" type="button">
              <span id="proj-current" data-i18n="proj.fallbackName">
                项目
              </span>
              <span class="chev" data-icon="chevron-down" data-icon-size="16" />
            </button>
            <button
              id="sidebar-collapse"
              class="icon-ghost"
              data-i18n-title="ws.sidebar.collapse"
              title="收起侧栏"
              data-icon="panel-left"
              data-icon-size="20"
              type="button"
            />
          </div>
          <div id="proj-menu" class="proj-menu hidden" role="menu" />
          <nav class="side-nav">
            <button id="search-btn" class="side-nav-item" type="button">
              <span class="ic" data-icon="search" data-icon-size="16" />
              <span data-i18n="palette.action.search">Search</span>
            </button>
            <button id="new-session" class="side-nav-item" type="button">
              <span class="ic" data-icon="plus" data-icon-size="16" />
              <span data-i18n="ws.nav.new">New</span>
            </button>
            <button id="customize-btn" class="side-nav-item" type="button">
              <span class="ic" data-icon="sliders" data-icon-size="16" />
              <span data-i18n="palette.action.customize">Customize</span>
            </button>
            <button id="files-btn" class="side-nav-item" type="button">
              <span class="ic" data-icon="files" data-icon-size="16" />
              <span data-i18n="ws.nav.files">Files</span>
            </button>
          </nav>
          <div id="session-list" class="session-list" />
          <div class="side-foot">
            <button
              id="settings-gear"
              class="icon-ghost"
              data-i18n-title="common.settings"
              title="设置"
              data-icon="settings"
              data-icon-size="20"
              type="button"
            />
            <button
              id="ws-theme"
              class="icon-ghost"
              data-i18n-title="theme.toggle"
              title="主题"
              data-icon="moon"
              data-icon-size="20"
              aria-label="Theme"
              type="button"
            />
            <div class="lang-seg" role="group" aria-label="Language / 语言">
              <button class="lang-btn" data-lang="zh" type="button">
                中文
              </button>
              <button class="lang-btn" data-lang="en" type="button">
                EN
              </button>
            </div>
          </div>
        </aside>

        <main id="main">
          <div id="conv-view">
            <div class="conv-head">
              <button
                id="sidebar-reopen"
                class="icon-ghost hidden"
                data-i18n-title="ws.sidebar.expand"
                title="展开侧栏"
                data-icon="panel-left"
                data-icon-size="20"
                type="button"
              />
              <div class="tabbar" id="tabbar">
                <div class="tab active" id="tab-session">
                  <input
                    id="conv-title"
                    class="conv-title"
                    size={24}
                    value="会话"
                    data-i18n-val="conv.title.default"
                    spellcheck={false}
                    data-i18n-title="conv.title.rename"
                    title="重命名会话（回车保存）"
                  />
                  <button
                    class="t-close"
                    id="tab-close"
                    type="button"
                    data-i18n-title="common.close"
                    title="关闭"
                    data-icon="x"
                    data-icon-size="14"
                    aria-label="Close"
                  />
                </div>
                <button
                  class="tab"
                  id="tab-new"
                  type="button"
                  data-i18n-title="palette.action.newSession"
                  title="新建会话"
                >
                  <span class="t-name" data-i18n="palette.action.newSession">
                    New session
                  </span>
                </button>
              </div>
              <div class="conv-head-actions">
                <button
                  id="session-menu-btn"
                  class="icon-ghost"
                  data-i18n-title="session.menu.tip"
                  title="会话操作"
                  data-icon="more-horizontal"
                  data-icon-size="16"
                  type="button"
                />
                <button
                  id="dock-toggle"
                  class="icon-ghost"
                  data-i18n-title="conv.dockToggle"
                  title="侧栏面板"
                  data-icon="panel-right"
                  data-icon-size="16"
                  type="button"
                />
              </div>
            </div>
            <div id="messages" class="messages" />
            <div class="composer-wrap">
              <button
                id="jump-pill"
                class="jump-pill hidden"
                data-i18n-title="conv.jumpLast"
                title="跳到最后一条"
                type="button"
              >
                <span class="ic" data-icon="chevron-down" data-icon-size="16" />
                <span class="jp-lbl" data-i18n="conv.jumpLastLabel">
                  最新
                </span>
              </button>
              <div class="notebook">
                <div class="nb-tray">
                  <div class="nb-head">
                    <span class="ic" data-icon="notebook" data-icon-size="16" />{" "}
                    <span data-i18n="dock.tab.notebook">Notebook</span>
                  </div>
                </div>
                <div class="nb-card">
                  <textarea
                    id="composer"
                    rows={1}
                    data-i18n-ph="composer.placeholder"
                    placeholder="Ask anything — @ for artifacts, # for sessions, / for skills, ⌘K to search…"
                  />
                  <div class="nb-foot">
                    <div class="nb-left">
                      <button
                        id="attach-btn"
                        class="nb-icon"
                        data-i18n-title="composer.addToMessage"
                        title="添加到消息"
                        data-icon="plus"
                        data-icon-size="20"
                        type="button"
                      />
                      <input type="file" id="file-input" style="display:none" multiple />
                      <button
                        id="session-options-btn"
                        class="nb-icon"
                        data-i18n-title="composer.sessionOptions"
                        title="会话选项"
                        data-icon="sliders"
                        data-icon-size="18"
                        type="button"
                      />
                      <button
                        id="plan-toggle"
                        class="nb-icon hidden"
                        data-i18n-title="composer.planMode"
                        title="计划模式"
                        data-icon="grid"
                        data-icon-size="20"
                        type="button"
                      />
                      <button
                        id="explore-toggle"
                        class="nb-icon hidden"
                        data-i18n-title="composer.exploreMode"
                        title="自主探索"
                        data-icon="compass"
                        data-icon-size="20"
                        type="button"
                      />
                    </div>
                    <div class="nb-right">
                      <button
                        id="cancel-btn"
                        class="nb-icon hidden"
                        data-i18n-title="nb.kernel.stopLabel"
                        title="停止"
                        data-icon="stop"
                        data-icon-size="18"
                        type="button"
                      />
                      <div class="nb-model">
                        <select id="model-select" data-i18n-title="composer.model" title="模型" />
                        <span class="ic" data-icon="chevron-down" data-icon-size="14" />
                      </div>
                    </div>
                  </div>
                </div>
              </div>
              <div id="composer-refs" class="composer-refs hidden" />
              <div
                class="composer-hint"
                id="composer-hint"
                role="status"
                aria-live="polite"
              />
            </div>
          </div>
        </main>

        <aside id="rightdock" class="rightdock collapsed">
          <div class="dock-head">
            <div class="dock-tabs" id="dock-tabs" />
            <div class="dock-head-actions">
              <button
                id="dock-collapse"
                class="icon-ghost"
                data-i18n-title="dock.collapse"
                title="收起"
                data-icon="x"
                data-icon-size="16"
                type="button"
              />
            </div>
          </div>
          <div class="dock-body">
            <div id="dock-viewer" class="dock-pane" />
            <div id="dock-notebook" class="dock-pane hidden" />
            <div id="dock-timeline" class="dock-pane hidden" aria-live="polite" />
            <div id="dock-files" class="dock-pane hidden">
              <div class="files-head">
                <span data-i18n="dock.files.heading">Files · Artifacts</span>
                <span id="results-count" class="count">
                  0
                </span>
              </div>
              <div id="results-list" class="files-grid" />
            </div>
          </div>
        </aside>
      </div>

      <div id="modal" class="modal hidden" role="dialog" aria-modal="true" aria-labelledby="modal-title">
        <div class="modal-box">
          <div class="modal-head">
            <span id="modal-title" data-i18n="modal.title.preview">
              预览
            </span>
            <div class="modal-actions">
              <a id="modal-download" class="outline-btn small" download>
                <span class="ic" data-icon="download" data-icon-size="14" />
                <span data-i18n="common.download">下载</span>
              </a>
              <button id="modal-close" class="icon-ghost" data-icon="x" data-icon-size="16" aria-label="Close" type="button" />
            </div>
          </div>
          <div id="modal-body" class="modal-body" />
        </div>
      </div>

      <div
        id="proj-modal"
        class="modal hidden"
        role="dialog"
        aria-modal="true"
        aria-labelledby="proj-modal-title"
      >
        <div class="modal-box small-box">
          <div class="modal-head">
            <span id="proj-modal-title" data-i18n="projModal.title">
              New Project
            </span>
            <button
              id="proj-modal-close"
              class="icon-ghost"
              data-icon="x"
              data-icon-size="16"
              aria-label="Close"
              type="button"
            />
          </div>
          <div class="form">
            <label data-i18n="skill.label.name">Name</label>
            <input
              id="pm-name"
              type="text"
              data-i18n-ph="projModal.name.placeholder"
              placeholder="Project name"
            />
            <label data-i18n="skill.label.desc">Description</label>
            <textarea
              id="pm-desc"
              rows={2}
              data-i18n-ph="projModal.desc.placeholder"
              placeholder="Shown in the project list"
            />
            <label data-i18n="projModal.ctx.label">Agent Context</label>
            <textarea
              id="pm-ctx"
              rows={3}
              data-i18n-ph="projModal.ctx.placeholder"
              placeholder="Included in every agent's prompt for this project"
            />
            <div class="form-actions">
              <button id="pm-delete" class="outline-btn danger hidden" data-i18n="common.delete" type="button">
                Delete
              </button>
              <span class="form-actions-spacer" />
              <button id="pm-cancel" class="outline-btn" data-i18n="common.cancel" type="button">
                Cancel
              </button>
              <button id="pm-create" class="solid-btn" data-i18n="projModal.create" type="button">
                Create
              </button>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
