/* Team-mode login page (docs/team-server-plan.md M1-9).
 * External file rather than inline: the shared CSP authorizes only same-origin
 * scripts, so an inline <script> here would be blocked. */
(function () {
  "use strict";
  var API = "/api/v1";

  // The page's words in both interface languages (TEAM-1). Kept JSON-shaped
  // -- quoted keys, no trailing commas -- so tests/test_team_auth_routes.py
  // can read the table and hold every `data-i18n` key in login.html to it.
  var COPY = {
    "en": {
      "title": "OpenAI4S — sign in",
      "subtitle": "Sign in to your team workspace",
      "username": "Username",
      "password": "Password",
      "submit": "Sign in",
      "busy": "Signing in…",
      "show": "Show",
      "hide": "Hide",
      "showLabel": "Show password",
      "hideLabel": "Hide password",
      "help": "Accounts are created by an administrator. Forgot your password? Ask an administrator to reset it.",
      "lang": "中文",
      "langLabel": "切换到中文",
      "themeDark": "Dark theme",
      "themeLight": "Light theme",
      "err.invalid": "Wrong username or password.",
      "err.disabled": "This account is disabled. Ask an administrator to enable it.",
      "err.rateLimited": "Too many attempts. Wait a minute, then try again.",
      "err.network": "Cannot reach the server. Is OpenAI4S running?",
      "err.other": "Sign-in failed (HTTP {0})."
    },
    "zh": {
      "title": "OpenAI4S — 登录",
      "subtitle": "登录团队工作区",
      "username": "用户名",
      "password": "密码",
      "submit": "登录",
      "busy": "正在登录…",
      "show": "显示",
      "hide": "隐藏",
      "showLabel": "显示密码",
      "hideLabel": "隐藏密码",
      "help": "账号由管理员创建。忘记密码请联系管理员重置。",
      "lang": "EN",
      "langLabel": "Switch to English",
      "themeDark": "深色主题",
      "themeLight": "浅色主题",
      "err.invalid": "用户名或密码不正确。",
      "err.disabled": "此账号已被停用，请联系管理员启用。",
      "err.rateLimited": "尝试次数过多，请等一分钟再试。",
      "err.network": "无法连接服务器，OpenAI4S 是否在运行？",
      "err.other": "登录失败（HTTP {0}）。"
    }
  };

  // theme-bootstrap.js has already set <html lang> from the saved pick, else
  // the browser's languages -- the same rule as the workbench.
  var lang = document.documentElement.lang === "zh" ? "zh" : "en";
  function t(key, arg) {
    var text = (COPY[lang] && COPY[lang][key]) || COPY.en[key] || key;
    return arg === undefined ? text : text.replace("{0}", String(arg));
  }

  var form = document.getElementById("f");
  var err = document.getElementById("err");
  var btn = document.getElementById("b");
  var langBtn = document.getElementById("login-lang");
  var themeBtn = document.getElementById("login-theme");
  var pw = document.getElementById("p");
  var pwToggle = document.getElementById("login-pw-toggle");
  // What the error line says, as a key, so a language switch can re-say it.
  var errKey = null;
  var errArg;

  function isDark() {
    return document.documentElement.getAttribute("data-theme") === "dark";
  }

  function paint() {
    document.documentElement.lang = lang;
    document.title = t("title");
    var nodes = document.querySelectorAll("[data-i18n]");
    for (var i = 0; i < nodes.length; i++) {
      nodes[i].textContent = t(nodes[i].getAttribute("data-i18n"));
    }
    if (btn.disabled) btn.textContent = t("busy");
    langBtn.textContent = t("lang");
    langBtn.setAttribute("aria-label", t("langLabel"));
    langBtn.title = t("langLabel");
    // The button names the theme it switches to.
    themeBtn.textContent = isDark() ? "☀" : "☾";
    themeBtn.setAttribute("aria-label", isDark() ? t("themeLight") : t("themeDark"));
    themeBtn.title = themeBtn.getAttribute("aria-label");
    // TEAM-2: an initial password is a long generated string; let it be read.
    var shown = pw.type === "text";
    pwToggle.textContent = shown ? t("hide") : t("show");
    pwToggle.setAttribute("aria-label", shown ? t("hideLabel") : t("showLabel"));
    pwToggle.setAttribute("aria-pressed", shown ? "true" : "false");
    err.textContent = errKey ? t(errKey, errArg) : "";
  }

  function showError(key, arg) {
    errKey = key;
    errArg = arg;
    err.textContent = key ? t(key, arg) : "";
  }

  langBtn.addEventListener("click", function () {
    lang = lang === "zh" ? "en" : "zh";
    // The workbench reads the same key, so it opens in this language too.
    try { localStorage.setItem("os-lang", lang); } catch (e) {}
    paint();
  });

  pwToggle.addEventListener("click", function () {
    pw.type = pw.type === "password" ? "text" : "password";
    paint();
    pw.focus();
  });

  themeBtn.addEventListener("click", function () {
    var next = isDark() ? "light" : "dark";
    try { localStorage.setItem("os-theme", next); } catch (e) {}
    document.documentElement.setAttribute("data-theme", next);
    document.documentElement.style.colorScheme = next;
    paint();
  });

  // Where to go once signed in: the page the team guard sent us from
  // (`?next=`, TEAM-9) when it is a path of this origin, otherwise home.
  // The same rule as the server's `team_auth.safe_next_path`, applied again
  // here because anybody can hand out a `/login?next=` link: one leading
  // "/" (never "//" or "/\"), printable ASCII only (browsers drop tabs and
  // newlines, which turns "/<TAB>/host" into "//host"), no backslash.
  function nextPath() {
    var raw = null;
    try { raw = new URLSearchParams(location.search).get("next"); } catch (e) { return "/"; }
    if (!raw || raw.length > 2048) return "/";
    if (!/^\/(?![\/\\])[!-~]*$/.test(raw) || raw.indexOf("\\") !== -1) return "/";
    var path = raw.split("?")[0].split("#")[0];
    if (path === "/login" || path.indexOf("/login/") === 0 ||
        path === "/api" || path.indexOf("/api/") === 0) return "/";
    try {
      if (new URL(raw, location.origin).origin !== location.origin) return "/";
    } catch (e) { return "/"; }
    return raw;
  }

  // Already signed in, or team mode off? Go on.
  fetch(API + "/auth/me")
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (me) {
      if (me && (me.team_mode === false || me.user)) location.replace(nextPath());
    })
    .catch(function () {});

  // A refusal by its stable code, in the page's language -- never the
  // server's English sentence.
  function failureKey(status, body) {
    var code = body && body.code;
    if (code === "account_disabled") return "err.disabled";
    if (code === "rate_limited" || status === 429) return "err.rateLimited";
    if (status === 401) return "err.invalid";
    return "err.other";
  }

  form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    showError(null);
    btn.disabled = true;
    btn.textContent = t("busy");
    fetch(API + "/auth/login", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        username: document.getElementById("u").value,
        password: document.getElementById("p").value,
      }),
    })
      .then(function (r) {
        if (r.ok) { location.replace(nextPath()); return null; }
        return r.json().catch(function () { return null; }).then(function (body) {
          showError(failureKey(r.status, body), r.status);
        });
      })
      .catch(function () { showError("err.network"); })
      .then(function () {
        btn.disabled = false;
        btn.textContent = t("submit");
      });
  });

  paint();
})();
