"""
Nifty Power-4 alert bot (runs on GitHub Actions).
Rules (15-minute candles):
  CALL: close > EMA20 > EMA50, RSI14 55-70 and rising, NIFTYBEES above its VWAP, close > 9:15 candle high
  PUT : close < EMA20 < EMA50, RSI14 30-45 and falling, NIFTYBEES below its VWAP, close < 9:15 candle low
  Entries 9:30-14:30 (13:00 on expiry day). Stop 1 x ATR14, target 2 x ATR14, exit 15:15.
  One losing trade ends the day.
Sends Telegram messages for new events and writes docs/status.json for the GitHub Pages site.
Not financial advice.
"""
import json, os, sys, time, urllib.request, urllib.parse, urllib.error, datetime as dt

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
INDEX = os.environ.get("P4_INDEX", "^NSEI")          # Nifty 50 (Yahoo Finance)
VOLSYM = os.environ.get("P4_VWAP_SYMBOL", "NIFTYBEES.NS")  # volume proxy for VWAP
EXPIRY_WEEKDAY = int(os.environ.get("P4_EXPIRY_WEEKDAY", "1"))  # 0=Mon,1=Tue...
RSI_MAX = 70
STATUS = os.path.join(os.path.dirname(__file__), "docs", "status.json")

# ---------------- data ----------------
def yahoo(symbol, rng="10d"):
    url = "https://query1.finance.yahoo.com/v8/finance/chart/" + urllib.parse.quote(symbol) + f"?interval=15m&range={rng}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                j = json.load(r)
            res = j["chart"]["result"][0]
            q = res["indicators"]["quote"][0]
            out = []
            for i, t in enumerate(res["timestamp"]):
                o, h, l, c, v = q["open"][i], q["high"][i], q["low"][i], q["close"][i], q["volume"][i]
                if None in (o, h, l, c):
                    continue
                ist = dt.datetime.fromtimestamp(t, IST)
                out.append(dict(t=int(t), day=ist.strftime("%Y-%m-%d"), hm=ist.strftime("%H:%M"),
                                wd=ist.weekday(), o=o, h=h, l=l, c=c, v=v or 0))
            return out
        except Exception as e:
            print("fetch failed", symbol, e, file=sys.stderr); time.sleep(5)
    raise SystemExit("Could not fetch " + symbol)

# ---------------- indicators ----------------
def ema(x, n):
    o = [None] * len(x)
    if len(x) < n: return o
    k = 2 / (n + 1); e = sum(x[:n]) / n; o[n - 1] = e
    for i in range(n, len(x)): e = x[i] * k + e * (1 - k); o[i] = e
    return o

def rsi(x, n=14):
    o = [None] * len(x)
    if len(x) <= n: return o
    g = l = 0
    for i in range(1, n + 1):
        d = x[i] - x[i - 1]; g += max(d, 0); l += max(-d, 0)
    g /= n; l /= n; o[n] = 100 if l == 0 else 100 - 100 / (1 + g / l)
    for i in range(n + 1, len(x)):
        d = x[i] - x[i - 1]; g = (g * (n - 1) + max(d, 0)) / n; l = (l * (n - 1) + max(-d, 0)) / n
        o[i] = 100 if l == 0 else 100 - 100 / (1 + g / l)
    return o

def atr(rows, n=14):
    tr = [rows[0]["h"] - rows[0]["l"]] + [max(r["h"] - r["l"], abs(r["h"] - p["c"]), abs(r["l"] - p["c"])) for p, r in zip(rows, rows[1:])]
    o = [None] * len(tr)
    if len(tr) < n: return o
    s = sum(tr[:n]) / n; o[n - 1] = s
    for i in range(n, len(tr)): s = (s * (n - 1) + tr[i]) / n; o[i] = s
    return o

def vwap_map(rows):
    m, day, pv, vv = {}, None, 0.0, 0.0
    for r in rows:
        if r["day"] != day: day, pv, vv = r["day"], 0.0, 0.0
        pv += (r["h"] + r["l"] + r["c"]) / 3 * r["v"]; vv += r["v"]
        m[r["t"]] = (r["c"], pv / vv if vv else None)
    return m

# ---------------- strategy ----------------
def analyse(idx, vol, now_ts):
    closes = [r["c"] for r in idx]
    E20, E50, R, A = ema(closes, 20), ema(closes, 50), rsi(closes), atr(idx)
    V = vwap_map(vol)
    orr = {r["day"]: (r["h"], r["l"]) for r in idx if r["hm"] == "09:15"}
    events, pb, pr = [], False, False
    tr = dict(day=None, dir=0, entry=None, sl=None, tg=None, locked=False)
    snap = None
    for i, r in enumerate(idx):
        if r["t"] + 900 > now_ts:      # candle still forming
            break
        if r["day"] != tr["day"]:
            tr.update(day=r["day"], dir=0, locked=False)
        c = r["c"]; mins = int(r["hm"][:2]) * 60 + int(r["hm"][3:])
        vv = V.get(r["t"])
        ok = None not in (E20[i], E50[i], R[i], A[i]) and i > 0 and R[i - 1] is not None and vv and vv[1]
        bull = bear = False
        if ok:
            o = orr.get(r["day"])
            bull = c > E20[i] > E50[i] and 55 < R[i] <= RSI_MAX and R[i] > R[i - 1] and vv[0] > vv[1] and o and c > o[0]
            bear = c < E20[i] < E50[i] and 100 - RSI_MAX <= R[i] < 45 and R[i] < R[i - 1] and vv[0] < vv[1] and o and c < o[1]
        exited = False
        if tr["dir"]:
            s = tr["dir"]; res = None
            if (s == 1 and r["l"] <= tr["sl"]) or (s == -1 and r["h"] >= tr["sl"]): res, px = "STOP", tr["sl"]
            elif (s == 1 and r["h"] >= tr["tg"]) or (s == -1 and r["l"] <= tr["tg"]): res, px = "TARGET", tr["tg"]
            elif mins >= 15 * 60 + 15: res, px = "TIME", c
            if res:
                pts = s * (px - tr["entry"])
                events.append(dict(id=f'{r["t"]}-{res}', t=r["t"], day=r["day"], hm=r["hm"], kind=res, side="CALL" if s == 1 else "PUT", price=round(px, 1), pts=round(pts, 1)))
                if pts <= 0: tr["locked"] = True
                tr["dir"] = 0; exited = True
        expiry = r["wd"] == EXPIRY_WEEKDAY
        window = 9 * 60 + 30 <= mins <= 14 * 60 + 30 and not (expiry and mins >= 13 * 60)
        can = window and tr["dir"] == 0 and not exited and not tr["locked"]
        for side, cond, prev in (("CALL", bull, pb), ("PUT", bear, pr)):
            if can and cond and not prev:
                s = 1 if side == "CALL" else -1; risk = A[i]
                tr.update(dir=s, entry=c, sl=c - s * risk, tg=c + 2 * s * risk)
                events.append(dict(id=f'{r["t"]}-{side}', t=r["t"], day=r["day"], hm=r["hm"], kind="ENTRY", side=side,
                                   price=round(c, 1), strike=int(round(c / 50) * 50), sl=round(tr["sl"]), tg=round(tr["tg"]), atr=round(risk, 1)))
                can = False
        pb, pr = bull, bear
        if ok:
            o = orr.get(r["day"])
            snap = dict(t=r["t"], day=r["day"], hm=r["hm"], close=round(c, 2), ema20=round(E20[i], 2), ema50=round(E50[i], 2),
                        rsi=round(R[i], 1), rsi_rising=R[i] > R[i - 1], atr=round(A[i], 1),
                        bees=round(vv[0], 2), bees_vwap=round(vv[1], 2), or_high=o[0] if o else None, or_low=o[1] if o else None,
                        trend="UP" if c > E20[i] > E50[i] else "DOWN" if c < E20[i] < E50[i] else "MIXED",
                        rsi_state="BULL" if 55 < R[i] <= RSI_MAX and R[i] > R[i - 1] else "BEAR" if 100 - RSI_MAX <= R[i] < 45 and R[i] < R[i - 1] else "STRETCHED" if R[i] > RSI_MAX or R[i] < 100 - RSI_MAX else "NEUTRAL",
                        vwap_state="ABOVE" if vv[0] > vv[1] else "BELOW",
                        or_state="BROKEN UP" if o and c > o[0] else "BROKEN DOWN" if o and c < o[1] else "INSIDE",
                        in_window=window, dir=tr["dir"], sl=round(tr["sl"]) if tr["dir"] else None, tg=round(tr["tg"]) if tr["dir"] else None,
                        locked=tr["locked"])
    return snap, events

# ---------------- notify ----------------
TG_ERRORS = []

def telegram(text):
    tok = (os.environ.get("TELEGRAM_TOKEN") or "").strip()
    chat = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if tok.lower().startswith("bot") and ":" in tok[3:]:
        tok = tok[3:]                       # people sometimes paste "bot123:ABC"
    if not tok or not chat:
        msg = "TELEGRAM NOT CONFIGURED: secret " + ("TELEGRAM_TOKEN" if not tok else "TELEGRAM_CHAT_ID") + " is missing or empty."
        print(msg); TG_ERRORS.append(msg); return False
    data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    try:
        urllib.request.urlopen("https://api.telegram.org/bot" + tok + "/sendMessage", data=data, timeout=20).read()
        print("Telegram message sent to chat", chat[:3] + "***")
        return True
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="ignore")
        hint = ""
        if e.code == 401 or e.code == 404: hint = " -> TELEGRAM_TOKEN is wrong. Copy it again from @BotFather (/mybots -> API Token)."
        elif "chat not found" in body: hint = " -> TELEGRAM_CHAT_ID is wrong, or you haven't pressed Start in your bot."
        elif "blocked" in body: hint = " -> You blocked the bot. Unblock it in Telegram."
        msg = f"TELEGRAM ERROR {e.code}: {body}{hint}"
    except Exception as e:
        msg = f"TELEGRAM ERROR: {e}"
    print(msg, file=sys.stderr); TG_ERRORS.append(msg); return False

def message(e):
    if e["kind"] == "ENTRY":
        up = e["side"] == "CALL"
        return ("🟢 POWER-4: BUY NIFTY %s CE (ATM)" if up else "🔴 POWER-4: BUY NIFTY %s PE (ATM)") % f'{e["strike"]:,}' + \
               f'\nSignal candle {e["hm"]} closed at {e["price"]:,}' + \
               f'\nStop if Nifty {"falls to" if up else "rises to"} {e["sl"]:,}\nTarget {e["tg"]:,} (ATR {e["atr"]})\nExit by 3:15 PM · nearest weekly expiry'
    if e["kind"] == "TARGET":
        return f'✅ POWER-4: TARGET HIT on {e["side"]} at {e["price"]:,} (+{e["pts"]} pts). Exit your option.'
    if e["kind"] == "STOP":
        return f'⛔ POWER-4: STOP HIT on {e["side"]} at {e["price"]:,} ({e["pts"]} pts). Exit now — done for today.'
    return f'⏰ POWER-4: 3:15 PM exit for {e["side"]} at {e["price"]:,} ({e["pts"]:+} pts).'

def main():
    now = int(time.time())
    idx, vol = yahoo(INDEX), yahoo(VOLSYM)
    snap, events = analyse(idx, vol, now)
    old = {}
    try: old = json.load(open(STATUS))
    except Exception: pass
    sent = set(old.get("sent_ids", []))
    today = dt.datetime.now(IST).strftime("%Y-%m-%d")
    for e in events:
        if e["day"] == today and e["id"] not in sent and now - (e["t"] + 900) < 45 * 60:
            telegram(message(e)); sent.add(e["id"])
    status = dict(updated=dt.datetime.now(IST).strftime("%Y-%m-%d %H:%M IST"), snapshot=snap,
                  events=[e for e in events if e["day"] == (snap or {}).get("day")], sent_ids=sorted(sent)[-200:])
    os.makedirs(os.path.dirname(STATUS), exist_ok=True)
    json.dump(status, open(STATUS, "w"), indent=1)
    print(json.dumps(snap, indent=1))

def demo():
    """Replay the last trading day's Power-4 events as Telegram messages (does not change status)."""
    idx, vol = yahoo(INDEX), yahoo(VOLSYM)
    snap, events = analyse(idx, vol, int(time.time()))
    if not snap:
        telegram("DEMO: no market data available."); return
    day = snap["day"]; evs = [e for e in events if e["day"] == day]
    nice = dt.datetime.strptime(day, "%Y-%m-%d").strftime("%a %d %b")
    telegram(f"🧪 DEMO — replay of Power-4 alerts for {nice}. These are past signals, not live trades.")
    if not evs:
        telegram(f"DEMO: Power-4 gave no signals on {nice}.")
    for e in evs:
        time.sleep(1)
        telegram(f"[DEMO {nice} {e['hm']}]\n" + message(e))

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "run"
    if mode == "test":
        telegram("✅ Power-4 bot is connected. You'll get alerts here during market hours.")
    elif mode == "demo":
        demo()
    else:
        main()
    if TG_ERRORS and mode in ("test", "demo"):
        print("\n".join(TG_ERRORS), file=sys.stderr)
        sys.exit(1)   # make the run show a red X so the problem is visible
