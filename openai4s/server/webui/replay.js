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

  function load(sid) {
    el("err").textContent = "";
    fetch(API + "/sessions/" + encodeURIComponent(sid) + "/replay")
      .then(function (r) {
        if (r.status === 401) { location.replace("/login"); return null; }
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
