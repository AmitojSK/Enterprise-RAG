# Deploying Enterprise RAG to Render

A step-by-step guide to putting this project online for free. Follow the phases
in order — each one produces a value the next phase needs.

Budget about 45 minutes end to end, most of it waiting on builds.

---

## 1. Target architecture

Five pieces, three of them outside Render:

| Piece | Where it runs | Plan | Notes |
| --- | --- | --- | --- |
| **API** (FastAPI) | Render **Web Service**, Docker runtime | Free | Built from the repo-root `Dockerfile` |
| **Frontend** (Angular) | Render **Static Site** | Free | Zero instance-hours; never sleeps |
| **Metadata DB** | **Postgres on your Hostinger VPS** | Already paid for | Never expires, unlike Render's free tier |
| **Vector DB** | **Qdrant Cloud** | Free (1 GB) | Render has no vector database offering |
| **LLM + embeddings** | **OpenAI API** | Pay-as-you-go | The only piece that costs real money |

Deliberately **not** deployed: the Celery worker and Redis broker. Render's
background workers require a paid plan, and this stack falls back to
synchronous ingestion when no broker is configured (see [§8](#8-known-limitations)).

**Why Postgres sits on the VPS and Qdrant does not.** `/v1/query` and
`/v1/query/stream` take no database dependency at all — answering a question
touches only OpenAI and Qdrant. Postgres is read and written solely by the
document CRUD endpoints, which are infrequent and not latency-sensitive. So a
cross-network hop to the VPS costs nothing on the path that matters, while the
same hop for Qdrant would land in the middle of every single question. Render's
free Postgres expiring after 30 days is a real problem; a few extra milliseconds
on an upload is not.

Because the frontend is a static site, the browser calls the API on its own
origin rather than through a proxy. That makes CORS load-bearing: the API's
`ALLOWED_ORIGINS` must list the static site's URL, and the static site's build
must bake in the API's URL. Phases 4 and 6 wire up those two halves.

---

## 2. Prerequisites

1. **Push this branch to GitHub.** Render deploys from the repo; it cannot see
   your local working tree.
   ```bash
   git push origin develop
   ```
2. **An OpenAI API key** with billing enabled — <https://platform.openai.com/api-keys>.
3. **A Render account** connected to your GitHub account — <https://dashboard.render.com>.
4. **Access to the Hostinger VPS panel** for `srv1952923.hstgr.cloud`. You do
   not need the root password: the panel's **Web console** opens a root shell in
   the browser, which is enough for all of phase 4a.

---

## 3. Create the Qdrant Cloud cluster

1. Sign up at <https://cloud.qdrant.io> (free tier, no card required).
2. **Create a free cluster**, or reactivate an existing one. Note its region —
   every retrieval crosses this hop, so the Render region in phase 4b must match
   it. Qdrant, not Postgres, is the dependency that has to be close to the API.
3. When the cluster is running, copy two values:
   - the **endpoint URL** from the Overview tab, of the form
     `https://<id>.<region>-0.aws.cloud.qdrant.io` — paste it exactly as shown
   - a freshly created **API key**

Keep both to hand; they become `QDRANT_URL` and `QDRANT_API_KEY`.

> The API key is shown **once**, at creation. If the dashboard only shows you a
> masked value like `********WfPdHOgc`, that key is unrecoverable — create a new
> one rather than trying to retrieve it.

> **Free clusters suspend when idle, and are then deleted.** Qdrant's own
> notice: *"Your free cluster was suspended due to inactivity and will be
> automatically deleted 3 weeks after you received the suspension
> notification."* A suspended cluster refuses connections, and the API reports
> that as a `503` from the vector store rather than anything naming Qdrant. A
> deleted one takes your indexed vectors with it. See
> [§8](#8-known-limitations) for how to keep it alive.

---

## 4. Stand up Postgres, then the API service

### 4a. Postgres on the VPS

Get a root shell. You do not need the root password for this — the Hostinger
panel's **Web console** button opens one in the browser. While you are in the
panel, **SSH key → Manage** is worth setting up for passwordless SSH later.

Create the deployment directory and generate a TLS certificate. Postgres will
be reachable over the public internet, so the connection must be encrypted —
without this, the password crosses the network in the clear on every connect.

```bash
mkdir -p /opt/rag-postgres/certs && cd /opt/rag-postgres && openssl req -new -x509 -days 3650 -nodes -text -out certs/server.crt -keyout certs/server.key -subj "/CN=200.234.45.38" && chmod 600 certs/server.key && chown 70:70 certs/server.key certs/server.crt
```

`70` is the `postgres` user inside the **alpine** image — the Debian-based
`postgres:16` uses `999` instead, and copying that number here is a common way
to end up with a container that will not start. Postgres also refuses to boot if
the key is group- or world-readable, so the `chmod` is not optional either.

Copy `deploy/vps-postgres.yml` from this repository into `/opt/rag-postgres/`,
then generate a password and start it:

```bash
openssl rand -base64 24 | tr -d '/+=' | head -c 32 | xargs -I{} echo "POSTGRES_PASSWORD={}" > .env && chmod 600 .env && cat .env
```

**Write that password down** — you need it in the next step, and Postgres bakes
it in on first start (see the warning below). Then:

```bash
docker compose -f vps-postgres.yml up -d && docker compose -f vps-postgres.yml logs --tail=20
```

Your `DATABASE_URL` is then:

```
postgresql://rag:THE_PASSWORD@200.234.45.38:5432/enterprise_rag?sslmode=require
```

> `sslmode=require` *guarantees* encryption rather than enabling it. psycopg's
> default is `prefer`, which already encrypts against a TLS-capable server — but
> it silently accepts a plaintext connection if TLS ever stops being offered, so
> a broken certificate would downgrade you without a single error. `require`
> fails the connection instead. The scheme stays `postgresql://` —
> `database.py` rewrites it to `postgresql+psycopg://` on startup and preserves
> the query string, so paste it exactly as written.

> **Set the password before the first start, and do not change it after.**
> Postgres only applies `POSTGRES_PASSWORD` when it initializes an empty data
> directory. Once the volume exists, editing `.env` changes what the API
> *sends* but not what the database *expects*, and every request then fails
> with `password authentication failed for user "rag"`. To rotate it later, use
> `ALTER USER rag WITH PASSWORD ...` inside the database and update `.env` to
> match.

### 4a-bis. Lock down the firewall

Right now 5432 is open to the entire internet, protected only by that password.
Scope it to Render instead. Once the API service exists (step 4b), its page has
an **Outbound IP addresses** section — come back and run:

```bash
ufw delete allow 5432/tcp; ufw allow from 74.220.51.0/24 to any port 5432 proto tcp && ufw allow from 74.220.59.0/24 to any port 5432 proto tcp && ufw status numbered | grep 5432
```

Those two ranges are Render's Frankfurt egress, read from the API service's
**Connect → Outbound** tab. Re-check them if the database ever starts timing
out for no apparent reason: Render can change them, and the resulting failure
looks like a network fault rather than a firewall rule.

Until then, `ufw allow 5432/tcp` gets you connected. **Do not leave it that
way.** An open Postgres port is exactly the exposure your food-delivery handoff
already flags on this box for MySQL, Redis, and Kafka — no reason to add a
fourth.

Two consequences worth knowing. Your own machine can no longer reach the
database; add your address with
`ufw allow from $(curl -s ifconfig.me) to any port 5432 proto tcp` if you want
to keep testing locally. And a `/24` is 256 addresses, so this permits roughly
512 Render-owned hosts rather than your service alone — the password remains
the thing protecting the data. Static outbound IPs, which would allow a tighter
rule, are a paid Render feature.

### 4b. API web service

Render dashboard → **New +** → **Web Service** → connect this repository.

| Field | Value |
| --- | --- |
| Name | `enterprise-rag-api` |
| Region | **Frankfurt** — must match your Qdrant cluster |
| Branch | `develop` |
| Root Directory | *(leave blank — the Dockerfile is at the repo root)* |
| Language | **Docker** |
| Dockerfile Path | `./Dockerfile` |
| Instance Type | Free |
| Health Check Path | `/healthz` |

> **Match the region to Qdrant, not to the VPS.** Your cluster is in
> `eu-central-1`, so the API belongs in **Frankfurt**. Qdrant is queried on every
> single question; Postgres is only touched by uploads and document listing, so
> the ~110 ms hop from Frankfurt to the VPS in India costs nothing users notice.
> Getting this backwards is what your food-delivery handoff records — an
> Oregon-to-India Redis handshake blowing a 500 ms timeout on three services.
> The region cannot be changed after the service is created.

Then add the environment variables:

| Key | Value |
| --- | --- |
| `DATABASE_URL` | The VPS connection string from 4a, including `?sslmode=require` |
| `OPENAI_API_KEY` | Your OpenAI key |
| `QDRANT_URL` | The Qdrant endpoint from phase 3, including `:6333` |
| `QDRANT_API_KEY` | The Qdrant API key from phase 3 |
| `QDRANT_COLLECTION` | `enterprise_documents_prod` — see below |
| `REDIS_URL` | *(empty string — this is what disables background ingestion)* |
| `MAX_UPLOAD_BYTES` | `4194304` |
| `ALLOWED_ORIGINS` | `http://localhost:4200` — corrected in phase 6 |
| `CHAT_MODEL` | `gpt-4o-mini` |
| `LOG_LEVEL` | `INFO` |

**Give production its own collection.** Qdrant holds the vectors, Postgres
holds the document records, and the two must describe the same set. Pointing a
fresh deployment at a collection you already used locally leaves vectors with no
matching rows: the document list renders empty from Postgres while questions
still return answers citing files that the UI cannot show or delete. A separate
collection name keeps the deployment consistent and leaves your local data
untouched. The API creates it automatically on the first upload.

`REDIS_URL` must be present and **empty**, not absent. Absent falls back to the
`redis://localhost:6379/0` default, and every upload then wastes time probing a
broker that is not there before giving up and ingesting inline.

`MAX_UPLOAD_BYTES` is lowered from the 10 MB default to 4 MB on purpose. With
no worker, parsing and embedding happen inside the HTTP request, and a large
PDF can outlast the platform's request timeout.

Deploy. The first build takes several minutes. When it is live, **copy the
service URL** — something like `https://enterprise-rag-api.onrender.com` — and
verify it:

```bash
curl https://enterprise-rag-api.onrender.com/healthz
```

Expect `{"status":"ok"}`. If this is the first request in a while, the free
instance is asleep and the call may hang for 50–90 seconds before answering.

---

## 5. Point the frontend at the API

The static site has no reverse proxy, so the API's origin is compiled into the
Angular bundle at build time. Edit one line:

**`web/src/environments/environment.render.ts`**

```ts
export const environment = {
  apiBaseUrl: 'https://enterprise-rag-api.onrender.com',  // ← your API URL from phase 4b
};
```

No trailing slash. This is a public URL, not a secret, so committing it is fine.

```bash
git add web/src/environments/environment.render.ts
git commit -m "point the deployed frontend at the Render API"
git push origin develop
```

---

## 6. Create the static site, then fix CORS

### 6a. Static site

Render dashboard → **New +** → **Static Site** → same repository.

| Field | Value |
| --- | --- |
| Name | `enterprise-rag-web` |
| Branch | `develop` |
| Root Directory | `web` |
| Build Command | `npm ci && npm run build -- --configuration render` |
| Publish Directory | `dist/web/browser` |

The `render` build configuration is what swaps `environment.ts` for
`environment.render.ts`. Building with plain `production` instead produces a
bundle that calls its own origin and every request 404s.

**Add the SPA rewrite rule.** On the service's **Redirects/Rewrites** tab:

| Source | Destination | Action |
| --- | --- | --- |
| `/*` | `/index.html` | **Rewrite** |

Without this, the root page works but refreshing any deeper route returns 404,
because Angular's routes have no matching files on disk.

Deploy, then copy the static site's URL — something like
`https://enterprise-rag-web.onrender.com`.

### 6b. Close the CORS loop

Go back to the **API** service → **Environment**, and set:

| Key | Value |
| --- | --- |
| `ALLOWED_ORIGINS` | `https://enterprise-rag-web.onrender.com` |

Exact scheme and host, no trailing slash, no path. Comma-separate if you want
to keep local development working too:

```
https://enterprise-rag-web.onrender.com,http://localhost:4200
```

Saving the variable redeploys the API automatically. Wait for it to go green.

---

## 7. Smoke test

1. **Wake the API first.** `curl https://enterprise-rag-api.onrender.com/healthz`
   and wait for the response. Doing this before opening the UI turns a
   confusing 90-second hang into a normal-looking page.
2. Open the static site URL.
3. Upload a small text-bearing PDF (under 4 MB). Expect a `201` and the
   document appearing with status `indexed`.
   - *Nothing happens and the browser console shows a CORS error* → phase 6b.
   - *`503` mentioning the vector store* → `QDRANT_URL` or `QDRANT_API_KEY`.
   - *`422` about no readable text* → the PDF is scanned images; there is no OCR
     in this pipeline. Try a text PDF.
4. Ask a question about the document. Expect a streamed answer with citations.
5. Delete the document and confirm it disappears from the list.

---

## 8. Known limitations

These are properties of the free-tier deployment, not bugs to chase.

**The database is a single point of failure you now own.** Postgres runs on
the same VPS as the food-delivery stack. A reboot, a full disk, or an OOM kill
there takes document operations down here, and there are no automated backups.
Take one periodically:

```bash
ssh root@200.234.45.38 'cd /opt/rag-postgres && docker compose -f vps-postgres.yml exec -T postgres pg_dump -U rag enterprise_rag' | gzip > rag-backup-$(date +%F).sql.gz
```

Hostinger's own VPS snapshots cover the whole box and are the easier win.

**The Postgres port is exposed to the internet.** Encrypted and password
protected, but reachable. Scoping the firewall to Render's outbound IPs
(step 4a-bis) is what actually reduces this to a small risk; leaving
`ufw allow 5432/tcp` in place does not. The certificate is self-signed, so
`sslmode=require` stops passive sniffing but not an active man-in-the-middle —
acceptable for a portfolio demo, not for real user data.

**The Qdrant free cluster suspends when idle and is deleted three weeks
later.** A suspended cluster fails every upload and every question with a `503`
from the vector store; the site still loads and the document list still renders
from Postgres, so the symptom is a demo that looks healthy right up until you
ask it something. Deletion is worse — the vectors are gone, and documents that
Postgres still lists as `indexed` become unanswerable.

Since the VPS is already running, the cheap fix is a weekly keep-alive from it:

Store the key in a root-only file rather than in the crontab itself, where
`crontab -l` would print it in the clear:

```bash
mkdir -p /opt/qdrant-keepalive && printf 'PASTE_ONLY_THE_KEY' > /opt/qdrant-keepalive/key && chmod 600 /opt/qdrant-keepalive/key
```

```bash
(crontab -l 2>/dev/null; echo '0 3 * * 1 curl -s -H "api-key: $(cat /opt/qdrant-keepalive/key)" https://YOUR-CLUSTER.eu-central-1-0.aws.cloud.qdrant.io/collections > /dev/null 2>&1') | crontab -
```

Then verify, because the cron job discards its own output and a broken key
would fail silently every week:

```bash
echo "bytes: $(wc -c < /opt/qdrant-keepalive/key), lines: $(wc -l < /opt/qdrant-keepalive/key)"; echo "HTTP: $(curl -s -o /dev/null -w '%{http_code}' -H "api-key: $(cat /opt/qdrant-keepalive/key)" https://YOUR-CLUSTER.eu-central-1-0.aws.cloud.qdrant.io/collections)"
```

Expect the key's exact length, **0 lines**, and **HTTP 200**. A stray newline
in that file makes the header malformed and curl reports `000` — pasting one
line too many out of `.env` is an easy way to get there. Trim it with
`head -1 file | tr -d '
'` rather than re-pasting.

That is a mitigation, not a guarantee — Qdrant defines the inactivity rule, not
you. Check the cluster is running before any demo regardless.

**The API sleeps after 15 minutes of inactivity.** The first request afterwards
takes 50–90 seconds. The static frontend does not sleep, so the page loads
instantly and then appears to hang on its first API call. Warm the API with a
`/healthz` call a couple of minutes before any demo.

**Free instance-hours are shared across your whole Render workspace** — 750 per
month, pooled across every service in the account, including unrelated
projects. The static site consumes none, so this deployment adds exactly one
service's worth of draw.

**Ingestion is synchronous.** With `REDIS_URL` empty, uploads are parsed,
chunked, embedded, and indexed inside the HTTP request. A large document can
outlast the request timeout, which is why `MAX_UPLOAD_BYTES` is 4 MB here. The
Celery path in `services/tasks.py` is intact and is what runs under Docker
Compose locally — restoring it in production needs a paid Render background
worker plus a Redis instance for the broker.

**The API has no authentication and the library is shared.** Every visitor can
read, upload, and delete every document. This is deliberate for a portfolio
demo and is called out in `routes.py`. Do not put anything confidential in it,
and add authentication before pointing real users at it.

**Uploads cost money.** Every ingestion calls OpenAI's embedding API and every
question calls the chat API against your key. A public URL with no
authentication is a public spend endpoint — set a billing limit on the OpenAI
account, and take the site down when you are not demoing it.

---

## 9. Self-hosting instead

If you ever outgrow the free tier's constraints — the cold start, the expiring
database, or the missing background worker — [docs/self-hosting.md](docs/self-hosting.md)
covers running the whole stack on your own server with `docker-compose.prod.yml`:
Postgres, Qdrant, Redis, the Celery worker, and Caddy for automatic TLS, none of
which the free platform tier can provide. That path has no cold start and no
expiry, at the cost of owning the operating system yourself.

---

## 10. Environment variable reference

Everything the API reads, with its default from `config.py`.

| Variable | Default | Purpose |
| --- | --- | --- |
| `OPENAI_API_KEY` | *(empty)* | Embeddings and chat completions |
| `DATABASE_URL` | local SQLite file | Document metadata; `postgres://` and `postgresql://` are normalized to psycopg 3, query strings preserved |
| `QDRANT_URL` | `http://localhost:6333` | Vector store endpoint |
| `QDRANT_API_KEY` | *(none)* | Required by Qdrant Cloud, unused locally |
| `QDRANT_COLLECTION` | `enterprise_documents_prod` — see below | Collection name |
| `QDRANT_TIMEOUT_SECONDS` | `60` | Raise if cloud writes time out |
| `INDEXING_BATCH_SIZE` | `32` | Vectors per upsert request |
| `REDIS_URL` | `redis://localhost:6379/0` | Celery broker; **empty disables background ingestion** |
| `ALLOWED_ORIGINS` | `http://localhost:4200,http://127.0.0.1:4200` | Comma-separated CORS origins |
| `MAX_UPLOAD_BYTES` | `10485760` | Upload size ceiling |
| `TOP_K` / `RERANK_K` | `20` / `8` | Candidates retrieved, then kept |
| `SCORE_THRESHOLD` | `0.25` | Minimum similarity to cite |
| `CHAT_MODEL` | `gpt-4o-mini` | Answer generation model |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | Embedding model |
| `LOG_LEVEL` | `INFO` | Root log level |
| `PORT` | `8000` | Set by Render; the container binds it automatically |
