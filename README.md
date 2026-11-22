# Nifty Power-4 alerts (GitHub Actions + Telegram)

Checks Nifty 50 every 5 minutes during market hours on GitHub's servers — your computer can be off.
When all four Power-4 conditions line up on a closed 15-minute candle, it sends a Telegram message
to your phone (for example: "🟢 POWER-4: BUY NIFTY 25,850 CE (ATM) · Stop 25,814 · Target 25,958"),
and updates a status website on GitHub Pages.

**Rules:** close > EMA 20 > EMA 50 · RSI 14 between 55 and 70 and rising · NIFTYBEES above its VWAP ·
close above the 9:15 candle's high (puts: the mirror image). Entries 9:30 AM – 2:30 PM (1 PM on expiry day).
Stop 1 × ATR(14), target 2 × ATR, exit at 3:15 PM. One losing trade ends the day.

Data comes from Yahoo Finance (Nifty 50 `^NSEI`, and `NIFTYBEES.NS` for VWAP because the index has no volume).
It can differ slightly from the Groww futures chart. Not financial advice — paper-trade first.

## Setup (about 15 minutes)

### 1. Create a Telegram bot
1. In Telegram, open **@BotFather**, send `/newbot`, and follow the steps.
2. Copy the **token** it gives you (looks like `123456:ABC-...`). Keep it secret.
3. Open your new bot and send it any message, e.g. `hi`.
4. In a browser, open `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
   and find `"chat":{"id":123456789` — that number is your **chat ID**.

### 2. Put this code on GitHub
1. Create a new **public** repository (public repos get unlimited free Actions minutes).
2. Upload all files from this folder, keeping the folders (`.github/workflows/power4.yml`, `docs/`, `power4.py`).

### 3. Add your secrets
Repository → **Settings → Secrets and variables → Actions → New repository secret**:
- `TELEGRAM_TOKEN` = your bot token
- `TELEGRAM_CHAT_ID` = your chat ID

Optional, under the **Variables** tab: `P4_EXPIRY_WEEKDAY` = `1` for Tuesday (default), `0` for Monday, etc.

### 4. Allow the bot to save its status
Settings → **Actions → General → Workflow permissions** → choose **Read and write permissions** → Save.

### 5. Turn on the website
Settings → **Pages** → Source: **Deploy from a branch** → Branch: `main`, folder `/docs` → Save.
Your site will be at `https://<your-username>.github.io/<repo-name>/`.

### 6. Test it
**Actions** tab → **Power-4 alerts** → **Run workflow** → mode `test`.
You should get "✅ Power-4 bot is connected" on Telegram within a minute.
Run it again with mode `run` to fill the website with the latest data.

## Good to know
- GitHub's scheduled runs can start a few minutes late (sometimes 10–15+) when GitHub is busy,
  so alerts can arrive late. Check the chart before entering.
- It doesn't know NSE holidays or shifted expiry days; it simply finds no new candles on holidays.
- GitHub pauses scheduled workflows in repos with no activity for 60 days; the bot's own status
  commits normally keep it active, but re-enable it from the Actions tab if it ever stops.
