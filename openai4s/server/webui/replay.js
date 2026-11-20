/* Read-only session replay viewer (M2-3). Renders the sanitized view.json
 * from GET /api/v1/sessions/{id}/replay — the same projection web shares
 * publish, which is the only data shape a guest may touch (D3).
 *
 * TEAM-27: messages and cells are shown in the order they happened, each cell
 * with its source and output; Markdown is rendered by a small safe renderer
 * (DOM nodes and textContent only, never parsed as HTML); figures
 * and artifacts are shown and downloadable when the reader may fetch them;
 * Enter opens the id. TEAM-28: the page names who is signed in (with Sign
 * out) and lists the sessions this login may read — a guest's whole world.
 * The page follows the saved theme and language (theme-bootstrap.js). */
(function () {
  "use strict";
  var API = "/api/v1";

  var COPY = {
    en: {
      heading: "OpenAI4S · Read-only session replay",
      placeholder: "session id (f-…)",
      open: "Open",
      pick: "Choose a session…",
      user: "User",
      assistant: "Assistant",
      cell: "Cell {0} · {1}",
      background: " · background task",
      artifacts: "Artifacts",
      download: "Download",
      summary: "{0} messages · {1} cells · {2} artifacts",
      hidden: " ({0} hidden)",
      truncated: "…(truncated)",
      empty: "(empty session)",
      unavailable: "Replay unavailable ({0}). Check the session id.",
      network: "Network error — the replay could not be loaded.",
      signedIn: "Signed in as {0}",
      signOut: "Sign out",
      listTitle: "Sessions you can read",
      none: "Nothing has been shared with you yet. Ask whoever invited you to make a session visible to the project.",
      untitled: "(untitled)",
      guest: "guest",
    },
    zh: {
      heading: "OpenAI4S · 只读会话回放",
      placeholder: "会话 id（f-…）",
      open: "打开",
      pick: "选择一个会话…",
      user: "用户",
      assistant: "助手",
      cell: "单元 {0} · {1}",
      background: " · 后台任务",
      artifacts: "产物",
      download: "下载",
      summary: "{0} 条消息 · {1} 个单元 · {2} 个产物",
      hidden: "（{0} 个已隐藏）",
      truncated: "…（已截断）",
      empty: "（空会话）",
      unavailable: "无法回放（{0}），请检查会话 id。",
      network: "网络错误，回放未能加载。",
      signedIn: "已登录：{0}",
      signOut: "退出登录",
      listTitle: "你可以查看的会话",
      none: "还没有分享给你的会话。请让邀请你的人把会话设为项目可见。",
      untitled: "（未命名）",
      guest: "访客",
    },
  };
  var lang = String(document.documentElement.lang || "").indexOf("zh") === 0 ? "zh" : "en";
  function t(key) {
    var text = (COPY[lang] && COPY[lang][key]) || COPY.en[key] || key;
    for (var i = 1; i < arguments.length; i++) text = text.split("{" + (i - 1) + "}").join(String(arguments[i]));
    return text;
  }

  function byId(id) { return document.getElementById(id); }
  function node(tag, cls, value) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (value != null) n.textContent = value;
    return n;
  }

  /* ---- safe Markdown: fences, headings, lists, paragraphs; inline code,
   * bold and links. Every piece of session text goes through textContent. */
  function inline(parent, text) {
    var re = /(`[^`\n]+`|\*\*[^*\n]+\*\*|!?\[[^\]\n]+\]\([^)\s]+\))/g;
    var last = 0;
    var m;
    while ((m = re.exec(text)) !== null) {
      if (m.index > last) parent.appendChild(document.createTextNode(text.slice(last, m.index)));
      var tok = m[0];
      if (tok.charAt(0) === "`") parent.appendChild(node("code", null, tok.slice(1, -1)));
      else if (tok.indexOf("**") === 0) parent.appendChild(node("strong", null, tok.slice(2, -2)));
      else {
        var link = /^!?\[([^\]]+)\]\(([^)\s]+)\)$/.exec(tok);
        var label = link ? link[1] : tok;
        var href = link ? link[2] : "";
        if (/^https?:\/\//i.test(href)) {
          var a = node("a", null, label);
          a.href = href;
          a.rel = "noopener noreferrer";
          a.target = "_blank";
          parent.appendChild(a);
        } else {
          // In-app links (/api/…) are named, not followed: the artifact is
          // listed below with what the reader may open.
          parent.appendChild(node("code", null, label));
        }
      }
      last = m.index + tok.length;
    }
    if (last < text.length) parent.appendChild(document.createTextNode(text.slice(last)));
  }

  function markdown(container, text) {
    var lines = String(text || "").replace(/\r\n?/g, "\n").split("\n");
    var i = 0;
    while (i < lines.length) {
      var line = lines[i];
      if (/^\s*```/.test(line)) {
        var buf = [];
        i++;
        while (i < lines.length && !/^\s*```/.test(lines[i])) { buf.push(lines[i]); i++; }
        i++;
        var pre = node("pre");
        pre.appendChild(node("code", null, buf.join("\n")));
        container.appendChild(pre);
        continue;
      }
      var heading = /^(#{1,6})\s+(.*)$/.exec(line);
      if (heading) {
        var h = node(heading[1].length <= 2 ? "h3" : "h4");
        inline(h, heading[2]);
        container.appendChild(h);
        i++;
        continue;
      }
      if (/^\s*([-*+]|\d+[.)])\s+/.test(line)) {
        var ordered = /^\s*\d+[.)]\s+/.test(line);
        var list = node(ordered ? "ol" : "ul");
        while (i < lines.length && /^\s*([-*+]|\d+[.)])\s+/.test(lines[i])) {
          var li = node("li");
          inline(li, lines[i].replace(/^\s*([-*+]|\d+[.)])\s+/, ""));
          list.appendChild(li);
          i++;
        }
        container.appendChild(list);
        continue;
      }
      if (!line.trim()) { i++; continue; }
      var para = [];
      while (i < lines.length && lines[i].trim() && !/^\s*```/.test(lines[i]) &&
             !/^#{1,6}\s/.test(lines[i]) && !/^\s*([-*+]|\d+[.)])\s+/.test(lines[i])) {
        para.push(lines[i]);
        i++;
      }
      var p = node("p");
      inline(p, para.join("\n"));
      container.appendChild(p);
    }
  }

  function artifactUrl(artifact) {
    return API + "/artifacts/" + encodeURIComponent(artifact.artifact_id || "");
  }
  function image(artifact, caption) {
    var fig = node("figure");
    var img = node("img");
    img.alt = artifact.filename || "";
    img.loading = "lazy";
    // A reader who may not fetch artifacts (a guest) sees the name only.
    img.onerror = function () { img.remove(); };
    img.src = artifactUrl(artifact);
    fig.appendChild(img);
    if (caption) fig.appendChild(node("figcaption", null, artifact.filename || ""));
    return fig;
  }
  var IMAGE = { "image/png": 1, "image/jpeg": 1, "image/gif": 1, "image/webp": 1 };

  function when(value) {
    if (typeof value === "number") return value;
    var parsed = Date.parse(String(value || ""));
    return isNaN(parsed) ? 0 : parsed;
  }

  function render(view) {
    var out = byId("transcript");
    out.textContent = "";
    byId("artifacts").textContent = "";
    var counts = view.counts || {};
    var session = view.session || {};
    var artifacts = view.artifacts || [];
    byId("meta").textContent =
      (session.name || session.task_summary ? (session.name || session.task_summary) + " — " : "") +
      t("summary", counts.messages || 0, counts.cells || 0, artifacts.length) +
      (view.hidden_cell_count ? t("hidden", view.hidden_cell_count) : "");
    var byName = {};
    artifacts.forEach(function (a) { byName[a.filename] = a; });

    var items = [];
    (view.messages || []).forEach(function (m, n) { items.push({ at: when(m.created_at), n: n, message: m }); });
    (view.cells || []).forEach(function (c, n) { items.push({ at: when(c.created_at), n: n + 0.5, cell: c }); });
    items.sort(function (a, b) { return (a.at && b.at ? a.at - b.at : 0) || a.n - b.n; });

    var shown = {};
    var count = 0;
    items.forEach(function (item) {
      var box;
      if (item.message) {
        var m = item.message;
        box = node("div", "msg " + (m.role === "user" ? "user" : "assistant"));
        box.appendChild(node("div", "who", m.role === "user" ? t("user") : t("assistant")));
        var body = node("div");
        var content = typeof m.content === "string" ? m.content : JSON.stringify(m.content);
        if (m.role === "user") body.appendChild(node("div", null, content));
        else markdown(body, content);
        box.appendChild(body);
        out.appendChild(box);
        return;
      }
      var c = item.cell;
      count += 1;
      box = node("div", "msg");
      var background = String(c.kernel_id || "").indexOf("background") >= 0;
      box.appendChild(node("div", "who", t("cell", count, c.language || "python") +
        (background ? t("background") : "") + (c.status && c.status !== "ok" ? " · " + c.status : "")));
      if (c.source) {
        var src = node("pre");
        src.appendChild(node("code", null, c.source));
        box.appendChild(src);
      }
      if (c.stdout) box.appendChild(node("pre", "out", c.stdout + (c.stdout_truncated ? "\n" + t("truncated") : "")));
      if (c.stderr) box.appendChild(node("pre", "out", c.stderr + (c.stderr_truncated ? "\n" + t("truncated") : "")));
      if (c.error) box.appendChild(node("pre", "err-out", c.error));
      var names = (c.figure_refs || []).map(function (f) { return f.filename; }).concat(c.files_written || []);
      names.forEach(function (name) {
        var artifact = byName[name];
        if (!artifact || shown[name] || !IMAGE[artifact.content_type]) return;
        shown[name] = true;
        box.appendChild(image(artifact, true));
      });
      out.appendChild(box);
    });
    if (!items.length) out.appendChild(node("div", "meta", t("empty")));

    if (artifacts.length) {
      var section = byId("artifacts");
      section.appendChild(node("h2", null, t("artifacts")));
      var grid = node("div", "arts");
      artifacts.forEach(function (a) {
        var card = node("div", "art");
        if (IMAGE[a.content_type]) card.appendChild(image(a, false));
        var link = node("a", null, a.filename || "");
        link.href = artifactUrl(a);
        link.download = a.filename || "";
        card.appendChild(link);
        card.appendChild(node("div", "meta", (a.content_type || "") + " · " + size(a.size_bytes)));
        grid.appendChild(card);
      });
      section.appendChild(grid);
    }
  }
  function size(n) {
    n = Number(n || 0);
    if (n < 1024) return n + " B";
    if (n < 1048576) return (n / 1024).toFixed(1) + " KB";
    return (n / 1048576).toFixed(1) + " MB";
  }

  function load(sid) {
    byId("err").textContent = "";
    fetch(API + "/sessions/" + encodeURIComponent(sid) + "/replay")
      .then(function (r) {
        if (r.status === 401) { toLogin(); return null; }
        if (!r.ok) {
          byId("err").textContent = t("unavailable", r.status);
          return null;
        }
        return r.json();
      })
      .then(function (view) { if (view) render(view); })
      .catch(function () { byId("err").textContent = t("network"); });
  }

  function open(sid) {
    sid = String(sid || "").trim();
    if (!sid) return;
    byId("sid").value = sid;
    location.hash = sid;
    load(sid);
  }

  // Signed out (or the cookie expired): the login, then back to this page.
  function toLogin() {
    var here = location.pathname + location.search + location.hash;
    location.replace("/login?next=" + encodeURIComponent(here));
  }

  function showWho(me) {
    var box = byId("who");
    if (!box || !me || !me.user) return;
    box.textContent = "";
    var who = me.user.username + (me.user.role === "guest" ? " (" + t("guest") + ")" : "");
    box.appendChild(node("span", null, t("signedIn", who)));
    var out = node("button", null, t("signOut"));
    out.type = "button";
    out.onclick = function () {
      var leave = function () { location.replace("/login"); };
      fetch(API + "/auth/logout", { method: "POST" }).then(leave, leave);
    };
    box.appendChild(out);
  }

  // rows: [{session_id, name, updated_at}] — what this login may read.
  function showList(rows, sayWhenEmpty) {
    var box = byId("list");
    if (!box) return;
    box.textContent = "";
    if (!rows.length && !sayWhenEmpty) return;
    box.appendChild(node("h2", null, t("listTitle")));
    if (!rows.length) {
      box.appendChild(node("div", "meta", t("none")));
      return;
    }
    rows.forEach(function (s) {
      var a = node("a");
      a.href = "#" + s.session_id;
      a.setAttribute("data-session", s.session_id);
      var at = when(s.updated_at);
      if (at) a.appendChild(node("span", "when", new Date(at).toLocaleString()));
      a.appendChild(node("span", null, s.name || t("untitled")));
      a.onclick = function (ev) { ev.preventDefault(); open(s.session_id); };
      box.appendChild(a);
    });
  }

  /* Team mode: who is signed in and the sessions shared with them (a guest
   * may read nothing else). Single-user mode: the owner's own sessions. */
  function listSessions() {
    fetch(API + "/auth/me")
      .then(function (r) {
        if (r.status === 401) { toLogin(); return null; }
        return r.ok ? r.json() : {};
      })
      .then(function (me) {
        if (me === null) return null;
        if (me && me.team_mode === true) {
          showWho(me);
          return fetch(API + "/auth/me/replays")
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (body) { if (body) showList(body.sessions || [], true); });
        }
        return fetch(API + "/frames?limit=50")
          .then(function (r) { return r.ok ? r.json() : null; })
          .then(function (data) {
            var frames = (data && (Array.isArray(data) ? data : data.frames)) || [];
            var rows = [];
            frames.forEach(function (f) {
              var id = f.root_frame_id || f.id || f.frame_id;
              if (!id || (f.id && f.root_frame_id && f.id !== f.root_frame_id)) return;
              rows.push({ session_id: id, name: f.name || f.task_summary, updated_at: f.updated_at || f.created_at });
            });
            showList(rows, false);
          });
      })
      .catch(function () { /* the id field still works */ });
  }

  byId("heading").textContent = t("heading");
  byId("sid").placeholder = t("placeholder");
  byId("go").textContent = t("open");
  byId("open-form").onsubmit = function (event) {
    event.preventDefault();
    open(byId("sid").value);
  };
  listSessions();
  var fromHash = decodeURIComponent((location.hash || "").replace(/^#/, "")) ||
    new URLSearchParams(location.search).get("session") || "";
  if (fromHash) open(fromHash);
})();
