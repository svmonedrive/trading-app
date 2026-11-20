import { Injectable, signal } from '@angular/core';

/** A toast message. */
export interface Notice {
  kind: 'ok' | 'err';
  text: string;
}

const TOAST_MS = 6000;

/** App-wide toast: any component calls show(); <app-notification> renders it. */
@Injectable({ providedIn: 'root' })
export class NotificationService {
  private readonly _toast = signal<Notice | null>(null);
  readonly toast = this._toast.asReadonly();
  private timer?: ReturnType<typeof setTimeout>;

  show(notice: Notice): void {
    this._toast.set(notice);
    clearTimeout(this.timer);
    this.timer = setTimeout(() => this._toast.set(null), TOAST_MS);
  }

  ok(text: string): void {
    this.show({ kind: 'ok', text });
  }

  err(text: string): void {
    this.show({ kind: 'err', text });
  }

  dismiss(): void {
    clearTimeout(this.timer);
    this._toast.set(null);
  }
}
