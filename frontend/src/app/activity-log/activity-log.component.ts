import { DatePipe } from '@angular/common';
import { Component, input, signal } from '@angular/core';

import { EventLog } from '../trading.service';

const STORAGE_KEY = 'activityLog.expanded';

/** Backend event stream (IP changes, signals, orders, errors), newest first. Grows with the page; collapsible. */
@Component({
  selector: 'app-activity-log',
  imports: [DatePipe],
  templateUrl: './activity-log.component.html',
  styleUrl: './activity-log.component.css',
})
export class ActivityLogComponent {
  readonly events = input<EventLog[]>([]);
  readonly expanded = signal(readExpanded());

  toggle(): void {
    this.expanded.update((v) => !v);
    try {
      localStorage.setItem(STORAGE_KEY, String(this.expanded()));
    } catch {
      /* storage unavailable - state just isn't remembered */
    }
  }
}

function readExpanded(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) !== 'false';
  } catch {
    return true;
  }
}
