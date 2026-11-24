"""
One-shot script: backfill the lost US100 trade (ticket 1914649867) into the ledger.

This trade was executed on 2026-07-20 by the MT5 bot but never recorded in the
trade_ledger.db because the deal reconciliation code failed to match the closed
deal (symbol+60s-window matching, broker magic-drop on exit deal).

Run once, then delete this script.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.memory import TradeLedger, TradeRecord as LedgerTradeRecord

DB_PATH = os.path.join(os.path.dirname(__file__), "trade_ledger.db")

# Verify no duplicate exists first
ledger = TradeLedger(DB_PATH)
existing = [t for t in ledger.get_all_trades() if t.get("ticket") == 1914649867]
if existing:
    print(f"[SKIP] ticket 1914649867 already exists in ledger (id={existing[0]['id']}).")
    print(f"       Existing: {existing[0]}")
    sys.exit(0)

# Build the trade record from the bot log:
# 2026-07-20 18:08:04,134 | [RISK] US100: entry=28779.85, SL=28817.36, TP=28715.33, vol=2.99
# 2026-07-20 18:08:04,398 | [MT5] Order filled: ticket=1914649867, price=28780.35, vol=2.99
# 2026-07-20 18:08:04,399 | Trade registered: ticket=1914649867 | actual entry=28779.95
# Balance dropped 55996.70 -> 54890.10 between 18:08 and 18:09 => PnL = -1106.60
# SL was at 28817.36; price hit it. Status: lost.

trade = LedgerTradeRecord(
    ticket=1914649867,
    symbol="US100",
    direction="SELL",
    entry=28779.95,       # actual fill (from "Trade registered" log)
    stop_loss=28817.36,
    take_profit=28715.33,
    volume=2.99,
    planned_rr=1.72,       # from "RR=1.72" in signal log
    htf_bias="bearish",    # from "HTF bias=bearish" in signal log
    killzone="London Close",
    day_of_week=0,          # 2026-07-20 is a Monday (0)
    hour_utc=18,
    setup_type="live",      # live MT5 trade
    confluence="OTE+FVG",   # from "OTE zone + CHoCH + FVG" in signal reason
    news_blocked=False,
    sentiment_score=0.49,   # sentiment was +0.49 at the time
    status="lost",
    close_price=28817.36,   # SL level (SL was hit)
    pnl=-1106.60,           # 55996.70 - 54890.10
    rr_achieved=-1.0,       # stopped out at 1R
    close_reason="SL",
    open_time="2026-07-20T18:08:04",
    close_time="2026-07-20T18:09:30",  # estimated (balance drop visible at 18:09:49)
    lessons="[]",
)

print("=== BACKFILL TRADE PREVIEW ===")
print(f"  Ticket:    {trade.ticket}")
print(f"  Symbol:    {trade.symbol} {trade.direction}")
print(f"  Entry:     {trade.entry}")
print(f"  SL:        {trade.stop_loss}")
print(f"  TP:        {trade.take_profit}")
print(f"  Volume:    {trade.volume}")
print(f"  PnL:       {trade.pnl:+.2f}")
print(f"  Status:    {trade.status}")
print(f"  Open:      {trade.open_time}")
print(f"  Close:     {trade.close_time}")
print(f"  Reason:    {trade.close_reason}")
print("")

confirm = input("Insert this trade into the ledger? [y/N]: ").strip().lower()
if confirm != "y":
    print("Aborted.")
    sys.exit(0)

trade_id = ledger.save_trade(trade)
print(f"\n✅ Trade inserted as id={trade_id}")

# Verify
row = ledger.get_all_trades()[0]  # should be the first (and only) trade
print(f"\nVerification:")
print(f"  id={row['id']}, ticket={row['ticket']}, symbol={row['symbol']}")
print(f"  direction={row['direction']}, status={row['status']}")
print(f"  entry={row['entry']}, pnl={row['pnl']:+.2f}")
