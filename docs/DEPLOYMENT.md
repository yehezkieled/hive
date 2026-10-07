# Hive — Deployment Runbook

Commands and procedures for installing, running, and maintaining the Hive
orchestrator on a Linux VPS. This is a literal record of what works, not a
polished ops doc — re-run these steps in order and you should get a working
installation.

Every path is absolute to the canonical install location
(`/home/hezki/projects/hive`). Adjust for other hosts.

---

## 1. Prerequisites

### System packages

```bash
sudo apt install python3.12-venv python3-pip gh
```

Docker + Docker Compose plugin must already be installed (we use
`pgvector/pgvector:pg16` in a container — see Section 3 for why). On this VPS
they're already present because n8n runs in Docker.

### Python tooling

`uv` is the preferred dep manager, but `pip` in a venv works too. All the
commands below use a venv at `.venv/`.

### Authentication

- **An agent harness, signed in** — Hive needs at least one of **Codex**
  (`codex login`, subscription login required), **Claude Code**
  (`claude auth login`), or **Pi** (`pi`, then `/login` or a provider API key).
  See "Harness selection" below for probing and fallback. A
  stronger second opinion comes from Claude Code's native `/advisor`
  (Ticket 013): Hive enables it per-entity by passing `--advisor <model>` at
  spawn, with a model-aware default (off for Opus mains, `opus`
  for a sub-Opus entity; override per entity with the `**Advisor**:`
  field). **Remove any global `advisorModel` from `~/.claude/settings.json`** —
  otherwise it re-enables the advisor on entities Hive deliberately leaves off.
- **Telegram bot token** (optional) — Telegram is a backup ping/approval channel
  since the cut-over ([ADR 0033](adr/0033-hive-cutover-retire-entity-runtime.md)).
  Create a bot via BotFather and paste the token into `.env` to enable it; with
  no token Hive logs `Telegram backup channel is off` and keeps running. The
  desk (`hive-gateway.service` over Tailscale) is the primary surface.
- **GitHub** (optional, for pushing) — `gh auth login` + `git config --global
  user.name/user.email`.

### Harness selection (ADR 0029)

Every turn, Hive probes (cached 60 s, no quota spent) which harnesses are
installed and signed in, then runs the turn on the first working
(harness, mode), following the role and mode orders below.
`/status` shows `via <harness>/<mode>` per entity; Telegram gets one line when an
entity's harness/mode changes. If nothing can run — e.g. Claude Code is logged out
and neither Codex nor Pi is configured — Hive says so in Telegram (at startup and on the first
failed turn, at most once per 10 min) with the one-line fix per harness; sign in to
any one and the next turn works, no restart.

| Variable | Default | Meaning |
|---|---|---|
| `HIVE_HARNESS_ORDER` | `codex,claude,pi` | Preference order. A harness not listed is tried after the listed ones. |
| `HIVE_HARNESS_ORDER_<ROLE>` | vault: `claude,codex,pi` | Per-role override, e.g. `HIVE_HARNESS_ORDER_VAULT=codex,claude,pi`. The Vault (Bash/Write/Edit denylist) defaults to Claude first, since Codex and Pi do not enforce that control. |
| `HIVE_RUN_MODE_ORDER` | `headless,pty` | Mode order within a harness; omit a mode to disable it (`headless` alone = never spawn a PTY). |
| `HIVE_PI_BINARY` / `HIVE_PI_PROVIDER` / `HIVE_PI_MODEL` | `pi` / *(unset)* / *(unset)* | Pi launcher; optional provider/model (`--provider`/`--model`). Unset = Pi's own default model. With a provider set, sign-in is probed with `pi auth check --provider`. |
| `HIVE_CODEX_BINARY` / `HIVE_CODEX_MODEL` / `HIVE_CODEX_EFFORT` | `codex` / `gpt-6.1-sol` / `medium` | Codex launcher, model, and reasoning effort. Login is probed with `codex login status`. |
| `HIVE_CLAUDE_MODEL_<ROLE>` | vault: `claude-sonnet-5-5` | Claude default per role, including PTY fallback; explicit entity/personality model choices take precedence. |
| `HIVE_CODEX_MODEL_<ROLE>` / `HIVE_CODEX_EFFORT_<ROLE>` | `HIVE_CODEX_MODEL` / `HIVE_CODEX_EFFORT` | Per-role Codex model and reasoning effort overrides. |
| `HIVE_PI_MODEL_<ROLE>` | `HIVE_PI_MODEL` | Per-role Pi model override. |
| `HIVE_HEADLESS_TIMEOUT_S` | `3600` | Wall-clock cap on one headless turn. |
| `HIVE_HEADLESS_QUOTA_RETRY_S` | `900` | After headless reports quota/refusal, how long its PTY serves turns before headless is retried. |
| `HIVE_HARNESS_RETRY_S` / `HIVE_HARNESS_DETECT_TTL_S` | `60` / `60` | Retry delay after an auth/unavailable failure; probe cache lifetime. |

New entities keep an omitted model unset so the serving harness applies its role default. Explicit entity and personality choices take precedence for Claude. Existing stored `opus` values are preserved: the roster does not distinguish an old implicit default from an explicit selection. `/model` selects the Claude model; Codex and Pi use their harness configuration in the table above.

Pi caveats: no MCP (`search_knowledge` unavailable), no `/goal`, and the
tool denylists do not apply to a Pi entity (cwd only). Codex also lacks Hive's
Claude-only denylist. The Vault therefore defaults to `claude,codex,pi`; when it
runs on Codex or Pi, `/status` shows the lost guardrail
("⚠️ tool denylist NOT enforced") next to it and the notification channels get an
alert. The startup log
prints one run order per distinct role order. Headless Claude strips
`ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN` from the subprocess so it can only use
the subscription login, never per-token API billing.

### Claude Code version policy

The fleet resolves the `claude` binary from `HIVE_CLAUDE_BINARY` (in `.env`),
**not** from an ambiguous PATH lookup. This exists because the host carries two
independent installs — the native installer (`~/.local/bin/claude`,
self-updating) and an npm global (`/usr/bin/claude`, frozen until a manual
`npm i -g`) — and the `hive.service` PATH omits `~/.local/bin`, so a bare
`claude` silently runs the **stale npm one** while dev runs the native one. The
PTY harness scrapes the Claude Code TUI to detect gates and turn completion
(see [ADR 0001](adr/0001-harness-agnostic-runtime.md)), so a version skew can
break gate detection in the fleet and never show in dev.

- **Policy = track-latest (default).** Point the knob at the native
  self-updating **symlink** so the fleet always runs exactly the version dev
  tests against — no promotion ritual:

  ```bash
  HIVE_CLAUDE_BINARY=/home/hezki/.local/bin/claude
  ```

  Unset, it defaults to the bare `"claude"` (legacy PATH-lookup behaviour).
- **The version is logged at every spawn.** Confirm which version the fleet
  actually resolved:

  ```bash
  journalctl --user -u hive.service | grep "on claude"
  # … PtySession: worker-3 on claude 2.1.162 (…/versions/2.1.162)
  ```

- **To freeze an exact version** (rarely needed): the native installer prunes
  old `versions/X` files, so a real freeze means the **npm** install — point the
  knob at the npm path (`/usr/bin/claude`) and bump deliberately with
  `npm i -g @anthropic-ai/claude-code@<version>`.

---

## 2. First-time setup

### Clone + install

```bash
cd ~/projects
git clone <repo-url> hive   # or already checked out
cd hive
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

The dev extras pull in `pytest`, `testcontainers[postgres]`, and `ruff`.

**Sprint 13 note**: `mcp` was added as a runtime dependency in `pyproject.toml`
(the per-entity `hive-knowledge` MCP server runs as a stdio subprocess; the
custom advisor server it once also carried was retired in Ticket 013). It is
installed automatically via `pip install -e .` — no manual step required.

### Create `.env`

Copy the template and fill in real values:

```bash
cp .env.example .env
$EDITOR .env
```

Required variables:

```bash
# Telegram
TELEGRAM_BOT_TOKEN=<from BotFather>
TELEGRAM_ALLOWED_USER_IDS=<your numeric Telegram user id>

# PostgreSQL (defaults match docker-compose.yml)
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5433
POSTGRES_DB=hive
POSTGRES_USER=hive
POSTGRES_PASSWORD=hive

# Claude Code binary the fleet spawns — point at the native self-updating
# symlink so the fleet tracks the version dev tests against (see
# "Claude Code version policy" in Section 1).
HIVE_CLAUDE_BINARY=/home/hezki/.local/bin/claude
```

`.env` is gitignored. Do not commit it.

---

## 3. Start PostgreSQL

Hive uses PostgreSQL via `asyncpg`. The repo ships a `docker-compose.yml`
that runs `pgvector/pgvector:pg16` on `127.0.0.1:5433` with a named volume
`hive_pgdata`:

```bash
docker compose up -d postgres
docker compose ps
```

Expected output: `hive-postgres` container is `Up (healthy)` with
`127.0.0.1:5433->5432/tcp`. The healthcheck uses `pg_isready`; it takes
~5–10s to go green on first start.

**Why Docker instead of `apt install postgresql`**: one `docker compose up`
command vs learning apt/systemd/pg_hba.conf/firewall config. The binding is
`127.0.0.1:5433` (not `0.0.0.0`) so the DB is not reachable from the
internet. Named volume `hive_pgdata` survives container restarts. Migration
path to managed PG (RDS, Supabase, Neon) later is a DSN swap.

### Verify connectivity

```bash
docker exec hive-postgres psql -U hive -d hive -c '\dt'
```

Initially the table list is empty — that's expected. Migrations run on the
first hive startup (Section 4).

---

## 4. Start the orchestrator

First run:

```bash
cd /home/hezki/projects/hive
source .venv/bin/activate
nohup python -m hive > data/hive.log 2>&1 &
echo "PID: $!"                      # shell wrapper PID (may die after launch)
sleep 3 && tail -25 data/hive.log   # watch startup
```

Expected log lines on a fresh install:

```
Starting Hive orchestrator...
Running migration 001_messages.sql
Running migration 002_entities.sql
Running migration 003_token_usage.sql
Running migration 004_tasks.sql
Running migration 005_audit_log.sql
Running migration 006_entity_session_id.sql
Running migration 007_entity_hierarchy.sql
Running migration 008_entity_modes.sql
Running migration 009_vault_actions.sql
Running migration 010_last_activity_at.sql
Running migration 011_blueprints_pgvector.sql
Running migration 012_mode_requests.sql
Running migration 013_task_retries.sql
Running migration 014_rename_loop_yolo.sql
Running migration 015_advisor_calls.sql
Running migration 016_embedding_dim_1024.sql
Telegram bridge started as a backup channel     # only with TELEGRAM_BOT_TOKEN
No TELEGRAM_BOT_TOKEN set: Telegram backup channel is off   # otherwise
Idle checker started (timeout=30m)
```

> **Cut-over upgrade (ADR 0033).** The first start after the cut-over applies
> migration `035_retire_maestro_runtime.sql`, which **permanently deletes** every
> persisted Maestro/Lead/Worker `entities` row, the `projects` table and the
> retired `entities` columns. Take a database backup first
> (`pg_dump hive > hive-pre-035.sql`). The Vault row, `tasks`, `token_usage`,
> `audit_log`, `vault_actions`, `mode_requests` and `messages` are untouched.
> The `worktrees/` directory is no longer managed; remove leftovers by hand.

Migrations are idempotent and tracked in `schema_migrations` — subsequent
startups skip already-applied ones silently.

### Web dashboard (Sprint 14)

The A.2 Paper Ops landing page lives at `/`. It's disabled by default —
set `HIVE_WEB_PORT` to a port to enable it:

```bash
HIVE_WEB_PORT=8080 python -m hive
```

Defaults & Tailscale binding:

- `HIVE_WEB_HOST=127.0.0.1` — **localhost-only by default**. Loopback
  bind means the service literally rejects connections from any other
  interface, including Tailscale, regardless of firewall rules.
- To make the dashboard reachable from your other devices, bind it to
  the **VPS's Tailscale IP** (find it with `tailscale ip -4`):
  ```bash
  HIVE_WEB_HOST=100.79.194.84  # this VPS's tailnet IP
  HIVE_WEB_PORT=8080
  ```
  Then from any other device in the same tailnet, browse to
  `http://100.79.194.84:8080/` (or the MagicDNS hostname,
  `http://ubuntu-s-4vcpu-8gb-sgp1-01:8080/`).
- **Why bind to the Tailscale IP, not `0.0.0.0`?** Defense in depth.
  `0.0.0.0` would also work today (ufw blocks public 8080), but binding
  to the Tailscale IP makes "Tailscale-only" a hard socket-level
  constraint — even if ufw was disabled, the public interface couldn't
  accept the traffic. The intent stays explicit in the config.
- **Note**: `tailfb3900.ts.net` is your tailnet *domain*, not a device
  hostname; it does not resolve to an IP on its own. Use the device's
  Tailscale IP or its MagicDNS short name.
- The page polls five htmx fragments — `/api/landing/{hero,vault,active,idle,dormant}`
  — at 30s / 15s / 5s / 5s / 30s respectively. JSON endpoints from
  earlier sprints (`/api/{status,org,tasks,cost,audit}`) remain
  available alongside.
- Static assets (`/static/landing.css`) are mounted from
  `src/hive/web/static/` — bumping the CSS file is hot-reloadable on
  browser refresh; no server restart needed for stylesheet-only changes.

Auth is deferred — do **not** flip `HIVE_WEB_HOST` to `0.0.0.0` until a
later sprint ships session-cookie or OAuth-based authentication.

### HTTPS for the PWA install (Ticket 040 / ADR 0023)

The home-screen PWA (Ticket 040) relies on a **service worker**, and a
service worker only runs in a **secure context** — `https://` or
`http://localhost`. Plain `http://100.79.194.84:8080/` is neither, so the
worker silently refuses to register and the install fails. Fix it by serving
the dashboard over HTTPS with **`tailscale serve`** (no app code, no extra
daemon, no public port — tailnet-only). See
[ADR 0023](adr/0023-https-via-tailscale-serve-for-pwa.md).

One-time setup on the VPS:

```bash
# 1. Enable MagicDNS + HTTPS certificates in the tailnet admin
#    (admin console → DNS → enable MagicDNS, then "HTTPS Certificates").

# 2. Re-bind uvicorn to loopback — tailscale serve fronts it, so the app
#    no longer needs to listen on the tailnet IP directly.
#    In .env:  HIVE_WEB_HOST=127.0.0.1   (HIVE_WEB_PORT stays 8080)

# 3. Reverse-proxy HTTPS → the local uvicorn (syntax varies by Tailscale
#    version; confirm with `tailscale serve --help` / `tailscale serve status`):
tailscale serve --bg https / http://127.0.0.1:8080

# 4. Apply and verify
systemctl --user restart hive.service
tailscale serve status        # shows the https://<node>.<tailnet>.ts.net mapping
```

Then browse — **and install** — from the iPad at the HTTPS MagicDNS name,
e.g. `https://ubuntu-s-4vcpu-8gb-sgp1-01.tailfb3900.ts.net/`. The first
request may take ~a minute while the cert is issued. This **replaces** the
plain-HTTP IP as the access surface; update the smoke URL below accordingly
(`https://<node>.<tailnet>.ts.net/...` instead of `http://100.79.194.84:8080/...`).

### Web Push notifications (Ticket 041 / ADR 0026)

Once the PWA is installed over HTTPS (above), Web Push lets Hive ping the
backgrounded iPad without Telegram. The channel is registered unconditionally
but is **inert until a VAPID keypair is configured** — so deploying 041 changes
nothing until you set the keys.

One-time setup on the VPS:

```bash
# 1. Generate a VAPID keypair (py-vapid ships with the pywebpush dependency):
.venv/bin/vapid --gen                      # writes private_key.pem + public_key.pem
.venv/bin/vapid --applicationServerKey     # prints the base64url public key (client side)

# 2. Put the keys in .env:
#   HIVE_VAPID_PUBLIC_KEY=<applicationServerKey from step 1>
#   HIVE_VAPID_PRIVATE_KEY=<private key (PEM contents or base64url)>
#   HIVE_VAPID_SUBJECT=mailto:you@example.com   # contact the push services require

# 3. Restart and confirm the channel comes up enabled (not "inert — no VAPID keys"):
systemctl --user restart hive.service
journalctl --user -u hive.service -n 30 | grep -i "web push"
```

Then, in the **installed** PWA on the iPad, allow notifications when prompted —
the client subscribes and POSTs to `/api/push-subscribe`. Background the app and
trigger a run-finished / decision event to confirm a banner arrives and
deep-links on tap.

**Turning Telegram's alert role down (only after parity is shown):** set
`HIVE_TELEGRAM_ALERTS=false` and restart. Telegram then stops relaying the
actionable alert-kinds (decisions, approvals, run-ended) — those arrive via Web
Push — while still relaying everything else as a debug/log surface. Leave it
`true` (the default) until the on-device push path is confirmed.

### Web write surface (Sprint 15 + 2026-04-26 polish)

Endpoints that accept input from the browser tab:

- `POST /api/command` — body `{"text": "/help"}`. Auth: `Authorization:
  Bearer $HIVE_WEB_TOKEN`. Routes through the same `CommandDispatcher`
  that Telegram uses, with `actor="web:user"`. Each round-trip is
  persisted to `MessageStore` (user → hive, hive → user) so chat
  history survives a page reload.
- `POST /api/mode-request/{id}/approve` — approve a pending mode
  elevation. Auth same as `/api/command`. Returns 404 if the row is
  missing or already resolved. Wired to the Allow button on inline
  mode-request bubbles.
- `POST /api/mode-request/{id}/deny` — symmetrical denial endpoint.
- `GET /sse/notifications` — `text/event-stream` of proactive events
  (mode requests, retries, escalations, daily summary…). Browsers'
  `EventSource` cannot set custom headers, so the gate also accepts the
  token via query string: `/sse/notifications?token=$HIVE_WEB_TOKEN`.
  Mode-request notifications now carry a structured `data` field that
  the browser dispatches to a clickable Allow/Deny bubble in the chat
  rail.
- `GET /api/messages?limit=20` — recent message history. **Open**
  (Tailscale bind is the gate); read-only.

**Setup**:

```bash
# Pick a long random string; the dashboard's chat input prompts the
# user for it on first send and caches it in sessionStorage.
HIVE_WEB_TOKEN=$(openssl rand -hex 32)
echo "HIVE_WEB_TOKEN=$HIVE_WEB_TOKEN" >> .env
systemctl --user restart hive.service
```

Smoke-test from any tailnet device:

```bash
curl -X POST http://100.79.194.84:8080/api/command \
  -H "Authorization: Bearer $HIVE_WEB_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"text": "/help"}'
```

**Empty `HIVE_WEB_TOKEN` disables writes entirely** — the dependency
rejects every request unconditionally rather than match-anything. Read
endpoints (landing GET, htmx fragments, `/api/messages`) keep working.

### Email digest (Sprint 15)

Off-line reach for proactive notifications. Disabled by default. With
`HIVE_EMAIL_ENABLED=true` and `HIVE_EMAIL_TO` set the channel buffers
events and flushes when either threshold trips:

- `HIVE_EMAIL_DIGEST_BUFFER_SIZE=20` — flush after N events.
- `HIVE_EMAIL_DIGEST_INTERVAL_MINUTES=60` — flush after T minutes.

**Console mode**: when `HIVE_SMTP_HOST` is unset the digest logs the
rendered body instead of sending. Useful on dev hosts and as a smoke
test before wiring real SMTP. Logs land in `journalctl --user -u
hive.service -g "Email digest"`.

For a real SMTP backend (Gmail app password, Mailgun, AWS SES, etc.):

```bash
HIVE_EMAIL_ENABLED=true
HIVE_EMAIL_TO=you@example.com
HIVE_SMTP_HOST=smtp.example.com
HIVE_SMTP_PORT=587
HIVE_SMTP_USER=apikey-or-user
HIVE_SMTP_PASSWORD=...
```

### File transit (Sprint 17)

Telegram and the web composer can both transfer files onto the VPS for
agents to read. Files land in `data/uploads/{uuid}{ext}` (outside
`BLUEPRINTS_DIR` — these are raw uploads, not embedded blueprints) and
the database `attachments` table records every upload as an audit row.

**Caption / text routing**:

- `/m:<entity> <text>` (e.g. `/m:dev summarize this`) → file is stored
  *and* the target entity receives a prompt with a prepended
  `[Attached file: /abs/path (mime, N bytes, original: name.ext)]`
  block. Yolo permission mode
  lets Claude Code's `Read` tool open the absolute path with no
  per-file prompt.
- Empty caption → file is stored only. Telegram replies with a
  "📎 File received and stored" hint; the web returns
  `{"id": N, "forwarded_to": null}`.
- Non-routable caption (e.g. `/status`) → file is stored, no routing.

**Size cap**: `HIVE_UPLOAD_MAX_BYTES` (default 20 MB, mirrors the
Telegram Bot API hard limit). Telegram replies with a size error;
the web `POST /api/upload` aborts mid-stream with HTTP 413 and removes
the partial file.

**Audit / inspection**: `/files [N]` (Telegram or web composer) lists
the most recent uploads (default 20, max 100):

```
Recent attachments (last 3):
  #42 2026-04-29 14:32 telegram →dev image/jpeg 1.2MB photo.jpg
  #41 2026-04-29 14:30 web      →—  application/pdf 3.4MB report.pdf
  #40 2026-04-29 14:28 telegram →qa text/plain  812B  notes.txt
```

`→—` means no entity received the file (caption was empty or
non-routable). `psql … -c "SELECT id, source, mime_type, forwarded_to
FROM attachments ORDER BY id DESC LIMIT 5"` is the SQL-level
equivalent.

**Smoke test (post-deploy)** — from the Tailscale URL
`http://100.79.194.84:8080/`, not loopback:

1. Telegram: send a photo with caption `/m:dev describe this image` →
   expect dev's response referencing the image content.
2. Telegram: send a PDF with no caption → expect "📎 File received"
   reply; `/files 5` shows it with `→—`.
3. Web: paperclip → pick PDF → text `/m:dev summarize` → response
   renders in the chat panel.
4. `ls data/uploads/` shows the four uuid-named files.

Out of scope for Sprint 17 (deferred to Sprint 18+): multi-file
messages, EXIF stripping, file expiry, `/files search` command. See
`docs/PROJECT_PLAN.md` for the full deferred list.

### File embeddings (Sprint 18 + chunking from Sprint 28)

Uploaded files are embedded into the same Voyage `voyage-multimodal-3`
1024d vector space as blueprints, so semantically-relevant attachments
are auto-retrieved into agent prompts the same way past blueprints are.
Sprint 28 split text/PDF embedding into `attachment_chunks` (one row
per chunk) so long uploads no longer truncate at 8000 chars and
retrieval ranks against the matching section.

**Schema** (migrations `018_attachment_embeddings.sql` →
`024_attachment_chunks.sql`): the parent `attachments` table holds
metadata only; `attachment_chunks (id, attachment_id, chunk_index,
text, embedding vector(1024))` owns embeddings. HNSW cosine index on
`attachment_chunks.embedding`. Partial null-embedding index
`WHERE embedding IS NULL` for the rechunk path. Sprint 24 dropped the
parent `embedding` and `embed_text` columns; the 11 pre-existing rows
were re-embedded by `scripts/rechunk_attachments.py`.

**Embedding strategy by mime type**:

- `image/*` → PIL opens the file, thumbnails to 1024×1024 (Voyage caps
  at ~16MP / 10MB), then `embed_multimodal([[image]])`. Images stay
  one chunk each — the chunker doesn't apply.
- `application/pdf` → `pypdf.PdfReader` joins page text, soft-truncates
  to `HIVE_ATTACHMENT_EMBED_MAX_CHARS` (default 32000) as a guard
  against monstrously-long PDFs, then `split_blueprint(target=
  HIVE_ATTACHMENT_CHUNK_TOKENS, overlap=
  HIVE_ATTACHMENT_CHUNK_OVERLAP_TOKENS)` → batched `embed_texts(chunks)`.
  Encrypted or image-only PDFs return empty text and are skipped (no
  chunks written).
- `text/*` → bytes decoded utf-8 with `errors="replace"`, soft-truncated,
  then chunked + embedded the same way.
- Other mime types → skipped; no chunks written.

**Failure isolation**: chunk-embedding runs *after*
`attachment_store.save()` in both upload paths (`web/app.py`,
`telegram/bridge.py`). If `embed_attachment` raises (Voyage outage,
`VOYAGE_API_KEY` revoked, malformed PDF), the row stays in the
database with no chunks and the user-facing upload still succeeds.
The row can be re-chunked later via `rechunk_attachments.py`.

**Auto-retrieve renders two labeled blocks** when both blueprints and
attachments match:

```
Relevant past blueprints (retrieved automatically):
### {title}
{chunk_text}

---

Relevant uploaded files (retrieved automatically):
- /abs/path (mime, original: name) — snippet: "{matched chunk text}"

---

{user prompt}
```

Blueprints get consumed inline; files surface as paths the agent must
`Read` itself (the Yolo permission default lets the absolute path open
without per-file prompts). Snippets now show the matching chunk text
instead of a 200-char `embed_text` prefix, so the agent sees *what*
in the file matched the query before deciding to open it.

**Re-chunk / backfill** — for any row that has no chunks (Voyage
outage during upload, fresh chunking knob, embedding-provider swap),
run the idempotent re-chunk script:

```bash
cd /home/hezki/projects/hive
.venv/bin/python scripts/rechunk_attachments.py
```

Output:

```
Found 11 attachment rows
Re-chunked #1 (/abs/path/...png) → 1 chunks
Re-chunked #12 (/abs/path/be4c9bc5...pdf) → 3 chunks
Re-chunked #13 (/abs/path/f7912b84...md) → 2 chunks
...
Done. Re-chunked=11 Skipped=0
```

The script iterates over every attachment row, runs the file through
`embed_attachment`, then `save_chunks` (which DELETEs existing chunks
+ INSERTs the new ones in one transaction). Safe to re-run.

### Dashboard tab (Sprint 20)

The landing's long-reserved **Dashboard** tab placeholder now points
to a working observability surface at `/dashboard` — 8 widgets
covering cost burn, token mix, cache efficiency, audit stream,
backlog, and system health. Same Tailscale bind as the landing; the
JSON API behind it is bearer-token gated.

**Access path** (Tailscale URL, not loopback):

```
http://100.79.194.84:8080/dashboard
```

**Widgets currently wired to live telemetry**:

- W1 30-day cost ribbon — per-DOW median ± stdev anomaly envelope
  derived from `token_usage.recorded_at` + `cost_usd`.
- W4 token burn (1h/24h/7d/30d range switcher) — input/output/cache
  mix in time-bucketed slices.
- W5 entity × model cost matrix — sparse `{entity: {model: cost}}`
  for the last 24h.
- W6 cache hit rate — per-entity hit %, 7-day daily sparkline.
- W7 audit timeline — 60-bucket histogram (1 min each) of
  `audit_log.action` namespaces (`command`, `entity`, `task`, `git`).

**Widgets with mock/derived data** (TODO Sprint 21+):

- W2 system health — all 5 strips hardcoded `ok` until probes land
  (postgres ping, claude API liveness, disk %, heartbeat gap).
- W3 workload CFD — basic 7-day stacked series from `tasks.status`,
  no real anomaly detection yet.
- W8 failure scatter — empty until a `task.failure_reason` classifier
  ships.

**JSON API** (token-gated):

```bash
curl http://100.79.194.84:8080/api/dashboard/all \
  -H "Authorization: Bearer $HIVE_WEB_TOKEN"
```

Returns the entire `window.HIVE_DASH` payload (16 keys: `cost30`,
`health`, `sankey`, `p0p1Backlog`, `cfd`, `burn`, `burnEvents`,
`matrix`, `cacheRows`, `cacheOverall`, `histogram`, `auditFeed`,
`failures`, `failuresSummary`, `entitiesY`, `lastUpdated`). The
landing's chat token (set in sessionStorage on first send) is reused
for the dashboard's 30s polling — no separate token to configure.

**Refresh behaviour**: 30s `setInterval`. Toggle off via the
auto-refresh chip in the UI to halt polling. First paint is
server-rendered (`window.HIVE_DASH = {{ data | tojson }}`) so the
dashboard renders instantly without an API round-trip — useful for
dropping in to verify widgets without a token.

**Renamed templates**: `templates/dashboard.html` (Sprint 14 landing)
was renamed to `templates/landing.html` to free the canonical name
for the new dashboard route. The landing still serves at `/`; only
the file name changed.

**JSX assets** (Babel-in-browser, no build step):

```
src/hive/web/static/dashboard/dashboard-shell.jsx
src/hive/web/static/dashboard/dashboard-w1234.jsx
src/hive/web/static/dashboard/dashboard-w5678.jsx
src/hive/web/static/dashboard/refresh.js
src/hive/web/static/dashboard/dashboard.css
```

Edit any of these and refresh the browser — Babel transpiles in
the page on every load. No server restart needed.

### Find the actual python PID

`$!` from `nohup … &` points at the shell wrapper, which often dies right
after launch (reparenting the python process to init). Use `pgrep`:

```bash
pgrep -af 'python -m hive'
```

The row with `python -m hive` as the exact command (not a wrapper bash
line) is the one to kill later.

---

## 4.5 Backups (Sprint 29)

Hive's only persistent state is the `hive-postgres` Docker volume
(`hive_pgdata`). A bad migration, accidental `DELETE`, or `docker volume
rm` would wipe every blueprint, attachment, audit-log row, and entity
record. Backups defend against that.

### Daily logical backup (pg_dump)

A systemd-user timer (`hive-backup.timer`) runs at **03:30 UTC** every
day and dumps the database to `~/backups/hive/<UTC-timestamp>.sql.gz`.
The script (`scripts/backup_postgres.sh`) prunes anything older than 14
days, so the directory stays at ~14 files.

The dump runs *inside* the `hive-postgres` container (`docker exec
hive-postgres pg_dump …`), guaranteeing the dumper version always
matches the server.

#### One-time install

```bash
systemctl --user daemon-reload
systemctl --user enable --now hive-backup.timer
```

#### Verify it's healthy

```bash
# Next-fire time
systemctl --user list-timers --all | grep hive-backup

# Last run + journal output
systemctl --user status hive-backup.service
journalctl --user -u hive-backup -n 30 --no-pager

# What's on disk
ls -lh ~/backups/hive/
```

#### Manual fire (smoke test)

```bash
systemctl --user start hive-backup.service
# A new ~/backups/hive/<timestamp>.sql.gz appears within seconds.
```

#### Override location

Set `HIVE_BACKUP_DIR` in `.env` to write somewhere other than
`~/backups/hive/`. The systemd unit re-reads `.env` at each run, so no
restart is needed after editing.

#### Configuration knobs

| Variable | Default | Purpose |
| --- | --- | --- |
| `HIVE_BACKUP_DIR` | `~/backups/hive` | Output directory for `*.sql.gz` files |
| (retention) | 14 days | Hard-coded in `scripts/backup_postgres.sh` (`-mtime +14`) |
| (schedule) | `03:30 UTC` daily | Hard-coded in `~/.config/systemd/user/hive-backup.timer` |

> **Restore procedure** lives in §8 (Troubleshooting → Restore from
> backup).

---

## 5. Normal operations

### Tail logs

```bash
tail -f /home/hezki/projects/hive/data/hive.log
```

The log is append-only; rotate or truncate manually if it grows too large.

### Graceful shutdown

```bash
kill $(pgrep -f 'python -m hive' | tail -1)
```

Send **SIGTERM** (`kill`, not `kill -9`). The orchestrator installs a
signal handler that runs `bridge.stop()`, `process_manager.stop_all()`,
and `store.close()` so the asyncpg pool closes cleanly. SIGKILL strands
connections.

Since Sprint 21 Phase 1 (2026-05-04), shutdown calls `stop_all()` instead
of `kill_all()`: subprocesses are terminated but entity rows + session_ids
are preserved in the DB so they restore on the next boot. `/kill <entity>`
keeps its hard-delete semantics for explicit removal.

Expected shutdown log tail:

```
Shutting down...
Application.stop() complete
Telegram bridge stopped
Stopped 1 entity sessions for restart
Hive stopped.
```

### Restart

```bash
kill $(pgrep -f 'python -m hive' | tail -1)
sleep 2 && pgrep -af 'python -m hive' | grep -v pgrep    # expect empty
source .venv/bin/activate
nohup python -m hive > data/hive.log 2>&1 &
sleep 3 && grep -E 'Restored|Running migration' data/hive.log
```

Persisted entities (the Vault) survive restart. Subsequent startup logs show
`Restored persisted entity: <name>`.

### Telegram commands (full list)

Telegram is an optional backup channel (enabled by `TELEGRAM_BOT_TOKEN`). The
typed approval commands stay until the desk covers vault and mode approvals.

**Status & monitoring:**
`/status`, `/health`, `/cost [24h|7d|30d]`, `/quota`, `/audit [prefix]`, `/files [N]`

**Messaging:**
`/m:<entity> <msg>`, `/kill <entity>`. Plain text with no `/m:` target is
answered with a pointer to the desk (there is no default chat agent).

**Tasks:**
`/task add "<title>"`, `/task done|cancel <id>`, `/tasks`

**Configuration:**
`/mode <yolo|yotree> [entity]`, `/model <opus|sonnet|haiku|opusplan|fable> [entity]`

> `opusplan` is a Claude Code alias: the planning phase uses Opus and execution
> uses Sonnet. Pass it exactly as `opusplan` to `/model`. Selecting an API-billed
> model prints a one-line billing warning; the API-billed set lives in one place
> and is empty today.

**Heartbeat:**
`/heartbeat on|off|status|<minutes>` — periodic status pings.

> `yolo` and `yotree` both pass `--dangerously-skip-permissions` to the
> Claude CLI. Entities cannot elevate themselves — they emit a
> `request_mode_change` hive action, which always routes to the user. The user
> resolves with `/approve mode <id>` or `/deny mode <id> [reason]`.

**Operations:**
`/compact <entity>`, `/reset <entity>`

**Approvals:** `/approve [mode <id>]`, `/deny mode <id> [reason]`

**Vault:** `/vault approve|deny|status|log <id>`

> `/vault approve <id>` routes through `process_manager.approve_vault_action`,
> which runs the daily/monthly spend-cap check and (on cap pass) executes the
> action against the configured `PaymentProvider`. The reply tells you the
> terminal status: `executed`, `failed`, `denied` (cap exceeded), or `approved`
> (generic actions without payment fields). `/vault deny <id> [reason]` records
> the optional reason and audits `vault.denied`. The legacy web app shows a
> `vault_action_pending` Allow/Deny bubble for every `vault.requested` event.
> The Vault is off by default (`HIVE_VAULT_ENABLED`) and only a stub provider
> exists; no real provider, account or bank details are connected.

**Blueprints:** `/blueprint save|search|list` — save a new blueprint, semantic search over past blueprints, list all

**Help:** `/help` lists all commands grouped by category; `/help <command>` shows detailed usage for one command.

When `AUTO_RETRIEVE_ENABLED=true` (default), the top-K semantically-similar
blueprints are also prepended as context to every prompt sent to any entity
(any entity) — no role gating.

**Sprint 27 — knowledge as a skill.** Auto-retrieve is now a thin safety
net: top_k=1, max_distance=0.5 (tighter than the prior 0.6), and (with
`AUTO_RETRIEVE_FIRST_TURN_ONLY=true`, the default) it fires only on the
first prompt of a fresh entity activation. Subsequent turns rely on the
agent calling the new `search_knowledge` MCP tool itself. The auto-block
also ends with a one-line nudge so agents know the tool exists.

**Blueprint chunking (Sprint 26):** Blueprint bodies are split into
~`HIVE_BLUEPRINT_CHUNK_TOKENS`-sized chunks (default 500) at save time;
each chunk gets its own Voyage embedding row in `blueprint_chunks`.
Auto-retrieve ranks against the chunk vectors and prepends only the
matching chunk under `### {title}` instead of the full body — sharper
context, less prompt bloat. Short bodies (under `tokens × 1.6` chars)
stay as a single chunk so personal notes don't get fragmented.

If you bump `HIVE_BLUEPRINT_CHUNK_TOKENS` (or `EMBEDDING_MODEL`), run the
idempotent rechunker to re-align existing rows:

```bash
.venv/bin/python -m scripts.rechunk_blueprints
# or
.venv/bin/python scripts/rechunk_blueprints.py
```

It deletes each blueprint's chunks, re-splits via current settings,
re-embeds, and bulk-inserts under one transaction per blueprint.

---

## 6. Verification commands

Schema + migration state:

```bash
docker exec hive-postgres psql -U hive -d hive -c '\dt'
docker exec hive-postgres psql -U hive -d hive -c \
  'SELECT version, applied_at FROM schema_migrations ORDER BY version'
```

Entity roster:

```bash
docker exec hive-postgres psql -U hive -d hive -c \
  'SELECT name, role, state, model, pid, updated_at FROM entities'
```

Recent messages:

```bash
docker exec hive-postgres psql -U hive -d hive -c \
  "SELECT id, sender, recipient, substring(content, 1, 50), status, timestamp
   FROM messages ORDER BY id DESC LIMIT 10"
```

Table column detail:

```bash
docker exec hive-postgres psql -U hive -d hive -c '\d+ entities'
docker exec hive-postgres psql -U hive -d hive -c '\d+ messages'
docker exec hive-postgres psql -U hive -d hive -c '\d+ token_usage'
docker exec hive-postgres psql -U hive -d hive -c '\d+ tasks'
docker exec hive-postgres psql -U hive -d hive -c '\d+ audit_log'
```

Token usage totals (matches `/cost`):

```bash
docker exec hive-postgres psql -U hive -d hive -c \
  "SELECT entity_name, sum(input_tokens) AS in_tok,
          sum(output_tokens) AS out_tok, sum(cost_usd) AS cost
   FROM token_usage
   WHERE recorded_at > NOW() - INTERVAL '24 hours'
   GROUP BY entity_name"
```

Task queue state:

```bash
docker exec hive-postgres psql -U hive -d hive -c \
  "SELECT id, title, status, priority, assigned_to, created_by
   FROM tasks ORDER BY status, priority, created_at"
```

Audit event distribution (useful sanity check — should show a healthy
mix of `command.*`, `entity.*`, and `task.*`):

```bash
docker exec hive-postgres psql -U hive -d hive -c \
  "SELECT action, count(*) FROM audit_log GROUP BY action ORDER BY action"
```

Recent audit trail:

```bash
docker exec hive-postgres psql -U hive -d hive -c \
  "SELECT timestamp, actor, action, target FROM audit_log
   ORDER BY timestamp DESC LIMIT 20"
```

---

## 7. Running the test suite

Tests use a session-scoped `testcontainers` PostgreSQL container, separate
from the live docker-compose one — they don't touch the dev DB:

```bash
.venv/bin/python -m pytest tests/ -v
```

Initial run pulls the `pgvector/pgvector:pg16` image (~200 MB — the pgvector
image is larger because it bundles the extension). Subsequent runs reuse the
cached image; a full suite takes ~14s.

Style:

```bash
.venv/bin/python -m ruff check src/ tests/
.venv/bin/python -m ruff format src/ tests/
```

---

## 8. Troubleshooting

### Orchestrator won't start

Tail `data/hive.log`, read the actual error. Common culprits in order of
frequency:

1. **PG container stopped** — `docker compose up -d postgres` and wait for
   healthy.
2. **`.env` missing POSTGRES_* vars** — `config.py` has sensible defaults
   (`127.0.0.1:5433/hive` as user `hive`) but if you set a partial override
   (e.g. only `POSTGRES_HOST`), the DSN can get malformed. Check
   `src/hive/config.py:23-31`.
3. **Stale venv without asyncpg** — `pip install -e ".[dev]"` again.
4. **Port 5433 already in use** — another service grabbed it. Either stop
   that service or change `POSTGRES_PORT` in `.env` and the published port
   in `docker-compose.yml`.

### Migration failed mid-run

Inspect `schema_migrations` and the partially-created table:

```bash
docker exec hive-postgres psql -U hive -d hive -c 'SELECT * FROM schema_migrations'
```

Clean a bad version manually:

```bash
docker exec hive-postgres psql -U hive -d hive -c \
  'DELETE FROM schema_migrations WHERE version = N; DROP TABLE IF EXISTS <table>'
```

Then restart hive — the migration runner will re-apply.

### Telegram bridge can't poll (409 Conflict)

Only one process can poll a given bot. Symptoms: log shows
`telegram.error.Conflict: terminated by other getUpdates request`. Kill any
other process holding the same token:

```bash
pgrep -af telegram
pgrep -af openclaw
```

On this VPS, the OpenClaw systemd service used to conflict — it's now
stopped and disabled (`sudo systemctl stop openclaw && sudo systemctl
disable openclaw`).

### Restore from backup (Sprint 29)

The daily timer (`hive-backup.timer`, see §4.5) writes
`~/backups/hive/<UTC-timestamp>.sql.gz` files, 14-day retention. Restore
flow when the live database is corrupt, mid-bad-migration, or
accidentally truncated:

```bash
# 1. Stop the orchestrator so it doesn't write to the DB during restore.
systemctl --user stop hive.service

# 2. Pick a dump.
ls -lt ~/backups/hive/
DUMP=~/backups/hive/2026-05-09T001825Z.sql.gz   # adjust

# 3. Drop + recreate the live DB (destructive — keep a "before" dump
#    first if you want a return path):
docker exec hive-postgres pg_dump -U hive -d hive --no-owner --no-acl \
    | gzip -9 > ~/backups/hive/pre-restore-$(date -u +%Y-%m-%dT%H%M%SZ).sql.gz
docker exec hive-postgres psql -U hive -d postgres -c "DROP DATABASE hive;"
docker exec hive-postgres psql -U hive -d postgres -c "CREATE DATABASE hive;"

# 4. Pipe the dump back in.
gunzip -c "$DUMP" | docker exec -i hive-postgres psql -U hive -d hive -q

# 5. Sanity-check (counts should match the pre-incident state):
docker exec hive-postgres psql -U hive -d hive -c \
    "SELECT (SELECT COUNT(*) FROM blueprints) AS blueprints,
            (SELECT COUNT(*) FROM attachments) AS attachments,
            (SELECT COUNT(*) FROM messages) AS messages;"

# 6. Restart hive.
systemctl --user start hive.service
```

The dump is captured with `--no-owner --no-acl`, so it replays cleanly
into any fresh `hive` database without role/grant errors. Migrations
embedded in the dump bring `schema_migrations` along, so subsequent
`run_migrations` calls are no-ops on the restored DB.

**Verification before restoring** — pipe the dump into a throwaway DB
first if you want zero-risk validation:

```bash
docker exec hive-postgres psql -U hive -d postgres -c "CREATE DATABASE hive_restore_test;"
gunzip -c "$DUMP" | docker exec -i hive-postgres psql -U hive -d hive_restore_test -q
docker exec hive-postgres psql -U hive -d hive_restore_test -c "SELECT COUNT(*) FROM messages;"
docker exec hive-postgres psql -U hive -d postgres -c "DROP DATABASE hive_restore_test;"
```

### Fresh-start the database

**Destructive — wipes all hive data:**

```bash
docker compose down
docker volume rm hive_pgdata
docker compose up -d postgres
# next `python -m hive` will re-run all migrations
```

Insurance: the pre-port SQLite DB is backed up at
`/home/hezki/projects/hive/data/hive.db.sqlite-bak` (22 messages, no
entities — Sprint 0+1 state). There's no SQLite → PG migration script;
treat the backup as reference-only.

### Roll back a bad commit

```bash
git log --oneline -10              # find the last known-good commit
git reset --soft HEAD~N            # drops N commits, keeps working tree
```

Never `git reset --hard` unless you've verified there's nothing you want
in the working tree.

---

## 9. Configuration reference

All env vars are read in `src/hive/config.py`. Defaults in parentheses.

| Variable | Default | Purpose |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | *(none)* | Bot API token (from BotFather) — optional; Telegram (backup channel) is off without it |
| `TELEGRAM_ALLOWED_USER_IDS` | *(none)* | Comma-separated numeric Telegram user IDs |
| `POSTGRES_HOST` | `127.0.0.1` | PG host |
| `POSTGRES_PORT` | `5433` | PG port — matches `docker-compose.yml` |
| `POSTGRES_DB` | `hive` | DB name |
| `POSTGRES_USER` | `hive` | User |
| `POSTGRES_PASSWORD` | `hive` | Password |
| `HIVE_DEFAULT_MODEL` | unset | Explicit model for the startup Vault; omitted values use serving harness role defaults |
| `HIVE_MAX_SESSIONS` | `3` | Process manager concurrency cap |
| `HIVE_WEB_PORT` | `0` | Web dashboard port (0 = disabled) |
| `HIVE_WEB_HOST` | `127.0.0.1` | Web dashboard bind address. Set to the VPS's Tailscale IP (e.g. `100.79.194.84`) for tailnet-only access from other devices. Keep off `0.0.0.0` until auth lands (deferred past Sprint 14). |
| `HIVE_AUTO_COMPACT_ENABLED` | `true` | Auto-compact entities when context exceeds threshold |
| `HIVE_AUTO_COMPACT_THRESHOLD` | `50000` | Measured final model-call input context that triggers auto-compact; unavailable context (including Codex JSON turns) skips automatic compaction |
| `HIVE_AUTO_KILL_IDLE_ENABLED` | `true` | Kill entities inactive beyond timeout |
| `HIVE_IDLE_TIMEOUT_MINUTES` | `30` | Minutes of inactivity before auto-kill |
| `HIVE_DAILY_SUMMARY_ENABLED` | `true` | Send daily Telegram summary |
| `HIVE_DAILY_SUMMARY_HOUR` | `23` | UTC hour for daily summary (23 = 9am AEST) |
| `HIVE_SUMMARY_CHAT_ID` | *(none)* | Telegram chat ID for proactive notifications |
| `VOYAGE_API_KEY` | *(none)* | Voyage AI API key — required for blueprint embeddings + semantic search + auto-retrieve. Get one at https://dash.voyageai.com/ |
| `EMBEDDING_MODEL` | `voyage-multimodal-3` | Voyage embedding model name. Multimodal-only model — pure text inputs are wrapped as single-segment docs internally |
| `EMBEDDING_DIM` | `1024` | Embedding vector dimension (must match model). Changed from 1536 → 1024 in Sprint 16 |
| `HIVE_BLUEPRINT_CHUNK_TOKENS` | `500` | Target tokens per chunk when splitting a blueprint body before embedding (Sprint 26). ~4 chars/token heuristic. Long bodies fan out; bodies under `tokens × 1.6` chars stay as one chunk. |
| `HIVE_BLUEPRINT_CHUNK_OVERLAP_TOKENS` | `50` | Tail of chunk N prepended to chunk N+1 so a fact straddling a boundary still appears in one full chunk (Sprint 26). |
| `AUTO_RETRIEVE_ENABLED` | `true` | Prepend top-K blueprints to entity prompts. Sprint 27 dialled this into a thin safety net (see below). |
| `AUTO_RETRIEVE_TOP_K` | `1` | Number of blueprints (and attachments) to retrieve per kind. Default lowered from 3 → 1 in Sprint 27 — agents call `search_knowledge` for more. |
| `AUTO_RETRIEVE_MAX_DISTANCE` | `0.5` | Maximum cosine distance for an auto-retrieved blueprint. 0=identical, 1=orthogonal. Default tightened from 0.6 → 0.5 in Sprint 27. |
| `AUTO_RETRIEVE_FIRST_TURN_ONLY` | `true` | When true (default), auto-retrieve only fires on the first prompt of a fresh entity activation (signalled by `entity.session_id is None`). Subsequent turns rely on the agent calling `search_knowledge` itself (Sprint 27). |
| `HIVE_KNOWLEDGE_MCP_ENABLED` | `true` | When true (default), spawns the per-entity `hive-knowledge` MCP server, exposing `search_knowledge(query, kind, limit)` to entities (Sprint 27). Since Ticket 013 it is the *only* MCP server; when `false`, no `--mcp-config` is passed at all. |
| `HIVE_HEARTBEAT_ENABLED` | `false` | `true` to enable periodic status pings to Telegram. |
| `HIVE_HEARTBEAT_INTERVAL_MINUTES` | `30` | Ping interval in minutes. |
| `HIVE_WEB_TOKEN` | *(empty)* | Bearer token for the web write surface (Sprint 15). Empty disables `POST /api/command` and `/sse/notifications` entirely. |
| `HIVE_EMAIL_ENABLED` | `false` | Enable the email digest channel (Sprint 15). |
| `HIVE_EMAIL_TO` | *(empty)* | Recipient address for digests. Required when enabled. |
| `HIVE_SMTP_HOST` | *(empty)* | SMTP server. Empty triggers console mode (digest is logged, not sent). |
| `HIVE_SMTP_PORT` | `587` | SMTP port (starttls). |
| `HIVE_SMTP_USER` / `HIVE_SMTP_PASSWORD` | *(empty)* | SMTP auth credentials. |
| `HIVE_EMAIL_DIGEST_INTERVAL_MINUTES` | `60` | Time-based flush trigger for the digest. |
| `HIVE_EMAIL_DIGEST_BUFFER_SIZE` | `20` | Size-based flush trigger for the digest. |
| `HIVE_UPLOAD_MAX_BYTES` | `20971520` (20 MB) | Cap for Telegram + web file uploads (Sprint 17). Mirrors Telegram's 20 MB Bot API limit. |
| `HIVE_ATTACHMENT_EMBED_MAX_CHARS` | `32000` | Soft cap on the extracted text length per PDF/text upload before chunking (Sprint 18, raised from 8000 in Sprint 28). Anything longer is head-truncated to keep the chunker bounded. |
| `HIVE_ATTACHMENT_CHUNK_TOKENS` | `500` | Target tokens per chunk for PDF and text-file uploads (Sprint 28). Reuses the Sprint 26 markdown-aware splitter. |
| `HIVE_ATTACHMENT_CHUNK_OVERLAP_TOKENS` | `50` | Tail of chunk N prepended to chunk N+1 so a fact straddling a boundary still appears in one full chunk (Sprint 28). |
| `HIVE_AUTO_RETRIEVE_INCLUDE_ATTACHMENTS` | `true` | Prepend the "Relevant uploaded files" block alongside blueprints in auto-retrieve (Sprint 18). |
| `HIVE_VAULT_ENABLED` | `false` | Auto-register a default `vault` entity on startup and wire the Vault payment pipeline (Sprint 25). Off by default — no real provider yet. |
| `HIVE_VAULT_CAP_CURRENCIES` | `AUD,USD` | Comma-separated allow-list of currencies the cap accepts. Caps are applied **per currency independently** — a $50/day cap means $50 AUD/day AND $50 USD/day, no FX. Action currencies outside this list are rejected at cap-check time. |
| `HIVE_VAULT_DAILY_CAP_CENTS` | `5000` ($50) | Daily spend cap (rolling 24h) enforced when approving a `request_payment` action. Applies to each currency in the allow-list separately. Set to `0` to disable. |
| `HIVE_VAULT_MONTHLY_CAP_CENTS` | `50000` ($500) | Monthly spend cap (rolling 30d). Same per-currency-independent semantics as the daily cap. Set to `0` to disable. |
| `HIVE_VAULT_PROVIDER` | `stub` | Payment provider name. Sprint 25 ships only `stub`. Unknown names fall back to stub with a warning. |

If `TELEGRAM_BOT_TOKEN` is empty/unset, hive does not start the Telegram bridge;
the Vault rail, notification channels and the legacy web app (`HIVE_WEB_PORT`)
still run. There is no local readline CLI fallback any more (removed at the
cut-over, ADR 0033).

Daily summary and proactive notifications require `HIVE_SUMMARY_CHAT_ID`
to be set. You can find your chat ID by sending a message to the bot and
checking the audit log.

Without `VOYAGE_API_KEY`, `/blueprint save|search` and auto-retrieve silently
become no-ops — Hive still boots.

---

## 10. Known limitations (as of 2026-04-26)

- **Headless first, PTY fallback** — turns run as one `codex exec` / `pi -p` / `claude -p`
  subprocess each (ADR 0029; Ticket 007's PTY-only runtime is superseded). The
  Claude PTY session spawns lazily, only when headless is refused or out of
  quota; conversation context carries across modes via `--resume`/`--continue`.
  Entity state still goes `idle` between turns — this is expected, not a bug.
- **`/cost` shows token counts, not dollars** — the PTY path is plan-billed,
  so per-turn `cost_usd` is `None`; token counts are the real accountability
  number. (Ticket 013 retired the advisor's one-shot `claude -p`; native
  `/advisor` is plan-billed in-session, so no metered call remains.)
- **Harness coverage** — Codex, Claude Code, and Pi. OpenCode (T016) and direct
  model-API support are not built. Failover between harnesses is automatic only
  for refusals (auth/quota/refused/unavailable), including unsupported Codex
  models before tool activity. Turns with Codex tool activity are not replayed.
  Codex reports accounting tokens per turn through its
  JSON stream; it does not provide Hive a numeric remaining-quota reading.
  `/quota` and its threshold alerts still describe the Claude plan only.
- **Blueprints require `VOYAGE_API_KEY`** — without it, `/blueprint save|search`
  and auto-retrieval of blueprints into agent prompts are disabled silently.
  Hive still boots, but these features are no-ops. (Switched from OpenAI →
  Voyage `voyage-multimodal-3` in Sprint 16 for joint text+image support.)
- **Web dashboard auth is bearer-token only** — Sprint 15 added a
  shared `HIVE_WEB_TOKEN` to gate `POST /api/command`, the mode-request
  approve/deny endpoints, and `GET /sse/notifications`. Read endpoints
  (the landing page itself, htmx fragments, `/api/messages`) stay open
  and rely on the Tailscale bind as the network-level gate. Multi-user
  OAuth/sessions still deferred. Do not flip `HIVE_WEB_HOST` to
  `0.0.0.0` until that lands.
- **`/api/command` is synchronous** — each call spawns a fresh `claude
  -p` and blocks the request until the response lands (5–30s typical).
  The browser paints an optimistic user bubble + typing indicator
  immediately, but the underlying long wait is real. A job-id + SSE
  streaming rewrite is deferred.
- **Daily summary timing** — the scheduler checks once per hour, so the
  summary may fire up to 59 minutes after the configured hour if the
  process restarts mid-cycle.

## Gateway (desk, ADR 0030)

`python -m hive.gateway` serves the desk (Home, Project and Chat pages)
on `127.0.0.1:8480` only; the bind is not configurable. It needs no database
and no running Hive process; the one piece of Hive code it uses is the headless
Claude adapter, imported lazily to write item descriptions (below). Each page load runs firstmate's
`$HIVE_GATEWAY_FM_HOME/bin/fm-fleet-snapshot.sh --json` (cached 5 s) and
pins schema major `fm-fleet-snapshot.v1`; any other schema, a failed run or
bad JSON shows a read-only fallback banner with every write button removed
(and every write refused). It never edits a backlog or any firstmate file.

Writes (the act slice) are `POST /act/<name>` only, and each calls one fixed
firstmate script with an argument list, never a shell string:

| Action | Script |
|---|---|
| answer a captain hold | `fm-captain-hold.sh answers --any-origin --source "hive website (<owner>)"`, one `task<TAB>answer<TAB>Hive desk<TAB>done\|release` row on stdin; the owner-aware intake closes it in whichever home holds the task. Answers are one line, max 512 bytes (the intake's limit) |
| chat, delegate, ticket create/edit, merge word, task-decision answer | `fm-inbox.sh note --request-id web-<hex> --json -` (body on stdin; shapes in `docs/gateway-requests.md`) |
| worker interrupt / relaunch | `fm-control.sh <task> interrupt` or `relaunch --note <text>`, after a confirm page. No exit or teardown. |

The website never merges: the Merge button only records the owner's merge
word as a note, after a confirm page. Each write needs the form's CSRF token
on top of the checks below; control and merge also need a short-lived
step-up token minted by the confirm page. A hold answer and a control confirm
carry a one-time request id kept in memory for the step-up lifetime (180 s):
a repeat POST, even after a failed run, shows the first result and never
re-runs the script. Every write logs one
`gateway-audit action=... subject=... outcome=...` line to stderr (the
journal); free text is never logged, only its length. Chat shows receipts
and replies from `fm-inbox.sh receipts` as a conversation thread (Chat is in
the header of every page).

Home is the **Stack home** (`docs/design/T002-stack-home.html`): the needs-you
lane as the hero, one card per project and the delegate bar. The lane is the
backlog across every project (the first mate's and each second mate's), grouped
by project: captain holds first, marked "waiting on you", then queued tickets (a
few per project, the rest behind "+N more" to the Project page). Per project the
first hold is the expanded card (its answer form unchanged); every other item is
a compact row. Picking a row (click or tap) animates it into the card slot and
the old card drops into the row's place (a short FLIP move; none under
`prefers-reduced-motion`); the pick survives the page's own refreshes. A second
click within 450 ms in the same project, on the picked card or at the spot clicked
first (where the old card now sits), opens the project; a quick click on another
row picks that row instead. Every item has an explicit "open ↗" (and a card has
"Open project ↗") for touch, where there is no double click.
Without the script every row is a plain link to the project. The card shows a
one-to-two sentence plain description of the item (what it is, why it is
parked, what is next), written by one `claude-haiku-4-5-20251001` turn on Hive's
headless Claude adapter (plan-billed `claude -p`, API-key env stripped, every
built-in tool denied, no MCP servers; the user's settings, hooks and CLAUDE.md
still load). `GET /describe?p=<project>&id=<item>` looks the item up
in the current snapshot (404 for anything else), starts at most one turn at a
time and answers `ready`, `pending` or `unavailable`; the page renders cached text
when it has some and otherwise fills it in, so a page load never waits. The
cache is `descriptions.json` (mode 0600) under `HIVE_GATEWAY_DATA_DIR`, keyed per
item and refreshed when its title, notes, hold reason, state or blockers change;
a failed turn is retried no sooner than 5 minutes later. The item text is passed
as quoted data and the reply is only ever shown as escaped text. Worker questions, task decisions and merge approvals are not in
the lane; they show on the Project page. An empty backlog shows a calm "all
clear · N loops running". Cards come from the snapshot plus every project named
in `data/projects.md` of the first mate's home and of each second mate home on
this host, so a registered project with no backlog rows still shows as idle. A
card is blocked exactly when it has a needs-you item, else running when a crew
is `working`, else idle; it shows the activity line and done/total tasks.
Below the cards, **Review pages** lists every open Lavish review session, read
only from `HIVE_GATEWAY_LAVISH_STATE`: a short title (the page's `<title>`, else
the file name), the project when a path component names one, a "reply" tag when
the agent answered last (those sort first, then newest), and a link to
`$HIVE_GATEWAY_BOARD_URL/session/<id>`, so it opens on the tailnet from an
iPhone or iPad. The link is built from the session key only; the recorded local
address is never shown. Six are listed, the rest fold under "+N more". Tapping a card selects it in
place (`/?focus=<project>`) and retargets the bar to that project's first or
second mate; tapping the selected card opens its Project page. With no card
selected the bar messages the first mate; a sole project is selected on load.
With the page script running, the bar sends in place (`Accept: application/json`
on `POST /act/<name>` returns `{ok, message}` instead of an outcome page): the
desk stays put, shows the result under the bar and takes a fresh request id
for the next goal. On wide screens (1100px and up) the desk widens to two
columns; the phone layout is unchanged.
The chrome on every page carries the quota chip: the percent **used** in the
busier of the Claude plan's 5-hour and 7-day windows (calm below 60%, warn from
60%, hot above 85%). Tapping it lists both plus the Fable week, each with its
reset as a clock time in the viewer's zone. Claude Code's own rate limits are
the authority for the two headline windows: the gateway reads the rate-limits
cache file another process writes (`{ts, rate_limits: {five_hour:
{used_percentage, resets_at}, seven_day: {…}}}`, epoch seconds) on every page,
and the popover shows the figures' age once they are over 10 minutes old. When
that file is missing or unreadable, or both its windows have reset, the headline
windows come from `quota-axi --provider claude --json --no-credential-refresh`
(used = 100 − percentRemaining); the Fable week always does (cached 60 s,
refreshed in the background). A window past its reset time is not shown; "—"
when no 5-hour or 7-day figure is left. The desk's fonts
(IBM Plex Mono, Nunito, Nunito Sans; OFL) are served from `/fonts/`, so the
CSP allows `font-src 'self'` and no third-party origin. One fixed inline script,
pinned by hash in the CSP, localises times and updates pages from the SSE
stream (below); only while that stream is down does it poll `/chat` every 4 s
and Home and Project every 30 s. It never swaps the page while text is
selected, and the refresh also waits while a form is being used.

Auth (everything else is a bare 403; a method other than GET/HEAD, or POST
outside `/act/` and the two `/push/` routes, is 405): the TCP peer must be loopback,
`Tailscale-User-Login` must equal the owner, `Host` must be in the allowlist,
and any `Origin` must be too. A POST must also carry an allowed `Origin` and
no cross-site `Sec-Fetch-Site`.

| Env | Default |
|---|---|
| `HIVE_GATEWAY_OWNER` | `yehezkieled1502@gmail.com` |
| `HIVE_GATEWAY_HOSTS` | `desktop-lfme032.tailfb3900.ts.net,localhost,127.0.0.1` |
| `HIVE_GATEWAY_FM_HOME` | `/home/hezki/firstmate` |
| `HIVE_GATEWAY_PORT` | `8480` |
| `HIVE_GATEWAY_BOARD_URL` | `https://desktop-lfme032.tailfb3900.ts.net:8445` (loopback Lavish `127.0.0.1:4387/session/<id>` links are rewritten to it) |
| `HIVE_GATEWAY_TZ` | `Australia/Sydney` (server-side fallback; the page script shows the viewer's zone) |
| `HIVE_GATEWAY_ORIGIN` | `https://desktop-lfme032.tailfb3900.ts.net:8446` (the public tailnet origin; its hostname seeds the Host/Origin allowlist when `HIVE_GATEWAY_HOSTS` is unset. The port here must match the `tailscale serve --https=` port.) |
| `HIVE_GATEWAY_DATA_DIR` | `~/.local/state/hive-gateway` (VAPID key and push subscriptions, files mode 0600, outside the repo so they cannot be committed) |
| `HIVE_GATEWAY_RATE_LIMITS` | `~/.claude/rate-limits-cache.json` (Claude Code's rate limits, the quota chip's headline source, read only; `off` leaves the chip on `quota-axi`) |
| `HIVE_GATEWAY_QUOTA_AXI` | `quota-axi` on `PATH`, else `~/.local/bin/quota-axi` (the Fable week, and the headline fallback; `off` disables it) |
| `HIVE_GATEWAY_LAVISH_STATE` | `~/.lavish-axi/state.json` (Lavish session state for the Review pages list, read only; `off` hides the list) |

### Live updates, alerts and live tail

- **SSE.** `GET /events` is one `text/event-stream` per open page. A background
  watcher (started with the app) stats `$FM_HOME/state/*.status` every 2 s and runs
  `fm-inbox.sh receipts` every 5 s. When a status file moves it re-runs the snapshot
  (at most every 5 s, plus a 60 s heartbeat) and sends `desk` or `chat` events. Pages
  re-fetch themselves and swap in place (same busy rules as before: never while text
  is selected or a form is in use; the refresh then waits). The 30 s (Home/Project)
  and 4 s (Chat) polls only run while the stream is down.
- **Web Push (ADR 0026, gateway-local).** The first call to `GET /push/key` creates the
  VAPID key under `HIVE_GATEWAY_DATA_DIR`. The header's **Alerts** button asks for
  notification permission and stores the subscription (`POST /push/subscribe`, with
  the page's CSRF token; the same owner, Host and Origin checks apply). Pushes go
  only for: a decision or hold waiting, a PR ready for review (merge approval), work
  whose latest status is `blocked` or `failed`, and a first-mate chat reply. Each is
  pushed once; the first look after a restart is a silent baseline. Subscriptions the
  push service reports gone (404/410) are pruned.
  **iPhone and iPad:** push only works for the desk added to the Home Screen
  (Safari Share, Add to Home Screen, iOS/iPadOS 16.4 or later), opened from that icon.
  The desk serves the manifest (`/manifest.webmanifest`), icons and service worker
  (`/sw.js`) that needs. Desktop Chrome and Android work from a normal tab.
- **Live tail.** Each crew on a Project page has **Watch live** (`/w/<task>`), which polls
  `GET /w/<task>/out` every 3 s. That runs `fm-peek.sh <task> 60` (read-only), only for a
  task the snapshot lists as a crew; output has terminal escapes stripped, is capped
  at 64 KB and is inserted as text, never HTML.

No new write actions: the only new POSTs store or remove the owner's own push
subscription.

### Run and update (production)

The desk binds `127.0.0.1:8480` and is published to the tailnet only:

```
tailscale serve --bg --https=8446 http://127.0.0.1:8480
tailscale serve status        # every line must say "(tailnet only)"; never use funnel
```

Install and run it as a user service (`deploy/hive-gateway.service`; edit
`WorkingDirectory` if the checkout is elsewhere):

```
mkdir -p ~/.config/systemd/user && cp deploy/hive-gateway.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now hive-gateway.service
```

Update after a merge to `main`:

```
git -C <checkout> pull --ff-only && uv sync --project <checkout>
systemctl --user restart hive-gateway.service
journalctl --user -u hive-gateway.service -n 20
```

Then, from the iPad or phone (a request from the PC itself carries no login header and
gets 403 by design), open `https://desktop-lfme032.tailfb3900.ts.net:8446/`, tap
**Alerts**, and a "Alerts are on" push confirms the subscription works. Undo the
publish with `tailscale serve --https=8446 off`.
