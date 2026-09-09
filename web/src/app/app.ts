import { Component, computed, OnInit, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { NgClass } from '@angular/common';
import { finalize } from 'rxjs';
import { RagApiService } from './api/rag-api.service';
import { ChatMessage, DocumentListItem } from './models/rag.models';

@Component({
  selector: 'app-root',
  imports: [FormsModule, NgClass],
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

  constructor(private readonly ragApi: RagApiService) {}

  ngOnInit(): void {
    this.loadDocuments();
  }

  protected loadDocuments(): void {
    this.ragApi.listDocuments().subscribe({
      next: (docs) => this.documents.set(docs),
    });
  }

  protected deleteDocument(doc: DocumentListItem): void {
    this.ragApi.deleteDocument(doc.document_id).subscribe({
      next: () => this.documents.update((docs) => docs.filter((d) => d.document_id !== doc.document_id)),
      error: (err: unknown) => this.showApiError(err, 'Could not delete document.'),
    });
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
          this.messages.update((msgs) => {
            const updated = [...msgs];
            const last = { ...updated[updated.length - 1] };
            last.citations = event.citations;
            last.requestId = event.request_id;
            updated[updated.length - 1] = last;
            return updated;
          });
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
