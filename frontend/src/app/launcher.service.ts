import { HttpClient } from '@angular/common/http';
import { Injectable, inject, signal } from '@angular/core';
import { Observable } from 'rxjs';

/** Served by launcher.py on the same origin as the dashboard (works even while the backend is down). */
const LAUNCHER = '/launcher';

export interface LauncherStatus {
  backend_running: boolean;
  starting: boolean;
  pid: number | null;
  /** Started from the "NIFTYBEES Algo" shortcut: everything runs hidden in the background. */
  app_mode: boolean;
}

export interface SetupStatus {
  python_ok: boolean;
  env_exists: boolean;
  api_key_set: boolean;
  api_secret_set: boolean;
  /** e.g. "abc…xy" - the full key is never sent back. */
  api_key_hint: string | null;
  mode: 'PAPER' | 'LIVE';
  backend_running: boolean;
  app_mode: boolean;
  /** Redirect URL to enter in the Kite Connect app. */
  redirect_url: string;
}

export interface LauncherResult {
  ok: boolean;
  message: string;
  restart_needed?: boolean;
}

/** Talks to launcher.py (setup + starting the backend) and opens the setup dialog from anywhere. */
@Injectable({ providedIn: 'root' })
export class LauncherService {
  private readonly http = inject(HttpClient);

  /** Setup dialog opened on purpose (header "Setup" button) - it also opens by itself while the backend is down. */
  readonly setupOpen = signal(false);
  /** Quit was clicked: backend + launcher are stopping/stopped; the page shows a "stopped" screen. */
  readonly stopped = signal(false);

  openSetup(): void {
    this.setupOpen.set(true);
  }

  status(): Observable<LauncherStatus> {
    return this.http.get<LauncherStatus>(`${LAUNCHER}/status`);
  }

  setupStatus(): Observable<SetupStatus> {
    return this.http.get<SetupStatus>(`${LAUNCHER}/setup-status`);
  }

  saveKeys(apiKey: string, apiSecret: string): Observable<LauncherResult> {
    return this.http.post<LauncherResult>(`${LAUNCHER}/env`, { api_key: apiKey, api_secret: apiSecret });
  }

  startBackend(): Observable<LauncherResult> {
    return this.http.post<LauncherResult>(`${LAUNCHER}/start-backend`, {});
  }

  /** Last lines of logs/backend.log (app mode) - shown when the backend fails to start. */
  backendLog(): Observable<{ lines: string[] }> {
    return this.http.get<{ lines: string[] }>(`${LAUNCHER}/backend-log`);
  }

  /** Stop the backend and the launcher. */
  quit(): Observable<LauncherResult> {
    return this.http.post<LauncherResult>(`${LAUNCHER}/quit`, {});
  }
}
