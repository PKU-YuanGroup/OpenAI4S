/* Team-mode login page (docs/team-server-plan.md M1-9).
 * External file rather than inline: the shared CSP authorizes only same-origin
 * scripts, so an inline <script> here would be blocked. */
(function () {
  "use strict";
  var API = "/api/v1";

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

  var form = document.getElementById("f");
  var err = document.getElementById("err");
  var btn = document.getElementById("b");
  form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    err.textContent = "";
    btn.disabled = true;
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
        return r.json().then(function (body) {
          err.textContent = (body && body.error) || ("sign-in failed (" + r.status + ")");
        });
      })
      .catch(function () { err.textContent = "network error; is the daemon up?"; })
      .then(function () { btn.disabled = false; });
  });
})();
