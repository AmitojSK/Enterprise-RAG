/** HTTP client wrapper for the public Enterprise RAG demo API. */

import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { apiBaseUrl } from './api.config';
import { IngestResponse, QueryResponse } from '../models/rag.models';

@Injectable({ providedIn: 'root' })
export class RagApiService {
  constructor(private readonly http: HttpClient) {}

  /** Upload a document using multipart/form-data, as FastAPI's UploadFile expects. */
  uploadDocument(file: File): Observable<IngestResponse> {
    const formData = new FormData();
    formData.append('file', file);
    return this.http.post<IngestResponse>(`${apiBaseUrl}/v1/documents`, formData);
  }

  /** Send a question to the RAG service and receive its cited response. */
  askQuestion(question: string): Observable<QueryResponse> {
    return this.http.post<QueryResponse>(
      `${apiBaseUrl}/v1/query`,
      { question },
    );
  }
}
