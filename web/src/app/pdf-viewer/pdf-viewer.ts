import { Component, ElementRef, HostListener, Input, OnChanges, OnDestroy, SimpleChanges, ViewChild, output, signal } from '@angular/core';
import type { PDFDocumentProxy } from 'pdfjs-dist';

/** The pdf.js module, loaded on demand. */
type PdfjsModule = typeof import('pdfjs-dist');

/** Minimal shape of a pdf.js render task; avoids a deep type import path. */
interface CancellableRender {
  cancel(): void;
  promise: Promise<void>;
}

/**
 * Renders a PDF (served by the API) to a canvas, one page at a time, with
 * prev/next navigation and a `page` input the parent drives to jump to a
 * cited page. Single-page-at-a-time keeps memory and render cost low, which
 * matters because this sits in a resizable side panel.
 *
 * pdf.js is ~0.5 MB, so it is loaded with a dynamic import: it becomes its own
 * lazy chunk instead of weighing down the initial bundle, and nothing about the
 * library runs until a document is actually opened.
 */
@Component({
  selector: 'app-pdf-viewer',
  imports: [],
  templateUrl: './pdf-viewer.html',
  styleUrl: './pdf-viewer.scss',
})
export class PdfViewer implements OnChanges, OnDestroy {
  /** URL of the PDF bytes; changing it loads a new document. */
  @Input() src = '';
  /** 1-based page to display; changing it jumps within the loaded document. */
  @Input() page = 1;
  /** Emits the page actually shown, so the parent's `page` mirrors it. Without
   *  this, navigating internally leaves the parent's value stale, and clicking
   *  a citation whose page equals that stale value would not trigger a jump. */
  pageChange = output<number>();

  @ViewChild('canvas', { static: true }) private canvasRef!: ElementRef<HTMLCanvasElement>;

  protected pageNumber = signal(1);
  protected totalPages = signal(0);
  protected loading = signal(false);
  protected errorMsg = signal('');

  private pdfjs: PdfjsModule | null = null;
  private pdf: PDFDocumentProxy | null = null;
  // Kept so the document can be torn down: in pdf.js v6, destroy() lives on the
  // loading task rather than on PDFDocumentProxy.
  private loadingTask: { destroy(): Promise<void> } | null = null;
  private currentSrc = '';
  private renderTask: CancellableRender | null = null;

  async ngOnChanges(changes: SimpleChanges): Promise<void> {
    if (changes['src'] && this.src !== this.currentSrc) {
      await this.loadDocument();
    } else if (changes['page'] && this.pdf && this.page !== this.pageNumber()) {
      // Guard against the value the parent just mirrored back from pageChange:
      // only re-render when the requested page differs from what is shown.
      await this.goToPage(this.page);
    }
  }

  /** Load pdf.js once, wiring up a same-origin module worker on first use. */
  private async getPdfjs(): Promise<PdfjsModule> {
    if (!this.pdfjs) {
      const pdfjs = await import('pdfjs-dist');
      // Angular's esbuild bundles this worker as a same-origin chunk, so there
      // is no CDN dependency or .mjs MIME-type problem. The path must be a
      // real relative path (not a bare package specifier) for esbuild to detect
      // and bundle it: from this file up to web/, then into node_modules.
      pdfjs.GlobalWorkerOptions.workerPort = new Worker(
        new URL('../../../node_modules/pdfjs-dist/build/pdf.worker.min.mjs', import.meta.url),
        { type: 'module' },
      );
      this.pdfjs = pdfjs;
    }
    return this.pdfjs;
  }

  private async loadDocument(): Promise<void> {
    this.currentSrc = this.src;
    this.errorMsg.set('');
    await this.destroyPdf();
    if (!this.src) {
      this.totalPages.set(0);
      return;
    }
    this.loading.set(true);
    try {
      const pdfjs = await this.getPdfjs();
      const task = pdfjs.getDocument({ url: this.src, withCredentials: false });
      this.loadingTask = task;
      this.pdf = await task.promise;
      this.totalPages.set(this.pdf.numPages);
      await this.renderPage(this.clampPage(this.page));
    } catch {
      await this.destroyPdf();
      this.totalPages.set(0);
      // A 404 here means the document was indexed before file storage existed
      // (or storage is off); guide the user rather than showing a raw error.
      this.errorMsg.set(
        'Preview not available. This document was indexed before file viewing was enabled — delete and re-upload it to view the source.',
      );
    } finally {
      this.loading.set(false);
    }
  }

  protected async prev(): Promise<void> {
    await this.goToPage(this.pageNumber() - 1);
  }

  protected async next(): Promise<void> {
    await this.goToPage(this.pageNumber() + 1);
  }

  private async goToPage(n: number): Promise<void> {
    if (!this.pdf) return;
    await this.renderPage(this.clampPage(n));
  }

  private clampPage(n: number): number {
    return Math.min(Math.max(n, 1), this.pdf?.numPages ?? 1);
  }

  private async renderPage(n: number): Promise<void> {
    if (!this.pdf) return;
    const page = await this.pdf.getPage(n);
    this.pageNumber.set(n);
    this.pageChange.emit(n);

    const canvas = this.canvasRef.nativeElement;
    const context = canvas.getContext('2d');
    if (!context) return;
    const container = canvas.parentElement;
    const unscaled = page.getViewport({ scale: 1 });
    // Fit to the container's *content* width. clientWidth includes padding, so
    // sizing the canvas to it makes the canvas wider than its box and the panel
    // grows a horizontal scrollbar; subtract the padding to avoid that.
    let available = unscaled.width;
    if (container) {
      const style = getComputedStyle(container);
      available = container.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
    }
    const scale = Math.max(available / unscaled.width, 0.2);
    const viewport = page.getViewport({ scale });

    // Render at device pixel ratio for crisp text on high-DPI screens, then
    // scale the canvas back down with CSS so layout width stays as computed.
    const ratio = window.devicePixelRatio || 1;
    canvas.width = Math.floor(viewport.width * ratio);
    canvas.height = Math.floor(viewport.height * ratio);
    canvas.style.width = `${Math.floor(viewport.width)}px`;
    canvas.style.height = `${Math.floor(viewport.height)}px`;
    context.setTransform(ratio, 0, 0, ratio, 0, 0);

    // Cancel an in-flight render before starting another, or fast page changes
    // throw "cannot use the same canvas during multiple render operations".
    if (this.renderTask) {
      try {
        this.renderTask.cancel();
      } catch {
        /* already settled */
      }
    }
    this.renderTask = page.render({ canvasContext: context, viewport, canvas }) as CancellableRender;
    try {
      await this.renderTask.promise;
    } catch {
      /* cancelled by a newer render */
    }
  }

  private resizeTimer: ReturnType<typeof setTimeout> | null = null;

  @HostListener('window:resize')
  onResize(): void {
    // Debounce: re-render the current page at the new panel width once resizing
    // settles, so text stays sharp instead of stretched.
    if (this.resizeTimer) clearTimeout(this.resizeTimer);
    this.resizeTimer = setTimeout(() => {
      if (this.pdf) void this.renderPage(this.pageNumber());
    }, 150);
  }

  private async destroyPdf(): Promise<void> {
    if (this.renderTask) {
      try {
        this.renderTask.cancel();
      } catch {
        /* already settled */
      }
      this.renderTask = null;
    }
    if (this.loadingTask) {
      const task = this.loadingTask;
      this.loadingTask = null;
      this.pdf = null;
      try {
        await task.destroy();
      } catch {
        /* nothing to clean up */
      }
    }
  }

  ngOnDestroy(): void {
    if (this.resizeTimer) clearTimeout(this.resizeTimer);
    void this.destroyPdf();
  }
}
