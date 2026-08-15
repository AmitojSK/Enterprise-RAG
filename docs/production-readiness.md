# Production-readiness guide

The first version proves the essential RAG path works. It is **not** a complete production deployment yet. This guide separates normal RAG behavior from the controls needed before real users or confidential documents are involved.

## Why upload and indexing exist

RAG answers questions from an index, not directly from arbitrary files on a user's computer. Ingestion validates a file, extracts text, splits it into retrievable chunks, creates embeddings, and records tenant metadata.

The current **Upload and index** button is a valid administrator workflow for a small internal knowledge base. In production, the same ingestion service is normally triggered by SharePoint, Google Drive, or S3 changes; runs through a durable background queue with retry and status; and detects document updates and deletions.

## Why the bearer token exists

The current development keys demonstrate authentication (who is calling) and authorization (what they may do and which tenant they may search). They are not a production login screen.

In production, Angular redirects users to an identity provider such as Microsoft Entra ID, Okta, Auth0, or Keycloak. That service issues a short-lived signed OIDC/JWT token. Angular attaches it automatically, and FastAPI verifies the signature and tenant/role claims. Users should never type API keys into a finished application.

## Citation policy

The UI now displays normal PDF citations such as `handbook.pdf · p. 12`. The supporting excerpt is collapsed, so users can verify an answer without seeing chunk IDs or similarity scores.

Only PDF has reliable extracted page boundaries. DOCX, TXT, and Markdown citations omit a page number rather than inventing one. A future source connector should add a heading, section, or canonical document URL for those formats.

## One-time page-citation migration

Documents indexed before this update have no page metadata. Create a clean collection in `.env`:

```env
QDRANT_COLLECTION=enterprise_documents_v2
```

Restart the API and upload source documents again. This avoids mixing old vectors with page-aware vectors. Keep the old collection until you validate the migration.

## Answer quality

The existing `gpt-4o-mini` is a cost-efficient development default. Output quality depends on parsing, retrieval, grounded prompting, the answer model, and a repeatable evaluation set. The updated prompt removes internal retrieval wording and refuses unsupported claims.

For a higher-quality non-reasoning answer model, test `CHAT_MODEL=gpt-4.1` against a representative evaluation set before promotion. It works with this project's Chat Completions integration and is documented by OpenAI as its strongest non-reasoning GPT-4.1 model with strong instruction following. [Official OpenAI model documentation](https://developers.openai.com/api/docs/models/gpt-4.1)

## Required before real production use

- Replace development keys with OIDC/JWT and organization/role claims.
- Use a secret manager, TLS, rate limits, and a web application firewall.
- Put ingestion on a durable queue and add document versioning/deletion.
- Add malware scanning and content-type verification before parsing.
- Add reranking and evaluate a versioned golden dataset.
- Operate monitoring, backups, retention, and authorization/prompt-injection tests.
