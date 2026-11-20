import { DatePipe } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { Component, inject, input, output, signal } from '@angular/core';
import { Observable } from 'rxjs';

import { LauncherService } from '../launcher.service';
import { NotificationService } from '../notification.service';
import { AlgoStatus, AuthStatus, TradingService } from '../trading.service';

/** Sticky top bar: brand + last update, Zerodha session badge/login, PAPER/LIVE badge, algo kill switch. */
@Component({
  selector: 'app-header',
  imports: [DatePipe],
  templateUrl: './app-header.component.html',
  styleUrl: './app-header.component.css',
})
export class AppHeaderComponent {
  private readonly api = inject(TradingService);
  private readonly notifier = inject(NotificationService);

  readonly auth = input<AuthStatus | null | undefined>(null);
  readonly algo = input<AlgoStatus | null | undefined>(null);
  readonly lastUpdated = input<Date | null>(null);
  /** Algo toggled - parent should re-poll now. */
  readonly changed = output<void>();

  readonly busy = signal<'login' | 'toggle' | 'quit' | null>(null);
  private readonly launcher = inject(LauncherService);

  openSetup(): void {
    this.launcher.openSetup();
  }

  /** Stop backend + launcher. Closing the window alone keeps the bot running in the background. */
  quit(): void {
    const active = this.algo()?.algo_active;
    const msg = active
      ? 'Quit NIFTYBEES Algo?\n\nThe Algo is RUNNING - quitting stops it, so no orders will be placed until you open the app again.'
      : 'Quit NIFTYBEES Algo?\n\nThis stops the backend. Open the app again from its shortcut.';
    if (!confirm(msg)) return;
    this.busy.set('quit');
    this.launcher.quit().subscribe({
      next: () => {
        this.busy.set(null);
        this.launcher.stopped.set(true);
      },
      error: () => {
        this.busy.set(null);
        this.notifier.show({ kind: 'err', text: "Quit only works when the app was opened from its shortcut (or start.bat)." });
      },
    });
  }

  login(): void {
    this.run('login', this.api.loginUrl(), (r) => (window.location.href = r.login_url));
  }

  toggleAlgo(): void {
    const a = this.algo();
    if (!a) return;
    const turningOn = !a.algo_active;
    if (turningOn && a.mode === 'LIVE' &&
        !confirm('Start the NIFTYBEES bot in LIVE mode? It will place REAL LIMIT orders on your Zerodha account.')) {
      return;
    }
    this.run('toggle', this.api.toggleAlgo(turningOn), (r) => {
      this.notifier.show({ kind: 'ok', text: r.algo_active ? 'Algo started.' : 'Algo paused (kill switch engaged).' });
      this.changed.emit();
    });
  }

  private run<T>(key: 'login' | 'toggle', obs: Observable<T>, onOk: (v: T) => void): void {
    this.busy.set(key);
    obs.subscribe({
      next: (v) => {
        this.busy.set(null);
        onOk(v);
      },
      error: (e: HttpErrorResponse) => {
        this.busy.set(null);
        this.notifier.show({ kind: 'err', text: e.error?.detail ?? e.message ?? 'Request failed' });
      },
    });
  }
}
