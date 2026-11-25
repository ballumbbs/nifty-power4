"""
Keeps the Power-4 bot running during market hours on GitHub Actions.

GitHub's own 5-minute schedule is often hours late, so this script runs one long job:
  - on weekdays between 09:20 and 15:40 IST it checks Power-4 every 5 minutes
    (same logic and Telegram alerts as power4.py) and saves docs/status.json;
  - outside market hours it waits until the next session;
  - shortly before GitHub's 6-hour job limit it starts its own next run
    (workflow_dispatch with GITHUB_TOKEN), so the chain continues day after day.
"""
import os, sys, time, json, subprocess, urllib.request, datetime as dt
import power4

IST = power4.IST
JOB_LIMIT = 5 * 3600 + 40 * 60           # hand over before GitHub's 6 h limit
OPEN, CLOSE = (9, 20), (15, 40)

def now(): return dt.datetime.now(IST)

def in_session(t):
    if t.weekday() >= 5: return False
    m = t.hour * 60 + t.minute
    return OPEN[0] * 60 + OPEN[1] <= m <= CLOSE[0] * 60 + CLOSE[1]

def next_open(t):
    d = t
    while True:
        cand = d.replace(hour=OPEN[0], minute=OPEN[1], second=0, microsecond=0)
        if cand > t and cand.weekday() < 5: return cand
        d = (d + dt.timedelta(days=1)).replace(hour=0, minute=0)

def git(*a):
    return subprocess.run(["git", *a], capture_output=True, text=True)

def save_status():
    git("add", "docs/status.json")
    if git("diff", "--cached", "--quiet").returncode != 0:
        git("commit", "-q", "-m", "Power-4 status update")
        for _ in range(3):
            git("pull", "--rebase", "-q", "origin", "main")
            if git("push", "-q", "origin", "HEAD:main").returncode == 0: return
            time.sleep(5)
        print("status push failed", file=sys.stderr)

def dispatch_next():
    repo, tok = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_TOKEN")
    if not repo or not tok:
        print("no GITHUB_TOKEN - cannot chain"); return
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/actions/workflows/power4.yml/dispatches",
        data=json.dumps({"ref": "main", "inputs": {"mode": "loop"}}).encode(), method="POST",
        headers={"Authorization": "Bearer " + tok, "Accept": "application/vnd.github+json"})
    try:
        urllib.request.urlopen(req, timeout=30).read(); print("next run requested")
    except Exception as e:
        print("dispatch failed:", e, file=sys.stderr)

def main():
    start = time.time()
    while time.time() - start < JOB_LIMIT:
        t = now()
        if in_session(t):
            try:
                power4.main()
                save_status()
            except SystemExit as e:
                print("check skipped:", e, file=sys.stderr)
            except Exception as e:
                print("check failed:", e, file=sys.stderr)
            t = now()
            nxt = (t + dt.timedelta(minutes=5)).replace(second=40, microsecond=0)
            nxt = nxt.replace(minute=(nxt.minute // 5) * 5)
            if nxt <= t: nxt += dt.timedelta(minutes=5)
            wait = (nxt - t).total_seconds()
        else:
            wait = (next_open(t) - t).total_seconds()
            print(f"{t:%a %H:%M} IST - market closed, next session {next_open(t):%a %d %b %H:%M}")
        left = JOB_LIMIT - (time.time() - start)
        if left <= 0: break
        time.sleep(max(5, min(wait, left)))
    dispatch_next()

if __name__ == "__main__":
    main()
