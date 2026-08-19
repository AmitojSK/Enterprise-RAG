/**
 * TypeScript versions of the FastAPI request/response contracts in
 * `src/enterprise_rag/schemas.py`. Keeping these types explicit prevents
 * accidental UI/API mismatches as the project grows.
 */

export interface Citation {
  filename: string;
  page_number: number | null;
  excerpt: string;
}

export interface QueryResponse {
  answer: string;
  citations: Citation[];
  request_id: string;
}

export interface IngestResponse {
  document_id: string;
  filename: string;
  chunks_indexed: number;
  status: string;
  /** True means the same file bytes were already indexed in the shared library. */
  duplicate: boolean;
}

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  citations?: Citation[];
  requestId?: string;
}
