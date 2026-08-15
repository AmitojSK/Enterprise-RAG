# Workflow Sequence Diagrams

These diagrams show the runtime order of the most important methods and the file that owns each method. Read them alongside the source code to connect the architecture to the implementation.

## 1. Document ingestion workflow

An administrator uploads one file. The API validates it, extracts its text, creates overlapping chunks, generates embeddings, and writes the chunks plus their tenant metadata to Qdrant.

```mermaid
sequenceDiagram
    autonumber
    actor Admin
    participant API as "api/routes.py\ningest_document()"
    participant Auth as "security.py\nrequire_admin()"
    participant Loader as "services/document_loader.py\nextract_text()"
    participant Chunker as "services/chunking.py\nchunk_text()"
    participant Embedder as "services/embeddings.py\nOpenAIEmbeddingService.embed()"
    participant Store as "services/vector_store.py\nQdrantStore.upsert()"
    participant Qdrant as "Qdrant vector database"
    participant Audit as "services/audit.py\nlog_ingestion()"

    Admin->>API: POST /v1/documents + bearer key + file
    API->>Auth: Validate role
    Auth-->>API: Principal(tenant_id, role=admin)
    API->>API: Check filename, type, and byte size
    API->>Loader: extract_text(filename, content)
    Loader-->>API: Plain document text
    API->>Chunker: chunk_text(text)
    Chunker-->>API: Overlapping text chunks
    API->>Embedder: embed(chunks)
    Embedder-->>API: Dense vector for every chunk
    API->>Store: upsert(tenant_id, document_id, ...)
    Store->>Store: ensure_collection(vector_size)
    Store->>Qdrant: Upsert vectors + tenant/document metadata
    Qdrant-->>Store: Write confirmed
    Store-->>API: Indexing complete
    API->>Audit: log_ingestion(...)
    API-->>Admin: 201 document_id + chunks_indexed
```

Important security point: `QdrantStore.upsert()` stores the `tenant_id` on every chunk. This metadata is later mandatory in search filters.

## 2. Grounded question-answering workflow

A reader asks a question. The service validates the caller, screens obvious prompt attacks, searches only the caller's tenant data, then asks the LLM to answer from retrieved sources.

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant API as "api/routes.py\nquery_knowledge()"
    participant Auth as "security.py\ncurrent_principal()"
    participant Safety as "security.py\nreject_prompt_injection()"
    participant RAG as "services/rag.py\nRAGService.answer()"
    participant Embedder as "services/embeddings.py\nOpenAIEmbeddingService.embed()"
    participant Store as "services/vector_store.py\nQdrantStore.search()"
    participant Qdrant as "Qdrant vector database"
    participant LLM as "OpenAI chat completion"
    participant Audit as "services/audit.py\nlog_query()"

    User->>API: POST /v1/query + question
    API->>Auth: Validate bearer key
    Auth-->>API: Principal(tenant_id, role)
    API->>Safety: reject_prompt_injection(question)
    Safety-->>API: Question accepted, or HTTP 400
    API->>RAG: answer(tenant_id, question, document_ids)
    RAG->>Embedder: embed([question])
    Embedder-->>RAG: Query vector
    RAG->>Store: search(tenant_id, vector, top_k, document_ids)
    Store->>Qdrant: query_points(filter tenant_id = caller tenant)
    Qdrant-->>Store: Relevant chunks and similarity scores
    Store-->>RAG: RetrievedChunk list
    RAG->>RAG: Build source-labelled context
    RAG->>LLM: System policy + question + retrieved sources
    LLM-->>RAG: Grounded answer with [Source N] references
    RAG-->>API: Answer + Citation objects
    API->>Audit: log_query(request_id, tenant_id, count)
    API-->>User: 200 answer + citations + request_id
```

If Qdrant returns no chunks, `RAGService.answer()` does **not** call the LLM. It returns a clear “no supporting information” response instead of encouraging an ungrounded answer.

## 3. Authentication and authorization decision

```mermaid
sequenceDiagram
    autonumber
    participant Request as "Incoming request"
    participant Bearer as "security.py\nHTTPBearer"
    participant Principal as "security.py\ncurrent_principal()"
    participant Admin as "security.py\nrequire_admin()"
    participant Endpoint as "api/routes.py\nendpoint method"

    Request->>Bearer: Read Authorization: Bearer token
    Bearer-->>Principal: Token credentials
    Principal->>Principal: parse_api_keys() + constant-time comparison
    alt Token missing or invalid
        Principal-->>Request: 401 Unauthorized
    else Valid token
        Principal-->>Endpoint: Principal(tenant_id, role)
        opt Document-ingestion endpoint
            Endpoint->>Admin: require_admin(principal)
            alt Role is not admin
                Admin-->>Request: 403 Forbidden
            else Role is admin
                Admin-->>Endpoint: Allow ingestion
            end
        end
    end
```

The `API_KEYS` mechanism is intentionally simple for local learning. In a real deployment, replace `current_principal()` with OIDC/JWT verification and take the tenant and role claims from the identity provider.

## File-to-workflow map

| File | Main responsibility | Key method(s) |
|---|---|---|
| `src/enterprise_rag/api/routes.py` | HTTP endpoints and request orchestration | `ingest_document()`, `query_knowledge()` |
| `src/enterprise_rag/security.py` | Authentication, roles, initial prompt safety | `current_principal()`, `require_admin()`, `reject_prompt_injection()` |
| `src/enterprise_rag/services/document_loader.py` | Reads supported document formats | `extract_text()` |
| `src/enterprise_rag/services/chunking.py` | Produces overlap-aware chunks | `chunk_text()` |
| `src/enterprise_rag/services/embeddings.py` | Turns text into semantic vectors | `OpenAIEmbeddingService.embed()` |
| `src/enterprise_rag/services/vector_store.py` | Tenant-filtered vector persistence/search | `QdrantStore.upsert()`, `QdrantStore.search()` |
| `src/enterprise_rag/services/rag.py` | Retrieval and grounded answer generation | `RAGService.answer()` |
| `src/enterprise_rag/services/audit.py` | Metadata-only audit events | `log_ingestion()`, `log_query()` |
| `src/enterprise_rag/config.py` | Environment-backed configuration | `get_settings()` |
