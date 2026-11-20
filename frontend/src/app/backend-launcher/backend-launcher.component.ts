import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { Component, DestroyRef, ElementRef, effect, inject, input, output, signal, viewChild } from '@angular/core';

/** Served by launcher.py on the same origin as the dashboard (not by the backend - it's the thing that's down). */
const LAUNCHER = '/launcher';
const POLL_MS = 1000;
const GIVE_UP_MS = 45_000;

interface LauncherStatus {
  backend_running: boolean;
  starting: boolean;
  pid: number | null;
}

/**
 * Full-page blocking dialog shown only while the backend is unreachable, with a "Start Backend Server"
 * button. Closes by itself as soon as the dashboard poll reaches the backend again; Esc can't dismiss it.
 */
@Component({
  selector: 'app-backend-launcher',
  templateUrl: './backend-launcher.component.html',
  styleUrl: './backend-launcher.component.css',
})
export class BackendLauncherComponent {
  private readonly http = inject(HttpClient);

  readonly backendDown = input(false);
  /** Backend came up - parent should re-poll now so the bar disappears immediately. */
  readonly started = output<void>();

  readonly phase = signal<'idle' | 'starting' | 'failed' | 'no-launcher'>('idle');
  readonly message = signal<string | null>(null);

  private readonly dialog = viewChild.required<ElementRef<HTMLDialogElement>>('dlg');
  private watch?: ReturnType<typeof setInterval>;

  constructor() {
    inject(DestroyRef).onDestroy(() => clearInterval(this.watch));

    // Open as a modal (page behind becomes inert) while the backend is down; close when it's back.
    effect(() => {
      const dlg = this.dialog().nativeElement;
      if (this.backendDown()) {
        if (!dlg.open) dlg.showModal();
      } else if (dlg.open) {
        dlg.close();
        clearInterval(this.watch);
        this.phase.set('idle');
        this.message.set(null);
      }
    });
  }

  start(): void {
    this.phase.set('starting');
    this.message.set(null);
    this.http.post<{ ok: boolean; message: string }>(`${LAUNCHER}/start-backend`, {}).subscribe({
      next: (r) => {
        this.message.set(r.message);
        this.waitUntilUp();
      },
      error: (e: HttpErrorResponse) => {
        if (e.status === 404 || e.status === 0 || e.status === 501) {
          this.phase.set('no-launcher');
        } else {
          this.phase.set('failed');
          this.message.set(e.error?.message ?? e.message);
        }
      },
    });
  }

  private waitUntilUp(): void {
    clearInterval(this.watch);
    const t0 = Date.now();
    this.watch = setInterval(() => {
      this.http.get<LauncherStatus>(`${LAUNCHER}/status`).subscribe({
        next: (s) => {
          if (s.backend_running) {
            clearInterval(this.watch);
            this.phase.set('idle');
            this.message.set(null);
            this.started.emit();
          } else if (Date.now() - t0 > GIVE_UP_MS) {
            clearInterval(this.watch);
            this.phase.set('failed');
            this.message.set('Backend did not come up within 45 s — check the "NIFTYBEES Backend" window for the error.');
          }
        },
        error: () => {
          clearInterval(this.watch);
          this.phase.set('no-launcher');
        },
      });
    }, POLL_MS);
  }
}
