import { useEffect, useState } from "preact/hooks";
import { t } from "../../i18n";
import { api, apiErrorText } from "../../features/customize/api";
import { closeCust, custTab } from "../../features/customize/actions";
import { nestedEditor } from "../../features/customize/state";
import { skillReadinessNoteText } from "../../features/customize/environment";
import {
  asList,
  asString,
  confirmAction,
  dropSkillsCatalog,
  effProject,
  hint,
  insertSkillMention,
} from "../../features/customize/host";
import { useAlive } from "./use-timer-lease";
import { markCustomizeFailed, markCustomizeLoaded } from "../../features/customize/load";
import { Empty, Hdr, IconGhost, Pill, Toggle } from "./ui";

type Skill = Record<string, unknown>;

function skillScope(s: Skill): "project" | "bundled" | "personal" {
  if (s.scope === "project") return "project";
  if (s.scope === "bundled") return "bundled";
  return "personal";
}

function SkillRow({ s, pid }: { s: Skill; pid: string | null }) {
  const scope = skillScope(s);
  const name = asString(s.displayName || s.name);
  const note = skillReadinessNoteText(s);
  const [enabled, setEnabled] = useState(s.enabled !== false);
  return (
    <div class="cust-row">
      <div class="info">
        <div class="nm">
          <span>{name}</span> <Pill>{t(`skill.scope.${scope}`)}</Pill>
        </div>
        <div class="ds">{asString(s.description)}</div>
        {note ? <div class="ds prof-warn">{note}</div> : null}
      </div>
      <IconGhost
        name="message-square"
        title={t("skill.useInChat")}
        onClick={() => {
          closeCust();
          insertSkillMention(asString(s.name));
        }}
      />
      {s.versioned ? (
        <IconGhost
          name="clock"
          title={t("skill.historyBtn")}
          onClick={() => {
            nestedEditor.value = {
              kind: "skill-history",
              name: asString(s.name),
              scope,
              projectId: scope === "project" ? pid : null,
            };
          }}
        />
      ) : null}
      {s.editable && scope === "personal" ? (
        <>
          <IconGhost
            name="pencil"
            title={t("common.edit")}
            onClick={() => {
              nestedEditor.value = { kind: "skill", name: asString(s.name) };
            }}
          />
          <IconGhost
            name="trash-2"
            title={t("common.delete")}
            onClick={async () => {
              if (!confirmAction(t("cust.skills.deleteConfirm", asString(s.name)))) return;
              try {
                await api(`/skills/${encodeURIComponent(asString(s.name))}`, {
                  method: "DELETE",
                });
                dropSkillsCatalog();
                custTab("skills");
              } catch (e) {
                hint(t("toast.deleteFailed", apiErrorText(e)), true);
              }
            }}
          />
        </>
      ) : null}
      {scope !== "project" ? (
        <Toggle
          on={enabled}
          onClick={async () => {
            const on = !enabled;
            setEnabled(on);
            try {
              await api(`/skills/catalog/${encodeURIComponent(asString(s.name))}/enabled`, {
                method: "PUT",
                body: JSON.stringify({ enabled: on }),
              });
            } catch {
              setEnabled(!on);
            }
          }}
        />
      ) : null}
    </div>
  );
}

export function SkillsTab() {
  const alive = useAlive();
  const [err, setErr] = useState<string | null>(null);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [openCollections, setOpenCollections] = useState<Record<string, boolean>>({});
  const pid = effProject();

  useEffect(() => {
    void (async () => {
      try {
        const personalRequest = api("/skills/catalog");
        const projectRequest = pid
          ? api(`/projects/${encodeURIComponent(pid)}/skills/catalog`).catch(() => ({
              skills: [],
            }))
          : Promise.resolve({ skills: [] });
        const [personalData, projectData] = await Promise.all([
          personalRequest,
          projectRequest,
        ]);
        if (!alive()) return;
        const personalSkills = asList(personalData.skills) as Skill[];
        const projectSkills = asList(projectData.skills) as Skill[];
        setSkills([...personalSkills, ...projectSkills]);
        markCustomizeLoaded();
      } catch (e) {
        if (!alive()) return;
        const message = t("versions.load.err", (e as Error).message);
        setErr(message);
        markCustomizeFailed(message);
      }
    })();
  }, [alive, pid]);

  if (err) return <div>{err}</div>;

  const singles: Skill[] = [];
  const collections = new Map<string, Skill[]>();
  skills.forEach((s) => {
    const cid = asString(s.collection);
    if (!cid) {
      singles.push(s);
      return;
    }
    const members = collections.get(cid) || [];
    members.push(s);
    collections.set(cid, members);
  });

  return (
    <div>
      <Hdr title={t("palette.group.skills")} sub={t("cust.skills.desc", skills.length)} />
      <div class="cust-row">
        <div class="info">
          <div class="nm">{t("cust.skills.yourSkills")}</div>
          <div class="cust-actrow">
            <button
              type="button"
              class="outline-btn small"
              onClick={() => {
                nestedEditor.value = { kind: "skill", name: null };
              }}
            >
              {t("cust.skills.newBtn")}
            </button>
            <button
              type="button"
              class="outline-btn small"
              onClick={() => {
                nestedEditor.value = { kind: "skill-import" };
              }}
            >
              {t("cust.skills.importBtn")}
            </button>
          </div>
        </div>
      </div>
      {singles.map((s) => (
        <SkillRow key={asString(s.name) + asString(s.scope)} s={s} pid={pid} />
      ))}
      {[...collections.keys()].sort().map((cid) => {
        const members = collections.get(cid) || [];
        const open = !!openCollections[cid];
        return (
          <div key={cid}>
            <div class="cust-row">
              <div class="info">
                <div class="nm">{t("cust.skills.collection", cid, members.length)}</div>
                <div class="ds">{t("cust.skills.collectionDesc")}</div>
              </div>
              <button
                type="button"
                class="outline-btn small"
                onClick={() =>
                  setOpenCollections((prev) => ({ ...prev, [cid]: !prev[cid] }))
                }
              >
                {t(open ? "cust.skills.collectionHide" : "cust.skills.collectionShow")}
              </button>
            </div>
            {open
              ? members.map((s) => (
                  <SkillRow key={asString(s.name) + cid} s={s} pid={pid} />
                ))
              : null}
          </div>
        );
      })}
      {!skills.length ? <Empty>{t("cust.skills.desc", 0)}</Empty> : null}
    </div>
  );
}
