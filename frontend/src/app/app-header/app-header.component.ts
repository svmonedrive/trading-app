import { DatePipe } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { Component, inject, input, output, signal } from '@angular/core';
import { Observable } from 'rxjs';

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

  readonly busy = signal<'login' | 'toggle' | null>(null);

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
