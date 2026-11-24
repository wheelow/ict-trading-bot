"""
Backfill trade_ledger.db from MT5 deal history.

Background: a broker-history window bug (now fixed in mt5_broker.py) made the
bot's sync loop miss every deal closed "today" on FBS. Real closed trades —
including two +$3,055.50 US30 wins on 2026-07-22 — were never persisted to the
ledger, so the learning engine never saw them. This script recovers them.

It reads ALL deals from MT5 (with the corrected wide window), pairs each
position's IN+OUT deal, and inserts any trade whose ticket is not already in
the ledger. Safe to re-run: idempotent on ticket.

Usage:
    python data/backfill_ledger.py [--days N]   (default 7)
    python data/backfill_ledger.py --dry-run     (show what would be inserted)
"""
import os
import sys
import sqlite3
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "data", "trade_ledger.db")
MAGIC = 20240714  # bot's magic; used only to FLAG our trades, never to filter


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    import MetaTrader5 as mt5
    if not mt5.initialize():
        print(f"[ERROR] MT5 initialize failed: {mt5.last_error()}")
        sys.exit(1)

    acct = mt5.account_info()
    print(f"MT5 account: {acct.login} | balance=${acct.balance:.2f} | server={acct.server}")

    # Corrected window: `to` must extend past the current trading day (FBS quirk).
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    utc_to = now + timedelta(days=2)
    utc_from = now - timedelta(days=args.days)
    deals = mt5.history_deals_get(utc_from, utc_to)
    if not deals:
        print(f"[INFO] No deals in window {utc_from} -> {utc_to}")
        mt5.shutdown()
        return

    DEAL_IN = getattr(mt5, "DEAL_ENTRY_IN", 0)
    DEAL_OUT = getattr(mt5, "DEAL_ENTRY_OUT", 1)

    # Group by position_id, prefer magic-tagged legs but accept magic=0
    # (FBS sometimes drops magic on the closing leg).
    by_pos = {}
    for d in deals:
        by_pos.setdefault(d.position_id, []).append(d)

    # Existing tickets in the ledger — skip those.
    conn = sqlite3.connect(DB_PATH)
    existing = {row[0] for row in conn.execute("SELECT ticket FROM trades")}
    print(f"Ledger already has {len(existing)} trade(s): {sorted(existing)}")

    to_insert = []
    for pos_id, leg in sorted(by_pos.items()):
        entries = [d for d in leg if d.entry == DEAL_IN]
        exits = [d for d in leg if d.entry == DEAL_OUT]
        if not exits:
            continue  # still open, or no close deal — skip
        e = entries[0] if entries else None
        x = exits[-1]  # if multiple OUT (partial closes), take the last total
        profit = sum(d.profit for d in exits)  # sum all OUT legs = true net PnL
        pnl = float(profit or 0.0)
        status = "won" if pnl > 0 else ("lost" if pnl < 0 else "be")
        close_reason = "TP" if status == "won" else ("SL" if status == "lost" else "BE")

        symbol = (e.symbol if e else x.symbol)
        direction_type = (e.type if e else x.type)
        # MT5 DEAL_TYPE_BUY=0, DEAL_TYPE_SELL=1
        direction = "BUY" if direction_type == 0 else "SELL"
        entry_price = float(e.price if e else 0.0)
        volume = float(e.volume if e else x.volume)
        exit_price = float(x.price)
        open_dt = datetime.utcfromtimestamp((e or x).time)
        close_dt = datetime.utcfromtimestamp(x.time)
        our_magic = bool((e and e.magic == MAGIC) or x.magic == MAGIC)

        # RR achieved: TradeDeal doesn't carry SL/TP, so we can't compute it here.
        # The field stays 0.0 — that's fine, the learning engine reads pnl/status.
        rr = 0.0

        if pos_id in existing:
            continue

        to_insert.append({
            "ticket": pos_id,
            "symbol": symbol,
            "direction": direction,
            "entry": entry_price,
            "stop_loss": 0.0,
            "take_profit": 0.0,
            "volume": volume,
            "planned_rr": 0.0,
            "htf_bias": "",
            "killzone": "",
            "day_of_week": open_dt.weekday(),
            "hour_utc": open_dt.hour,
            "setup_type": "live" if our_magic else "external",
            "sweep_direction": "",
            "confluence": "",
            "news_blocked": 0,
            "sentiment_score": 0.0,
            "status": status,
            "close_price": exit_price,
            "pnl": pnl,
            "rr_achieved": rr,
            "close_reason": close_reason,
            "open_time": open_dt.isoformat(),
            "close_time": close_dt.isoformat(),
            "magic_tagged": our_magic,
        })

    print(f"\nFound {len(to_insert)} closed trade(s) not yet in the ledger:")
    for t in to_insert:
        tag = "[BOT]" if t["magic_tagged"] else "[EXT]"
        print(f"  {tag} ticket={t['ticket']:>11} {t['symbol']} {t['direction']:<4} "
              f"vol={t['volume']:<5} entry={t['entry']:<9} exit={t['close_price']:<9} "
              f"PnL={t['pnl']:+10.2f} {t['status']:<4} @ {t['open_time']}")

    if args.dry_run:
        print("\n--dry-run: not writing. Re-run without --dry-run to persist.")
        mt5.shutdown()
        return

    if not to_insert:
        print("\nNothing to insert — ledger is already up to date.")
        mt5.shutdown()
        return

    conn.executemany("""
        INSERT INTO trades (
            ticket, symbol, direction, entry, stop_loss, take_profit,
            volume, planned_rr, htf_bias, killzone, day_of_week, hour_utc,
            setup_type, sweep_direction, confluence, news_blocked,
            sentiment_score, status, close_price, pnl, rr_achieved,
            close_reason, open_time, close_time, lessons
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [(
        t["ticket"], t["symbol"], t["direction"], t["entry"], t["stop_loss"],
        t["take_profit"], t["volume"], t["planned_rr"], t["htf_bias"],
        t["killzone"], t["day_of_week"], t["hour_utc"], t["setup_type"],
        t["sweep_direction"], t["confluence"], t["news_blocked"],
        t["sentiment_score"], t["status"], t["close_price"], t["pnl"],
        t["rr_achieved"], t["close_reason"], t["open_time"], t["close_time"], "[]",
    ) for t in to_insert])
    conn.commit()

    total_pnl = sum(t["pnl"] for t in to_insert)
    print(f"\nInserted {len(to_insert)} trade(s). Net PnL recovered: ${total_pnl:+.2f}")
    conn.close()
    mt5.shutdown()


if __name__ == "__main__":
    main()
