# NIFTYBEES Algo: getting started

A small app that runs on **your own Windows laptop**. It watches NIFTYBEES and places LIMIT orders on **your own Zerodha account** through Zerodha's Kite Connect API. It starts in **PAPER mode**, where orders are only simulated.

## What you need
- **Windows 10 or 11**, 64-bit, and an **internet connection** for the one-time setup. No admin rights are needed.
- **A Zerodha account.**
- **Your own Kite Connect app.** You create it at https://developers.kite.trade (the app's setup screen explains how). Never use someone else's API key.

## 1. One-time setup
1. Unzip `NIFTYBEES-Algo.zip` into a folder you'll keep, e.g. `Documents\NIFTYBEES-Algo`. Don't run it from inside the zip.
2. Double-click **`setup.bat`**. It takes about 2–5 minutes the first time.
   - It downloads a portable Python from python.org and the packages the app needs from PyPI. Both go **into this folder only**; nothing is installed on Windows.
   - It creates a **NIFTYBEES Algo** shortcut on your Desktop and in the Start menu, then opens the app.
3. The app shows a short setup guide:
   1. Create your Kite Connect app, using the Redirect URL shown, with a Copy button.
   2. Paste the app's **API key** and **API secret**, then click **Save keys**. The app then starts the backend by itself.
   3. **Login to Zerodha**, check the **Public IP** bar, pick a strategy, and switch **Algo** on.

## 2. Every day
- Open the **NIFTYBEES Algo** shortcut. Everything starts by itself in the background, with no black windows, and the app opens in its own window.
- **Login to Zerodha** (top right). Zerodha logs everyone out every morning around 6 AM.
- If the **Public IP** bar is red, click **Fix** and follow the steps to whitelist your IP.

**Closing the app window does NOT stop the bot.** It keeps running in the background, which is what lets it trade. Open the shortcut again to see it.

**To stop everything,** click **⏻ Quit** in the app.

## Uninstall
Double-click **`uninstall.bat`** in the app folder. It:
1. Stops the app, even if it's running hidden in the background.
2. Asks whether to **back up** your settings, order history and API keys to `Documents\NIFTYBEES-Algo-backup-<date>`. To restore, copy `data` and `.env` into `backend\` of a new install.
3. Removes the **NIFTYBEES Algo** shortcuts.
4. Asks whether to **delete the folder** (the default is No).

Nothing else on the PC was changed.

## Your private files (never share them)
- **`backend\.env`:** your Kite API key and secret.
- **`backend\data\`:** your Zerodha login token, settings, orders and activity log. Copy this folder to keep your history when moving to another PC.
- **`logs\`:** technical logs. Send `logs\backend.log` to whoever helps you if something doesn't start.

## Real money
`backend\.env` has `DRY_RUN=true` (PAPER mode). Only change it to `false` after testing in paper mode, and once you understand the strategy you selected. Trading involves risk, and you are responsible for the orders placed on your account.
