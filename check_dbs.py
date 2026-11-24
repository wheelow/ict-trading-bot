import sqlite3, os, sys

for name, path in [("trade_ledger", r"data/trade_ledger.db"), ("news_calendar", r"data/news_calendar.db")]:
    print(f"\n=== {name} ===")
    if not os.path.exists(path):
        print(f"  File not found: {path}")
        continue
    try:
        conn = sqlite3.connect(path)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [t[0] for t in cur.fetchall()]
        print(f"  Tables: {tables}")
        if "events" in tables:
            cur.execute("SELECT COUNT(*) FROM events")
            print(f"  Events count: {cur.fetchone()[0]}")
            cur.execute("SELECT event_time, title, impact FROM events ORDER BY event_time DESC LIMIT 5")
            for row in cur.fetchall():
                print(f"    {row[0]} | {row[1]} | {row[2]}")
        if "trades" in tables:
            cur.execute("SELECT COUNT(*) FROM trades")
            print(f"  Trades count: {cur.fetchone()[0]}")
            cur.execute("SELECT COUNT(*) FROM backtest_results")
            print(f"  Backtest results count: {cur.fetchone()[0]}")
        conn.close()
    except Exception as e:
        print(f"  Error: {e}")
