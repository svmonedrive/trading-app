import { Component, ElementRef, effect, input, output, viewChild } from '@angular/core';

/**
 * "Add new strategy" dialog. Placeholder for now - the strategy builder that goes inside is
 * still to be specified.
 */
@Component({
  selector: 'app-add-strategy-dialog',
  templateUrl: './add-strategy-dialog.component.html',
  styleUrl: './add-strategy-dialog.component.css',
})
export class AddStrategyDialogComponent {
  readonly open = input(false);
  readonly closed = output<void>();

  private readonly dialog = viewChild.required<ElementRef<HTMLDialogElement>>('dlg');

  constructor() {
    effect(() => {
      const dlg = this.dialog().nativeElement;
      if (this.open() && !dlg.open) dlg.showModal();
      else if (!this.open() && dlg.open) dlg.close();
    });
  }

  close(): void {
    this.dialog().nativeElement.close();
  }
}
