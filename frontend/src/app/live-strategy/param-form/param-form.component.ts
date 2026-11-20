import { Component, effect, input, output, signal } from '@angular/core';

import { FieldValues, ParamSpec } from '../../trading.service';

export interface FormAction {
  id: string;
  label: string;
  primary?: boolean;
}

/**
 * Editable "limits" form generated from field specs sent by the backend (presets.py), so adding a
 * strategy there needs no UI change. The backend re-validates everything on save.
 *
 * Keeps what the user typed while the dashboard re-polls; resyncs from `values` once the saved
 * values come back (or if nothing was edited).
 */
@Component({
  selector: 'app-param-form',
  templateUrl: './param-form.component.html',
  styleUrl: './param-form.component.css',
})
export class ParamFormComponent {
  readonly fields = input.required<ParamSpec[]>();
  readonly values = input.required<FieldValues>();
  readonly actions = input<FormAction[]>([{ id: 'save', label: 'Save', primary: true }]);
  readonly busy = input(false);
  /** Error from the backend, shown above the buttons. */
  readonly error = input<string | null>(null);

  readonly submitted = output<{ action: string; values: FieldValues }>();

  readonly form = signal<Record<string, string>>({});
  readonly dirty = signal(false);
  readonly localError = signal<string | null>(null);

  constructor() {
    effect(() => {
      const incoming = this.asStrings(this.values());
      const same = JSON.stringify(incoming) === JSON.stringify(this.form());
      if (same) this.dirty.set(false); // saved values came back = no pending edits
      else if (!this.dirty()) this.form.set(incoming);
    });
  }

  get(key: string): string {
    return this.form()[key] ?? '';
  }

  set(key: string, value: string): void {
    this.form.update((v) => ({ ...v, [key]: value }));
    this.dirty.set(true);
    this.localError.set(null);
  }

  reset(): void {
    this.form.set(this.asStrings(this.values()));
    this.dirty.set(false);
    this.localError.set(null);
  }

  run(action: string): void {
    try {
      this.submitted.emit({ action, values: this.parse() });
    } catch (e) {
      this.localError.set((e as Error).message);
    }
  }

  private asStrings(v: FieldValues): Record<string, string> {
    const out: Record<string, string> = {};
    for (const f of this.fields()) out[f.key] = String(v?.[f.key] ?? f.default ?? '');
    return out;
  }

  /** Light client-side check so obvious typos don't round-trip; the backend has the final say. */
  private parse(): FieldValues {
    const out: FieldValues = {};
    for (const f of this.fields()) {
      const raw = (this.form()[f.key] ?? '').trim();
      if (f.kind === 'select') {
        out[f.key] = raw;
        continue;
      }
      const n = Number(raw);
      if (raw === '' || !Number.isFinite(n)) throw new Error(`'${f.label}' must be a number`);
      if (f.kind === 'int' && !Number.isInteger(n)) throw new Error(`'${f.label}' must be a whole number`);
      if (f.min != null && n < f.min) throw new Error(`'${f.label}' must be at least ${f.min}`);
      if (f.max != null && n > f.max) throw new Error(`'${f.label}' must be at most ${f.max}`);
      out[f.key] = n;
    }
    return out;
  }
}
