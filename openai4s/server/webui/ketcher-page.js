/* Host page for the vendored Ketcher editor (`GET /ketcher`).
 *
 * External file rather than inline, for the same reason as login.js: the shared
 * CSP authorizes only same-origin scripts, so the inline <script> this replaces
 * was refused by `script-src 'self'` and the editor never initialized. The
 * artifact id arrives on a data attribute instead of being interpolated into
 * executable source. */
(function () {
  "use strict";

  const host = document.getElementById("openai4s-artifact");
  const status = document.getElementById("ketcher-status");
  const frame = document.getElementById("ketcher-frame");
  const save = document.getElementById("ketcher-save");
  if (!host || !status || !frame || !save) return;

  const artifactId = host.dataset.artifactId || "";

  function ketcher() {
    try {
      return frame.contentWindow && frame.contentWindow.ketcher;
    } catch (error) {
      return null;
    }
  }

  async function loadArtifact() {
    if (!artifactId) {
      status.textContent = "ready";
      return;
    }
    const response = await fetch(
      "/api/v1/artifacts/" + encodeURIComponent(artifactId)
    );
    if (!response.ok) {
      status.textContent = "artifact load failed";
      return;
    }
    const text = await response.text();
    const editor = ketcher();
    if (editor && editor.setMolecule) await editor.setMolecule(text);
    // The file's name, which the person knows, rather than its internal id.
    status.textContent = "loaded " + (shownName(response) || artifactId);
  }

  function shownName(response) {
    const header = response.headers.get("content-disposition") || "";
    const encoded = /filename\*=UTF-8''([^;]+)/i.exec(header);
    if (encoded) {
      try {
        return decodeURIComponent(encoded[1]);
      } catch (error) {
        /* fall back to the plain parameter */
      }
    }
    const plain = /filename="([^"]*)"/i.exec(header);
    return plain ? plain[1] : "";
  }

  window.addEventListener("message", (event) => {
    if (event.data && event.data.eventType === "init") loadArtifact();
  });
  frame.addEventListener("load", () => setTimeout(loadArtifact, 400));

  save.onclick = async () => {
    const editor = ketcher();
    if (!editor || !artifactId) {
      status.textContent = "nothing to save";
      return;
    }
    const mol = editor.getMolfile ? await editor.getMolfile() : "";
    const response = await fetch(
      "/api/v1/artifacts/" + encodeURIComponent(artifactId) + "/structure",
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ content: mol, format: "mol" }),
      }
    );
    const payload = await response.json().catch(() => ({}));
    status.textContent = response.ok
      ? "saved " +
        (payload.version_id || "") +
        (payload.unchanged ? " (unchanged)" : "")
      : "save failed: " + (payload.error || response.status);
  };
})();
