import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { EMPTY, Subject, catchError, merge, switchMap, timer } from 'rxjs';

import { ActivityLogComponent } from './activity-log/activity-log.component';
import { AppHeaderComponent } from './app-header/app-header.component';
import { AppNotificationComponent } from './app-notification/app-notification.component';
import { BackendLauncherComponent } from './backend-launcher/backend-launcher.component';
import { LauncherService } from './launcher.service';
import { LiveStrategyComponent } from './live-strategy/live-strategy.component';
import { NotificationService } from './notification.service';
import { OrderListComponent } from './order-list/order-list.component';
import { Dashboard, TradingService } from './trading.service';

const POLL_MS = 5000;

/** App shell: polls /api/dashboard and hands each slice to its component. */
@Component({
  selector: 'app-root',
  imports: [BackendLauncherComponent, AppHeaderComponent, AppNotificationComponent, ActivityLogComponent, LiveStrategyComponent, OrderListComponent],
  templateUrl: './app.component.html',
  styleUrl: './app.component.css',
})
export class AppComponent {
  private readonly api = inject(TradingService);
  private readonly notifier = inject(NotificationService);
  /** Quit clicked -> show the "stopped" screen instead of the dashboard. */
  readonly stopped = inject(LauncherService).stopped;
  private readonly refresh$ = new Subject<void>();

  readonly data = signal<Dashboard | null>(null);
  readonly backendDown = signal(false);
  readonly lastUpdated = signal<Date | null>(null);

  readonly auth = computed(() => this.data()?.auth);
  readonly ip = computed(() => this.data()?.ip);
  readonly algo = computed(() => this.data()?.algo);
  readonly strat = computed(() => this.data()?.strategy);
  readonly strategyConfig = computed(() => this.data()?.strategy_config);
  readonly orders = computed(() => this.data()?.orders);
  readonly events = computed(() => this.data()?.events ?? []);

  constructor() {
    merge(timer(0, POLL_MS), this.refresh$)
      .pipe(
        switchMap(() =>
          this.api.dashboard().pipe(
            catchError(() => {
              this.backendDown.set(true);
              return EMPTY;
            }),
          ),
        ),
        takeUntilDestroyed(),
      )
      .subscribe((d) => {
        this.data.set(d);
        this.backendDown.set(false);
        this.lastUpdated.set(new Date());
      });

    this.handleLoginRedirect();
  }

  /** Zerodha -> backend /api/auth/callback -> redirects here with ?login=success|failed */
  private handleLoginRedirect(): void {
    const params = new URLSearchParams(window.location.search);
    const login = params.get('login');
    if (!login) return;
    if (login === 'success') this.notifier.ok('Zerodha session connected.');
    else this.notifier.err(`Zerodha login failed: ${params.get('reason') ?? 'unknown error'}`);
    history.replaceState({}, '', window.location.pathname);
  }

  /** Re-poll the dashboard now (after an action changed backend state). */
  refresh(): void {
    this.refresh$.next();
  }
}
