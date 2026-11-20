import { HttpErrorResponse } from '@angular/common/http';
import { Component, DestroyRef, ElementRef, computed, effect, inject, input, output, signal, viewChild } from '@angular/core';

import { LauncherService, SetupStatus } from '../launcher.service';

const POLL_MS = 1000;
const GIVE_UP_MS = 45_000;
const KITE_APPS_URL = 'https://developers.kite.trade/apps';

/**
 * Setup & start dialog (talks to launcher.py, so it works while the backend is down).
 *
 * - Opens by itself, as a BLOCKING modal, whenever the backend is unreachable: first run it walks through
 *   creating the Kite app and saving the API key/secret; every other day it's just "Start Backend Server".
 * - Can also be opened on purpose from the header's Setup button (then it can be closed).
 * - Closes by itself once the backend is reachable (unless opened on purpose).
 */
@Component({
  selector: 'app-backend-launcher',
  templateUrl: './backend-launcher.component.html',
  styleUrl: './backend-launcher.component.css',
})
export class BackendLauncherComponent {
  private readonly launcher = inject(LauncherService);

  readonly backendDown = input(false);
  /** Backend came up - parent should re-poll now so the dialog disappears immediately. */
  readonly started = output<void>();

  readonly kiteAppsUrl = KITE_APPS_URL;
  readonly setup = signal<SetupStatus | null>(null);
  /** Page not served by launcher.py (e.g. `ng serve`) - setup and start must be done by hand. */
  readonly noLauncher = signal(false);

  readonly editingKeys = signal(false);
  readonly apiKey = signal('');
  readonly apiSecret = signal('');
  readonly savingKeys = signal(false);
  readonly keysMsg = signal<{ ok: boolean; text: string } | null>(null);

  readonly phase = signal<'idle' | 'starting' | 'failed'>('idle');
  readonly message = signal<string | null>(null);
  readonly copied = signal(false);

  readonly open = computed(() => !this.launcher.stopped() && (this.backendDown() || this.launcher.setupOpen()));
  /** Last lines of the backend log after a failed start (app mode runs the backend hidden). */
  readonly logTail = signal<string[]>([]);
  readonly logText = computed(() => this.logTail().join('\n'));
  readonly keysDone = computed(() => !!this.setup()?.api_key_set && !!this.setup()?.api_secret_set);
  readonly showKeyForm = computed(() => !this.keysDone() || this.editingKeys());

  private readonly dialog = viewChild.required<ElementRef<HTMLDialogElement>>('dlg');
  private watch?: ReturnType<typeof setInterval>;

  constructor() {
    inject(DestroyRef).onDestroy(() => clearInterval(this.watch));

    effect(() => {
      const dlg = this.dialog().nativeElement;
      if (this.open()) {
        if (!dlg.open) {
          dlg.showModal();
          this.refreshSetup();
        }
      } else if (dlg.open) {
        dlg.close();
        this.resetTransient();
      }
    });

    // Backend just came back while the dialog stays open (opened via Setup): refresh the checklist.
    effect(() => {
      if (!this.backendDown() && this.dialog().nativeElement.open) this.refreshSetup();
    });
  }

  refreshSetup(): void {
    this.launcher.setupStatus().subscribe({
      next: (s) => { this.setup.set(s); this.noLauncher.set(false); },
      error: () => this.noLauncher.set(true),
    });
  }

  /** Esc closes only when the dialog isn't blocking. */
  onCancel(e: Event): void {
    e.preventDefault();
    if (!this.backendDown()) this.close();
  }

  close(): void {
    this.launcher.setupOpen.set(false);
  }

  async copyRedirect(url: string): Promise<void> {
    try {
      await navigator.clipboard.writeText(url);
      this.copied.set(true);
      setTimeout(() => this.copied.set(false), 2000);
    } catch {
      /* clipboard blocked - the URL is visible to copy by hand */
    }
  }

  saveKeys(): void {
    this.savingKeys.set(true);
    this.keysMsg.set(null);
    this.launcher.saveKeys(this.apiKey(), this.apiSecret()).subscribe({
      next: (r) => {
        this.savingKeys.set(false);
        this.keysMsg.set({ ok: true, text: r.message });
        this.apiKey.set('');
        this.apiSecret.set('');
        this.editingKeys.set(false);
        this.refreshSetup();
        if (this.backendDown() && !r.restart_needed) this.start(); // keys saved -> start straight away
      },
      error: (e: HttpErrorResponse) => {
        this.savingKeys.set(false);
        this.keysMsg.set({ ok: false, text: e.error?.message ?? e.message });
      },
    });
  }

  start(): void {
    this.phase.set('starting');
    this.message.set(null);
    this.logTail.set([]);
    this.launcher.startBackend().subscribe({
      next: (r) => {
        this.message.set(r.message);
        this.waitUntilUp();
      },
      error: (e: HttpErrorResponse) => {
        if (e.status === 404 || e.status === 0) {
          this.noLauncher.set(true);
          this.phase.set('idle');
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
      this.launcher.status().subscribe({
        next: (s) => {
          if (s.backend_running) {
            clearInterval(this.watch);
            this.phase.set('idle');
            this.message.set(null);
            this.started.emit();
          } else if (!s.starting || Date.now() - t0 > GIVE_UP_MS) {
            // process exited (crash) or took too long
            clearInterval(this.watch);
            this.phase.set('failed');
            this.message.set(s.app_mode
              ? 'The backend did not start. Its last log lines are below.'
              : 'Backend did not come up — check the "NIFTYBEES Backend" window for the error.');
            if (s.app_mode) this.launcher.backendLog().subscribe((l) => this.logTail.set(l.lines));
          }
        },
        error: () => {
          clearInterval(this.watch);
          this.noLauncher.set(true);
          this.phase.set('idle');
        },
      });
    }, POLL_MS);
  }

  private resetTransient(): void {
    clearInterval(this.watch);
    this.phase.set('idle');
    this.message.set(null);
    this.editingKeys.set(false);
    this.keysMsg.set(null);
    this.apiKey.set('');
    this.apiSecret.set('');
    this.logTail.set([]);
  }
}
