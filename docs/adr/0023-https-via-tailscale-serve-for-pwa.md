# ADR 0023 — Serve the dashboard over HTTPS via Tailscale to unlock the PWA

- **Status:** Accepted
- **Date:** 2026-06-21
- **Tickets:** [040](../tickets/040-pwa-install/) (establishes the HTTPS origin +
  PWA); the secure-context prerequisite for [041](../tickets/041-web-push/) (Web
  Push)

## Context

Phase 4 makes the web the **primary control surface** for Hive from an iPad.
040/041 add Progressive-Web-App behaviour — home-screen install, an offline
shell, and (041) Web Push. All three are built on a **service worker**, and a
service worker only runs in a **secure context**: `https://` or
`http://localhost`.

Hive is served plain HTTP today. `src/hive/__main__.py:406` starts uvicorn with
no `ssl_*` params; production `.env` binds `HIVE_WEB_HOST=100.79.194.84`,
`HIVE_WEB_PORT=8080`; `docs/DEPLOYMENT.md:229-230,369` documents access + smoke at
`http://100.79.194.84:8080/`. A repo-wide search finds **no** reverse proxy
(nginx/caddy/traefik), **no** `tailscale serve`/`funnel`, and **no** TLS/cert
configuration anywhere — Tailscale is used only for the IP bind. So on the iPad,
the dashboard origin is non-localhost plain HTTP and **the service worker
silently refuses to register** — 040's definition of done ("verified installed on
an actual iPad, standalone") is unreachable without first providing HTTPS.

This is a deployment/architecture decision (it changes how **every** device
reaches Hive and gates the whole PWA line), so it is recorded once here rather
than buried in a ticket plan.

## Decision

**Serve the dashboard over HTTPS by enabling `tailscale serve`** on the VPS:

1. Enable **MagicDNS + HTTPS certificates** in the tailnet admin (one-time).
2. `tailscale serve` terminates TLS with a Tailscale-managed Let's-Encrypt cert
   and reverse-proxies to the local uvicorn — exposing
   `https://<node>.tailfb3900.ts.net/`, **tailnet-only** (not `funnel`/public).
3. **Re-bind uvicorn to loopback** (`HIVE_WEB_HOST=127.0.0.1`) and front it
   **exclusively** through `tailscale serve https / → http://127.0.0.1:8080`, so
   Hive has a **single HTTPS origin**.

No application code changes — every route and asset URL is already root-relative,
the Bearer auth is origin-agnostic, and SSE is same-origin. The PWA artifacts
(manifest `start_url`/`scope`, SW `register()`) stay **relative** so the app
remains host-agnostic.

## Consequences

- The dashboard becomes a **secure context** → the service worker registers, the
  app installs to the iPad home screen, the offline shell works, and **041 Web
  Push** (iOS 16.4+, installed-PWA-only) becomes possible.
- **The access URL changes** for every device: `http://100.79.194.84:8080/` →
  `https://<node>.tailfb3900.ts.net/`. `docs/DEPLOYMENT.md` is updated (the
  `tailscale serve` step, the loopback re-bind, and the new access + smoke URL) —
  the cross-cutting impact declared in 040's `plan.md`.
- **No new daemon, no new public port, no per-app cert management** — the cost is
  one admin toggle and a `tailscale serve` invocation, versus standing up and
  maintaining caddy/nginx.
- **Operational caveats:** the exact `tailscale serve` syntax varies by version
  (confirm on the VPS at deploy); the managed cert can take ~a minute to issue on
  first request; a tailnet-wide HTTPS-certs setting is required.

## Alternatives rejected

- **Plain HTTP (status quo)** — the bug; non-localhost HTTP is not a secure
  context, so the SW never registers.
- **uvicorn-native TLS (`--ssl-keyfile`)** — the app terminates TLS and owns cert
  provisioning/rotation; a self-signed cert triggers iPad trust prompts and isn't
  installable cleanly.
- **caddy / nginx reverse proxy** — works with automatic Let's-Encrypt, but adds a
  second daemon + config to run and maintain for the same result Tailscale already
  provides.
- **`http://localhost` + SSH tunnel** — a secure context, but defeats the entire
  "just open it on the iPad" daily-driver goal.
- **`tailscale funnel` (public HTTPS)** — would expose Hive to the public internet
  unnecessarily; `serve` keeps it tailnet-only.
- **Dual origin (keep HTTP-IP + add HTTPS)** — splits the app across two origins
  with separate storage/SW state; the installed PWA and a bookmarked HTTP tab
  would diverge. Rejected in favour of a single HTTPS origin (loopback re-bind).
