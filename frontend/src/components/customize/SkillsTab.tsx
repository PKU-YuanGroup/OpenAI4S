import { useState } from "preact/hooks";
import { t } from "../../i18n";
import { copyLookup } from "../../i18n/copy";
import { rankSkillItems, skillToAcItem, type AcItem } from "../../features/autocomplete/rank";
import { api, apiErrorText } from "../../features/customize/api";
import { closeCust, refreshCustTab } from "../../features/customize/actions";
import { nestedEditor } from "../../features/customize/state";
import { skillReadinessNoteText } from "../../features/customize/environment";
import {
  asList,
  asString,
  confirmAction,
  customizeProject,
  dropSkillsCatalog,
  insertSkillMention,
} from "../../features/customize/host";
import { useOptimisticToggle, useTabRead } from "./hooks";
import { ActionStatus, Empty, Hdr, IconGhost, Pill, Toggle, type ActionNote } from "./ui";

type Skill = Record<string, unknown>;

/** Search and network-limit strings the generated dictionaries do not carry. */
const st = copyLookup({
  zh: {
    "skills.search": "搜索技能：名称、说明里的词或中文关键词",
    "skills.searchCount": "{0} 个匹配",
    "skills.searchNone": "没有匹配「{0}」的技能",
    "skills.net": "限定联网 · {0} 个域名",
    "skills.netTip": "加载这个技能后，本会话的联网请求只能访问它声明的这些域名：",
  },
  en: {
    "skills.search": "Search skills: name, a word from the description, or a Chinese keyword",
    "skills.searchCount": "{0} matches",
    "skills.searchNone": "No skill matches “{0}”",
    "skills.net": "Limits network · {0} domains",
    "skills.netTip": "Loading this skill limits the session's network requests to the domains it declares:",
  },
});

/** The domains a `host_only` skill narrows a session to, or null. */
export function skillNetworkDomains(s: Skill): string[] | null {
  const caps = s.capabilities && typeof s.capabilities === "object" ? (s.capabilities as Skill) : {};
  const network = caps.network && typeof caps.network === "object" ? (caps.network as Skill) : {};
  if (network.mode !== "host_only") return null;
  return asList(network.domains).map((domain) => String(domain));
}

function skillScope(s: Skill): "project" | "bundled" | "personal" {
  if (s.scope === "project") return "project";
  if (s.scope === "bundled") return "bundled";
  return "personal";
}

/**
 * The catalog rows matching `query`, best first -- the `/` picker's ranking,
 * so a skill found one way is found the other.
 */
export function searchSkills(skills: Skill[], query: string): Skill[] {
  const byItem = new Map<AcItem, Skill>();
  const items = skills.map((s) => {
    const domains = skillNetworkDomains(s);
    const item = skillToAcItem({
      name: asString(s.name),
      displayName: asString(s.displayName || s.name),
      // The domains are searchable too: "pubmed" finds the skill that reaches it.
      description: asString(s.description) + (domains ? " " + domains.join(" ") : ""),
      collection: s.collection,
    });
    byItem.set(item, s);
    return item;
  });
  return rankSkillItems(items, query).map((item) => byItem.get(item) as Skill);
}

export function SkillRow({ s, pid }: { s: Skill; pid: string | null }) {
  const scope = skillScope(s);
  const name = asString(s.displayName || s.name);
  const note = skillReadinessNoteText(s);
  const domains = skillNetworkDomains(s);
  const enabled = useOptimisticToggle(s.enabled !== false, (on) =>
    api(`/skills/catalog/${encodeURIComponent(asString(s.name))}/enabled`, {
      method: "PUT",
      body: JSON.stringify({ enabled: on }),
    }),
  );
  // SCI-04: a failed delete is said in the row, not behind the dialog.
  const [actionNote, setActionNote] = useState<ActionNote>(null);
  return (
    <div class="cust-row">
      <div class="info">
        <div class="nm">
          <span>{name}</span> <Pill>{t(`skill.scope.${scope}`)}</Pill>
        </div>
        <div class="ds">{asString(s.description)}</div>
        {note ? <div class="ds prof-warn">{note}</div> : null}
        {domains ? (
          <details class="skill-net">
            <summary>{st("skills.net", domains.length)}</summary>
            <div class="ds">
              {st("skills.netTip")} {domains.join(", ")}
            </div>
          </details>
        ) : null}
        <ActionStatus note={actionNote} />
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
                refreshCustTab("skills");
              } catch (e) {
                setActionNote({ text: t("toast.deleteFailed", apiErrorText(e)), err: true });
              }
            }}
          />
        </>
      ) : null}
      {scope !== "project" ? (
        <Toggle on={enabled.on} onClick={enabled.toggle} />
      ) : null}
    </div>
  );
}

export function SkillsTab() {
  const [err, setErr] = useState<string | null>(null);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [openCollections, setOpenCollections] = useState<Record<string, boolean>>({});
  const [query, setQuery] = useState("");
  const pid = customizeProject.value;

  useTabRead(
    "skills",
    async (current) => {
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
      if (!current()) return;
      const personalSkills = asList(personalData.skills) as Skill[];
      const projectSkills = asList(projectData.skills) as Skill[];
      setSkills([...personalSkills, ...projectSkills]);
    },
    setErr,
    [pid],
  );

  if (err) return <div>{err}</div>;

  const search = (
    <input
      class="cust-input skill-search"
      type="search"
      value={query}
      placeholder={st("skills.search")}
      aria-label={st("skills.search")}
      onInput={(e) => setQuery((e.target as HTMLInputElement).value)}
    />
  );
  const trimmed = query.trim();
  if (trimmed) {
    // A search reaches collection members too: 561 of the 612 skills are inside one.
    const hits = searchSkills(skills, trimmed);
    return (
      <div>
        <Hdr title={t("palette.group.skills")} sub={t("cust.skills.desc", skills.length)} />
        {search}
        <div class="cust-note skill-search-count" role="status">
          {hits.length ? st("skills.searchCount", hits.length) : st("skills.searchNone", trimmed)}
        </div>
        {hits.map((s) => (
          <SkillRow key={asString(s.name) + asString(s.scope)} s={s} pid={pid} />
        ))}
      </div>
    );
  }

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
      {search}
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
