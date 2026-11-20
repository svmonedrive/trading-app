import { DatePipe, DecimalPipe } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { Component, computed, effect, inject, input, output, signal } from '@angular/core';
import { Observable } from 'rxjs';

import { NotificationService } from '../notification.service';
import {
  FieldValues, Indicator, ParamSpec, Preset, PresetsCatalog, StrategyConfig, StrategySnapshot, TradingService,
} from '../trading.service';
import { AddStrategyDialogComponent } from './add-strategy-dialog/add-strategy-dialog.component';
import { FormAction, ParamFormComponent } from './param-form/param-form.component';

/** Expandable row id for the shared order settings (strategy rows use the strategy key). */
const ORDERS = '__order_settings';

/**
 * Live strategy: what the bot sees right now (price, the selected strategy's indicators, holding, armed rules)
 * and the strategy list - one selected strategy (green) that stays until changed, each with editable limits.
 */
@Component({
  selector: 'app-live-strategy',
  imports: [DatePipe, DecimalPipe, ParamFormComponent, AddStrategyDialogComponent],
  templateUrl: './live-strategy.component.html',
  styleUrl: './live-strategy.component.css',
})
export class LiveStrategyComponent {
  private readonly api = inject(TradingService);
  private readonly notifier = inject(NotificationService);

  readonly snapshot = input<StrategySnapshot | null | undefined>(null);
  readonly config = input<StrategyConfig | null | undefined>(null);
  /** How often the backend scheduler runs the check automatically. */
  readonly intervalSeconds = input<number | null | undefined>(null);
  /** LIVE mode: changes to the running strategy ask for confirmation. */
  readonly liveMode = input(false);
  /** Backend state changed - parent should re-poll now. */
  readonly changed = output<void>();

  readonly ORDERS = ORDERS;
  readonly running = signal(false);
  readonly catalog = signal<PresetsCatalog | null>(null);
  /** Which row is expanded: a strategy key, ORDERS, or null. */
  readonly expanded = signal<string | null>(null);
  /** Row currently saving, and the last error per row. */
  readonly savingRow = signal<string | null>(null);
  readonly errors = signal<Record<string, string | null>>({});
  readonly addOpen = signal(false);
  private catalogLoading = false;

  readonly activeName = computed(() => this.presetName(this.config()?.active));

  constructor() {
    // Load the strategy list once the backend is reachable (it only changes when the backend code does).
    effect(() => {
      if (this.config() && !this.catalog() && !this.catalogLoading) this.loadCatalog();
    });
  }

  private loadCatalog(): void {
    this.catalogLoading = true;
    this.api.presets().subscribe({
      next: (c) => { this.catalog.set(c); this.catalogLoading = false; },
      error: () => { this.catalogLoading = false; },
    });
  }

  // ---------------------------------------------------------------- actions
  runCheckNow(): void {
    this.running.set(true);
    this.api.runCheckNow().subscribe({
      next: (s) => {
        this.running.set(false);
        this.notifier.show(
          s.status === 'error'
            ? { kind: 'err', text: `Check failed: ${s.error}` }
            : { kind: 'ok', text: `Signal: ${s.signal} — ${s.last_action ?? s.reason}` },
        );
        this.changed.emit();
      },
      error: (e: HttpErrorResponse) => {
        this.running.set(false);
        this.notifier.err(this.errorText(e));
      },
    });
  }

  toggle(row: string): void {
    this.expanded.set(this.expanded() === row ? null : row);
  }

  strategyActions(key: string): FormAction[] {
    return key === this.config()?.active
      ? [{ id: 'save', label: 'Save limits', primary: true }]
      : [{ id: 'save', label: 'Save limits' }, { id: 'select', label: 'Save & select this strategy', primary: true }];
  }

  readonly orderActions: FormAction[] = [{ id: 'save', label: 'Save order settings', primary: true }];

  onStrategyForm(p: Preset, e: { action: string; values: FieldValues }): void {
    const isActive = p.key === this.config()?.active;
    if (e.action === 'select') {
      if (!this.confirmLive(`switch the bot to "${p.name}"`)) return;
      // Save the limits first, then select - selecting validates the saved limits.
      this.save(p.key, this.api.saveLimits(p.key, e.values), () =>
        this.save(p.key, this.api.selectStrategy(p.key), () => {
          this.notifier.ok(`"${p.name}" is now the selected strategy.`);
          this.expanded.set(null);
        }),
      );
      return;
    }
    if (isActive && !this.confirmLive(`change the limits of the running strategy "${p.name}"`)) return;
    this.save(p.key, this.api.saveLimits(p.key, e.values), () => this.notifier.ok(`${p.name} limits saved.`));
  }

  onOrderForm(e: { action: string; values: FieldValues }): void {
    if (!this.confirmLive('change the order settings')) return;
    this.save(ORDERS, this.api.saveOrderSettings(e.values), () => this.notifier.ok('Order settings saved.'));
  }

  private save(row: string, obs: Observable<StrategyConfig>, onOk: () => void): void {
    this.savingRow.set(row);
    this.errors.update((m) => ({ ...m, [row]: null }));
    obs.subscribe({
      next: () => {
        this.savingRow.set(null);
        onOk();
        this.changed.emit();
      },
      error: (e: HttpErrorResponse) => {
        this.savingRow.set(null);
        this.errors.update((m) => ({ ...m, [row]: this.errorText(e) }));
      },
    });
  }

  // ---------------------------------------------------------------- view helpers
  presetName(key: string | undefined): string {
    return this.catalog()?.presets.find((p) => p.key === key)?.name ?? key ?? '—';
  }

  /** "RSI period 14 candles · Buy when RSI below 30 · …" */
  summary(fields: ParamSpec[], values: FieldValues | undefined): string {
    if (!values) return '';
    return fields.map((f) => {
      const v = values[f.key];
      if (f.kind === 'select') return `${f.label} ${f.options?.find((o) => o.value === v)?.label ?? v}`;
      if (v === 0 && f.help.startsWith('0 =')) return `${f.label} off`;
      return f.unit === '₹' ? `${f.label} ₹${v}` : `${f.label} ${v}${f.unit ? ' ' + f.unit : ''}`;
    }).join(' · ');
  }

  /** Indicator tiles; tolerates an older backend that sent {rsi, sma} instead of a list. */
  tiles(s: StrategySnapshot): Indicator[] {
    return Array.isArray(s.indicators) ? s.indicators : [];
  }

  /** Marker position (%) and buy/sell zones for an indicator gauge. */
  gauge(i: Indicator): { pos: number; low: number; high: number } | null {
    const g = i.gauge;
    if (!g || i.value == null) return null;
    const pct = (v: number) => Math.max(0, Math.min(100, ((v - g.min) / (g.max - g.min)) * 100));
    return { pos: pct(i.value), low: pct(g.low), high: pct(g.high) };
  }

  private confirmLive(what: string): boolean {
    return !this.liveMode() || confirm(`LIVE mode: ${what}? The bot may place REAL orders immediately.`);
  }

  private errorText(e: HttpErrorResponse): string {
    const d = e.error?.detail;
    if (typeof d === 'string') return d;
    if (Array.isArray(d)) return d.map((x) => x.msg ?? JSON.stringify(x)).join('; ');
    return e.message ?? 'Request failed';
  }
}
