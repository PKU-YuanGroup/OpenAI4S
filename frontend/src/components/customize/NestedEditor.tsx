import { useEffect, useState } from "preact/hooks";
import { LANG, t } from "../../i18n";
import { api, apiErrorText } from "../../features/customize/api";
import { custTab } from "../../features/customize/actions";
import { nestedEditor } from "../../features/customize/state";
import { skillReadinessNoteText } from "../../features/customize/environment";
import {
  asList,
  asString,
  confirmAction,
  dropSkillsCatalog,
  hint,
} from "../../features/customize/host";
import { scheduleTimeout } from "../../features/customize/timers";
import { useAlive, useTimerLease } from "./use-timer-lease";
import { Icon } from "./icons";

function skillVersionPath(name: string, scope: string, projectId: string | null): string {
  const encodedName = encodeURIComponent(name);
  if (scope === "project") {
    if (!projectId) throw new Error("project scope is unavailable");
    return `/projects/${encodeURIComponent(projectId)}/skills/${encodedName}`;
  }
  return `/skills/${encodedName}`;
}

export function NestedEditor() {
  const editor = nestedEditor.value;
  if (!editor) return null;
  return (
    <div
      class="cust-nested"
      onClick={(e) => {
        if (e.target === e.currentTarget) nestedEditor.value = null;
      }}
    >
      <div class="cust-nested-box">
        <div class="modal-head">
          <span>{titleFor(editor)}</span>
          <button
            type="button"
            class="icon-ghost"
            onClick={() => {
              nestedEditor.value = null;
            }}
          >
            <Icon name="x" size={16} />
          </button>
        </div>
        {editor.kind === "skill" ? <SkillForm name={editor.name} /> : null}
        {editor.kind === "skill-import" ? <SkillImport /> : null}
        {editor.kind === "skill-history" ? (
          <SkillHistory name={editor.name} scope={editor.scope} projectId={editor.projectId} />
        ) : null}
        {editor.kind === "specialist" ? <SpecialistForm name={editor.name} /> : null}
        {editor.kind === "connector" ? <ConnectorForm k={editor.connector} /> : null}
        {editor.kind === "job" ? <JobOutput id={editor.id} /> : null}
      </div>
    </div>
  );
}

function titleFor(editor: NonNullable<typeof nestedEditor.value>): string {
  if (editor.kind === "skill")
    return editor.name ? t("skill.editTitle", editor.name) : t("skill.newTitle");
  if (editor.kind === "skill-import") return t("skill.importTitle");
  if (editor.kind === "skill-history") return t("skill.historyTitle", editor.name);
  if (editor.kind === "specialist")
    return editor.name ? t("specialist.editTitle", editor.name) : t("specialist.newTitle");
  if (editor.kind === "connector")
    return t("cust.connectors.editTitle", asString(editor.connector.name));
  if (editor.kind === "job") return t("job.outputTitle", editor.id);
  return "";
}

function SkillForm({ name }: { name: string | null }) {
  const alive = useAlive();
  const [nm, setNm] = useState(name || "");
  const [desc, setDesc] = useState("");
  const [body, setBody] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!name) return;
    void (async () => {
      try {
        const cur = await api(`/skills/${encodeURIComponent(name)}`);
        if (!alive()) return;
        setNm(asString(cur.name, name));
        setDesc(asString(cur.description));
        setBody(asString(cur.body));
      } catch {
        /* keep blanks */
      }
    })();
  }, [alive, name]);

  return (
    <div class="skill-form">
      <label class="skill-lbl">{t("cust.connectors.namePlaceholder")}</label>
      <input
        class="cust-input"
        placeholder={t("skill.namePlaceholder")}
        value={nm}
        disabled={!!name}
        onInput={(e) => setNm((e.target as HTMLInputElement).value)}
      />
      <label class="skill-lbl">{t("skill.label.desc")}</label>
      <input
        class="cust-input"
        placeholder={t("skill.descPlaceholder")}
        value={desc}
        onInput={(e) => setDesc((e.target as HTMLInputElement).value)}
      />
      <label class="skill-lbl">{t("skill.label.body")}</label>
      <textarea
        class="skill-body"
        placeholder={t("skill.bodyPlaceholder")}
        value={body}
        onInput={(e) => setBody((e.target as HTMLTextAreaElement).value)}
      />
      <div class="form-actions">
        <button
          type="button"
          class="solid-btn"
          disabled={saving}
          onClick={async () => {
            const next = nm.trim();
            if (!next) {
              hint(t("toast.skill.enterName"), true);
              return;
            }
            setSaving(true);
            try {
              if (name)
                await api(`/skills/${encodeURIComponent(name)}`, {
                  method: "PUT",
                  body: JSON.stringify({ description: desc, body }),
                });
              else
                await api("/skills", {
                  method: "POST",
                  body: JSON.stringify({ name: next, description: desc, body }),
                });
              dropSkillsCatalog();
              nestedEditor.value = null;
              hint(t("toast.skill.saved", next));
              custTab("skills");
            } catch (e) {
              setSaving(false);
              hint(t("artifact.save.err", apiErrorText(e)), true);
            }
          }}
        >
          {saving ? t("common.saving") : t("skill.saveBtn")}
        </button>
      </div>
    </div>
  );
}

export function SkillImport() {
  const [content, setContent] = useState("");
  const [saving, setSaving] = useState(false);
  const [review, setReview] = useState<Record<string, unknown> | null>(null);
  const caps = rec(rec(review && review.capabilities).network);
  const ui = rec(caps.ui);
  const requirements = asList(review && review.requirements).map(String);
  const warnings = asList(review && review.warnings).map(String);
  const networkMode = asString(caps.mode);
  const networkLabel = asString(LANG === "zh" ? ui.label_zh : ui.label_en, networkMode);
  const readinessState = asString(rec(review && review.readiness).state);
  const readinessNote = review ? skillReadinessNoteText(review) : null;
  return (
    <div class="skill-form">
      <label class="skill-lbl">{t("skill.importLabel")}</label>
      <textarea
        class="skill-body"
        placeholder={t("skill.importPlaceholder")}
        style={{ minHeight: "260px" }}
        value={content}
        disabled={!!review}
        onInput={(e) => setContent((e.target as HTMLTextAreaElement).value)}
      />
      {review ? (
        <div class="skill-import-review" data-skill-import-review="1">
          <div class="ds" data-review-requirements={requirements.join(",")}>
            {requirements.join(", ")}
          </div>
          <div class="ds" data-review-capabilities={networkMode}>
            {networkLabel}
          </div>
          <div class="ds" data-review-readiness={readinessState}>
            {readinessNote || readinessState}
          </div>
          {warnings.map((warning) => (
            <div class="ds prof-warn" key={warning}>
              {warning}
            </div>
          ))}
        </div>
      ) : null}
      <div class="form-actions">
        {review ? (
          <button
            type="button"
            class="solid-btn"
            onClick={() => {
              dropSkillsCatalog();
              nestedEditor.value = null;
              custTab("skills");
            }}
          >
            {t("common.close")}
          </button>
        ) : (
          <button
            type="button"
            class="solid-btn"
            disabled={saving}
            onClick={async () => {
              if (!content.trim()) return;
              setSaving(true);
              try {
                const r = await api("/skills/import", {
                  method: "POST",
                  body: JSON.stringify({ content }),
                });
                setReview(rec(r.review));
                setSaving(false);
                hint(t("toast.skill.imported", asString(r.name)));
              } catch (e) {
                setSaving(false);
                hint(t("toast.importFailed", apiErrorText(e)), true);
              }
            }}
          >
            {saving ? t("cust.importing") : t("skill.importBtn")}
          </button>
        )}
      </div>
    </div>
  );
}

function SkillHistory({
  name,
  scope,
  projectId,
}: {
  name: string;
  scope: string;
  projectId: string | null;
}) {
  const alive = useAlive();
  const [err, setErr] = useState<string | null>(null);
  const [versions, setVersions] = useState<Record<string, unknown>[]>([]);
  const [readOnly, setReadOnly] = useState(false);

  const load = async () => {
    try {
      const data = await api(skillVersionPath(name, scope, projectId) + "/versions?limit=100");
      if (!alive()) return;
      setVersions(asList(data.versions) as Record<string, unknown>[]);
      setReadOnly(!!(data.status && rec(data.status).read_only));
    } catch (e) {
      if (!alive()) return;
      setErr((e as Error).message);
    }
  };

  useEffect(() => {
    void load();
  }, [alive, name, scope, projectId]);

  return (
    <div>
      <div class="subtle">{t(`skill.scope.${scope}`)}</div>
      {err ? <div class="empty">{err}</div> : null}
      {!err && !versions.length ? <div class="empty">{t("skill.historyEmpty")}</div> : null}
      <div class="skill-version-list">
        {versions.map((version) => {
          const versionId = asString(version.version_id);
          const manifest = rec(version.manifest);
          const sidecar = rec(manifest.sidecar);
          const sidecarText = sidecar.present
            ? asString(sidecar.sha256).slice(0, 12)
            : "—";
          const when = Number(version.created_at || 0);
          return (
            <div class="skill-version-card" key={versionId}>
              <div class="skill-version-head">
                <div class="info">
                  <div class="nm" title={versionId}>
                    {versionId.slice(0, 20) + (versionId.length > 20 ? "…" : "")}
                  </div>
                  <div class="ds">{when ? new Date(when).toLocaleString() : ""}</div>
                  <div class="ds">{t("skill.versionSidecar", sidecarText)}</div>
                </div>
                {version.active ? (
                  <span class="pill">{t("skill.versionActive")}</span>
                ) : !readOnly ? (
                  <button
                    type="button"
                    class="outline-btn small"
                    onClick={async () => {
                      if (
                        !confirmAction(
                          t("skill.rollbackConfirm", name, versionId.slice(0, 18)),
                        )
                      )
                        return;
                      try {
                        await api(skillVersionPath(name, scope, projectId) + "/rollback", {
                          method: "POST",
                          body: JSON.stringify({ version_id: versionId }),
                        });
                        hint(t("skill.rollbackDone", name));
                        await load();
                        custTab("skills");
                      } catch (e) {
                        hint(t("toast.failed", apiErrorText(e)), true);
                      }
                    }}
                  >
                    {t("skill.rollbackBtn")}
                  </button>
                ) : null}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function rec(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function SpecialistForm({ name }: { name: string | null }) {
  const alive = useAlive();
  const [nm, setNm] = useState(name || "");
  const [desc, setDesc] = useState("");
  const [prompt, setPrompt] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!name) return;
    void (async () => {
      try {
        const cur = await api(`/specialists/${encodeURIComponent(name)}`);
        if (!alive()) return;
        setNm(asString(cur.name, name));
        setDesc(asString(cur.description));
        setPrompt(asString(cur.system_prompt));
      } catch {
        /* keep blanks */
      }
    })();
  }, [alive, name]);

  return (
    <div class="skill-form">
      <label class="skill-lbl">{t("cust.connectors.namePlaceholder")}</label>
      <input
        class="cust-input"
        placeholder={t("specialist.namePlaceholder")}
        value={nm}
        disabled={!!name}
        onInput={(e) => setNm((e.target as HTMLInputElement).value)}
      />
      <label class="skill-lbl">{t("skill.label.desc")}</label>
      <input
        class="cust-input"
        placeholder={t("specialist.descPlaceholder")}
        value={desc}
        onInput={(e) => setDesc((e.target as HTMLInputElement).value)}
      />
      <label class="skill-lbl">{t("specialist.label.systemPrompt")}</label>
      <textarea
        class="skill-body"
        placeholder={t("specialist.promptPlaceholder")}
        value={prompt}
        onInput={(e) => setPrompt((e.target as HTMLTextAreaElement).value)}
      />
      <div class="form-actions">
        <button
          type="button"
          class="solid-btn"
          disabled={saving}
          onClick={async () => {
            const next = nm.trim();
            if (!next) {
              hint(t("toast.specialist.enterName"), true);
              return;
            }
            setSaving(true);
            const b = { name: next, description: desc, system_prompt: prompt };
            try {
              if (name)
                await api(`/specialists/${encodeURIComponent(name)}`, {
                  method: "PUT",
                  body: JSON.stringify(b),
                });
              else await api("/specialists", { method: "POST", body: JSON.stringify(b) });
              nestedEditor.value = null;
              hint(t("toast.specialist.saved", next));
              custTab("specialists");
            } catch (e) {
              setSaving(false);
              hint(t("artifact.save.err", apiErrorText(e)), true);
            }
          }}
        >
          {saving ? t("common.saving") : t("specialist.saveBtn")}
        </button>
      </div>
    </div>
  );
}

function ConnectorForm({ k }: { k: Record<string, unknown> }) {
  const [name, setName] = useState(asString(k.name));
  const [desc, setDesc] = useState(asString(k.description));
  const [cmd, setCmd] = useState(JSON.stringify(k.command || [], null, 2));
  const [args, setArgs] = useState(JSON.stringify(k.args || [], null, 2));
  const [envIn, setEnvIn] = useState("");
  const [removeIn, setRemoveIn] = useState("");
  const [saving, setSaving] = useState(false);
  const keys = Array.isArray(k.env_keys) ? (k.env_keys as string[]) : [];
  return (
    <div class="skill-form">
      <label class="skill-lbl">{t("cust.connectors.namePlaceholder")}</label>
      <input
        class="cust-input"
        value={name}
        onInput={(e) => setName((e.target as HTMLInputElement).value)}
      />
      <label class="skill-lbl">{t("skill.label.desc")}</label>
      <textarea
        class="connector-edit-area"
        rows={3}
        value={desc}
        onInput={(e) => setDesc((e.target as HTMLTextAreaElement).value)}
      />
      <label class="skill-lbl">{t("cust.connectors.commandLabel")}</label>
      <textarea
        class="connector-edit-area connector-json"
        rows={3}
        spellcheck={false}
        value={cmd}
        onInput={(e) => setCmd((e.target as HTMLTextAreaElement).value)}
      />
      <label class="skill-lbl">{t("cust.connectors.argsLabel")}</label>
      <textarea
        class="connector-edit-area connector-json"
        rows={2}
        spellcheck={false}
        value={args}
        onInput={(e) => setArgs((e.target as HTMLTextAreaElement).value)}
      />
      <div class="connector-env-state">
        {keys.length
          ? t("cust.connectors.envConfigured", keys.join(", "))
          : t("cust.connectors.envNone")}
      </div>
      <label class="skill-lbl">{t("cust.connectors.envUpdatesLabel")}</label>
      <textarea
        class="connector-edit-area connector-json"
        rows={4}
        spellcheck={false}
        placeholder={t("cust.connectors.envUpdatesPlaceholder")}
        value={envIn}
        onInput={(e) => setEnvIn((e.target as HTMLTextAreaElement).value)}
      />
      <label class="skill-lbl">{t("cust.connectors.envRemoveLabel")}</label>
      <textarea
        class="connector-edit-area connector-json"
        rows={3}
        spellcheck={false}
        placeholder={t("cust.connectors.envRemovePlaceholder")}
        value={removeIn}
        onInput={(e) => setRemoveIn((e.target as HTMLTextAreaElement).value)}
      />
      <div class="form-actions">
        <button
          type="button"
          class="solid-btn"
          disabled={saving}
          onClick={async () => {
            let command: unknown;
            let parsedArgs: unknown;
            try {
              command = JSON.parse(cmd);
              parsedArgs = JSON.parse(args || "[]");
            } catch {
              hint(t("cust.connectors.invalidJson"), true);
              return;
            }
            if (
              (!Array.isArray(command) && typeof command !== "string") ||
              !(command as { length: number }).length ||
              !Array.isArray(parsedArgs)
            ) {
              hint(t("cust.connectors.invalidJson"), true);
              return;
            }
            const envUpdates: Record<string, string> = {};
            for (const raw of envIn.split(/\r?\n/)) {
              if (!raw.trim()) continue;
              const at = raw.indexOf("=");
              if (at <= 0) {
                hint(t("cust.connectors.invalidEnv"), true);
                return;
              }
              envUpdates[raw.slice(0, at).trim()] = raw.slice(at + 1);
            }
            const removeEnv = removeIn
              .split(/\r?\n/)
              .map((v) => v.trim())
              .filter(Boolean);
            if (removeEnv.some((n) => Object.prototype.hasOwnProperty.call(envUpdates, n))) {
              hint(t("cust.connectors.invalidEnv"), true);
              return;
            }
            setSaving(true);
            try {
              await api(`/connectors/${encodeURIComponent(asString(k.connector_id))}`, {
                method: "PUT",
                body: JSON.stringify({
                  name: name.trim(),
                  description: desc,
                  command,
                  args: parsedArgs,
                  env_updates: envUpdates,
                  remove_env: removeEnv,
                }),
              });
              nestedEditor.value = null;
              hint(t("cust.connectors.saved", name.trim()));
              custTab("connectors");
            } catch (e) {
              setSaving(false);
              hint(t("artifact.save.err", apiErrorText(e)), true);
            }
          }}
        >
          {saving ? t("common.saving") : t("common.save")}
        </button>
      </div>
    </div>
  );
}

function JobOutput({ id }: { id: string }) {
  const alive = useAlive();
  const lease = useTimerLease();
  const [text, setText] = useState(t("common.loading"));

  useEffect(() => {
    const load = async () => {
      if (!alive() || nestedEditor.value?.kind !== "job" || nestedEditor.value.id !== id)
        return;
      try {
        const d = await api(`/compute/jobs/${id}`);
        if (!alive()) return;
        setText(asString(d.output) || t("job.outputEmpty"));
        if (d.status === "running" || d.status === "queued") {
          scheduleTimeout(lease, () => {
            void load();
          }, 1200);
        }
      } catch {
        if (alive()) setText(t("job.outputLoadFailed"));
      }
    };
    void load();
  }, [alive, lease, id]);

  return <pre class="job-output">{text}</pre>;
}
