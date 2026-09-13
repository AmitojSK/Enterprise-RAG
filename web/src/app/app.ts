import { Component, computed, OnInit, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { NgClass } from '@angular/common';
import { finalize } from 'rxjs';
import { RagApiService } from './api/rag-api.service';
import { PdfViewer } from './pdf-viewer/pdf-viewer';
import { ChatMessage, Citation, DocumentListItem } from './models/rag.models';

@Component({
  selector: 'app-root',
  imports: [FormsModule, NgClass, PdfViewer],
  templateUrl: './app.html',
  styleUrl: './app.scss'
})
export class App implements OnInit {
  protected question = signal('');
  protected selectedFile = signal<File | null>(null);
  protected uploadStatus = signal('No document selected.');
  protected error = signal('');
  protected isUploading = signal(false);
  protected isAsking = signal(false);
  protected messages = signal<ChatMessage[]>([]);
  protected documents = signal<DocumentListItem[]>([]);
  protected readonly canAsk = computed(() => this.question().trim().length >= 3 && !this.isAsking());

  // Left-panel state. The panel collapses to a rail, and switches between
  // browsing the library and viewing one document's source PDF.
  protected panelCollapsed = signal(false);
  protected viewerDocId = signal<string | null>(null);
  protected viewerFilename = signal('');
  protected viewerPage = signal(1);
  protected readonly viewerSrc = computed(() => {
    const id = this.viewerDocId();
    return id ? this.ragApi.documentFileUrl(id) : '';
  });

  constructor(private readonly ragApi: RagApiService) {}

  ngOnInit(): void {
    this.loadDocuments();
  }

  protected loadDocuments(): void {
    this.ragApi.listDocuments().subscribe({
      next: (docs) => this.documents.set(docs),
    });
  }

  /** Permanently delete a document (metadata, vectors, and stored file). */
  protected deleteDocument(doc: DocumentListItem): void {
    if (!confirm(`Delete "${doc.filename}" from the library? This removes it from the database and cannot be undone.`)) {
      return;
    }
    this.ragApi.deleteDocument(doc.document_id).subscribe({
      next: () => {
        this.documents.update((docs) => docs.filter((d) => d.document_id !== doc.document_id));
        // If the deleted document is open in the viewer, return to the library.
        if (this.viewerDocId() === doc.document_id) this.closeViewer();
      },
      error: (err: unknown) => this.showApiError(err, 'Could not delete document.'),
    });
  }

  /** Toggle the left panel between its full width and a collapsed rail. */
  protected togglePanel(): void {
    this.panelCollapsed.update((collapsed) => !collapsed);
  }

  /** Open a document in the left-panel viewer at an optional page. */
  protected openDocument(doc: DocumentListItem, page = 1): void {
    this.panelCollapsed.set(false);
    this.viewerFilename.set(doc.filename);
    this.viewerPage.set(page);
    this.viewerDocId.set(doc.document_id);
  }

  /** Open the document a citation points to, at its cited page. */
  protected openCitation(citation: Citation): void {
    const doc = this.documentForCitation(citation);
    if (!doc) return;
    this.openDocument(doc, citation.page_number ?? 1);
  }

  /** Return to the library list from the viewer. */
  protected closeViewer(): void {
    this.viewerDocId.set(null);
    this.viewerFilename.set('');
  }

  /** Auto-open the source of the most relevant citation when an answer lands.
   *
   * Citations arrive ranked most-relevant first, so opening the first one that
   * resolves to a document puts the strongest evidence in front of the user
   * without a click. Later, clicking any citation switches the viewer to it.
   */
  private autoOpenTopCitation(citations: Citation[]): void {
    for (const citation of citations) {
      const doc = this.documentForCitation(citation);
      if (doc) {
        this.openDocument(doc, citation.page_number ?? 1);
        return;
      }
    }
  }

  /** Resolve a citation to a known document, preferring its ID over filename. */
  private documentForCitation(citation: Citation): DocumentListItem | undefined {
    const docs = this.documents();
    return (
      docs.find((d) => d.document_id === citation.document_id) ??
      docs.find((d) => d.filename === citation.filename)
    );
  }

  /** Capture the selected file; uploading happens only after the user clicks Index. */
  protected chooseFile(event: Event): void {
    const input = event.target as HTMLInputElement;
    this.selectedFile.set(input.files?.[0] ?? null);
    this.uploadStatus.set(this.selectedFile() ? 'Ready to index.' : 'No document selected.');
  }

  /** Send the current file to the public demo API endpoint. */
  protected upload(): void {
    const file = this.selectedFile();
    if (!file) {
      this.error.set('Choose a supported document first.');
      return;
    }
    this.error.set('');
    this.isUploading.set(true);
    this.uploadStatus.set('Extracting text, embedding chunks, and indexing…');
    this.ragApi.uploadDocument(file).pipe(finalize(() => this.isUploading.set(false))).subscribe({
      next: (response) => {
        if (response.status === 'processing') {
          this.uploadStatus.set(`${response.filename} queued for background indexing…`);
          this.pollDocumentStatus(response.document_id, response.filename);
        } else {
          this.uploadStatus.set(
            response.duplicate ? `${response.filename} was already indexed.` : `${response.filename} indexed: ${response.chunks_indexed} chunks.`,
          );
        }
        this.loadDocuments();
      },
      error: (error: unknown) => {
        // Clear the progress line too: showApiError only populates the error
        // banner, so without this the status keeps claiming the document is
        // still being indexed long after the request failed.
        this.uploadStatus.set('Indexing failed.');
        this.showApiError(error, 'Document upload failed.');
      },
    });
  }

  /** Poll the document detail endpoint until a background task finishes. */
  private pollDocumentStatus(documentId: string, filename: string): void {
    const interval = setInterval(() => {
      this.ragApi.getDocument(documentId).subscribe({
        next: (doc) => {
          if (doc.status === 'indexed') {
            clearInterval(interval);
            this.uploadStatus.set(`${filename} indexed: ${doc.chunk_count} chunks.`);
            this.loadDocuments();
          } else if (doc.status === 'failed') {
            clearInterval(interval);
            this.uploadStatus.set(`${filename} failed: ${doc.error_message ?? 'unknown error'}.`);
            this.loadDocuments();
          }
        },
        // Without this the timer runs forever once polling starts failing, and
        // the status line stays on "queued" for a document that will never
        // report back.
        error: (error: unknown) => {
          clearInterval(interval);
          this.uploadStatus.set(`Lost track of ${filename} while it was indexing.`);
          this.showApiError(error, 'Could not check indexing status.');
        },
      });
    }, 2000);
  }

  /** Submit the current prompt when Enter is pressed, while allowing Shift+Enter for multiline input. */
  protected onComposerKeydown(event: Event): void {
    const keyboardEvent = event as KeyboardEvent;
    if (keyboardEvent.shiftKey || keyboardEvent.key !== 'Enter') {
      return;
    }
    keyboardEvent.preventDefault();
    this.ask();
  }

  /** Add the user question immediately, then stream the assistant answer token-by-token. */
  protected ask(): void {
    const question = this.question().trim();
    if (question.length < 3 || this.isAsking()) return;
    this.error.set('');
    this.messages.update((messages) => [...messages, { role: 'user', content: question }]);
    this.question.set('');
    this.isAsking.set(true);

    // Add a placeholder assistant message that fills in as tokens arrive.
    this.messages.update((msgs) => [...msgs, { role: 'assistant', content: '' }]);

    this.ragApi.streamQuestion(
      question,
      (event) => {
        if (event.token) {
          this.messages.update((msgs) => {
            const updated = [...msgs];
            const last = { ...updated[updated.length - 1] };
            last.content += event.token;
            updated[updated.length - 1] = last;
            return updated;
          });
        }
        if (event.citations) {
          const citations = event.citations;
          this.messages.update((msgs) => {
            const updated = [...msgs];
            const last = { ...updated[updated.length - 1] };
            last.citations = citations;
            last.requestId = event.request_id;
            updated[updated.length - 1] = last;
            return updated;
          });
          this.autoOpenTopCitation(citations);
        }
      },
      () => this.isAsking.set(false),
      (err) => { this.error.set(err); this.isAsking.set(false); },
    );
  }

  /** Convert FastAPI's useful `detail` field into a friendly visible error. */
  private showApiError(error: unknown, fallback: string): void {
    this.isUploading.set(false);
    const detail = (error as { error?: { detail?: string } })?.error?.detail;
    this.error.set(detail ?? fallback);
  }
}
