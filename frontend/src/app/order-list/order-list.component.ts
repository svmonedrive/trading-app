import { DatePipe, DecimalPipe } from '@angular/common';
import { Component, input } from '@angular/core';

import { Order, OrdersResponse } from '../trading.service';

/** Today's order book: paper orders from the local DB, or kite.orders() in LIVE mode. */
@Component({
  selector: 'app-order-list',
  imports: [DatePipe, DecimalPipe],
  templateUrl: './order-list.component.html',
  styleUrl: './order-list.component.css',
})
export class OrderListComponent {
  readonly orders = input<OrdersResponse | null | undefined>(null);

  statusClass(o: Order): string {
    switch (o.status) {
      case 'COMPLETE': return 'pill green';
      case 'REJECTED': return 'pill red';
      case 'CANCELLED': return 'pill grey';
      case 'UNKNOWN': return 'pill amber';
      default: return 'pill blue'; // OPEN, TRIGGER PENDING, PUT ORDER REQ RECEIVED, ...
    }
  }
}
