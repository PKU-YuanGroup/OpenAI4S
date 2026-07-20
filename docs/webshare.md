# Web sharing

Share a session as a **read-only snapshot** that anyone with the link can view
at `https://<share-id>.<your-domain>/`, download as a portable bundle, and
import into their own local OpenAI4S to run or continue.

Sharing is **off by default** and does nothing until you both configure a relay
and enable it. The daemon never binds a public port — it dials *out* to a relay
you run.

## Trust model — read this first

- **The link is a credential.** Anyone who has it can view the snapshot. There
  is no separate password. Treat a share URL like a secret; revoke it when done.
- **A share is a snapshot, not a live view.** It captures the session at the
  moment you shared (or last updated) it. Later changes are not exposed until
  you explicitly update the share.
- **The relay sees plaintext.** Content is TLS-encrypted to your relay, but the
  relay process (which you operate) handles it in the clear and could observe or
  replace it. Run the relay on infrastructure you control. v1 does not add
  end-to-end encryption (the relay is in your own trust domain).
- **What a share publishes** (shown in the confirm dialog): the conversation of
  the current branch, the Notebook code and output, artifact files, and the
  environment package list. It never includes project memories, permission or
  capability state, API keys, `.env`/key files, or the live kernel. Residual
  secrets fail the publish closed; filtered files are reported as an excluded
  count.

## Architecture

```
visitor ──HTTPS──▶ Caddy (*.<domain>) ──▶ openai4s relay (your VPS, stateless)
                                              ▲
                                 outbound WSS │ (daemon dials out, Bearer token)
your machine: openai4s daemon (127.0.0.1) ────┘
```

The relay forwards each visitor GET/HEAD through the tunnel to a **read-only
snapshot** materialized on your machine; it never reaches the kernel, the
dispatcher, or any writable gateway route.

## Deploy the relay (on a VPS)

### 1. DNS

Point a wildcard and a tunnel host at the VPS, and pin the CA:

```
*.openai4s.org        A     <vps-ip>
share.openai4s.org    A     <vps-ip>        # tunnel endpoint (optional convenience)
openai4s.org          CAA   0 issue "letsencrypt.org"
```

Use a **wildcard TLS certificate** (DNS-01). Per-subdomain certificates would
publish every share id into Certificate Transparency logs — the wildcard keeps
share ids out of public logs. Enable 2FA on your registrar and DNS provider.

### 2. TLS front (Caddy)

```
*.openai4s.org {
    tls { dns <provider> {env.DNS_API_TOKEN} }   # needs a DNS-plugin caddy build
    reverse_proxy 127.0.0.1:8770                  # WebSocket upgrades pass through
    log { output discard }                        # Host carries the plaintext share id
}
```

nginx equivalent: obtain a wildcard cert via `certbot --preferred-challenges dns`,
`proxy_pass http://127.0.0.1:8770;`, forward `Upgrade`/`Connection` headers for
the WebSocket, and set `access_log off;`.

### 3. Relay process (systemd)

```ini
[Service]
DynamicUser=yes
EnvironmentFile=/etc/openai4s/relay.env      # 0600, holds OPENAI4S_RELAY_* if used
ExecStart=/usr/local/bin/openai4s relay serve \
    --listen 127.0.0.1:8770 --base-domain openai4s.org \
    --tokens-file /etc/openai4s/relay-tokens
Restart=always
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
```

`relay-tokens` (mode 0600) holds one publisher token per line, `label token`.
Generate one with `openai4s relay gen-token`. Editing the file is picked up live
(the relay reloads on mtime change and disconnects any tunnel whose token was
removed) — rotation needs no restart.

### 4. Firewall

Open only 80/443. Keep the relay's `127.0.0.1:8770` loopback-only behind Caddy.

## Configure the daemon (your machine)

Put these in the git-ignored `.env` (or export them):

| variable | value |
|---|---|
| `OPENAI4S_SHARE_RELAY_URL` | `wss://share.openai4s.org/tunnel` |
| `OPENAI4S_SHARE_AUTH_TOKEN` | the publisher token from the relay's tokens file |
| `OPENAI4S_SHARE_BASE_DOMAIN` | `openai4s.org` |

The token is named `*_AUTH_TOKEN` on purpose so the session-package secret
scanners treat it as a secret and never let it leave in a bundle. It is never
written to the database.

Then enable sharing (once): Customize → Sharing, or `openai4s share enable`.

## Use it

- **Web UI**: session menu → *Share (read-only link)* → *Create share link* →
  copy. The dialog also updates the snapshot or revokes it, and shows what will
  be published.
- **CLI**:
  ```
  openai4s share create latest --expires 7d   # or a root frame id; 30m/24h/7d
  openai4s share update <share_id> --expires 24h   # reset expiry (or --no-expiry)
  openai4s share list
  openai4s share revoke <share_id>
  openai4s share status
  openai4s share import <url>           # pull a shared session into your daemon
  ```

### Expiry (auto-revoke)

A share can carry an expiry so it revokes itself. Set it in the Web dialog
(Never / 1 day / 7 days / 30 days) or with `--expires 30m|24h|7d` (the REST body
field is `expires_in`, in seconds; `0`/absent = never). When it lapses the daemon
auto-revokes it — deleting the snapshot and unregistering it from the relay, so
the link returns 404. A background sweeper checks about once a minute, and any
share that expired while the daemon was off is revoked on the next startup, so an
expired link never comes back online. Expiry is enforced daemon-side; the relay
needs no changes.

Recipients open the link, click **Run locally**, download the bundle (or run
`openai4s share import <url>`), and get a quarantined, view-only session. They
click **Restart fresh** in its recovery panel to establish a trusted runtime,
then continue or fork. Imported package code is never replayed.

## Security notes

- **`X-Content-SHA256` proves transfer integrity, not authenticity.** The relay
  (or anyone controlling its DNS/TLS) can view and replace a bundle. The import
  boundary — quarantine, `replay_policy=never`, and an explicit fresh restart —
  is the real defense. The CLI prints the computed sha256 so you can compare it
  out-of-band with the sharer.
- **URL import is SSRF-hardened**: HTTPS-only off loopback, no URL credentials,
  every redirect hop re-validated, private/loopback/link-local addresses
  refused, and a hard 128 MiB streamed cap.
- **Imported content is untrusted.** Static injection markers in messages/cells
  are flagged and banner-annotated (a hint, not a guarantee); quarantine remains
  the boundary.
- **Revoke / disable.** Revoke deletes the snapshot and unregisters the share —
  it is unreachable within a round-trip. Disable takes all shares offline but
  keeps them for later. In-flight downloads already streaming may finish; the
  system cannot recall bytes already sent.
- **Token blast radius.** A leaked publisher token lets the holder host content
  under *your* share subdomains and take over/unregister *their own* shares on
  your relay — it grants no access to your local daemon and cannot touch another
  principal's shares. Remove the token's line from the tokens file to cut it off.

## End-to-end verification checklist

1. `dig share.openai4s.org` and `dig <random>.openai4s.org` resolve to the VPS.
2. `curl -I https://<26-char-id>.openai4s.org/` on an unknown id → 404 with
   `x-content-type-options`, `referrer-policy`, `x-robots-tag` headers.
3. Enable sharing; daemon log / `openai4s share status` shows `connected`.
4. Create a share; open it in a private window: conversation, Notebook, and
   artifacts render, HTML/SVG artifacts download rather than inline, and the
   browser console shows **zero CSP violations and zero external requests**.
5. Download the bundle; confirm its sha256 matches what the CLI printed.
6. `openai4s share import <url>` into a second install → view-only quarantine →
   *Restart fresh* → continue the conversation.
7. Revoke → the link returns 404. Restart the relay → the share auto-recovers
   (the daemon reconnects and re-registers). Disable → 404; re-enable → restored.

## Local testing without a VPS

`*.localtest.me` resolves to `127.0.0.1`, so you can exercise the whole path
locally over plaintext:

```
openai4s relay serve --listen 127.0.0.1:8770 --base-domain localtest.me \
    --tokens-file /tmp/relay-tokens
# daemon .env:
OPENAI4S_SHARE_RELAY_URL=ws://127.0.0.1:8770/tunnel
OPENAI4S_SHARE_AUTH_TOKEN=<token from /tmp/relay-tokens>
OPENAI4S_SHARE_BASE_DOMAIN=localtest.me
OPENAI4S_SHARE_ALLOW_INSECURE=1        # permits ws:// on loopback for testing only
```

Then visit `http://<share-id>.localtest.me:8770/`.
