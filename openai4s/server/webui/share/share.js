/* OpenAI4S read-only share viewer — self-contained, no external deps.
 * Runs under a strict CSP (script-src 'self'); all untrusted content is placed
 * via textContent, never innerHTML, so shared-session text cannot inject markup.
 */
(function () {
  "use strict";

  var I18N = {
    en: {
      runLocally: "Run locally", runTitle: "Run or continue this session locally",
      runStep1: "Install & start OpenAI4S:", runStep2: "Import this snapshot:",
      runStep3: "The imported session opens view-only. Use “Restart fresh” to establish a trusted runtime, then continue or fork.",
      copy: "Copy", copied: "Copied", downloadBundle: "Download bundle",
      conversation: "Conversation", notebook: "Notebook", artifacts: "Artifacts",
      hidden: "hidden non-scientific cell(s)", download: "Download",
      footer: "A read-only snapshot shared via OpenAI4S. The link is a credential — anyone with it can view this content.",
      langToggle: "中文",
    },
    zh: {
      runLocally: "在本地运行", runTitle: "在本地查看或继续该会话",
      runStep1: "安装并启动 OpenAI4S：", runStep2: "导入本快照：",
      runStep3: "导入的会话为只读隔离态。点击「重新启动全新运行时」建立可信内核后即可继续或分叉。",
      copy: "复制", copied: "已复制", downloadBundle: "下载数据包",
      conversation: "对话", notebook: "Notebook", artifacts: "产物",
      hidden: "个非科学单元已隐藏", download: "下载",
      footer: "经 OpenAI4S 分享的只读快照。链接即凭据——任何持有链接的人都能查看本内容。",
      langToggle: "EN",
    },
  };
  var lang = (navigator.language || "en").toLowerCase().indexOf("zh") === 0 ? "zh" : "en";

  function t(k) { return (I18N[lang] && I18N[lang][k]) || I18N.en[k] || k; }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function api(path) {
    return fetch(path, { credentials: "omit" }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }

  function applyI18n() {
    var nodes = document.querySelectorAll("[data-i18n]");
    for (var i = 0; i < nodes.length; i++) {
      var k = nodes[i].getAttribute("data-i18n");
      if (I18N[lang][k]) nodes[i].textContent = I18N[lang][k];
    }
    document.getElementById("lang-btn").textContent = t("langToggle");
  }

  /* ---- minimal, safe markdown ---- */
  function renderMarkdown(container, text) {
    var lines = String(text || "").split("\n");
    var i = 0;
    while (i < lines.length) {
      if (lines[i].indexOf("```") === 0) {
        var buf = [];
        i++;
        while (i < lines.length && lines[i].indexOf("```") !== 0) { buf.push(lines[i]); i++; }
        i++;
        var pre = el("pre"); pre.appendChild(el("code", null, buf.join("\n")));
        container.appendChild(pre);
        continue;
      }
      var para = [];
      while (i < lines.length && lines[i].trim() !== "" && lines[i].indexOf("```") !== 0) {
        para.push(lines[i]); i++;
      }
      while (i < lines.length && lines[i].trim() === "") i++;
      if (para.length) container.appendChild(inlineParagraph(para.join("\n")));
    }
  }
  function inlineParagraph(text) {
    var p = el("p");
    // Split on `code` and **bold**; everything else is plain text.
    var re = /(`[^`]+`|\*\*[^*]+\*\*)/g;
    var last = 0, m;
    while ((m = re.exec(text)) !== null) {
      if (m.index > last) p.appendChild(document.createTextNode(text.slice(last, m.index)));
      var tok = m[0];
      if (tok.charAt(0) === "`") p.appendChild(el("code", null, tok.slice(1, -1)));
      else p.appendChild(el("strong", null, tok.slice(2, -2)));
      last = m.index + tok.length;
    }
    if (last < text.length) p.appendChild(document.createTextNode(text.slice(last)));
    return p;
  }

  function renderMessages(view) {
    var host = document.getElementById("messages");
    (view.messages || []).forEach(function (m) {
      var box = el("div", "msg " + (m.role === "user" ? "user" : "assistant"));
      box.appendChild(el("div", "role", m.role));
      var body = el("div", "msg-body");
      if (m.role === "user") body.appendChild(el("div", null, m.content || ""));
      else renderMarkdown(body, m.content || "");
      box.appendChild(body);
      host.appendChild(box);
    });
  }

  function renderCells(view) {
    var host = document.getElementById("cells");
    (view.cells || []).forEach(function (c) {
      var cell = el("div", "cell");
      cell.appendChild(el("div", "cell-head",
        "[" + (c.state_revision || "") + "] " + (c.language || "python") +
        (c.status ? " · " + c.status : "")));
      var pre = el("pre", "cell-src"); pre.appendChild(el("code", null, c.source || ""));
      cell.appendChild(pre);
      var out = el("div", "out");
      if (c.stdout) out.appendChild(streamBlock(c.stdout, c.stdout_truncated));
      if (c.stderr) { var e = el("div", "err"); e.appendChild(streamBlock(c.stderr, c.stderr_truncated)); out.appendChild(e); }
      if (c.error) { var er = el("div", "err"); er.appendChild(el("pre", null, c.error)); out.appendChild(er); }
      cell.appendChild(out);
      (c.figure_refs || []).forEach(function (f) {
        var fig = el("figure");
        var img = el("img"); img.src = "/api/artifacts/" + f.sha256; img.alt = f.filename;
        fig.appendChild(img);
        cell.appendChild(fig);
      });
      host.appendChild(cell);
    });
    if (view.hidden_cell_count) {
      var note = document.getElementById("hidden-note");
      note.textContent = view.hidden_cell_count + " " + t("hidden");
      note.hidden = false;
    }
  }
  function streamBlock(text, truncated) {
    var pre = el("pre", null, truncated ? text + "\n…(truncated)" : text);
    return pre;
  }

  var INLINE_IMG = { "image/png": 1, "image/jpeg": 1, "image/gif": 1, "image/webp": 1 };
  function renderArtifacts(view) {
    var grid = document.getElementById("artifact-grid");
    (view.artifacts || []).forEach(function (a) {
      var card = el("div", "art");
      if (INLINE_IMG[a.content_type]) {
        var img = el("img"); img.src = "/api/artifacts/" + a.sha256; img.alt = a.filename;
        card.appendChild(img);
      }
      card.appendChild(el("div", "art-name", a.filename));
      card.appendChild(el("div", "art-meta", (a.content_type || "") + " · " + bytes(a.size_bytes)));
      card.onclick = function () { openArtifact(a); };
      grid.appendChild(card);
    });
  }
  function bytes(n) {
    n = Number(n || 0);
    if (n < 1024) return n + " B";
    if (n < 1048576) return (n / 1024).toFixed(1) + " KB";
    return (n / 1048576).toFixed(1) + " MB";
  }

  function openArtifact(a) {
    var url = "/api/artifacts/" + a.sha256;
    var body = document.getElementById("preview-body");
    body.textContent = "";
    if (INLINE_IMG[a.content_type]) {
      var img = el("img"); img.src = url; body.appendChild(img);
    } else if (a.content_type === "text/csv" || /\.csv$/i.test(a.filename || "")) {
      fetch(url, { credentials: "omit" }).then(function (r) { return r.text(); }).then(function (txt) {
        body.appendChild(csvTable(txt));
      });
    } else if ((a.content_type || "").indexOf("text/") === 0) {
      fetch(url, { credentials: "omit" }).then(function (r) { return r.text(); }).then(function (txt) {
        var pre = el("pre", null, txt.slice(0, 200000)); body.appendChild(pre);
      });
    } else {
      var link = el("a", "primary", t("download")); link.href = url; link.download = a.filename;
      body.appendChild(link);
    }
    document.getElementById("preview-overlay").hidden = false;
  }
  function csvTable(txt) {
    var table = el("table", "csv");
    txt.split(/\r?\n/).slice(0, 200).forEach(function (line, r) {
      if (line === "" && r > 0) return;
      var tr = el("tr");
      line.split(",").forEach(function (cell) {
        tr.appendChild(el(r === 0 ? "th" : "td", null, cell));
      });
      table.appendChild(tr);
    });
    return table;
  }

  function setup(meta, view) {
    var s = view.session || {};
    document.getElementById("title").textContent = s.name || s.task_summary || "Shared session";
    var sub = [];
    if (s.model) sub.push(s.model);
    if (s.project_name) sub.push(s.project_name);
    document.getElementById("subtitle").textContent = sub.join(" · ");

    var origin = window.location.origin;
    document.getElementById("import-cmd").textContent = "openai4s share import " + origin + "/";
    document.getElementById("bundle-link").href = "/bundle";

    document.getElementById("run-btn").onclick = function () {
      var p = document.getElementById("run-panel");
      p.hidden = !p.hidden;
    };
    document.getElementById("copy-import").onclick = function (ev) {
      var cmd = document.getElementById("import-cmd").textContent;
      if (navigator.clipboard) navigator.clipboard.writeText(cmd);
      ev.target.textContent = t("copied");
    };
    document.getElementById("lang-btn").onclick = function () {
      lang = lang === "zh" ? "en" : "zh"; applyI18n();
    };
    document.getElementById("preview-close").onclick = function () {
      document.getElementById("preview-overlay").hidden = true;
    };
    document.getElementById("preview-overlay").onclick = function (ev) {
      if (ev.target.id === "preview-overlay") ev.currentTarget.hidden = true;
    };

    applyI18n();
    renderMessages(view);
    renderCells(view);
    renderArtifacts(view);
  }

  Promise.all([api("/api/meta"), api("/api/view")]).then(function (r) {
    setup(r[0], r[1]);
  }).catch(function (err) {
    document.getElementById("messages").appendChild(
      el("div", "muted", "Unable to load this share (" + err.message + ")."));
  });
})();
