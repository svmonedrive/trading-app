# NIFTYBEES Algo Trader (FastAPI + Angular 22)

Local-only algo bot for **NIFTYBEES** on Zerodha Kite Connect (Personal API), with a JioFiber IPv6 whitelist guard.

```
NIFTY_BEES_TRADING_APP/
├── start.bat / launcher.py      Daily start: serves the dashboard, "Start Backend Server" button
├── backend/                     FastAPI on http://localhost:8000
│   ├── .env                     SETTINGS: Kite API key/secret, DRY_RUN, intervals (secret - not in git)
│   ├── .env.example             SETTINGS template
│   ├── requirements.txt         Python packages
│   ├── CONFIG.md                what every .env setting and data/config.json key means
│   ├── data/                    RUNTIME DATA (not in git)
│   │   ├── config.json          saved state: selected strategy + limits, whitelisted IP, Zerodha token
│   │   └── algo.db              SQLite: bot orders + activity log
│   └── src/app/                 CODE
│       ├── main.py              API routes + scheduler (IP check, strategy check)
│       ├── core/
│       │   ├── config.py        loads .env + data/config.json, NSE market hours
│       │   └── db.py            orders + activity log (data/algo.db)
│       ├── services/
│       │   ├── kite_service.py  Zerodha login, LIMIT orders, order book, holdings
│       │   ├── ip_monitor.py    live public IP vs whitelisted IP
│       │   └── ip_sharing.py    shared (CGNAT) vs dedicated IP detection
│       └── strategy/
│           ├── engine.py        runs the selected strategy, safety gates, places orders
│           └── presets.py       the strategy list + their editable limits
└── frontend/                    Angular 22 standalone + zoneless + signals, http://localhost:4200
    └── src/app/                 one folder per component (header, live-strategy, order-list, ...)
```

## 1. One-time setup

### Zerodha developer console (https://developers.kite.trade)
1. Create (or edit) your **Personal** app.
2. **Redirect URL:** `http://127.0.0.1:8000/api/auth/callback` (or `http://localhost:8000/api/auth/callback`)
3. **IP whitelist:** your public IP (IPv4 or IPv6). The dashboard shows it, with a Copy button.
4. Copy the API key and secret into `backend/.env`.

### Backend (PowerShell)
```powershell
cd C:\NON_ESCRIBA\NIFTY_BEES_TRADING_APP\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1          # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
pip install -r requirements.txt
Copy-Item .env.example .env           # then edit .env: KITE_API_KEY, KITE_API_SECRET, keep DRY_RUN=true
```

### Frontend
```powershell
cd C:\NON_ESCRIBA\NIFTY_BEES_TRADING_APP\frontend
npm install --legacy-peer-deps        # --legacy-peer-deps avoids an npm 10 "edgesOut" bug
```

## 2. Run

### Everyday: double-click `start.bat`
It runs `launcher.py`, which serves the built dashboard on http://localhost:4200 and opens it in the browser.
If the backend isn't running, the page shows a **Start Backend Server** button at the top: it opens the
backend in its own console window ("NIFTYBEES Backend") and the bar disappears once it is up (~5 s).
Close that window (or Ctrl+C in it) to stop the backend; closing the launcher does not stop it.
After changing frontend code, rebuild with `npx ng build` in `frontend\` so the launcher serves the new version.

### Development (two terminals)
```powershell
# Terminal 1: backend
cd C:\NON_ESCRIBA\NIFTY_BEES_TRADING_APP\backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir src --host 127.0.0.1 --port 8000
```
```powershell
# Terminal 2: frontend
cd C:\NON_ESCRIBA\NIFTY_BEES_TRADING_APP\frontend
npx ng serve --open                    # http://localhost:4200 (no Start Backend button: that needs launcher.py)
```
API docs: http://localhost:8000/docs

**Daily routine:** open the dashboard → **Login to Zerodha** (tokens expire around 06:00 IST every day) → check that the Public IP bar is green → switch **Algo** on.

## 3. Which IP gets whitelisted (IPv4 or IPv6)
The monitor looks up your public IP via `api64.ipify.org` (dual-stack). The answer is whichever IP your PC actually uses for outbound traffic. That is the same IP Zerodha sees, because `api.kite.trade` also accepts both IPv4 and IPv6.
- **IPv6 disabled on the PC:** the IPv4 address is monitored. This is the current setup.
- **IPv6 enabled later:** the card shows "Connection switched IPv4 → IPv6" and blocks orders until you whitelist the new address.

The card also shows the **IP type**:
- **Dedicated:** the router itself holds the public IP. This is detected over UPnP when the router allows it.
- **Shared / CGNAT:** the ISP shares one public IPv4 among many customers. Detected over UPnP, or with a traceroute fallback that checks whether your router's upstream gateway has a private address.

A shared IP can change at any time, and Zerodha may refuse it if another client has already registered it. The app also compares two lookup services (`IP_SECONDARY_LOOKUP_URL`) and blocks orders if they disagree.

### Optional: stop Windows rotating your IPv6 (privacy extensions)
Only relevant if you use IPv6.
By default Windows uses a random, rotating *temporary* IPv6 for outbound traffic. Zerodha would see a different IP every few hours. Run **PowerShell as Administrator**:
```powershell
netsh interface ipv6 set privacy state=disabled store=persistent
netsh interface ipv6 set global randomizeidentifiers=disabled store=persistent
# then reconnect the adapter (or reboot) and verify:
netsh interface ipv6 show privacy
ipconfig    # there should no longer be a "Temporary IPv6 Address" line
```
After this, your outbound IPv6 is stable until JioFiber gives the router a new **prefix**. That usually happens on a router reboot. When it does, the dashboard turns red: copy the new IP, update it on developers.kite.trade, then click **I Have Updated Zerodha**.

To undo: `netsh interface ipv6 set privacy state=enabled store=persistent` and `netsh interface ipv6 set global randomizeidentifiers=enabled store=persistent`.

## 4. Safety behaviour
- The bot boots **PAUSED** every time the backend starts.
- Before any order, all of these must pass: algo running, live public IP = whitelisted IP, NSE hours 09:15–15:30 IST on Mon–Fri, Zerodha session active (LIVE mode only), and a yfinance price at most 20 minutes old.
- Orders are always **LIMIT** orders (`LTP ± limit_buffer_pct`, default 0.10%, rounded to the ₹0.01 tick) with `CNC` product and `DAY` validity.
- The bot only sells the quantity **it bought** (tracked in `backend/data/algo.db`). Your existing NIFTYBEES holdings are never sold.
- **No-loss rule:** a SELL only goes through if the sell price is at least `min_profit_pct` (default 0.5%) above the bot's average buy price. Otherwise the bot keeps holding. Set it above your charges (STT, exchange fees, DP charge).
- The bot stops buying once it holds `max_holding_qty`.
- There is no new order while an earlier one is still OPEN. After a rejection there is a 15-minute cooldown.
- Add NSE trading holidays to `"market_holidays"` in `backend/data/config.json` (e.g. `"2026-12-25"`).
- The API has **no authentication**. Keep it bound to `127.0.0.1`.

## 5. Strategies (Live Strategy card)
- **Strategy list:** the card lists every strategy.
  - RSI Mean Reversion
  - Moving Average Crossover
  - Bollinger Band Bounce
  - Buy the Dip %
  - Fixed Price Levels
- **Selected strategy:** shown in green. It stays selected until you pick another one.
- **Limits:** click a strategy to edit its limits. Each strategy keeps its own saved limits.
- **Order settings:** quantity, max holding, no-loss min profit %, LIMIT buffer and candle size are shared by every strategy.
- **Re-check:** changing the running strategy, or its limits or order settings, re-runs the check immediately. This is refused while an order is OPEN.
- **Add new strategy:** the button opens a dialog. The builder inside it is still to be designed.

API: `GET /api/strategy/presets`, `GET /api/strategy/config`, `PUT /api/strategy/active`, `PUT /api/strategy/limits/{key}`, `PUT /api/strategy/order-settings`.

To add a strategy in code, subclass `Preset` in `backend/src/app/strategy/presets.py` (declare its `fields`, implement `evaluate`) and add it to `PRESETS`. The UI form is generated from `fields`, so no frontend change is needed.

## 6. Sharing the app with others
```powershell
cd C:\NON_ESCRIBA\NIFTY_BEES_TRADING_APP\frontend
npm run deploy                      # build + publish
npm run deploy -- --no-push         # build only, upload nothing
```
`npm run deploy` does the following:
1. Refuses to run if there are uncommitted changes, so the zip always matches a commit. Use `-- --allow-dirty` to override.
2. Builds the UI and creates `frontend\release\NIFTYBEES-Algo\` and `frontend\release\NIFTYBEES-Algo.zip`. Both are git-ignored.
3. Pushes the zip and a short `README.md` to the **`downloads`** branch on GitHub, like `angular-cli-ghpages` does.
   - The branch holds only the release; each deploy adds one commit, so older versions stay available.
   - Your working folder and current branch are never touched.

**Download link.** The repo is private, so this only works for people you invite as collaborators, logged in to GitHub:
`https://github.com/svmonedrive/trading-app/raw/refs/heads/downloads/NIFTYBEES-Algo.zip`
Alternatively, they can open the `downloads` branch on GitHub, click the zip, then click *Download*.

- **What's in the zip:**
  - `app\` (the built UI)
  - `backend\` (code, `requirements.txt`, `.env.example`, `CONFIG.md`)
  - `launcher.py`, `setup.bat` / `setup.ps1`, `uninstall.bat` / `uninstall.ps1`
  - `GETTING_STARTED.md`, `VERSION.txt`
- **Safety check:** packaging stops if `.env`, `data\`, a database, a Zerodha token, `.venv` or real keys would end up in the zip. The example `.env` must also keep `DRY_RUN=true`.
- **What the receiver does:**
  1. Unzip.
  2. Run `setup.bat` once (needs internet).
     - It downloads portable Python 3.12 from python.org into the folder, with no admin rights and nothing installed system-wide.
     - It installs the packages from PyPI and creates `backend\.env`.
     - It adds a **NIFTYBEES Algo** entry to the Start menu (no Desktop shortcut) and opens the app. pip runs with `--no-cache-dir`, so nothing is left outside the folder.
  3. Every day: open **NIFTYBEES Algo** from the Start menu.
     - It runs `pythonw launcher.py --app`. The launcher and the backend run hidden, the backend starts by itself, and the app opens in its own window.
     - Closing the window leaves the bot running; **⏻ Quit** in the app stops everything.
  4. On first start, the app's setup dialog asks for **their own** Kite API key and secret, then starts the backend. The header's **⚙ Setup** button reopens it later.
  5. Logs are written to `logs\launcher.log` and `logs\backend.log`.
  6. To remove it, run `uninstall.bat`. It stops the app, offers a backup of `backend\data` and `.env` to Documents, removes the shortcuts, and offers to delete the folder.
- **Templates:** the files the receiver gets live in `packaging\`. `launcher.py` is shared with development. In the repo, `start.bat` runs it in dev mode, with console windows and `backend\.venv`.

## 7. PWA
`ng add @angular/pwa` has been applied. The service worker only runs in a production build:
```powershell
npx ng build; npx http-server dist/frontend/browser -p 4200
```
Then use **Install app** in Chrome or Edge.
