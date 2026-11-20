# Backend configuration and runtime data

The backend uses two different kinds of files:

| File | Kind | Who writes it | When it is read | In git? |
|---|---|---|---|---|
| `backend/.env` | **Settings** | You, by hand | Once, at backend start | No (holds secrets) |
| `backend/data/config.json` | **Runtime state** | The backend, automatically | At start, then kept in memory | No (holds your Zerodha token) |
| `backend/data/algo.db` | **Runtime data** (SQLite) | The backend, automatically | Continuously | No |

- **To change a setting:** edit `.env`, then restart the backend.
- **To change state:** use the dashboard. Edit `config.json` by hand only while the backend is **stopped**. See [Editing config.json by hand](#editing-configjson-by-hand).

---

## `.env`: settings

Copy `.env.example` to `.env` and fill it in.

| Key | Default | Meaning |
|---|---|---|
| `KITE_API_KEY` | – | API key of your Kite Connect app (developers.kite.trade → My apps). |
| `KITE_API_SECRET` | – | API secret of the same app. Keep it private. |
| `DRY_RUN` | `true` | `true` = **PAPER** mode: orders are simulated in `algo.db`. `false` = **LIVE** mode: real LIMIT orders on Zerodha. |
| `PAPER_IGNORE_MARKET_HOURS` | `false` | Paper mode only: allow simulated fills outside 09:15–15:30 IST, e.g. to test at the weekend. |
| `FRONTEND_URL` | `http://localhost:4200` | Dashboard address. Used for CORS and for the redirect after Zerodha login. |
| `IP_CHECK_INTERVAL_SECONDS` | `15` | How often the public IP is re-checked. It is also re-checked right before every order. |
| `STRATEGY_INTERVAL_SECONDS` | `300` | How often the selected strategy is evaluated automatically. |
| `IP_LOOKUP_URL` | `https://api64.ipify.org?format=json` | Public-IP lookup. This service handles both IPv4 and IPv6, so it reports the same IP Zerodha sees. |
| `IP_SECONDARY_LOOKUP_URL` | `https://icanhazip.com` | A second, independent lookup that detects a shared IP changing per connection. Leave it empty to disable it. |

The Kite API key and secret are a one-time setup; they don't expire. Only the daily Zerodha **login** must be repeated, and its token is stored in `config.json`.

---

## `data/config.json`: runtime state

The backend keeps this file in memory and **rewrites the whole file** whenever something changes. Missing keys are filled in with defaults on start.

### Written automatically by the app

| Key(s) | What it holds | Written when |
|---|---|---|
| `access_token`, `access_token_created_at` | Today's Zerodha login token. **Secret:** anyone holding it can trade on your account until it expires. | You log in to Zerodha. It expires around 06:00 IST the next morning. |
| `user_id`, `user_name` | Your Zerodha client ID and name | You log in |
| `whitelisted_ip`, `whitelisted_at` | The IP you confirmed is saved in Zerodha's IP whitelist, and when you confirmed it | You click **Yes, saved it** or **I have updated Zerodha** |
| `last_seen_ip` | The last public IP the app detected | The IP check runs (every few seconds) |
| `ip_changes` | The last 20 public-IP changes (`from_ip`, `to_ip`, `at`), newest first | Your public IP changes |
| `zerodha_ip_rejection` | Set when Zerodha rejects an order because of the IP. Orders stay blocked until you re-confirm the whitelist. | Zerodha rejects an order with an IP-related message. Cleared when you confirm. |
| `algo_active` | Algo switch: `true` = running, `false` = paused | You toggle it. It is **reset to `false` on every backend start** for safety. |
| `active_strategy` | Key of the selected strategy, e.g. `rsi_mean_reversion` | You click **Save & select this strategy** |
| `strategy_limits` | The saved limits of each strategy, keyed by strategy. Strategies never saved use their defaults. | You click **Save limits** |
| `order_settings` | Shared by all strategies: `order_quantity`, `max_holding_qty`, `min_profit_pct` (no-loss rule), `limit_buffer_pct`, `candle_interval` | You click **Save order settings** |

### Only edited by hand (backend stopped)

| Key | What it holds | Example |
|---|---|---|
| `market_holidays` | NSE trading holidays. No orders are placed on these dates; weekends are handled automatically. | `["2026-12-25", "2027-01-26"]` |
| `strategy.symbol` | Trading symbol on Zerodha | `"NIFTYBEES"` |
| `strategy.exchange` | Exchange | `"NSE"` |
| `strategy.yf_ticker` | The same instrument on Yahoo Finance (market data) | `"NIFTYBEES.NS"` |
| `strategy.product` | Zerodha product type. `CNC` = delivery. | `"CNC"` |
| `strategy.retry_cooldown_minutes` | How long to wait after a failed or rejected order before retrying the same side | `15` |
| `strategy.max_data_age_minutes` | Refuse to trade when the market price is older than this during market hours | `20` |

### Leftover keys

- `whitelisted_ipv6`: from the first, IPv6-only version. It is no longer used and safe to delete while the backend is stopped.

### Editing config.json by hand

1. Stop the backend: close the **NIFTYBEES Backend** window.
2. Edit and save the file. It must stay valid JSON: double quotes, and no trailing commas.
3. Start the backend again.

If you edit it while the backend runs, your change is overwritten on the next automatic save.

---

## `data/algo.db`: orders and activity log

This is a SQLite database with two tables:

- **`orders`:** every order the bot placed (PAPER and LIVE), with fills and status. The bot's holding and average buy price are calculated from this table, so it only ever sells what **it** bought.
- **`events`:** the Activity Log shown on the dashboard.

Don't edit it while the backend is running. Deleting it resets the bot's order history and holding, which the bot would then treat as zero.
