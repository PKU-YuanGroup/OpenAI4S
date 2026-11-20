import { useEffect, useState } from "preact/hooks";
import { LANG, t } from "../../i18n";
import { copyLookup } from "../../i18n/copy";
import { rankSkillItems, skillToAcItem, type AcItem } from "../../features/autocomplete/rank";
import { renderMd } from "../../features/md/render";
import { api, apiErrorText } from "../../features/customize/api";
import { closeCust, custTab, refreshCustTab } from "../../features/customize/actions";
import { nestedEditor } from "../../features/customize/state";
import { skillReadinessNote, skillReadinessT } from "../../features/customize/environment";
import {
  asList,
  asString,
  confirmAction,
  customizeProject,
  dropSkillsCatalog,
  insertSkillMention,
} from "../../features/customize/host";
import { useOptimisticToggle, useTabRead } from "./hooks";
import { Icon } from "./icons";
import { ActionStatus, Empty, Hdr, IconGhost, Pill, Toggle, type ActionNote } from "./ui";

type Skill = Record<string, unknown>;

/** Search and network-limit strings the generated dictionaries do not carry. */
const st = copyLookup({
  zh: {
    "skills.search": "搜索技能：名称、说明里的词或中文关键词",
    "skills.searchCount": "{0} 个匹配",
    "skills.searchNone": "没有匹配「{0}」的技能",
    "skills.net": "限定联网 · {0} 个域名",
    "skills.netOff": "加载后关闭联网",
    "skills.netTip":
      "加载这个技能后，本会话余下的联网只能访问：{0}。访问其他网站的请求会被拒绝，批准也无法放宽；要访问其他网站，请新开一个会话。",
    "skills.netOffTip":
      "加载这个技能后，本会话余下的时间里不能再联网：网页检索、网页读取、下载以及 host.bash 命令里的网址都会被拒绝，批准也无法放开。要再联网，请新开一个会话。",
    "skills.expand": "展开完整说明",
    "skills.collapse": "收起",
    "skills.needs": "需要：{0}",
    "skills.needsNothing": "需要：除内核外没有声明其他要求",
    "skills.req.gpu": "NVIDIA GPU",
    "skills.req.science": "科学计算 Python 包",
    "skills.network": "联网：{0}",
    "skills.doc": "SKILL.md 全文",
    "skills.docLoading": "正在读取 SKILL.md…",
    "skills.docFailed": "无法读取 SKILL.md：{0}",
  },
  en: {
    "skills.search": "Search skills: name, a word from the description, or a Chinese keyword",
    "skills.searchCount": "{0} matches",
    "skills.searchNone": "No skill matches “{0}”",
    "skills.net": "Limits network · {0}",
    "skills.netOff": "Turns network off",
    "skills.netTip":
      "Loading this Skill limits network access for the rest of this session to: {0}. Requests to other sites are refused, and approving one cannot widen the limit; start a new session to reach them.",
    "skills.netOffTip":
      "Loading this Skill turns network access off for the rest of this session: web search, web fetch, downloads and URLs in host.bash commands are refused, and approving one cannot lift it. Start a new session to use the network again.",
    "skills.expand": "Show the full description",
    "skills.collapse": "Hide",
    "skills.needs": "Needs: {0}",
    "skills.needsNothing": "Needs: nothing declared beyond the kernel",
    "skills.req.gpu": "an NVIDIA GPU",
    "skills.req.science": "scientific Python packages",
    "skills.network": "Network: {0}",
    "skills.doc": "SKILL.md",
    "skills.docLoading": "Reading SKILL.md…",
    "skills.docFailed": "Could not read SKILL.md: {0}",
  },
});

/** The domains a `host_only` skill narrows a session to, or null. */
export function skillNetworkDomains(s: Skill): string[] | null {
  const caps = s.capabilities && typeof s.capabilities === "object" ? (s.capabilities as Skill) : {};
  const network = caps.network && typeof caps.network === "object" ? (caps.network as Skill) : {};
  if (network.mode !== "host_only") return null;
  return asList(network.domains).map((domain) => String(domain));
}

/**
 * SCI-07: what loading a `host_only` skill does to the session, in words. With
 * no declared domain it turns the session's network off, which used to read
 * as "limited to the domains it declares:" followed by nothing. Same wording
 * as the conversation's network notice (`send/skill-network.ts`).
 */
export function skillNetworkCopy(domains: string[]): { summary: string; detail: string } {
  if (!domains.length) return { summary: st("skills.netOff"), detail: st("skills.netOffTip") };
  const count =
    LANG === "en" ? (domains.length === 1 ? "1 domain" : `${domains.length} domains`) : domains.length;
  return {
    summary: st("skills.net", count),
    detail: st("skills.netTip", domains.join(LANG === "en" ? ", " : "、")),
  };
}

/**
 * SCI-06: what a Skill's expanded row states besides its recipe -- the whole
 * description (the catalog's is cut at 200 characters, usually before "use
 * this skill when"), what it needs in words, and its network mode. `doc` is
 * `GET /skills/{name}` once it has answered.
 */
export function skillDetailFacts(
  s: Skill,
  doc: Skill | null,
): { description: string; needs: string; network: string | null } {
  const description =
    asString(doc?.full_description) || asString(doc?.description) || asString(s.description);
  const needs = asList(s.requirements)
    .map((item) => String(item).toLowerCase())
    .map((item) =>
      item === "gpu" ? st("skills.req.gpu") : item === "science" ? st("skills.req.science") : item,
    );
  const caps = s.capabilities && typeof s.capabilities === "object" ? (s.capabilities as Skill) : {};
  const net = caps.network && typeof caps.network === "object" ? (caps.network as Skill) : {};
  const ui = net.ui && typeof net.ui === "object" ? (net.ui as Skill) : {};
  const label = asString(LANG === "en" ? ui.label_en : ui.label_zh);
  return {
    description,
    needs: needs.length ? st("skills.needs", needs.join(LANG === "en" ? ", " : "、")) : st("skills.needsNothing"),
    network: label ? st("skills.network", label) : null,
  };
}

/** The expanded half of a Skill row: the full description, its needs, and SKILL.md. */
function SkillDetail({ s, scope }: { s: Skill; scope: string }) {
  const [doc, setDoc] = useState<Skill | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const name = asString(s.name);
  // A project Skill has no document route outside its project; its row says
  // what the catalog knows.
  const readable = scope !== "project";
  useEffect(() => {
    if (!readable) return undefined;
    let live = true;
    api(`/skills/${encodeURIComponent(name)}`).then(
      (read) => {
        if (live) setDoc(read);
      },
      (error) => {
        if (live) setFailed(apiErrorText(error));
      },
    );
    return () => {
      live = false;
    };
  }, [name, readable]);
  const facts = skillDetailFacts(s, doc);
  const body = doc ? asString(doc.body) : "";
  return (
    <div class="skill-detail">
      <div class="skill-detail-desc">{facts.description}</div>
      <div class="skill-detail-fact">{facts.needs}</div>
      {facts.network ? <div class="skill-detail-fact">{facts.network}</div> : null}
      {!readable ? null : failed ? (
        <div class="skill-detail-fact prof-warn">{st("skills.docFailed", failed)}</div>
      ) : !doc ? (
        <div class="skill-detail-fact">{st("skills.docLoading")}</div>
      ) : body.trim() ? (
        <>
          <div class="skill-detail-head">{st("skills.doc")}</div>
          <div
            class="md skill-doc"
            dangerouslySetInnerHTML={{ __html: renderMd(body, { resources: "none" }) }}
          />
        </>
      ) : null}
    </div>
  );
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
  const note = skillReadinessNote(s);
  const domains = skillNetworkDomains(s);
  const network = domains ? skillNetworkCopy(domains) : null;
  const enabled = useOptimisticToggle(s.enabled !== false, (on) =>
    api(`/skills/catalog/${encodeURIComponent(asString(s.name))}/enabled`, {
      method: "PUT",
      body: JSON.stringify({ enabled: on }),
    }),
  );
  // SCI-04: a failed delete is said in the row, not behind the dialog.
  const [actionNote, setActionNote] = useState<ActionNote>(null);
  // SCI-06: the name opens the whole description, the needs and SKILL.md.
  const [open, setOpen] = useState(false);
  const toggleOpen = () => setOpen(!open);
  return (
    <div class={"cust-row" + (open ? " skill-open" : "")}>
      <div class="info">
        <div class="nm">
          <button
            type="button"
            class="skill-name"
            data-skill-expand=""
            aria-expanded={open ? "true" : "false"}
            title={st(open ? "skills.collapse" : "skills.expand")}
            onClick={toggleOpen}
          >
            <Icon name={open ? "chevron-down" : "chevron-right"} size={14} />
            <span>{name}</span>
          </button>{" "}
          <Pill>{t(`skill.scope.${scope}`)}</Pill>
        </div>
        {open ? (
          <SkillDetail s={s} scope={scope} />
        ) : (
          <div class="ds skill-desc" onClick={toggleOpen}>
            {asString(s.description)}
          </div>
        )}
        {note ? (
          <div class="ds prof-warn skill-ready">
            <span>{note.text}</span>
            {note.compute ? (
              <button type="button" class="outline-btn small skill-ready-go" onClick={() => custTab("compute")}>
                {skillReadinessT("ready.openCompute")}
              </button>
            ) : null}
          </div>
        ) : null}
        {network ? (
          <details class="skill-net">
            <summary>{network.summary}</summary>
            <div class="ds">{network.detail}</div>
          </details>
        ) : null}
        <ActionStatus note={actionNote} />
      </div>
      <button
        type="button"
        class="outline-btn small skill-use"
        onClick={() => {
          closeCust();
          insertSkillMention(asString(s.name));
        }}
      >
        <Icon name="message-square" size={14} />
        <span>{t("skill.useInChat")}</span>
      </button>
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
