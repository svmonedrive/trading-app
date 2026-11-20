import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

export const API_BASE = 'http://localhost:8000/api';

export interface AuthStatus {
  active: boolean;
  api_configured: boolean;
  user_name: string | null;
  user_id: string | null;
  login_time: string | null;
}

export interface IpSharing {
  status: 'SHARED' | 'DEDICATED' | 'UNKNOWN';
  confidence: 'high' | 'likely' | 'none';
  method: 'upnp' | 'traceroute' | 'ipv6' | 'error';
  detail: string;
  router_wan_ip: string | null;
  isp_gateway: string | null;
  checked_at: string;
}

export interface IpChange {
  from_ip: string;
  to_ip: string;
  at: string;
}

export interface IpStatus {
  previous_ip: string | null;
  changed_at: string | null;
  ip_changes: IpChange[];
  check_interval_seconds: number;
  current_ip: string | null;
  ip_version: 4 | 6 | null;
  whitelisted_ip: string | null;
  whitelisted_version: 4 | 6 | null;
  /** When the user last confirmed the whitelist (null for whitelists saved before this field existed). */
  whitelisted_at: string | null;
  protocol_changed: boolean;
  is_matched: boolean;
  last_checked: string | null;
  error: string | null;
  sharing: IpSharing | null;
  zerodha_rejection: ZerodhaIpRejection | null;
}

/** Zerodha rejected an order and the message blamed the IP. Cleared when the user re-confirms. */
export interface ZerodhaIpRejection {
  ip: string | null;
  whitelisted_ip: string | null;
  message: string;
  order_ref: string;
  at: string;
}

export interface AlgoStatus {
  algo_active: boolean;
  mode: 'PAPER' | 'LIVE';
  dry_run: boolean;
  can_trade: boolean;
  block_reasons: string[];
  market_open: boolean;
  market_message: string;
  server_time: string;
  strategy_interval_seconds: number;
}

export interface StrategySnapshot {
  status: 'ok' | 'error' | 'not_run_yet';
  strategy?: string;
  symbol?: string;
  price?: number;
  price_time?: string;
  data_age_minutes?: number;
  candles?: number;
  candle_interval?: string;
  /** Key of the strategy that produced this snapshot. */
  preset?: string;
  signal?: 'BUY' | 'SELL' | 'HOLD';
  reason?: string;
  next_trigger?: string;
  /** One line per armed rule (BUY / SELL / "no more buying"). */
  rules?: string[];
  indicators?: Indicator[];
  algo_qty?: number;
  avg_price?: number | null;
  demat_qty?: number | null;
  max_holding_qty?: number;
  min_profit_pct?: number;
  /** No-loss floor: the bot won't SELL below this price. */
  min_sell_price?: number | null;
  last_action?: string | null;
  checked_at?: string;
  trigger?: string;
  error?: string;
  note?: string;
}

/** A stat tile produced by the active preset. `gauge` draws a bar with buy (below low) / sell (above high) zones. */
export interface Indicator {
  label: string;
  value: number | null;
  note: string;
  unit: string;
  gauge: { min: number; max: number; low: number; high: number } | null;
}

// ---------------------------------------------------------------- strategies
/** One form field (a "limit"), described by the backend so new strategies need no UI changes. */
export interface ParamSpec {
  key: string;
  label: string;
  kind: 'int' | 'float' | 'select';
  default: number | string | null;
  min: number | null;
  max: number | null;
  step: number | null;
  unit: string;
  help: string;
  options: { value: string; label: string }[] | null;
}

export interface Preset {
  key: string;
  name: string;
  description: string;
  fields: ParamSpec[];
}

export interface PresetsCatalog {
  presets: Preset[];
  /** Order settings shared by every strategy: quantity, max holding, no-loss min profit, buffer, candle size. */
  common_fields: ParamSpec[];
}

export type FieldValues = Record<string, number | string>;

export interface StrategyConfig {
  /** Selected strategy - stays selected until changed. */
  active: string;
  /** Saved (or default) limits of every strategy, keyed by strategy key. */
  limits: Record<string, FieldValues>;
  order_settings: FieldValues;
}
export interface Order {
  order_id: string;
  timestamp: string | null;
  tradingsymbol: string;
  transaction_type: 'BUY' | 'SELL';
  quantity: number;
  filled_quantity?: number;
  price: number;
  status: string;
  status_message: string | null;
  source: 'ZERODHA' | 'LOCAL' | 'PAPER';
}

export interface OrdersResponse {
  mode: 'PAPER' | 'LIVE';
  orders: Order[];
  error: string | null;
}

export interface EventLog {
  timestamp: string;
  level: 'INFO' | 'WARN' | 'ERROR' | 'TRADE';
  message: string;
}

export interface Dashboard {
  auth: AuthStatus;
  ip: IpStatus;
  algo: AlgoStatus;
  strategy: StrategySnapshot;
  strategy_config: StrategyConfig;
  orders: OrdersResponse;
  events: EventLog[];
}

@Injectable({ providedIn: 'root' })
export class TradingService {
  private readonly http = inject(HttpClient);

  /** One aggregated call: auth + public IP + algo + strategy + orders + activity log. */
  dashboard(): Observable<Dashboard> {
    return this.http.get<Dashboard>(`${API_BASE}/dashboard`);
  }

  loginUrl(): Observable<{ login_url: string }> {
    return this.http.get<{ login_url: string }>(`${API_BASE}/auth/login-url`);
  }

  authStatus(): Observable<AuthStatus> {
    return this.http.get<AuthStatus>(`${API_BASE}/auth/status`);
  }

  ipStatus(): Observable<IpStatus> {
    return this.http.get<IpStatus>(`${API_BASE}/ip/status`);
  }

  checkIpNow(): Observable<IpStatus> {
    return this.http.post<IpStatus>(`${API_BASE}/ip/check`, {});
  }

  confirmIp(): Observable<IpStatus> {
    return this.http.post<IpStatus>(`${API_BASE}/ip/confirm`, {});
  }

  toggleAlgo(active?: boolean): Observable<AlgoStatus> {
    return this.http.post<AlgoStatus>(`${API_BASE}/algo/toggle`, active === undefined ? {} : { active });
  }

  runCheckNow(): Observable<StrategySnapshot> {
    return this.http.post<StrategySnapshot>(`${API_BASE}/strategy/run`, {});
  }

  presets(): Observable<PresetsCatalog> {
    return this.http.get<PresetsCatalog>(`${API_BASE}/strategy/presets`);
  }

  selectStrategy(key: string): Observable<StrategyConfig> {
    return this.http.put<StrategyConfig>(`${API_BASE}/strategy/active`, { strategy: key });
  }

  saveLimits(key: string, values: FieldValues): Observable<StrategyConfig> {
    return this.http.put<StrategyConfig>(`${API_BASE}/strategy/limits/${key}`, values);
  }

  saveOrderSettings(values: FieldValues): Observable<StrategyConfig> {
    return this.http.put<StrategyConfig>(`${API_BASE}/strategy/order-settings`, values);
  }
  orders(): Observable<OrdersResponse> {
    return this.http.get<OrdersResponse>(`${API_BASE}/orders`);
  }
}
