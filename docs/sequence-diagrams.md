# Workflow Sequence Diagrams

These diagrams show the runtime order of the most important methods and the file that owns each method. Read them alongside the source code to connect the architecture to the implementation.

## 1. Document ingestion workflow

A visitor uploads one non-confidential file. The API validates it, detects duplicate file bytes, extracts its text, creates overlapping chunks, generates embeddings, and writes the chunks to Qdrant.

```mermaid
sequenceDiagram
    autonumber
    actor Visitor
    participant API as "api/routes.py\ningest_document()"
    participant DB as "models.py\nDocumentRecord"
    participant Loader as "services/document_loader.py\nextract_pages()"
    participant Chunker as "services/chunking.py\nchunk_pages()"
    participant Embedder as "services/embeddings.py\nOpenAIEmbeddingService.embed()"
    participant Store as "services/vector_store.py\nQdrantStore.upsert()"
    participant Qdrant as "Qdrant vector database"
    participant Audit as "services/audit.py\nlog_ingestion()"

    Visitor->>API: POST /v1/documents + file
    API->>API: Validate filename, type, and byte size
    API->>DB: Find content_hash
    alt Same bytes already indexed
        DB-->>API: Existing document
        API-->>Visitor: 201 duplicate=true
    else New or retryable document
        API->>Loader: extract_pages(filename, content)
        Loader-->>API: Page-aware source text
        API->>Chunker: chunk_pages(pages)
        Chunker-->>API: Overlapping text chunks
        API->>Embedder: embed(chunks)
        Embedder-->>API: Dense vector for every chunk
        API->>Store: upsert(document_id, ...)
        Store->>Store: ensure_collection(vector_size)
        Store->>Qdrant: Upsert vectors + document metadata
        Qdrant-->>Store: Write confirmed
        Store-->>API: Indexing complete
        API->>Audit: log_ingestion(...)
        API-->>Visitor: 201 document_id + chunks_indexed
    end
```

## 2. Grounded question-answering workflow

A visitor asks a question. The service screens obvious instruction overrides, retrieves relevant chunks from the shared library, then asks the LLM to answer only from that evidence.

```mermaid
sequenceDiagram
    autonumber
    actor Visitor
    participant API as "api/routes.py\nquery_knowledge()"
    participant Safety as "security.py\nreject_prompt_injection()"
    participant RAG as "services/rag.py\nRAGService.answer()"
    participant Embedder as "services/embeddings.py\nOpenAIEmbeddingService.embed()"
    participant Store as "services/vector_store.py\nQdrantStore.search()"
    participant Qdrant as "Qdrant vector database"
    participant LLM as "OpenAI chat completion"
    participant Audit as "services/audit.py\nlog_query()"

    Visitor->>API: POST /v1/query + question
    API->>Safety: reject_prompt_injection(question)
    Safety-->>API: Question accepted, or HTTP 400
    API->>RAG: answer(question, document_ids)
    RAG->>Embedder: embed([question])
    Embedder-->>RAG: Query vector
    RAG->>Store: search(vector, top_k, document_ids)
    Store->>Qdrant: query_points(optional document filter)
    Qdrant-->>Store: Relevant chunks and similarity scores
    Store-->>RAG: RetrievedChunk list
    RAG->>LLM: System policy + question + retrieved sources
    LLM-->>RAG: Grounded answer
    RAG-->>API: Answer + Citation objects
    API->>Audit: log_query(request_id, count)
    API-->>Visitor: 200 answer + citations + request_id
```

If Qdrant returns no chunks, `RAGService.answer()` does **not** call the LLM. It returns a clear “no supporting information” response instead of encouraging an ungrounded answer.

## File-to-workflow map

| File | Main responsibility | Key method(s) |
|---|---|---|
| `src/enterprise_rag/api/routes.py` | Public HTTP endpoints and request orchestration | `ingest_document()`, `query_knowledge()` |
| `src/enterprise_rag/security.py` | Prompt-safety gate | `reject_prompt_injection()` |
| `src/enterprise_rag/services/document_loader.py` | Reads supported document formats | `extract_pages()` |
| `src/enterprise_rag/services/chunking.py` | Produces page-aware, overlap-aware chunks | `chunk_pages()` |
| `src/enterprise_rag/services/embeddings.py` | Turns text into semantic vectors | `OpenAIEmbeddingService.embed()` |
| `src/enterprise_rag/services/vector_store.py` | Vector persistence and search | `QdrantStore.upsert()`, `QdrantStore.search()` |
| `src/enterprise_rag/services/rag.py` | Retrieval and grounded answer generation | `RAGService.answer()` |
| `src/enterprise_rag/services/audit.py` | Metadata-only audit events | `log_ingestion()`, `log_query()` |
