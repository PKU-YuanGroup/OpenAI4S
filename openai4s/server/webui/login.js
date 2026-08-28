/* Team-mode login page (docs/team-server-plan.md M1-9).
 * External file rather than inline: the shared CSP authorizes only same-origin
 * scripts, so an inline <script> here would be blocked. */
(function () {
  "use strict";
  var API = "/api/v1";

  // Already signed in, or team mode off? Go home.
  fetch(API + "/auth/me")
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (me) {
      if (me && (me.team_mode === false || me.user)) location.replace("/");
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
        if (r.ok) { location.replace("/"); return null; }
        return r.json().then(function (body) {
          err.textContent = (body && body.error) || ("sign-in failed (" + r.status + ")");
        });
      })
      .catch(function () { err.textContent = "network error; is the daemon up?"; })
      .then(function () { btn.disabled = false; });
  });
})();
