import { Component, inject, input, output } from '@angular/core';

import { NotificationService } from '../notification.service';
import { PublicIpMonitorComponent } from '../public-ip-monitor/public-ip-monitor.component';
import { AlgoStatus, IpStatus } from '../trading.service';

/**
 * Everything that sits between the header and the cards: the Public IP
 * monitor bar, the safety-gate banner (can trade / orders blocked) and the toast.
 */
@Component({
  selector: 'app-notification',
  imports: [PublicIpMonitorComponent],
  templateUrl: './app-notification.component.html',
  styleUrl: './app-notification.component.css',
})
export class AppNotificationComponent {
  readonly notifier = inject(NotificationService);

  readonly ip = input<IpStatus | null | undefined>(null);
  readonly algo = input<AlgoStatus | null | undefined>(null);
  /** The IP monitor changed backend state - parent should re-poll now. */
  readonly changed = output<void>();
}
