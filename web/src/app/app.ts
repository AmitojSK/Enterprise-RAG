import { Component, computed, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { NgClass } from '@angular/common';
import { finalize } from 'rxjs';
import { RagApiService } from './api/rag-api.service';
import { ChatMessage } from './models/rag.models';

@Component({
  selector: 'app-root',
  imports: [FormsModule, NgClass],
  templateUrl: './app.html',
  styleUrl: './app.scss'
})
export class App {
  protected question = signal('');
  protected selectedFile = signal<File | null>(null);
  protected uploadStatus = signal('No document selected.');
  protected error = signal('');
  protected isUploading = signal(false);
  protected isAsking = signal(false);
  protected messages = signal<ChatMessage[]>([]);
  protected readonly canAsk = computed(() => this.question().trim().length >= 3 && !this.isAsking());

  constructor(private readonly ragApi: RagApiService) {}

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
      next: (response) => this.uploadStatus.set(
        response.duplicate ? `${response.filename} was already indexed.` : `${response.filename} indexed: ${response.chunks_indexed} chunks.`,
      ),
      error: (error: unknown) => this.showApiError(error, 'Document upload failed.'),
    });
  }

  /** Add the user question immediately, then append the cited assistant answer. */
  protected ask(): void {
    const question = this.question().trim();
    if (question.length < 3 || this.isAsking()) return;
    this.error.set('');
    this.messages.update((messages) => [...messages, { role: 'user', content: question }]);
    this.question.set('');
    this.isAsking.set(true);
    this.ragApi.askQuestion(question).pipe(finalize(() => this.isAsking.set(false))).subscribe({
      next: (response) => this.messages.update((messages) => [...messages, {
        role: 'assistant', content: response.answer, citations: response.citations, requestId: response.request_id,
      }]),
      error: (error: unknown) => this.showApiError(error, 'Question could not be answered.'),
    });
  }

  /** Convert FastAPI's useful `detail` field into a friendly visible error. */
  private showApiError(error: unknown, fallback: string): void {
    this.isUploading.set(false);
    const detail = (error as { error?: { detail?: string } })?.error?.detail;
    this.error.set(detail ?? fallback);
  }
}
