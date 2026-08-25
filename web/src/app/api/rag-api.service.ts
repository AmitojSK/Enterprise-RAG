/** HTTP client wrapper for the public Enterprise RAG demo API. */

import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { apiBaseUrl } from './api.config';
import { DocumentDetail, DocumentListItem, IngestResponse, QueryResponse, StreamTokenEvent } from '../models/rag.models';

@Injectable({ providedIn: 'root' })
export class RagApiService {
  constructor(private readonly http: HttpClient) {}

  /** Upload a document using multipart/form-data, as FastAPI's UploadFile expects. */
  uploadDocument(file: File): Observable<IngestResponse> {
    const formData = new FormData();
    formData.append('file', file);
    return this.http.post<IngestResponse>(`${apiBaseUrl}/v1/documents`, formData);
  }

  /** List all documents in the shared library. */
  listDocuments(): Observable<DocumentListItem[]> {
    return this.http.get<DocumentListItem[]>(`${apiBaseUrl}/v1/documents`);
  }

  /** Get full metadata for a single document. */
  getDocument(documentId: string): Observable<DocumentDetail> {
    return this.http.get<DocumentDetail>(`${apiBaseUrl}/v1/documents/${documentId}`);
  }

  /** Delete a document and its vectors from the shared library. */
  deleteDocument(documentId: string): Observable<void> {
    return this.http.delete<void>(`${apiBaseUrl}/v1/documents/${documentId}`);
  }

  /** Send a question to the RAG service and receive its cited response. */
  askQuestion(question: string): Observable<QueryResponse> {
    return this.http.post<QueryResponse>(
      `${apiBaseUrl}/v1/query`,
      { question },
    );
  }

  /** Stream tokens via SSE from the query/stream endpoint. */
  streamQuestion(question: string, onToken: (event: StreamTokenEvent) => void, onDone: () => void, onError: (err: string) => void): EventSource {
    const url = `${apiBaseUrl}/v1/query/stream`;
    const eventSource = new EventSource(url, { withCredentials: false });
    // SSE GET won't work with a POST body; use fetch + ReadableStream instead.
    eventSource.close();

    const controller = new AbortController();
    fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question }),
      signal: controller.signal,
    }).then(async (response) => {
      if (!response.ok || !response.body) {
        onError(`API error: ${response.status}`);
        return;
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() ?? '';
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          const payload = line.slice(6).trim();
          if (payload === '[DONE]') { onDone(); return; }
          try { onToken(JSON.parse(payload)); } catch { /* skip malformed */ }
        }
      }
      onDone();
    }).catch((err) => {
      if (!controller.signal.aborted) onError(String(err));
    });

    // Return a fake EventSource so callers can abort via .close()
    return { close: () => controller.abort() } as unknown as EventSource;
  }
}
