# Deploying Enterprise RAG to the Hostinger VPS

The whole stack runs on one server: no cold starts, no expiring free database,
and the Celery worker restored so ingestion is genuinely asynchronous.

The one real constraint is memory. This VPS already runs the food-delivery
stack, so [phase 1](#1-memory-gate--do-this-first) is a hard gate: if the
numbers come back badly, stop there rather than pushing on and taking down a
project that is already live.

---

## 0. Target architecture

Seven containers on `200.234.45.38`, one network, one public entry point.

```
internet ──▶ caddy :80/:443  (TLS, the only published ports)
                  │
                  ▼
             web  (nginx: serves the Angular bundle, proxies /v1/ ──▶ api)
                  │
                  ▼
             api  (FastAPI) ──┬──▶ postgres   document metadata
                              ├──▶ qdrant     vectors
                              └──▶ redis      Celery broker
                                      ▲
             worker (Celery) ─────────┘   parses, embeds, indexes
```

Postgres, Qdrant, and Redis publish **no ports**. They are reachable only from
inside the Docker network. This is deliberately unlike the food-delivery
infrastructure on this same box, where MySQL, Redis, and an unauthenticated
Kafka are exposed to the whole internet — a gap your own handoff document flags.

Because nginx proxies `/v1/` to the API, the browser sees a single origin.
CORS is not involved at all, and neither is the `render` Angular build
configuration — the deployed bundle is a plain `production` build.

**Cost:** nothing beyond the VPS you already pay for, plus OpenAI usage.

---

## 1. Memory gate — do this first

The Hostinger panel reports **77% memory used** on a 4 GB KVM 1. If that is
real rather than reclaimable cache, this stack will not fit. Find out:

```bash
ssh root@200.234.45.38 'free -h; echo "--- containers ---"; docker stats --no-stream --format "table {{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}"; echo "--- swap ---"; swapon --show'
```

Read the **`available`** column of `free -h`, not `used` — Linux counts page
cache as used, and Hostinger's gauge probably does too.

| `available` | Verdict |
| --- | --- |
| **> 1.5 GB** | Proceed. Comfortable. |
| **0.8 – 1.5 GB** | Proceed, but add swap in phase 2 and treat it as required. |
| **< 0.8 GB** | **Stop.** See [§9](#9-if-memory-is-too-tight). |

The stack needs roughly 800 MB–1 GB at rest. The `mem_limit` values in
`docker-compose.prod.yml` cap it at 1.5 GB so it can never starve the
food-delivery containers, but a cap is not a reservation — the memory has to
actually exist.

**Expect Kafka to be the biggest consumer.** It is a JVM and routinely holds
1–1.5 GB. If food-delivery is not being actively demoed, stopping Kafka alone
may free everything this project needs.

---

## 2. Add swap

Do this even if memory looks fine. The Angular production build is the
memory-hungriest step of the whole deployment — far heavier than anything at
runtime — and on a 1 vCPU box it will fail without headroom.

```bash
ssh root@200.234.45.38 'fallocate -l 4G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile && echo "/swapfile none swap sw 0 0" >> /etc/fstab && free -h'
```

The disk has 45 GB free, so 4 GB costs nothing you need. The `/etc/fstab` line
makes it survive a reboot. Skip this if `swapon --show` already listed a swap
file in phase 1.

---

## 3. Get the code onto the server

```bash
ssh root@200.234.45.38
```

```bash
mkdir -p /opt/enterprise-rag && git clone -b develop https://github.com/AmitojSK/Enterprise-rag.git /opt/enterprise-rag && cd /opt/enterprise-rag
```

If the repository is private, generate a deploy key on the VPS
(`ssh-keygen -t ed25519`) and add the public half under the repository's
**Settings → Deploy keys** on GitHub, then clone over SSH instead.

---

## 4. Write the secrets file

Still on the VPS, in `/opt/enterprise-rag`:

```bash
cp .env.prod.example .env && openssl rand -base64 24 | tr -d '/+=' | head -c 32 | xargs -I{} sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD={}|" .env && chmod 600 .env && nano .env
```

That generates the Postgres password and locks the file to root. In the editor,
fill in the two remaining values:

| Key | Value |
| --- | --- |
| `OPENAI_API_KEY` | Your OpenAI key |
| `SITE_ADDRESS` | `srv1952923.hstgr.cloud`, or `:80` to skip HTTPS for now |

`SITE_ADDRESS` is what decides HTTPS. Given a hostname, Caddy requests a Let's
Encrypt certificate on first boot and renews it forever with no further
involvement. Given `:80`, it serves plain HTTP. Start with the hostname — if
the certificate fails, fall back to `:80` and diagnose from there.

Do **not** set `DATABASE_URL`, `QDRANT_URL`, or `REDIS_URL`. Compose derives
them from the internal service names and will override anything you put here.

> **Set `POSTGRES_PASSWORD` before the first `up`, and do not change it after.**
> Postgres only applies that variable when it initializes an empty data
> directory. Once `pg_data` exists, editing the value in `.env` changes what the
> API *sends* but not what the database *expects*, and every service then dies
> with `password authentication failed for user "rag"`. If you must rotate it,
> change it inside the database with `ALTER USER rag WITH PASSWORD ...` and
> update `.env` to match — or destroy the volume with `down -v`, which also
> destroys every indexed document.

---

## 5. Open the firewall

Only the two web ports. Everything else stays internal.

```bash
ufw allow 80/tcp && ufw allow 443/tcp && ufw status
```

Port 80 is not optional even if you only want HTTPS: Let's Encrypt validates
over HTTP before it will issue the certificate.

Check the output for a conflict — if food-delivery already has something on 80
or 443, Caddy will fail to bind and phase 6 stops immediately.

---

## 6. Build and start

```bash
cd /opt/enterprise-rag && docker compose -f docker-compose.prod.yml up -d --build
```

The first run takes **10–20 minutes**, nearly all of it the Angular build on a
single vCPU. It will look frozen. It is not.

> **If the `web` build fails or the box locks up**, it is the Angular
> compilation, not the stack. Node needs more memory than a 1 vCPU VPS
> comfortably has. Two ways out: retry with `NODE_OPTIONS=--max-old-space-size=2048`
> prefixed to the command, or build that image on your laptop and push it to
> GHCR (the same registry the food-delivery project already publishes to), then
> replace `build: ./web` with `image: ghcr.io/amitojsk/enterprise-rag-web:latest`
> and re-run. The API image is small and always builds fine on the VPS.

Watch it come up:

```bash
docker compose -f docker-compose.prod.yml ps && docker compose -f docker-compose.prod.yml logs -f --tail=40 api worker
```

Expect `Application startup complete` from the API and `celery@... ready` from
the worker. Then confirm from your own machine:

```bash
curl https://srv1952923.hstgr.cloud/healthz
```

Expect `{"status":"ok"}` — immediately, with no cold-start delay ever.

---

## 7. Smoke test

1. Open `https://srv1952923.hstgr.cloud` in a browser.
2. Upload a text-bearing PDF. It should return **immediately** with status
   `processing`, and flip to `indexed` on its own a few seconds later — the
   frontend polls for you. That transition is the worker doing its job, and is
   the part Render's free tier could not have shown at all.
3. Watch it happen server-side:
   ```bash
   docker compose -f docker-compose.prod.yml logs -f worker
   ```
4. Ask a question. Expect an answer streaming in token by token, with citations.
5. Delete the document and confirm it disappears.

Then check what the stack actually costs you in memory:

```bash
docker stats --no-stream --format "table {{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}"
```

---

## 8. Operating it

**Deploy a change:**
```bash
cd /opt/enterprise-rag && git pull && docker compose -f docker-compose.prod.yml up -d --build
```

**Logs:**
```bash
docker compose -f docker-compose.prod.yml logs -f --tail=100 api worker
```

**Back up Postgres** (the volume is the only copy of your document metadata):
```bash
docker compose -f docker-compose.prod.yml exec -T postgres pg_dump -U rag enterprise_rag | gzip > ~/rag-backup-$(date +%F).sql.gz
```

**Stop everything** without deleting data:
```bash
docker compose -f docker-compose.prod.yml down
```
Add `-v` only if you genuinely want the documents and vectors destroyed.

---

## 9. If memory is too tight

In rough order of preference:

1. **Stop Kafka when not demoing food-delivery.** Almost certainly the single
   largest consumer on the box, and easily the cheapest 1 GB you will ever
   reclaim.
2. **Drop the Celery worker.** Delete the `worker` service from
   `docker-compose.prod.yml` and set `REDIS_URL=` (empty) in `.env`; the API
   falls back to synchronous ingestion. Saves ~380 MB, and also lets you drop
   Redis. Costs you the async architecture, which is much of why option A was
   worth doing.
3. **Move the vector store to Qdrant Cloud.** The free 1 GB tier is genuinely
   free forever. Delete the `qdrant` service and point `QDRANT_URL` /
   `QDRANT_API_KEY` at the cloud cluster. Saves ~380 MB, and unlike the
   Render/VPS split this hop is not in a latency-critical loop with a second
   remote dependency.
4. **Upgrade to KVM 2.** 8 GB removes the problem entirely.

---

## 10. Known limitations

**Single point of failure.** One box runs both projects. A reboot, a full disk,
or an OOM kill takes down everything at once. There is no redundancy.

**The API has no authentication and the library is shared.** Every visitor can
read, upload, and delete every document — deliberate for a portfolio demo, and
flagged in `routes.py`. Now that it is on a permanent public URL rather than a
sleeping free instance, this matters more: add authentication before pointing
real users at it.

**Uploads cost money.** Every ingestion calls OpenAI's embedding API and every
question calls the chat API against your key. A public, unauthenticated URL is
a public spend endpoint. Set a billing limit on the OpenAI account.

**`create_all`, not migrations.** The schema is created on startup from the
SQLAlchemy models. That is fine for the current single-table schema, but a
future model change will not migrate existing rows — introduce Alembic before
the schema changes in anger.

**No automated backups.** The `pg_dump` in [§8](#8-operating-it) is manual.
Hostinger's own snapshot feature covers the whole VPS and is the easier win.

---

## Appendix: the Render path

The repository still supports deploying to Render instead — `Dockerfile` binds
the platform-assigned `PORT`, `database.py` normalizes managed `postgres://`
URLs, `ALLOWED_ORIGINS` drives CORS for a separate-origin frontend, and the
`render` Angular build configuration bakes an absolute API origin into the
bundle for a static site.

None of that is used by this VPS deployment, and none of it is in the way. It
is kept as a fallback: it costs nothing to carry, and it is the escape hatch if
this VPS ever runs out of room.
