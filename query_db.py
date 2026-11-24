import sqlite3
conn = sqlite3.connect(r'C:/Users/HP/Downloads/ict_trading_bot/data/trade_ledger.db')
c = conn.cursor()

print("=== RECENT BACKTEST RESULTS ===")
c.execute("SELECT symbol,total_trades,win_rate,total_pnl,avg_rr,max_drawdown,notes,period_end FROM backtest_results ORDER BY period_end DESC LIMIT 10")
for r in c.fetchall():
    print(r)

print("\n=== RECENT TRADES ===")
c.execute("SELECT symbol,direction,status,pnl,rr_achieved,open_time FROM trades ORDER BY open_time DESC LIMIT 10")
for r in c.fetchall():
    print(r)

print("\n=== LESSONS ===")
c.execute("SELECT category,rule,confidence,sample_size FROM lessons ORDER BY id DESC LIMIT 10")
for r in c.fetchall():
    print(r)

print("\n=== BACKTEST STATS BY SYMBOL ===")
c.execute("SELECT symbol, COUNT(*) as runs, AVG(win_rate) as avg_wr, SUM(total_trades) as total_trades, SUM(total_pnl) as total_pnl FROM backtest_results GROUP BY symbol")
for r in c.fetchall():
    print(r)

conn.close()
