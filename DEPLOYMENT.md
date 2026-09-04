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
| **Metadata DB** | Render **PostgreSQL** | Free | ⚠️ Render deletes free databases 30 days after creation — see [§7](#7-known-limitations) |
| **Vector DB** | **Qdrant Cloud** | Free (1 GB) | Render has no vector database offering |
| **LLM + embeddings** | **OpenAI API** | Pay-as-you-go | The only piece that costs real money |

Deliberately **not** deployed: the Celery worker and Redis broker. Render's
background workers require a paid plan, and this stack falls back to
synchronous ingestion when no broker is configured (see [§7](#7-known-limitations)).

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

---

## 3. Create the Qdrant Cloud cluster

1. Sign up at <https://cloud.qdrant.io> (free tier, no card required).
2. **Create a free cluster.** Pick the region geographically closest to the
   Render region you will choose in phase 4 — every retrieval crosses this hop,
   so a mismatched pair adds latency to every single question.
3. When the cluster is running, copy two values:
   - the **endpoint URL**, of the form `https://<id>.<region>.aws.cloud.qdrant.io:6333`
     (keep the `:6333` — this project talks REST, not gRPC)
   - a freshly created **API key**

Keep both to hand; they become `QDRANT_URL` and `QDRANT_API_KEY`.

---

## 4. Create the Postgres database and the API service

### 4a. Postgres

Render dashboard → **New +** → **Postgres**.

| Field | Value |
| --- | --- |
| Name | `enterprise-rag-db` |
| Database | `enterprise_rag` |
| Region | **Pick one and remember it** — the API must use the same region |
| Plan | Free |

Once it is available, open the database page and copy the **Internal Database
URL**. Use the *internal* one, not the external: it is faster, it does not
count against bandwidth, and it only works from services in the same region.

> The URL Render gives you starts with `postgresql://`, which SQLAlchemy reads
> as a request for psycopg 2 — a driver this project does not install. You do
> **not** need to rewrite it by hand: `database.py` normalizes the scheme to
> `postgresql+psycopg://` on startup. Paste the URL exactly as Render gives it.

### 4b. API web service

Render dashboard → **New +** → **Web Service** → connect this repository.

| Field | Value |
| --- | --- |
| Name | `enterprise-rag-api` |
| Region | **The same region as the database** |
| Branch | `develop` |
| Root Directory | *(leave blank — the Dockerfile is at the repo root)* |
| Language | **Docker** |
| Dockerfile Path | `./Dockerfile` |
| Instance Type | Free |
| Health Check Path | `/healthz` |

Then add the environment variables:

| Key | Value |
| --- | --- |
| `DATABASE_URL` | The **Internal Database URL** from 4a |
| `OPENAI_API_KEY` | Your OpenAI key |
| `QDRANT_URL` | The Qdrant endpoint from phase 3, including `:6333` |
| `QDRANT_API_KEY` | The Qdrant API key from phase 3 |
| `QDRANT_COLLECTION` | `enterprise_documents` |
| `REDIS_URL` | *(empty string — this is what disables background ingestion)* |
| `MAX_UPLOAD_BYTES` | `4194304` |
| `ALLOWED_ORIGINS` | `http://localhost:4200` — corrected in phase 6 |
| `CHAT_MODEL` | `gpt-4o-mini` |
| `LOG_LEVEL` | `INFO` |

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

**The free Postgres database expires.** Render deletes free PostgreSQL
instances **30 days after creation**. When that happens the API stays up but
every document operation fails. Set a calendar reminder. To make the demo
durable, either move to Render's paid database, or switch `DATABASE_URL` to a
free-forever host such as Neon or Supabase — no code change is needed, since
`database.py` normalizes any `postgresql://` URL.

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

## 9. Environment variable reference

Everything the API reads, with its default from `config.py`.

| Variable | Default | Purpose |
| --- | --- | --- |
| `OPENAI_API_KEY` | *(empty)* | Embeddings and chat completions |
| `DATABASE_URL` | local SQLite file | Document metadata; `postgres://` and `postgresql://` are normalized to psycopg 3 |
| `QDRANT_URL` | `http://localhost:6333` | Vector store endpoint |
| `QDRANT_API_KEY` | *(none)* | Required by Qdrant Cloud, unused locally |
| `QDRANT_COLLECTION` | `enterprise_documents` | Collection name |
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
