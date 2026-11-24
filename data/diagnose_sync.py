"""
Sync diagnostic: checks MT5 deal history against risk.trades to find
unreconciled (orphan) trades — trades that opened on MT5 but were never
recorded as closed in the bot's internal state.

Run this while MT5 is connected to verify that Fix 1 works correctly.

Usage:
    python data/diagnose_sync.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta, timezone


def main():
    try:
        import MetaTrader5 as mt5
    except ImportError:
        print("[ERROR] MetaTrader5 package not installed. This script requires MT5.")
        sys.exit(1)

    if not mt5.initialize():
        print(f"[ERROR] MT5 initialize failed: {mt5.last_error()}")
        sys.exit(1)

    account = mt5.account_info()
    print(f"MT5 account: {account.login} | balance=${account.balance:.2f} | server={account.server}")

    MAGIC = 20240714

    # Pull deal history for the last 3 days
    utc_to = datetime.now(timezone.utc).replace(tzinfo=None)
    utc_from = utc_to - timedelta(days=3)
    deals = mt5.history_deals_get(utc_from, utc_to)

    if deals is None or len(deals) == 0:
        print("[INFO] No deals in the last 3 days.")
        mt5.shutdown()
        return

    DEAL_IN = getattr(mt5, 'DEAL_ENTRY_IN', 0)
    DEAL_OUT = getattr(mt5, 'DEAL_ENTRY_OUT', 1)

    print(f"\n=== DEALS (last 3 days, magic={MAGIC}) ===")
    print(f"Total deals retrieved: {len(deals)}")

    # Group by position_id
    deals_by_pos = {}
    all_deals_magic = []
    for d in deals:
        if d.magic == MAGIC:
            all_deals_magic.append(d)
            deals_by_pos.setdefault(d.position_id, []).append(d)

    print(f"Deals with magic={MAGIC}: {len(all_deals_magic)}")

    # Show each position's entry + exit
    print("\n--- Positions (magic-filtered) ---")
    for pos_id, pos_deals in sorted(deals_by_pos.items()):
        entry = [d for d in pos_deals if d.entry == DEAL_IN]
        exits = [d for d in pos_deals if d.entry == DEAL_OUT]
        for e in entry:
            dt = datetime.utcfromtimestamp(e.time).strftime("%Y-%m-%d %H:%M:%S")
            print(f"  OPEN  pos={pos_id} ticket={e.order} {e.symbol} {e.type} "
                  f"vol={e.volume} price={e.price} profit={e.profit:+.2f} @ {dt}")
        for x in exits:
            dt = datetime.utcfromtimestamp(x.time).strftime("%Y-%m-%d %H:%M:%S")
            print(f"  CLOSE pos={pos_id} ticket={x.order} {x.symbol} {x.type} "
                  f"vol={x.volume} price={x.price} profit={x.profit:+.2f} @ {dt}")

    # Also check deals WITHOUT our magic (potential broker magic-drop)
    no_magic = [d for d in deals if d.magic != MAGIC]
    print(f"\n--- Deals with magic != {MAGIC}: {len(no_magic)} ---")
    # Group by symbol to see if any of ours leaked
    our_symbols = {"US30", "US100", "US500"}
    leaked = [d for d in no_magic if d.symbol in our_symbols]
    if leaked:
        print(f"  ⚠️  {len(leaked)} deals on our symbols without our magic:")
        for d in leaked[:20]:
            dt = datetime.utcfromtimestamp(d.time).strftime("%Y-%m-%d %H:%M:%S")
            entry_type = "IN" if d.entry == DEAL_IN else "OUT"
            print(f"    {entry_type} pos={d.position_id} {d.symbol} vol={d.volume} "
                  f"price={d.price} profit={d.profit:+.2f} magic={d.magic} @ {dt}")
    else:
        print("  (none on our symbols)")

    # Check open positions
    positions = mt5.positions_get(magic=MAGIC)
    open_tickets = {p.ticket for p in (positions or [])}
    print(f"\n--- Open positions (magic={MAGIC}): {len(open_tickets)} ---")
    for p in (positions or []):
        dt = datetime.utcfromtimestamp(p.time).strftime("%Y-%m-%d %H:%M:%S")
        print(f"  ticket={p.ticket} {p.symbol} {p.type} vol={p.volume} "
              f"entry={p.price_open} SL={p.sl} TP={p.tp} @ {dt}")

    # Check closed positions (opened but no longer in positions_get)
    all_opened_pos_ids = {d.position_id for d in all_deals_magic if d.entry == DEAL_IN}
    orphan_pos_ids = all_opened_pos_ids - open_tickets
    print(f"\n--- Orphan positions (opened but not currently open): {len(orphan_pos_ids)} ---")
    for pid in orphan_pos_ids:
        deals_for = deals_by_pos.get(pid, [])
        entry = [d for d in deals_for if d.entry == DEAL_IN]
        exits = [d for d in deals_for if d.entry == DEAL_OUT]
        if entry:
            e = entry[0]
            dt = datetime.utcfromtimestamp(e.time).strftime("%Y-%m-%d %H:%M:%S")
            if exits:
                x = exits[0]
                xdt = datetime.utcfromtimestamp(x.time).strftime("%Y-%m-%d %H:%M:%S")
                print(f"  pos={pid} {e.symbol} OPEN@{dt} CLOSED@{xdt} profit={x.profit:+.2f}")
            else:
                print(f"  pos={pid} {e.symbol} OPEN@{dt} — ⚠️ NO EXIT DEAL (orphan!)")
        else:
            # No entry deal with our magic — might be magic-drop on entry side
            print(f"  pos={pid} — no entry deal with magic={MAGIC}")

    mt5.shutdown()
    print("\nDone. If any positions show 'NO EXIT DEAL', Fix 1 should catch them on the next manage_open_positions() call.")


if __name__ == "__main__":
    main()
