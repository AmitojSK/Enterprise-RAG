/** HTTP client wrapper for the authenticated Enterprise RAG API. */

import { Injectable } from '@angular/core';
import { HttpClient, HttpHeaders } from '@angular/common/http';
import { Observable } from 'rxjs';
import { apiBaseUrl } from './api.config';
import { IngestResponse, QueryResponse } from '../models/rag.models';

@Injectable({ providedIn: 'root' })
export class RagApiService {
  constructor(private readonly http: HttpClient) {}

  /** Upload a document using multipart/form-data, as FastAPI's UploadFile expects. */
  uploadDocument(token: string, file: File): Observable<IngestResponse> {
    const formData = new FormData();
    formData.append('file', file);
    return this.http.post<IngestResponse>(`${apiBaseUrl}/v1/documents`, formData, {
      headers: this.authorizationHeader(token),
    });
  }

  /** Send a question to the RAG service and receive its cited response. */
  askQuestion(token: string, question: string): Observable<QueryResponse> {
    return this.http.post<QueryResponse>(
      `${apiBaseUrl}/v1/query`,
      { question },
      { headers: this.authorizationHeader(token) },
    );
  }

  /** Keep authentication header creation in one place to avoid inconsistencies. */
  private authorizationHeader(token: string): HttpHeaders {
    return new HttpHeaders({ Authorization: `Bearer ${token.trim()}` });
  }
}
