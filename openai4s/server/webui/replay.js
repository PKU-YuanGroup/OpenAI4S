/* Read-only session replay viewer (M2-3). Renders the sanitized view.json
 * from GET /api/v1/sessions/{id}/replay — the same projection web shares
 * publish, which is the only data shape a guest may touch (D3). */
(function () {
  "use strict";
  var API = "/api/v1";

  function el(id) { return document.getElementById(id); }
  function text(parent, tag, cls, value) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    node.textContent = value;
    parent.appendChild(node);
    return node;
  }

  function render(view) {
    var out = el("transcript");
    out.textContent = "";
    var counts = view.counts || {};
    el("meta").textContent =
      (view.session && view.session.name ? view.session.name + " — " : "") +
      (counts.messages || 0) + " messages, " + (counts.cells || 0) + " cells" +
      (counts.hidden_cell_count ? " (" + counts.hidden_cell_count + " hidden)" : "");
    (view.messages || []).forEach(function (m) {
      var box = document.createElement("div");
      box.className = "msg " + (m.role === "user" ? "user" : "assistant");
      text(box, "div", "who", m.role || "assistant");
      var content = typeof m.content === "string" ? m.content : JSON.stringify(m.content);
      text(box, "div", "", content);
      out.appendChild(box);
    });
    (view.cells || []).forEach(function (c) {
      var box = document.createElement("div");
      box.className = "msg";
      text(box, "div", "who", "cell · " + (c.language || "python"));
      if (c.code) text(box, "pre", "", c.code);
      if (c.stdout) text(box, "pre", "cell-out", c.stdout);
      if (c.error) text(box, "pre", "cell-out", c.error);
      out.appendChild(box);
    });
    if (!(view.messages || []).length && !(view.cells || []).length) {
      text(out, "div", "meta", "(empty session)");
    }
  }

  // The page's own additions (TEAM-28), in the document language that
  // theme-bootstrap.js chose: who is signed in, and what they may read.
  var COPY = {
    en: {
      signedIn: "Signed in as {0}",
      signOut: "Sign out",
      listTitle: "Sessions you can read",
      none: "Nothing has been shared with you yet. Ask whoever invited you to make a session visible to the project.",
      untitled: "(untitled)",
      guest: "guest"
    },
    zh: {
      signedIn: "已登录：{0}",
      signOut: "退出登录",
      listTitle: "你可以查看的会话",
      none: "还没有分享给你的会话。请让邀请你的人把会话设为项目可见。",
      untitled: "（未命名）",
      guest: "访客"
    }
  };
  var lang = document.documentElement.lang === "zh" ? "zh" : "en";
  function t(key, arg) {
    var s = COPY[lang][key] || COPY.en[key] || key;
    return arg === undefined ? s : s.replace("{0}", String(arg));
  }

  // Signed out (or the cookie expired): the login, then back to this page.
  function toLogin() {
    var here = location.pathname + location.search + location.hash;
    location.replace("/login?next=" + encodeURIComponent(here));
  }

  function showWho(me) {
    var box = el("who");
    if (!box || !me || !me.user) return;
    box.textContent = "";
    var who = me.user.username + (me.user.role === "guest" ? " (" + t("guest") + ")" : "");
    text(box, "span", "", t("signedIn", who));
    var out = document.createElement("button");
    out.type = "button";
    out.textContent = t("signOut");
    out.onclick = function () {
      var leave = function () { location.replace("/login"); };
      fetch(API + "/auth/logout", { method: "POST" }).then(leave, leave);
    };
    box.appendChild(out);
  }

  function showList(sessions) {
    var box = el("list");
    if (!box) return;
    box.textContent = "";
    text(box, "h2", "", t("listTitle"));
    if (!sessions.length) {
      text(box, "div", "meta", t("none"));
      return;
    }
    sessions.forEach(function (s) {
      var a = document.createElement("a");
      a.href = "#" + s.session_id;
      a.setAttribute("data-session", s.session_id);
      if (s.updated_at) text(a, "span", "when", new Date(Number(s.updated_at)).toLocaleString());
      text(a, "span", "", s.name || t("untitled"));
      a.onclick = function (ev) {
        ev.preventDefault();
        location.hash = s.session_id;
        el("sid").value = s.session_id;
        load(s.session_id);
      };
      box.appendChild(a);
    });
  }

  fetch(API + "/auth/me")
    .then(function (r) {
      if (r.status === 401) { toLogin(); return null; }
      return r.ok ? r.json() : null;
    })
    .then(function (me) {
      if (!me || me.team_mode !== true) return;
      showWho(me);
      return fetch(API + "/auth/me/replays")
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (body) { if (body) showList(body.sessions || []); });
    })
    .catch(function () {});

  function load(sid) {
    el("err").textContent = "";
    fetch(API + "/sessions/" + encodeURIComponent(sid) + "/replay")
      .then(function (r) {
        if (r.status === 401) { toLogin(); return null; }
        if (!r.ok) {
          el("err").textContent = "replay unavailable (" + r.status + ")";
          return null;
        }
        return r.json();
      })
      .then(function (view) { if (view) render(view); })
      .catch(function () { el("err").textContent = "network error"; });
  }

  el("go").onclick = function () {
    var sid = el("sid").value.trim();
    if (sid) { location.hash = sid; load(sid); }
  };
  var fromHash = (location.hash || "").replace(/^#/, "") ||
    new URLSearchParams(location.search).get("session") || "";
  if (fromHash) { el("sid").value = fromHash; load(fromHash); }
})();
