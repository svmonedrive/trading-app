import { DatePipe } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { Component, DestroyRef, computed, effect, inject, input, output, signal } from '@angular/core';
import { Observable } from 'rxjs';

import { NotificationService } from '../notification.service';
import { IpChange, IpStatus, TradingService } from '../trading.service';

const ZERODHA_APPS_URL = 'https://developers.kite.trade/apps';

/**
 * Public IP monitor shown as a one-line banner under the header.
 * Collapsed: a single line (green when verified, red when orders are blocked by the IP).
 * Expanded: the full monitor - whitelisted/current/previous chips, IP type, history and the whitelist flow.
 */
@Component({
  selector: 'app-public-ip-monitor',
  imports: [DatePipe],
  templateUrl: './public-ip-monitor.component.html',
  styleUrl: './public-ip-monitor.component.css',
})
export class PublicIpMonitorComponent {
  private readonly api = inject(TradingService);
  private readonly notifier = inject(NotificationService);

  /** Latest /api/ip/status snapshot (from the dashboard poll). */
  readonly status = input.required<IpStatus>();
  /** Something changed on the backend - parent should re-poll now. */
  readonly changed = output<void>();

  readonly expanded = signal(false);
  readonly busy = signal<string | null>(null);
  readonly copied = signal(false);
  /** Set when the backend reports a NEW public-IP change during this session. */
  readonly newChange = signal<IpChange | null>(null);
  /** Zerodha console popup is open and being watched. */
  readonly consoleOpen = signal(false);
  /** Popup closed: ask "did you save <ip> in Zerodha?". */
  readonly consolePromptIp = signal<string | null>(null);
  readonly zerodhaAppsUrl = ZERODHA_APPS_URL;

  /** Dismissal is tied to the situation; a new change/rejection/whitelist brings the banner back. */
  private readonly dismissedKey = signal<string | null>(null);
  private readonly stateKey = computed(() => {
    const s = this.status();
    return [s.is_matched, s.changed_at, s.zerodha_rejection?.at, s.whitelisted_ip, !!s.error].join('|');
  });
  /** Never hide the banner in the middle of the whitelist flow. */
  readonly inFlow = computed(() => this.consoleOpen() || this.consolePromptIp() !== null);
  /** Only the green (verified) bar can be dismissed; a red bar is the only way to fix the IP, so it always stays. */
  readonly visible = computed(() =>
    !this.status().is_matched || this.inFlow() || this.dismissedKey() !== this.stateKey());
  readonly isOpen = computed(() => this.expanded() || this.inFlow());

  readonly headline = computed(() => {
    const s = this.status();
    if (s.is_matched) return null;
    if (s.zerodha_rejection) return 'Zerodha rejected an order because of the IP';
    if (!s.current_ip) return 'Public IP unavailable';
    if (!s.whitelisted_ip) return 'No whitelisted IP saved yet';
    if (s.error) return 'Public IP could not be verified';
    if (s.protocol_changed) return `Connection switched IPv${s.whitelisted_version} → IPv${s.ip_version}`;
    return 'Public IP changed';
  });

  private consoleWatch?: ReturnType<typeof setInterval>;
  private knownChangeAt: string | null | undefined = undefined; // undefined = first snapshot not seen yet

  constructor() {
    inject(DestroyRef).onDestroy(() => clearInterval(this.consoleWatch));

    // Detect a new change between polls.
    effect(() => {
      const s = this.status();
      if (this.knownChangeAt !== undefined && s.changed_at && s.changed_at !== this.knownChangeAt) {
        const c = s.ip_changes[0];
        this.newChange.set(c);
        this.notifier.show({ kind: 'err', text: `Public IP changed: ${c.from_ip} → ${c.to_ip}` });
      }
      this.knownChangeAt = s.changed_at;
      if (s.is_matched) this.newChange.set(null);
    });

    // Tab title doubles as an alert when the dashboard is in a background tab.
    effect(() => {
      document.title = this.newChange()
        ? '⚠ IP CHANGED · NIFTYBEES Algo'
        : !this.status().is_matched
          ? '⚠ IP not verified · NIFTYBEES Algo'
          : 'NIFTYBEES Algo';
    });
  }

  toggle(): void {
    this.expanded.update((v) => !v);
  }

  dismiss(): void {
    if (!this.status().is_matched) return;
    this.expanded.set(false);
    this.newChange.set(null);
    this.dismissedKey.set(this.stateKey());
  }

  // ---------------------------------------------------------------- actions
  checkNow(): void {
    this.run('ipcheck', this.api.checkIpNow(), (r) =>
      this.notifier.show(
        r.is_matched
          ? { kind: 'ok', text: 'Public IP verified.' }
          : { kind: 'err', text: 'Public IP still does not match the whitelist.' },
      ),
    );
  }

  confirmIp(alreadyAsked = false): void {
    const current = this.status().current_ip;
    if (!current) return;
    if (!alreadyAsked &&
        !confirm(`Confirm you have whitelisted\n\n${current}\n\nin the Zerodha Developer Console (developers.kite.trade)?`)) {
      return;
    }
    this.run('confirm', this.api.confirmIp(), (r) => {
      this.newChange.set(null);
      this.consolePromptIp.set(null);
      this.expanded.set(false);
      this.notifier.show({ kind: 'ok', text: `Whitelisted IP ${r.whitelisted_ip} saved. Orders unlocked.` });
    });
  }

  /** One click: copy the IP, open Zerodha's console in a popup, ask for confirmation when it closes. */
  async copyAndOpenZerodha(): Promise<void> {
    const ip = this.status().current_ip;
    if (!ip) return;
    await this.copyIp();
    const w = 1100, h = 850;
    const popup = window.open(
      ZERODHA_APPS_URL,
      'zerodha-console', // reuse the same window on repeated clicks
      `popup,width=${w},height=${h},left=${window.screenX + 60},top=${window.screenY + 40}`,
    );
    if (!popup) {
      this.notifier.show({ kind: 'err', text: 'Popup blocked — allow popups for localhost:4200, or use the "open in a tab" link.' });
      return;
    }
    popup.focus();
    this.consolePromptIp.set(null);
    this.consoleOpen.set(true);
    clearInterval(this.consoleWatch);
    // window.closed is readable cross-origin; if Zerodha ever severs the opener (COOP) it reads true
    // straight away and the prompt simply appears early - still the same one-click confirmation.
    this.consoleWatch = setInterval(() => {
      if (popup.closed) this.consoleDone(ip);
    }, 700);
  }

  /** Popup closed, or user clicked "I'm done" while leaving it open. */
  consoleDone(ip: string | null = this.status().current_ip): void {
    clearInterval(this.consoleWatch);
    this.consoleOpen.set(false);
    this.consolePromptIp.set(ip);
  }

  answerConsolePrompt(saved: boolean): void {
    const asked = this.consolePromptIp();
    if (!saved) {
      this.consolePromptIp.set(null);
      return;
    }
    const now = this.status().current_ip;
    if (asked && asked !== now) {
      this.consolePromptIp.set(null);
      this.notifier.show({ kind: 'err', text: `Your IP changed to ${now} meanwhile — whitelist that one instead.` });
      return;
    }
    this.confirmIp(true);
  }

  async copyIp(): Promise<void> {
    const current = this.status().current_ip;
    if (!current) return;
    try {
      await navigator.clipboard.writeText(current);
      this.copied.set(true);
      setTimeout(() => this.copied.set(false), 2000);
    } catch {
      this.notifier.show({ kind: 'err', text: 'Clipboard blocked — select the IP and copy manually.' });
    }
  }

  private run<T>(key: string, obs: Observable<T>, onOk: (v: T) => void): void {
    this.busy.set(key);
    obs.subscribe({
      next: (v) => {
        this.busy.set(null);
        onOk(v);
        this.changed.emit();
      },
      error: (e: HttpErrorResponse) => {
        this.busy.set(null);
        this.notifier.show({ kind: 'err', text: e.error?.detail ?? e.message ?? 'Request failed' });
      },
    });
  }
}
