"""
ICT Trading Bot — Main Entry Point

Usage:
    python main.py                     # run with config.yaml in simulator mode
    python main.py --config prod.yaml  # run with a different config
    python main.py --steps 200         # run N simulated steps then exit (for demo)

The main loop:
1. Refresh news calendar + sentiment
2. For each symbol: pull latest bars (H1/M15/M5) from broker
3. Generate ICT signal (killzone + HTF bias + liquidity sweep + CHoCH + OTE confluence)
4. Risk manager check (daily DD, trade count, news block)
5. Open position with proper SL/TP
6. Manage open positions (breakeven, partial close, trailing stop)
7. Log everything
"""
import os
import sys
import time
import argparse
import logging
import yaml
import socket

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
from datetime import datetime, timedelta
from typing import List

# >>> FIX 5: Global socket timeout safety net (15 seconds).
# Any unguarded HTTP call (urllib, feedparser, requests) will fail after 15s
# instead of blocking forever. The calendar fetch already has 10s via urllib
# timeout kwarg, and sentiment.py sets its own 8s, but this catches anything
# else we might add. This was the root cause of the bot hanging: Yahoo Finance
# RSS feeds going slow/dead inside feedparser.parse() with no timeout, which
# also made Ctrl+C unresponsive (C-level socket block with no Python bytecode
# to interrupt).
socket.setdefaulttimeout(15)

from core.strategy import generate_signal, ICTStrategy
from core.killzones import parse_kz, in_killzone, current_killzone
from core.memory import TradeLedger, TradeRecord as LedgerTrade
from core.learning import LearningEngine, AdaptiveRiskConfig
from core.filters import apply_all_filters, check_historical_day_confidence
from core.enhancements import apply_enhancements
from core.dealing_range import compute_dealing_range
from core.backtester import IdleBacktester
from core.strategy_lab import StrategyLab
from core.parallel_learner import ParallelLearner, SetupObservation
from core.news_trader import NewsTrader
# NEW: Perpetual learning components
from core.time_travel import TimeTraveler
from core.week_backtester import WeekBacktester
from core.summary_generator import SummaryGenerator
# Aliased to avoid collision with the local `signal = generate_signal(...)` variable
# used inside run_bot()'s scan loop.
import signal as signal_module
from risk.manager import RiskManager, RiskConfig, TradeRecord
from news.filter import NewsFilter, NewsFilterConfig
from broker.simulator import SimulatorBroker
from broker.base import BaseBroker, Position

# Force UTF-8 on stdout/stderr so emoji and other non-ASCII characters in log
# messages don't crash the cp1252 codec on Windows consoles.
# Without this, any logger.info() with an emoji (🆕 🕐 → etc.) raises
# UnicodeEncodeError and prints a "Logging error" traceback to stderr.
# errors='replace' is a final safety net — never let encoding kill a log line.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, Exception):
        pass  # stream doesn't support reconfigure (rare) — ignore silently

# Set up logging
def setup_logging(level: str = "INFO", log_file: str = None):
    fmt = "%(asctime)s | %(levelname)-7s | %(name)-25s | %(message)s"
    handlers = [logging.StreamHandler(sys.stdout)]
    if log_file:
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        # encoding='utf-8' so emoji/non-ASCII in messages don't break file logging
        handlers.append(logging.FileHandler(log_file, encoding='utf-8'))
    logging.basicConfig(level=getattr(logging, level.upper()), format=fmt, handlers=handlers, force=True)

logger = logging.getLogger("ict.main")


def load_config(path: str) -> dict:
    with open(path, "r") as f:
        return yaml.safe_load(f)


def build_broker(cfg: dict) -> BaseBroker:
    mode = cfg.get("mode", "simulator")
    if mode == "simulator":
        symbols = [s["name"] for s in cfg["symbols"]]
        tf_cfg = cfg["timeframes"]
        # Include daily + H4 so Phase 2 reference-level modules (PDH/PDL, dealing range,
        # IPDA) have data to work with on the simulator.
        tfs = [tf_cfg["bias_tf"], tf_cfg["structure_tf"], tf_cfg["entry_tf"]]
        for extra in ("daily_tf", "h4_tf"):
            if extra in tf_cfg and tf_cfg[extra] not in tfs:
                tfs.append(tf_cfg[extra])
        return SimulatorBroker(
            symbols=symbols,
            starting_balance=cfg["risk"]["account_balance"],
            timeframes=tfs,
        )
    elif mode == "mt5_paper":
        from broker.mt5_broker import MT5Broker
        mt5_cfg = cfg["mt5"]
        login_value = os.environ.get("MT5_LOGIN", mt5_cfg.get("login", ""))
        password = os.environ.get("MT5_PASSWORD", mt5_cfg.get("password", ""))
        server = os.environ.get("MT5_SERVER", mt5_cfg.get("server", ""))
        path = os.environ.get("MT5_PATH", mt5_cfg.get("path", ""))
        if not login_value or not password or not server:
            raise ValueError("MT5_LOGIN, MT5_PASSWORD, and MT5_SERVER must be set in .env or environment variables")
        login = int(login_value)
        return MT5Broker(
            login=login,
            password=password,
            server=server,
            path=path,
        )
    else:
        raise ValueError(f"Unknown mode: {mode}")


def build_risk_manager(cfg: dict) -> RiskManager:
    rcfg = cfg["risk"]
    return RiskManager(RiskConfig(
        account_balance=rcfg["account_balance"],
        risk_per_trade_pct=rcfg["risk_per_trade_pct"],
        max_daily_drawdown_pct=rcfg["max_daily_drawdown_pct"],
        max_trades_per_day=rcfg["max_trades_per_day"],
        reward_to_risk=rcfg["reward_to_risk"],
        breakeven_at_rr=rcfg["breakeven_at_rr"],
        partial_close_at_rr=rcfg["partial_close_at_rr"],
        trailing_stop_pips=rcfg["trailing_stop_pips"],
    ))


def build_news_filter(cfg: dict) -> NewsFilter:
    ncfg = cfg["news"]
    return NewsFilter(NewsFilterConfig(
        enabled=ncfg["enabled"],
        block_minutes_before=ncfg["block_minutes_before"],
        block_minutes_after=ncfg["block_minutes_after"],
        high_impact_keywords=ncfg["high_impact_keywords"],
        calendar_url=ncfg["calendar_url"],
        sentiment_enabled=ncfg["sentiment_enabled"],
        sentiment_threshold=ncfg["sentiment_threshold"],
        rss_feeds=ncfg.get("rss_feeds", []),
    ))


# Track how many consecutive sync loops each open trade has been "waiting for
# exit deal". Used by manage_open_positions to escalate a stuck reconciliation
# from INFO to WARNING after a threshold (default 12 loops ~= 1 min at the 5s
# poll interval) and dump diagnostics, so a silent sync failure (like the
# 2026-07-22 incident where +$3,055.50 of wins were never recorded) can't go
# unnoticed for hours.
_sync_wait_counts: dict = {}
_SYNC_WARN_AFTER = 12


def manage_open_positions(broker: BaseBroker, risk: RiskManager, cfg: dict, ledger=None):
    """Apply breakeven, partial close, and trailing stop rules to open positions.
    Also detects positions that were closed by MT5 (SL/TP hit) and syncs them
    with the risk manager AND the persistent ledger.

    Args:
        ledger: optional TradeLedger — closed live trades are persisted here so
                stats survive restarts and feed the learning engine. Pass None
                only for legacy call sites.
    """
    positions = broker.get_open_positions()

    # === RECONCILIATION (Fix 1) ===
    # Sync risk manager's open trades with broker's actual open positions.
    # If a trade in risk.trades is still "open" but no longer in broker.positions,
    # it means MT5 closed it (SL/TP hit) — we need to record the P&L and persist
    # it to the ledger.
    #
    # The previous implementation matched deals by symbol + a ±60-second window
    # on entry_deal.time, and filtered closing deals by magic. This silently
    # failed for the 2026-07-20 loss: FBS does not always propagate `magic`
    # onto the closing-side deal, and the tight window missed deals that took
    # longer to land in MT5's history. Net result: a real −$1,106.60 loss was
    # invisible to the bot forever (0 rows in the ledger).
    #
    # New approach: match by trade.ticket → deal.position_id. The position_id
    # is the authoritative key preserved on BOTH the entry and exit legs of
    # every deal, regardless of broker magic quirks. We pull 3 days of history
    # (not 1) so a slow-indexing deal is still caught.
    if hasattr(broker, 'get_recent_closed_deals'):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        # Resolve MT5 deal entry constants up front (avoids NameError if mt5 not imported)
        try:
            import MetaTrader5 as mt5
            DEAL_IN = getattr(mt5, 'DEAL_ENTRY_IN', 0)
            DEAL_OUT = getattr(mt5, 'DEAL_ENTRY_OUT', 1)
        except ImportError:
            mt5 = None
            DEAL_IN = 0
            DEAL_OUT = 1

        try:
            # 3-day window (was 1) — some brokers index deals into history 1-2 min late.
            closed_deals = broker.get_recent_closed_deals(magic=20240714, days=3)

            # Index deals by position_id. Include ALL deals for our symbols even if
            # magic wasn't propagated to the closing leg (broker quirk on FBS).
            # We re-filter by ticket match below, which is authoritative.
            deals_by_position = {}
            for d in closed_deals:
                deals_by_position.setdefault(d.position_id, []).append(d)

            open_tickets = {p.ticket for p in positions if getattr(p, 'ticket', 0)}

            for trade in risk.trades:
                if trade.status != "open":
                    continue
                if not trade.ticket:
                    # No ticket — can't reconcile. Surface it so we notice.
                    logger.warning(f"[SYNC] open trade has no ticket, cannot reconcile: "
                                   f"{trade.symbol} {trade.direction} opened {trade.open_time}")
                    continue

                # Still open at the broker? Nothing to do.
                if trade.ticket in open_tickets:
                    _sync_wait_counts.pop(trade.ticket, None)  # healthy; reset wait counter
                    continue

                # Closed at broker — find the entry+exit deal pair keyed by position_id.
                deals = deals_by_position.get(trade.ticket, [])
                entry_deal = None
                exit_deal = None
                for d in deals:
                    if d.entry == DEAL_IN:
                        entry_deal = d
                    elif d.entry == DEAL_OUT:
                        exit_deal = d

                if not exit_deal:
                    # Trade is gone from open positions but we have no exit deal yet.
                    # This can happen for a few seconds right after SL/TP fires, before
                    # MT5 indexes the deal. We'll catch it next loop. If it persists
                    # beyond _SYNC_WARN_AFTER loops (~1 min), escalate to WARNING and
                    # dump diagnostics — a deal that never appears almost always means
                    # a real reconciliation bug (e.g. the 2026-07-22 window bug where
                    # FBS omitted today's deals when `to` didn't cross the trading day).
                    waits = _sync_wait_counts.get(trade.ticket, 0) + 1
                    _sync_wait_counts[trade.ticket] = waits
                    if waits >= _SYNC_WARN_AFTER and waits % _SYNC_WARN_AFTER == 0:
                        have_pos_ids = sorted(deals_by_position.keys())
                        logger.warning(
                            f"[SYNC] {trade.symbol} ticket={trade.ticket} no exit deal after "
                            f"{waits} loops (~{waits * 5}s) — LIKELY RECONCILIATION BUG. "
                            f"open_time={trade.open_time} | "
                            f"deals_in_window={len(closed_deals)} | "
                            f"position_ids_seen={have_pos_ids[:12]}"
                        )
                    else:
                        logger.info(f"[SYNC] {trade.symbol} ticket={trade.ticket} no longer open, "
                                    f"waiting for exit deal to index (will retry next loop)")
                    continue

                # We have an exit deal — record the P&L.
                # exit_deal.profit already accounts for volume, direction, and broker fees.
                pnl = float(exit_deal.profit or 0.0)
                status = "won" if pnl > 0 else ("lost" if pnl < 0 else "be")
                exit_price = float(exit_deal.price or 0.0)

                # Sanity: the entry deal should be on the same symbol as our trade.
                # If it isn't, ticket reuse may have happened — warn but proceed.
                if entry_deal and entry_deal.symbol != trade.symbol:
                    logger.warning(f"[SYNC] ticket={trade.ticket} symbol mismatch: "
                                   f"trade={trade.symbol} deal={entry_deal.symbol} — proceeding anyway")

                risk.close_trade(trade, exit_price, now, status=status)
                _sync_wait_counts.pop(trade.ticket, None)  # reconciled; reset wait counter

                # Persist to the ledger so the loss survives restarts and feeds learning.
                # Without this, stats_summary() and get_pattern_win_rates() never see
                # live trades — the learning engine runs blind.
                if ledger is not None:
                    try:
                        from core.memory import TradeRecord as LedgerTradeRecord
                        close_reason = "SL" if status == "lost" else ("TP" if status == "won" else "BE")
                        # RR achieved: use what risk.close_trade just computed, or fall back to planned.
                        rr_for_ledger = trade.rr_achieved or 0.0
                        ledger.save_trade(LedgerTradeRecord(
                            ticket=trade.ticket,
                            symbol=trade.symbol,
                            direction=trade.direction,
                            entry=trade.entry,
                            stop_loss=trade.stop_loss,
                            take_profit=trade.take_profit,
                            volume=trade.volume,
                            planned_rr=rr_for_ledger,
                            htf_bias="",            # not tracked on the runtime TradeRecord
                            killzone="",            # (could be enriched later if we thread it through)
                            day_of_week=trade.open_time.weekday() if trade.open_time else 0,
                            hour_utc=trade.open_time.hour if trade.open_time else 0,
                            setup_type="live",
                            confluence="",
                            status=status,
                            close_price=exit_price,
                            pnl=pnl,
                            rr_achieved=rr_for_ledger,
                            close_reason=close_reason,
                            open_time=trade.open_time.isoformat() if trade.open_time else "",
                            close_time=now.isoformat(),
                        ))
                    except Exception as e:
                        logger.error(f"[SYNC] failed to persist closed trade to ledger "
                                     f"(ticket={trade.ticket}): {e}")

                logger.info(f"[SYNC] MT5 closed trade detected: {trade.symbol} {trade.direction} | "
                            f"ticket={trade.ticket} | PnL={pnl:+.2f} | Status={status} | "
                            f"exit={exit_price:.2f}")

        except Exception as e:
            # Reconciliation errors are trading-critical — log at ERROR, not WARNING,
            # so they don't disappear. Re-raise is too aggressive (would kill the loop);
            # but we want this to be loud.
            logger.error(f"[SYNC] MT5 deal reconciliation error: {e}", exc_info=True)

    # === POSITION MANAGEMENT (Fix 4) ===
    # BE/partial/trailing state now lives on the persistent risk.trades entries
    # (looked up by ticket), not on the transient Position objects returned by
    # get_open_positions(). Previously these flags reset to False every loop,
    # causing the bot to re-issue BE and partial-close modify requests every
    # 5 seconds.
    trailing_pips = cfg.get("risk", {}).get("trailing_stop_pips", 10.0)
    partial_pct = 0.5  # close 50% at partial_at_rr

    # Build a ticket -> TradeRecord map for O(1) lookup.
    trade_by_ticket = {t.ticket: t for t in risk.trades if t.ticket and t.status == "open"}

    for pos in positions:
        current_price = broker.get_tick(pos.symbol)
        risk_dist = abs(pos.entry - pos.stop_loss) if pos.stop_loss else 0.0

        # Resolve the persistent TradeRecord for this position (if we have one).
        # If we don't (e.g. position opened by another tool), fall back to the
        # transient Position object — old behavior preserved.
        trade = trade_by_ticket.get(getattr(pos, 'ticket', 0), None)
        # Use the persistent record's flags when available, else the position's.
        if trade is not None:
            be_applied = trade.be_applied
            partial_closed = trade.partial_closed
        else:
            be_applied = pos.be_applied
            partial_closed = pos.partial_closed

        # Infer BE state from SL position (handles bot reconnect after BE was already applied).
        # If SL is at or better than entry, BE must have fired already.
        if not be_applied:
            if pos.direction == "BUY" and pos.stop_loss >= pos.entry:
                be_applied = True
            elif pos.direction == "SELL" and pos.stop_loss <= pos.entry:
                be_applied = True
            if be_applied and trade is not None:
                trade.be_applied = True

        # --- (a) BREAKEVEN: move SL to entry once price reaches breakeven_at_rr ---
        if not be_applied and risk_dist > 0 and risk.should_move_to_breakeven(pos, current_price):
            new_sl = pos.entry
            moved = False
            if pos.direction == "BUY" and new_sl > pos.stop_loss:
                moved = broker.modify_position(pos, new_stop_loss=new_sl)
            elif pos.direction == "SELL" and new_sl < pos.stop_loss:
                moved = broker.modify_position(pos, new_stop_loss=new_sl)
            if moved:
                pos.stop_loss = new_sl
                pos.be_applied = True
                if trade is not None:
                    trade.be_applied = True
                logger.info(f"Moved {pos.symbol} SL to breakeven @ {new_sl:.2f}")
                be_applied = True

        # --- (b) PARTIAL CLOSE: take 50% off at partial_close_at_rr ---
        if not partial_closed and risk.should_partial_close(pos, current_price):
            close_vol = round(pos.volume * partial_pct, 2)
            if close_vol >= 0.01:
                result = broker.close_position(pos, partial_volume=close_vol)
                if result:
                    pos.partial_closed = True
                    if trade is not None:
                        trade.partial_closed = True
                    partial_closed = True
                    logger.info(f"[PARTIAL] Closed 50% of {pos.symbol} {pos.direction} | "
                                f"closed_vol={close_vol:.2f} remaining={pos.volume:.2f} @ {current_price:.2f}")

        # --- (c) TRAILING STOP: after BE fires, trail SL behind price ---
        # Only tightens — never loosens. Trails by trailing_pips from current price.
        if be_applied and trailing_pips > 0:
            if pos.direction == "BUY":
                new_sl = current_price - trailing_pips
                if new_sl > pos.stop_loss:
                    if broker.modify_position(pos, new_stop_loss=new_sl):
                        pos.stop_loss = new_sl
                        logger.info(f"[TRAIL] {pos.symbol} BUY SL trailed to {new_sl:.2f} (price={current_price:.2f})")
            elif pos.direction == "SELL":
                new_sl = current_price + trailing_pips
                if new_sl < pos.stop_loss:
                    if broker.modify_position(pos, new_stop_loss=new_sl):
                        pos.stop_loss = new_sl
                        logger.info(f"[TRAIL] {pos.symbol} SELL SL trailed to {new_sl:.2f} (price={current_price:.2f})")


def run_bot(cfg: dict, max_steps: int = None, fast: bool = False):
    """Main trading loop."""
    broker = build_broker(cfg)
    if not broker.connect():
        logger.error("Failed to connect to broker")
        return

    risk = build_risk_manager(cfg)
    risk.sync_balance(broker.account_balance())  # match broker's actual balance on startup
    news = build_news_filter(cfg)
    kzs = parse_kz(cfg["strategy"])

    # === INITIALIZE ALL SYSTEMS ===
    ledger = TradeLedger(db_path=os.path.join(os.path.dirname(__file__), "data", "trade_ledger.db"))
    adaptive_cfg = AdaptiveRiskConfig(
        base_risk_pct=cfg["risk"].get("risk_per_trade_pct", 2.0),
        min_risk_pct=cfg.get("learning", {}).get("min_risk_pct", 0.5),
        max_risk_pct=cfg.get("learning", {}).get("max_risk_pct", 3.0),
        loss_streak_reduction=cfg.get("learning", {}).get("loss_streak_reduction", 0.5),
        win_streak_increase=cfg.get("learning", {}).get("win_streak_increase", 0.25),
        min_trades_for_patterns=cfg.get("learning", {}).get("min_trades_for_patterns", 10),
        min_win_rate_to_trade=cfg.get("learning", {}).get("min_win_rate_to_trade", 30.0),
        max_loss_streak=cfg.get("learning", {}).get("max_loss_streak", 5),
    )
    learning = LearningEngine(ledger, adaptive_cfg)
    backtester = IdleBacktester(ledger, broker, cfg)
    strategy_lab = StrategyLab(ledger, backtester, cfg)
    parallel_learner = ParallelLearner(ledger, learning, strategy_lab)
    news_trader = NewsTrader(ledger, broker)
    
    # === NEW: PERPETUAL LEARNING SYSTEMS ===
    time_traveler = TimeTraveler(
        checkpoint_path=os.path.join(os.path.dirname(__file__), "data", "time_travel_checkpoint.json"),
        earliest_year=2020
    )
    ict_strategy = ICTStrategy(cfg)
    week_backtester = WeekBacktester(broker, cfg, ict_strategy)
    summary_generator = SummaryGenerator(time_traveler, learning, ledger)
    
    # Signal handler for graceful Ctrl+C
    # The signal handler is COOPERATIVE: the first Ctrl+C sets a flag and logs a
    # message, then lets the main loop exit cleanly so the `finally` block below
    # can print the full SESSION SUMMARY and save the checkpoint. (An earlier
    # version called os._exit(0) here to avoid blocking on slow I/O, but that
    # skipped the summary entirely — Ctrl+C showed "shutting down" with no stats.)
    # The second Ctrl+C is the nuclear escape hatch: it force-kills the process,
    # useful if the loop is stuck in a blocking network call.
    _shutdown_requested = False

    def signal_handler(sig, frame):
        nonlocal _shutdown_requested
        if _shutdown_requested:
            # Second Ctrl+C — force immediate exit (nuclear)
            logger.info("⚠️  Second Ctrl+C — forcing immediate exit")
            os._exit(1)
        _shutdown_requested = True
        logger.info("\n" + "=" * 70)
        logger.info("🛑 Ctrl+C received — finishing current step, then printing summary...")
        logger.info("   (Press Ctrl+C again to force-quit immediately)")
        logger.info("=" * 70)

    signal_module.signal(signal_module.SIGINT, signal_handler)

    symbols = cfg["symbols"]
    tf_bias = cfg["timeframes"]["bias_tf"]
    tf_struct = cfg["timeframes"]["structure_tf"]
    tf_entry = cfg["timeframes"]["entry_tf"]
    min_rr = cfg["risk"]["reward_to_risk"]

    # === APPLY STRATEGY MODE (strict / normal / loose) ===
    strategy_mode = cfg.get("strategy_mode", "normal")
    if strategy_mode == "loose":
        # Loosen filters to get more trades flowing (build history for learning engine)
        cfg["strategy"]["ote_min"] = 0.38
        cfg["strategy"]["ote_max"] = 0.88
        cfg["strategy"]["liquidity_lookback"] = 30
        cfg["strategy"]["sweep_buffer_pips"] = 1.0
        cfg["strategy"]["bos_buffer_pips"] = 1.0
        cfg["strategy"]["fvg_min_size_pips"] = 2
        cfg["risk"]["reward_to_risk"] = 2.0  # lower RR minimum
        min_rr = 2.0
        logger.info("[MODE] LOOSE — relaxed filters to build trade history (lower RR, wider OTE, more sweeps)")
    elif strategy_mode == "strict":
        logger.info("[MODE] STRICT — pure ICT filters (fewer trades, higher quality)")
    else:
        logger.info("[MODE] NORMAL — balanced filters")

    poll_interval = cfg["execution"]["poll_interval_seconds"]
    if fast and isinstance(broker, SimulatorBroker):
        poll_interval = 0  # no sleep in fast simulator mode
    step = 0
    last_failed_signal = {}  # symbol -> (timestamp, entry_price) — to avoid retrying failed orders every 5s
    last_idle_task_hour = -1  # track which idle task ran last (rotate: backtest/evolve/discover)
    last_analysis_day = -1
    last_kz = "Off-session"  # track killzone transitions for parallel learner
    last_regime_check = 0  # track when we last checked market regime
    from datetime import timezone as tz
    session_start = datetime.now(tz.utc).replace(tzinfo=None)

    # === APPLY BEST EVOLVED STRATEGY IF AVAILABLE ===
    best_strategy = strategy_lab.get_best_strategy_params()
    if best_strategy and best_strategy.score > 0:
        logger.info("=" * 70)
        logger.info("[EVOLUTION] Loading best evolved strategy for live trading")
        logger.info(f"  Strategy ID: {best_strategy.id} (gen {best_strategy.generation})")
        logger.info(f"  Score: {best_strategy.score:.4f} | Win rate: {best_strategy.win_rate:.1f}%")
        logger.info(f"  OTE range: [{best_strategy.ote_min:.2f} - {best_strategy.ote_max:.2f}]")
        logger.info(f"  Min RR: {best_strategy.min_rr:.1f} | NY KZ: {best_strategy.ny_am_kz_start}-{best_strategy.ny_am_kz_end}")
        # Apply to config
        cfg["strategy"]["ote_min"] = best_strategy.ote_min
        cfg["strategy"]["ote_max"] = best_strategy.ote_max
        cfg["strategy"]["ny_am_kz_start"] = best_strategy.ny_am_kz_start
        cfg["strategy"]["ny_am_kz_end"] = best_strategy.ny_am_kz_end
        cfg["strategy"]["london_kz_start"] = best_strategy.london_kz_start
        cfg["strategy"]["london_kz_end"] = best_strategy.london_kz_end
        cfg["strategy"]["fvg_lookback"] = best_strategy.fvg_lookback
        cfg["strategy"]["liquidity_lookback"] = best_strategy.liquidity_lookback
        cfg["strategy"]["sweep_buffer_pips"] = best_strategy.sweep_buffer_pips
        cfg["strategy"]["bos_buffer_pips"] = best_strategy.bos_buffer_pips
        cfg["risk"]["reward_to_risk"] = best_strategy.min_rr
        min_rr = best_strategy.min_rr
        kzs = parse_kz(cfg["strategy"])  # re-parse with evolved killzones
        logger.info("  [OK] Evolved strategy applied to live trading")
    else:
        logger.info("=" * 70)
        logger.info("[EVOLUTION] No evolved strategy yet — using base ICT strategy")
        logger.info("  Run --evolve manually, or let idle time build up strategies")
        logger.info("=" * 70)

    logger.info("=" * 70)
    logger.info("ICT Trading Bot — FULLY AUTOMATED MODE (PERPETUAL LEARNING)")
    logger.info(f"Mode: {cfg['mode']} | Symbols: {[s['name'] for s in symbols]} | Risk/trade: {cfg['risk']['risk_per_trade_pct']}%")
    logger.info(f"Killzones: {[kz.name for kz in kzs]}")
    logger.info(f"Memory: {ledger.db_path}")
    logger.info(f"Learning: adaptive risk {adaptive_cfg.min_risk_pct}-{adaptive_cfg.max_risk_pct}%")
    logger.info(f"Strategy Lab: evolution + pattern discovery")
    logger.info(f"🕐 Time Traveler: Week-by-week backtest ({time_traveler.direction} from {time_traveler.current_date.date()})")
    logger.info(f"   Weeks analyzed: {time_traveler.weeks_analyzed} | Years covered: {time_traveler.weeks_analyzed/52:.1f}")
    logger.info(f"Auto-mode: IDLE → week backtest/evolve/discover | KILLZONE → live trade")
    logger.info(f"Press Ctrl+C anytime to see summary and save progress")
    if fast:
        logger.info("Fast mode enabled — no sleep between steps (simulator only)")
    logger.info("=" * 70)

    # Show existing memory stats
    existing_stats = ledger.stats_summary()
    if existing_stats["total_trades"] > 0:
        logger.info(f"[MEMORY] {existing_stats['total_trades']} trades | W/L={existing_stats['wins']}/{existing_stats['losses']} | "
                    f"win_rate={existing_stats['win_rate']:.1f}% | PnL={existing_stats['total_pnl']:+.2f}")
    else:
        logger.info("[MEMORY] No existing trades — starting fresh")
    if strategy_lab.population:
        logger.info(f"[EVOLUTION] {len(strategy_lab.population)} evolved strategies in memory | "
                    f"best score: {strategy_lab.best_variant.score:.4f}" if strategy_lab.best_variant else "")

    try:
        while True:
            # Graceful shutdown: if the signal handler set the flag, exit the
            # loop so the `finally` block prints the SESSION SUMMARY.
            if _shutdown_requested:
                logger.info("Finishing up — generating session summary...")
                break

            step += 1
            from datetime import timezone
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            closed_positions = []

            # Step the simulator forward (advance one M5 bar)
            if isinstance(broker, SimulatorBroker):
                broker.step_time()
                closed_positions = broker.check_position_hits()
                # Sync any closed positions with the risk manager
                for pos, close_price, status in closed_positions:
                    # Find matching open trade in risk manager
                    for trade in risk.trades:
                        if trade.status == "open" and trade.symbol == pos.symbol and \
                           trade.direction == pos.direction and \
                           abs(trade.entry - pos.entry) < 0.01:
                            risk.close_trade(trade, close_price, now, status=status)
                            # Update the ledger
                            if hasattr(trade, 'ledger_id') and trade.ledger_id:
                                ledger.update_trade_outcome(
                                    trade_id=trade.ledger_id,
                                    status=status,
                                    close_price=close_price,
                                    pnl=trade.pnl,
                                    rr_achieved=trade.rr_achieved,
                                    close_reason="SL" if status == "lost" else "TP",
                                    close_time=now.isoformat()
                                )
                            # === PARALLEL LEARNER: Real-time lesson extraction ===
                            parallel_learner.on_trade_closed({
                                "symbol": trade.symbol,
                                "direction": trade.direction,
                                "entry": trade.entry,
                                "status": status,
                                "rr_achieved": trade.rr_achieved,
                                "killzone": last_kz,
                                "confluence": getattr(trade, 'confluence', 'OTE_only'),
                                "hour_utc": now.hour,
                            })
                            break
                # Use simulated bar time for killzone/news checks (so the demo
                # can find setups regardless of real-world clock time)
                now = broker.current_simulated_time()

            # Daily rollover & risk check
            can_trade, reason = risk.can_trade(now)

            # News filter check
            news_ok, news_reason = news.can_trade(now)
            if not news_ok:
                logger.info(f"News block: {news_reason}")

            # === NEWS TRADER: Observe upcoming news events ===
            try:
                upcoming = news.upcoming_events(within_minutes=30)
                for event in upcoming:
                    event_time = event.datetime if hasattr(event, 'datetime') else None
                    if event_time:
                        # Convert to naive datetime if needed
                        et = event_time
                        if hasattr(et, 'tzinfo') and et.tzinfo is not None:
                            et = et.replace(tzinfo=None)
                        minutes_until = (et - now).total_seconds() / 60
                        if 5 <= minutes_until <= 30:  # observe 5-30 min before
                            news_trader.observe_upcoming_event({
                                "title": event.title,
                                "datetime": et,
                                "currency": event.currency,
                                "impact": event.impact,
                            }, broker)
            except Exception as e:
                logger.debug(f"News observation error: {e}")

            # === NEWS TRADER: Analyze events that happened 60+ min ago ===
            try:
                pending_keys = list(news_trader.pending_observations.keys())
                for key in pending_keys:
                    obs = news_trader.pending_observations.get(key)
                    if obs:
                        event_dt = datetime.fromisoformat(obs.event_time)
                        minutes_after = (now - event_dt).total_seconds() / 60
                        if minutes_after >= 60:
                            news_trader.analyze_post_news(key, broker)
            except Exception as e:
                logger.debug(f"News analysis error: {e}")

            # Killzone check
            in_kz = in_killzone(now, kzs)
            kz_name = current_killzone(now, kzs)

            # === WEEKEND / MARKET-CLOSED GUARD ===
            # Check once per ~5 min (every 60 steps at 5s poll) to avoid hammering MT5.
            # If every symbol's market is closed, we idle cleanly instead of polling
            # pointlessly (which is what was happening on weekends).
            if step % 60 == 0 and not isinstance(broker, SimulatorBroker):
                try:
                    any_open = any(broker.is_market_open(s["name"]) for s in symbols)
                    if not any_open:
                        if step % 720 == 0:  # log once per hour, not every 5 min
                            logger.info(f"[MARKET-CLOSED] All symbols closed (weekend/holiday). Idling until next session. step={step}")
                        # Skip the per-symbol scanning below; still manage open positions
                        # (in case any are left from Friday close) and run idle learning tasks.
                        manage_open_positions(broker, risk, cfg, ledger=ledger)
                        time.sleep(poll_interval)
                        continue
                except Exception as e:
                    logger.debug(f"Market-open check error (non-fatal): {e}")

            # === PARALLEL LEARNER: Track killzone transitions ===
            if kz_name != last_kz:
                if last_kz != "Off-session" and kz_name == "Off-session":
                    # Exiting a killzone
                    parallel_learner.on_killzone_exit(last_kz)
                if kz_name != "Off-session":
                    # Entering a killzone
                    parallel_learner.on_killzone_enter(kz_name)
                last_kz = kz_name

            # === PARALLEL LEARNER: Detect market regime every 5 minutes (60 steps) ===
            if step - last_regime_check >= 60:
                last_regime_check = step
                try:
                    parallel_learner.detect_market_regime(broker, "US30")
                except Exception:
                    pass

            # === PARALLEL LEARNER: Run micro-evolution during killzone (once per KZ) ===
            if in_kz and not parallel_learner.micro_evolution_done_this_kz and step % 36 == 0:  # ~3 min in
                try:
                    parallel_learner.run_micro_evolution(broker, cfg)
                except Exception as e:
                    logger.debug(f"Micro-evolution error (non-fatal): {e}")

            # Manage existing positions
            manage_open_positions(broker, risk, cfg, ledger=ledger)

            # Scan for new setups (only if allowed to trade)
            if can_trade and news_ok and in_kz:
                for sym_cfg in symbols:
                    symbol = sym_cfg["name"]

                    # Skip if we already have an open position on this symbol
                    existing = [p for p in broker.get_open_positions() if p.symbol == symbol]
                    if existing:
                        continue

                    # Skip if we closed a trade on this symbol in the last step
                    # (prevents immediately re-entering the same setup)
                    just_closed = any(
                        pos.symbol == symbol for pos, _, _ in (closed_positions if isinstance(broker, SimulatorBroker) else [])
                    )
                    if just_closed:
                        continue

                    # Skip if we already tried this symbol recently and the order failed
                    # (wait for a new M5 bar before retrying)
                    if symbol in last_failed_signal:
                        last_attempt, last_entry = last_failed_signal[symbol]
                        # Only retry if 5 minutes (1 M5 bar) have passed
                        if (now - last_attempt).total_seconds() < 300:
                            continue
                        # Or if the signal price has changed significantly
                        # (we'll re-evaluate below)
                        del last_failed_signal[symbol]

                    # Pull bars
                    bias_df = broker.get_history(symbol, tf_bias, count=200)
                    struct_df = broker.get_history(symbol, tf_struct, count=200)
                    entry_df = broker.get_history(symbol, tf_entry, count=200)

                    # Pull daily bars for ICT 2022 model + liquidity pools + dealing range
                    daily_df = broker.get_history(symbol, "D1", count=50) if hasattr(broker, 'get_history') else None

                    if bias_df.empty or struct_df.empty or entry_df.empty:
                        continue
                    if len(entry_df) < 50:
                        continue

                    signal = generate_signal(symbol, bias_df, struct_df, entry_df, cfg["strategy"], min_rr=min_rr, daily_df=daily_df)
                    if signal is None:
                        continue

                    # === DIRECTIONAL SENTIMENT CHECK ===
                    # Don't trade AGAINST the prevailing market sentiment.
                    # This prevented the 2026-07-20 scenario: sentiment +0.49 (bullish),
                    # the bot shorted US100 the instant sentiment dropped below the extreme
                    # threshold — a classic whipsaw.
                    sentiment_ok, sentiment_reason = news.can_trade(now, direction=signal.direction)
                    if not sentiment_ok:
                        logger.info(f"*** SENTIMENT REJECT: {signal.direction} {symbol} | {sentiment_reason}")
                        continue

                    # === HARD MIN-RR FILTER ===
                    # Reject any signal whose reward-to-risk is below the configured minimum.
                    # This was the root cause of the 2026-07-20 loss: a 1.72 RR SELL on US100
                    # was taken while config min was 3.0 (and the evolved strategy required 3.4).
                    # The strategy generator may still emit sub-min-RR setups when TP is anchored
                    # to a liquidity pool rather than a fixed R-multiple, so we enforce it here
                    # as a hard backstop before any expensive filter work runs.
                    if signal.rr < min_rr:
                        logger.info(f"*** MIN-RR REJECT: {signal.direction} {symbol} | "
                                    f"RR={signal.rr:.2f} < min_rr={min_rr:.2f}")
                        continue

                    # === APPLY BOOK-ALIGNED FILTERS (day-of-week, NFP, FOMC, dealing range, displacement) ===
                    try:
                        dr = compute_dealing_range(bias_df) if not bias_df.empty else None
                        dr_tuple = (dr.low, dr.high) if dr else None

                        # Collect news events as dicts for the filter module
                        news_event_dicts = []
                        try:
                            upcoming_events = news.upcoming_events(within_minutes=120)
                            for ev in upcoming_events:
                                news_event_dicts.append({
                                    "title": ev.title,
                                    "time": ev.datetime.isoformat() if hasattr(ev, 'datetime') else "",
                                    "impact": getattr(ev, 'impact', ''),
                                })
                        except Exception:
                            pass

                        atr_val = 0.0
                        try:
                            from core.strategy import _compute_atr
                            atr_val = _compute_atr(entry_df, period=14)
                        except Exception:
                            pass

                        filter_cfg = {
                            "strict_day_filter": strategy_mode == "strict",
                            "dealing_range_strictness": 0.5,
                            "min_displacement_atr": 1.5,
                        }
                        filters_passed, filter_reasons = apply_all_filters(
                            timestamp=now,
                            current_price=signal.entry,
                            direction=signal.direction,
                            displacement_size=atr_val * 1.5,  # conservative estimate
                            atr=atr_val,
                            dealing_range=dr_tuple,
                            news_events=news_event_dicts,
                            config=filter_cfg,
                        )
                        if not filters_passed:
                            logger.info(f"*** FILTERED OUT: {signal.direction} {symbol} | {'; '.join(filter_reasons)}")
                            continue
                        elif any("warn" in r for r in filter_reasons):
                            logger.info(f"*** Signal passed with warnings: {'; '.join(filter_reasons)}")
                    except Exception as e:
                        logger.debug(f"Filter application error (non-fatal): {e}")

                    # === HISTORICAL DAY CONFIDENCE FILTER ===
                    # Uses actual win rates from the ledger instead of hardcoded rules.
                    # This is Feedback Loop #3: time_travel/weekly stats → live filter.
                    try:
                        day_conf = parallel_learner.get_day_confidence_map()
                        hdc_result = check_historical_day_confidence(
                            now, day_conf, strict=strategy_mode == "strict"
                        )
                        if not hdc_result.passed:
                            logger.info(f"*** DAY CONFIDENCE REJECT: {signal.direction} {symbol} | {hdc_result.reason}")
                            continue
                        if hdc_result.severity == "warn":
                            logger.info(f"*** Day confidence warning: {hdc_result.reason}")
                    except Exception as e:
                        logger.debug(f"Day confidence filter error (non-fatal): {e}")

                    # === QUALITY GATE: reject low-quality ICT 2022 setups ===
                    min_quality = cfg.get("strategy", {}).get("min_quality_score", 0.65)
                    if hasattr(signal, 'quality_score') and signal.quality_score < min_quality:
                        logger.info(f"*** QUALITY REJECT: {signal.direction} {symbol} | "
                                   f"quality={signal.quality_score:.2f} < {min_quality:.2f}")
                        continue

                    # Determine confluence type for pattern matching
                    obs_confluence = "OTE_only"
                    if "FVG" in signal.reason:
                        obs_confluence = "FVG"
                    elif "Order Block" in signal.reason:
                        obs_confluence = "OB"

                    # === PATTERN-BASED LEARNING REJECTION (Feedback Loop #1) ===
                    # Before committing to a trade, check if the parallel learner
                    # has observed this exact pattern lose money before.
                    try:
                        reject, reason = parallel_learner.should_reject_pattern(
                            symbol, signal.direction, kz_name, obs_confluence
                        )
                        if reject:
                            logger.info(f"*** PATTERN REJECT: {signal.direction} {symbol} @ {signal.entry:.2f} | {reason}")
                            # Still log as an observed setup so the learner knows
                            # it was skipped (builds the 'skipped' dataset too)
                            parallel_learner.observe_setup(SetupObservation(
                                timestamp=now.isoformat(),
                                symbol=symbol,
                                direction=signal.direction,
                                would_entry=signal.entry,
                                would_sl=signal.stop_loss,
                                would_tp=signal.take_profit,
                                would_rr=signal.rr,
                                killzone=kz_name,
                                htf_bias=signal.direction,
                                confluence=obs_confluence,
                                traded=False,
                                skip_reason=f"pattern_reject_{reason[:60]}",
                            ))
                            continue
                        if reason == "boost":
                            # Pattern has strong winning history — boost quality score
                            old_q = signal.quality_score
                            signal.quality_score = min(1.0, signal.quality_score * 1.12)
                            logger.info(f"*** PATTERN BOOST: {signal.direction} {symbol} "
                                       f"| quality {old_q:.2f} -> {signal.quality_score:.2f}")
                    except Exception as e:
                        logger.debug(f"Pattern rejection check error (non-fatal): {e}")

                    # Position sizing — query broker for real symbol info
                    broker_symbol_info = broker.get_symbol_info(symbol) if hasattr(broker, 'get_symbol_info') else None
                    volume = risk.calculate_position_size(
                        entry=signal.entry,
                        stop_loss=signal.stop_loss,
                        symbol_pip_value=sym_cfg.get("pip_value", 1.0),
                        min_volume=sym_cfg.get("min_volume", 0.01),
                        max_volume=sym_cfg.get("max_volume", 5.0),
                        symbol_name=symbol,
                        broker_symbol_info=broker_symbol_info,
                    )
                    if volume <= 0:
                        continue

                    logger.info(f"*** SIGNAL: {signal.direction} {symbol} @ {signal.entry:.2f} | SL={signal.stop_loss:.2f} | TP={signal.take_profit:.2f} | RR={signal.rr:.2f} | vol={volume}")
                    logger.info(f"    Reason: {signal.reason}")

                    # === PARALLEL LEARNER: Observe this setup (will be traded) ===
                    parallel_learner.observe_setup(SetupObservation(
                        timestamp=now.isoformat(),
                        symbol=symbol,
                        direction=signal.direction,
                        would_entry=signal.entry,
                        would_sl=signal.stop_loss,
                        would_tp=signal.take_profit,
                        would_rr=signal.rr,
                        killzone=kz_name,
                        htf_bias=signal.direction,
                        confluence=obs_confluence,
                        traded=True,
                    ))

                    pos = broker.open_position(symbol, signal.direction, volume, signal.stop_loss, signal.take_profit, entry_price=signal.entry)
                    if pos:
                        trade = TradeRecord(
                            symbol=symbol, direction=signal.direction,
                            entry=signal.entry, stop_loss=signal.stop_loss,
                            take_profit=signal.take_profit, volume=volume,
                            open_time=now,
                        )
                        # Store the MT5 ticket for later sync
                        if hasattr(pos, 'ticket') and pos.ticket:
                            trade.ticket = pos.ticket
                            trade.entry = pos.entry  # use actual fill price
                        risk.register_trade(trade, now)
                        # >>> FIX: log the real $ risk (already computed by RiskManager using
                        # the broker's tick_value/tick_size). Previously this used
                        # sym_cfg['pip_value'] which is 1.0 for US100/US500 in config.yaml,
                        # under-reporting the actual dollar risk by ~10x.
                        # Fallback to the old formula only if last_actual_risk wasn't set.
                        actual_risk_log = risk.last_actual_risk or (
                            volume * abs(signal.entry - signal.stop_loss) * sym_cfg.get('pip_value', 1.0)
                        )
                        logger.info(f"Trade registered: ticket={getattr(pos, 'ticket', 'N/A')} | "
                                    f"actual entry={pos.entry:.2f} | risk=${actual_risk_log:.2f}")
                    else:
                        # Order failed — don't retry this symbol for 5 minutes
                        last_failed_signal[symbol] = (now, signal.entry)
                        logger.info(f"Order failed for {symbol} — will retry in 5 min (next M5 bar)")
            else:
                if step % 12 == 0:  # log every ~1 min
                    logger.info(f"Step {step} | KZ={kz_name} | can_trade={can_trade}({reason}) | news={news_ok} | positions={len(broker.get_open_positions())} | risk={learning.current_risk_pct():.1f}%")

                # === PERPETUAL WEEK-BY-WEEK BACKTEST (IDLE MODE) ===
                # Instead of repeating 14-day backtest, analyze history week-by-week
                current_hour = now.hour
                if (not in_kz and
                    current_hour != last_idle_task_hour and
                    not isinstance(broker, SimulatorBroker)):
                    last_idle_task_hour = current_hour

                    # Rotate tasks: 0=week-backtest, 1=evolve, 2=discover
                    task_type = current_hour % 3
                    try:
                        if task_type == 0:
                            # WEEK-BY-WEEK TIME TRAVEL BACKTEST
                            logger.info("[IDLE] Analyzing historical week (perpetual learning)...")
                            monday, friday = time_traveler.get_current_week()
                            logger.info(f"  📅 Week: {monday.date()} to {friday.date()} ({time_traveler.direction})")
                            
                            week_results = week_backtester.analyze_week(monday, friday)
                            
                            # Record results
                            time_traveler.record_week_results(
                                trades=week_results['total_trades'],
                                wins=week_results['wins'],
                                losses=week_results['losses'],
                                patterns=0
                            )
                            
                            # Store in ledger
                            for trade in week_results['trades']:
                                ledger.record_trade({**trade, 'source': 'backtest'})
                            
                            # Advance to next week
                            time_traveler.advance()
                            time_traveler.save_checkpoint()
                            
                            logger.info(f"  ✅ {week_results['total_trades']} trades | "
                                       f"{week_results['wins']}W/{week_results['losses']}L | "
                                       f"WR={week_results['win_rate']:.1f}% | "
                                       f"Total weeks: {time_traveler.weeks_analyzed}")
                        
                        elif task_type == 1:
                            logger.info("[IDLE] Running strategy evolution (task 2/3)...")
                            strategy_lab.run_evolution_cycle(days_back=14, population_size=4)
                        else:  # task_type == 2
                            logger.info("[IDLE] Running pattern discovery (task 3/3)...")
                            strategy_lab.discover_patterns(days_back=14)
                    except Exception as e:
                        logger.warning(f"Idle task error: {e}")
                        import traceback
                        traceback.print_exc()

            # === DAILY PATTERN ANALYSIS: Run once per day ===
            today = now.day
            if today != last_analysis_day and step > 10:
                last_analysis_day = today
                try:
                    learning.analyze_patterns()
                    learning.print_insights()
                except Exception as e:
                    logger.debug(f"Pattern analysis error (non-fatal): {e}")

            # Print stats periodically
            if step % 30 == 0:
                risk.sync_balance(broker.account_balance())
                stats = risk.stats()
                stats["simulator_balance"] = broker.account_balance()
                stats["open_positions"] = len(broker.get_open_positions())
                idle_status = "IDLE(backtest/evolve)" if not in_kz else f"LIVE({kz_name})"
                regime = parallel_learner.market_regime
                logger.info(f"--- STATS step {step} | balance={stats['simulator_balance']:.2f} | trades={stats['total_trades']} | W/L={stats['wins']}/{stats['losses']} | winrate={stats['win_rate']:.1f}% | risk={learning.current_risk_pct():.1f}% | mode={idle_status} | regime={regime['volatility']}/{regime['trend_strength']} ---")

            # Exit if max_steps reached (used for demo / sandbox runs)
            if max_steps and step >= max_steps:
                logger.info(f"Reached max_steps={max_steps}, exiting")
                break

            time.sleep(poll_interval)

    except KeyboardInterrupt:
        logger.info("\n" + "=" * 70)
        logger.info("🛑 Ctrl+C received — generating summary and saving checkpoint...")
        logger.info("=" * 70)
        summary_generator.print_summary()
        summary_generator.save_summary()
        time_traveler.save_checkpoint()
    finally:
        # === COMPREHENSIVE SESSION SUMMARY ===
        risk.sync_balance(broker.account_balance())
        stats = risk.stats()
        stats["open_positions"] = len(broker.get_open_positions())
        ledger_stats = ledger.stats_summary()
        session_end = datetime.now(tz.utc).replace(tzinfo=None)
        session_duration = session_end - session_start
        hours = session_duration.total_seconds() / 3600

        logger.info("")
        logger.info("=" * 70)
        logger.info("SESSION SUMMARY")
        logger.info("=" * 70)
        logger.info(f"Session duration: {hours:.1f} hours ({step} steps)")
        logger.info(f"Current time (UTC): {session_end.strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info("")

        # --- LIVE TRADING ---
        logger.info("-" * 70)
        logger.info("LIVE TRADING")
        logger.info("-" * 70)
        logger.info(f"  Trades this session:    {stats['total_trades']}")
        logger.info(f"  Wins / Losses / BE:     {stats['wins']} / {stats['losses']} / {stats.get('breakeven', 0)}")
        logger.info(f"  Win rate:               {stats['win_rate']:.1f}%")
        logger.info(f"  Avg win:                {stats['avg_win']:+.2f}")
        logger.info(f"  Avg loss:               {stats['avg_loss']:+.2f}")
        logger.info(f"  Current balance:        ${broker.account_balance():.2f}")
        starting_balance = cfg['risk']['account_balance']
        current_balance = broker.account_balance()
        logger.info(f"  Starting balance:       ${starting_balance:.2f}")
        logger.info(f"  Session P&L:            {current_balance - starting_balance:+.2f}")
        logger.info(f"  Open positions:         {stats['open_positions']}")
        logger.info(f"  Trading halted:         {stats['trading_halted']}")

        # --- MEMORY (LEDGER) ---
        logger.info("")
        logger.info("-" * 70)
        logger.info("MEMORY (TRADE LEDGER)")
        logger.info("-" * 70)
        logger.info(f"  Total trades in ledger: {ledger_stats['total_trades']}")
        logger.info(f"  Overall win rate:       {ledger_stats['win_rate']:.1f}%")
        logger.info(f"  Total P&L (all-time):   {ledger_stats['total_pnl']:+.2f}")
        logger.info(f"  Avg RR (all-time):      {ledger_stats['avg_rr']:.2f}")
        if ledger_stats['by_symbol']:
            logger.info("  By symbol:")
            for s in ledger_stats['by_symbol']:
                logger.info(f"    {s['symbol']}: {s['total']} trades, {s['wins']} wins, PnL={s['pnl']:+.2f}")
        if ledger_stats['by_killzone']:
            logger.info("  By killzone:")
            for k in ledger_stats['by_killzone']:
                logger.info(f"    {k['killzone']}: {k['total']} trades, {k['wins']} wins")

        # --- LEARNING ENGINE ---
        logger.info("")
        logger.info("-" * 70)
        logger.info("LEARNING ENGINE")
        logger.info("-" * 70)
        logger.info(f"  Current risk %:         {learning.current_risk_pct():.2f}%")
        logger.info(f"  Adaptive range:         {adaptive_cfg.min_risk_pct}-{adaptive_cfg.max_risk_pct}%")
        lessons = ledger.get_active_lessons()
        learning_lessons = [l for l in lessons if l['category'] != 'strategy_variant' and l['category'] != 'discovered_pattern']
        logger.info(f"  Active lessons:         {len(learning_lessons)}")
        for lesson in learning_lessons[:5]:  # show top 5
            logger.info(f"    [{lesson['category']}] {lesson['rule'][:80]}")
        if len(learning_lessons) > 5:
            logger.info(f"    ... and {len(learning_lessons) - 5} more")

        # --- STRATEGY EVOLUTION ---
        logger.info("")
        logger.info("-" * 70)
        logger.info("STRATEGY EVOLUTION LAB")
        logger.info("-" * 70)
        logger.info(f"  Generation reached:     {strategy_lab.generation}")
        logger.info(f"  Population size:        {len(strategy_lab.population)}")
        if strategy_lab.best_variant:
            best = strategy_lab.best_variant
            logger.info(f"  Best strategy:          {best.id}")
            logger.info(f"    Score:                {best.score:.4f}")
            logger.info(f"    Win rate:             {best.win_rate:.1f}%")
            logger.info(f"    Total trades tested:  {best.total_trades}")
            logger.info(f"    P&L (backtest):       {best.total_pnl:+.2f}")
            logger.info(f"    Avg RR:               {best.avg_rr:.2f}")
            logger.info(f"    Parameters:")
            logger.info(f"      OTE range:          [{best.ote_min:.2f} - {best.ote_max:.2f}]")
            logger.info(f"      Min RR:             {best.min_rr:.1f}")
            logger.info(f"      NY AM KZ:           {best.ny_am_kz_start} - {best.ny_am_kz_end}")
            logger.info(f"      London KZ:          {best.london_kz_start} - {best.london_kz_end}")
            logger.info(f"      FVG lookback:       {best.fvg_lookback}")
            logger.info(f"      Liquidity lookback: {best.liquidity_lookback}")
        else:
            logger.info(f"  Best strategy:          (none yet — needs more idle time)")

        # --- DISCOVERED PATTERNS ---
        logger.info("")
        logger.info("-" * 70)
        logger.info("DISCOVERED PATTERNS")
        logger.info("-" * 70)
        discovered = [l for l in lessons if l['category'] == 'discovered_pattern']
        if discovered:
            for p in discovered:
                logger.info(f"  [+] {p['rule']}")
        else:
            logger.info(f"  (none yet — needs more idle time)")

        # --- PARALLEL LEARNING (NEW) ---
        logger.info("")
        logger.info("-" * 70)
        logger.info("PARALLEL LEARNING (learned during killzones)")
        logger.info("-" * 70)
        pl_status = parallel_learner.get_status()
        logger.info(f"  Market regime:          {pl_status['market_regime']}")
        logger.info(f"  Setups observed:        {pl_status['observations_this_kz']} this killzone")
        logger.info(f"  Micro-evolution:        {'done' if pl_status['micro_evolution_done'] else 'pending'}")
        logger.info(f"  Real-time lessons:      extracted from each closed trade")
        # Show real-time lessons from this session
        realtime_lessons = [l for l in lessons if l['category'] in ('winning_setup', 'losing_setup', 'winning_confluence', 'bad_time')]
        if realtime_lessons:
            logger.info(f"  Real-time lessons learned: {len(realtime_lessons)}")
            for rl in realtime_lessons[:5]:
                logger.info(f"    [{rl['category']}] {rl['rule'][:80]}")
        else:
            logger.info(f"  Real-time lessons:     (none yet — needs closed trades)")

        # --- NEWS TRADER ---
        logger.info("")
        logger.info("-" * 70)
        logger.info("NEWS TRADER (learning to trade news events)")
        logger.info("-" * 70)
        nt_status = news_trader.get_status()
        logger.info(f"  Phase:                  {nt_status['phase']}")
        logger.info(f"  Total observations:     {nt_status['total_observations']}")
        logger.info(f"  Patterns learned:       {nt_status['patterns_learned']}")
        logger.info(f"  Session observations:   {nt_status['session_observations']}")
        nt_patterns = news_trader.get_pattern_stats()
        if nt_patterns:
            logger.info(f"  Learned news patterns:")
            for p in nt_patterns[:5]:
                logger.info(f"    {p['event_type']}: n={p['occurrence_count']} | "
                           f"reversal={p['reversal_rate']:.0f}% | "
                           f"setup_rate={p['setup_success_rate']:.0f}% | "
                           f"avg_spike={p['avg_spike_points']:.0f}pts")
        else:
            logger.info(f"  (no patterns yet — bot is observing news events)")

        # --- BACKTEST RESULTS ---
        logger.info("")
        logger.info("-" * 70)
        logger.info("BACKTEST RESULTS (saved in ledger)")
        logger.info("-" * 70)
        try:
            import sqlite3
            conn = sqlite3.connect(ledger.db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT symbol, total_trades, win_rate, total_pnl, avg_rr, max_drawdown, run_date
                FROM backtest_results ORDER BY run_date DESC LIMIT 10
            """).fetchall()
            conn.close()
            if rows:
                for r in rows:
                    logger.info(f"  {r['symbol']}: {r['total_trades']} trades | win={r['win_rate']:.0f}% | "
                               f"PnL={r['total_pnl']:+.2f} | RR={r['avg_rr']:.2f} | max_dd={r['max_drawdown']:.2f}")
            else:
                logger.info(f"  (no backtests saved yet)")
        except Exception as e:
            logger.info(f"  (could not load backtest results: {e})")

        logger.info("")
        logger.info("=" * 70)
        logger.info("SESSION COMPLETE")
        logger.info("=" * 70)
        logger.info("All data saved to persistent memory (trade_ledger.db).")
        logger.info("Next session will load all memory and continue learning.")
        logger.info("")

        broker.disconnect()


def run_analyze(cfg: dict):
    """Run pattern analysis on historical trades and print insights."""
    ledger = TradeLedger(db_path=os.path.join(os.path.dirname(__file__), "data", "trade_ledger.db"))
    adaptive_cfg = AdaptiveRiskConfig(
        base_risk_pct=cfg["risk"].get("risk_per_trade_pct", 2.0),
    )
    learning = LearningEngine(ledger, adaptive_cfg)

    logger.info("=" * 70)
    logger.info("TRADE LEDGER ANALYSIS")
    logger.info("=" * 70)

    stats = ledger.stats_summary()
    logger.info(f"Total trades: {stats['total_trades']}")
    logger.info(f"Wins / Losses / BE: {stats['wins']} / {stats['losses']} / {stats['breakeven']}")
    logger.info(f"Win rate: {stats['win_rate']:.1f}%")
    logger.info(f"Total P&L: {stats['total_pnl']:+.2f}")
    logger.info(f"Avg RR: {stats['avg_rr']:.2f}")

    logger.info("\nBy Symbol:")
    for s in stats["by_symbol"]:
        logger.info(f"  {s['symbol']}: {s['total']} trades, {s['wins']} wins, PnL={s['pnl']:+.2f}")

    logger.info("\nBy Killzone:")
    for k in stats["by_killzone"]:
        logger.info(f"  {k['killzone']}: {k['total']} trades, {k['wins']} wins")

    learning.analyze_patterns()
    learning.print_insights()

    lessons = ledger.get_active_lessons()
    if lessons:
        logger.info(f"\nActive Lessons ({len(lessons)}):")
        for lesson in lessons:
            logger.info(f"  [{lesson['category']}] {lesson['rule']} (confidence={lesson['confidence']:.2f})")


def run_backtest(cfg: dict, deep: bool = False, days: int = 30):
    """Run a full backtest of all symbols.
    If deep=True, uses MT5 historical data by date range (much more data).
    """
    broker = build_broker(cfg)
    if not broker.connect():
        logger.error("Failed to connect to broker")
        return

    ledger = TradeLedger(db_path=os.path.join(os.path.dirname(__file__), "data", "trade_ledger.db"))
    backtester = IdleBacktester(ledger, broker, cfg)

    if deep:
        logger.info("=" * 70)
        logger.info(f"DEEP BACKTEST — {days} days of real market history")
        logger.info("=" * 70)
        backtester.run_deep_full_backtest(days_back=days)
    else:
        logger.info("=" * 70)
        logger.info("FULL BACKTEST (recent bars)")
        logger.info("=" * 70)
        backtester.run_full_backtest(bars_per_symbol=500)

    broker.disconnect()


def run_evolve(cfg: dict, days: int = 30, generations: int = 3):
    """Run strategy evolution — the bot invents and tests strategy variants.

    This is the bot's 'imagination'. It generates mutated versions of the base
    ICT strategy, tests them on historical data, and keeps the winners.
    Over multiple generations, the strategy evolves beyond the original rules.
    """
    broker = build_broker(cfg)
    if not broker.connect():
        logger.error("Failed to connect to broker")
        return

    ledger = TradeLedger(db_path=os.path.join(os.path.dirname(__file__), "data", "trade_ledger.db"))
    backtester = IdleBacktester(ledger, broker, cfg)
    lab = StrategyLab(ledger, backtester, cfg)

    logger.info("=" * 70)
    logger.info("STRATEGY EVOLUTION LAB")
    logger.info(f"Testing {generations} generations on {days} days of history")
    logger.info("=" * 70)

    # Show starting state
    lab.print_status()

    # Run evolution cycles
    for gen in range(generations):
        logger.info(f"\n--- Generation {gen + 1}/{generations} ---")
        result = lab.run_evolution_cycle(days_back=days, population_size=6)

        logger.info(f"Generation {result['generation']} complete:")
        logger.info(f"  Best score: {result['best_score']:.4f}")
        logger.info(f"  Best variant: {result['best_variant_id']}")
        logger.info(f"  Best win rate: {result['best_win_rate']:.1f}%")
        logger.info(f"  Best P&L: {result['best_pnl']:+.2f}")

    # Also run pattern discovery
    logger.info("\n--- Pattern Discovery ---")
    patterns = lab.discover_patterns(days_back=days)

    # Show final state
    lab.print_status()

    broker.disconnect()


def run_discover(cfg: dict, days: int = 30):
    """Run only pattern discovery (faster than full evolution)."""
    broker = build_broker(cfg)
    if not broker.connect():
        logger.error("Failed to connect to broker")
        return

    ledger = TradeLedger(db_path=os.path.join(os.path.dirname(__file__), "data", "trade_ledger.db"))
    backtester = IdleBacktester(ledger, broker, cfg)
    lab = StrategyLab(ledger, backtester, cfg)

    logger.info("=" * 70)
    logger.info(f"PATTERN DISCOVERY — {days} days")
    logger.info("=" * 70)

    patterns = lab.discover_patterns(days_back=days)

    logger.info("\n" + "=" * 70)
    logger.info("DISCOVERED PATTERNS SUMMARY")
    logger.info("=" * 70)
    for p in patterns:
        logger.info(f"  [STAT] {p['name']}")
        logger.info(f"      {p['description']}")
        logger.info(f"      Win rate: {p['win_rate']:.0f}% | Sample: {p['sample_size']}")
    logger.info("=" * 70)

    broker.disconnect()


def run_news_stats(cfg: dict):
    """Show news trading statistics and learned patterns."""
    ledger = TradeLedger(db_path=os.path.join(os.path.dirname(__file__), "data", "trade_ledger.db"))
    news_trader = NewsTrader(ledger)

    logger.info("=" * 70)
    logger.info("NEWS TRADING STATISTICS")
    logger.info("=" * 70)

    news_trader.print_stats()

    # Show recent observations
    try:
        import sqlite3
        conn = sqlite3.connect(ledger.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT event_type, event_time, spike_direction, spike_magnitude_points,
                   post_behavior, setup_appeared_15_60min, setup_type
            FROM news_observations
            ORDER BY event_time DESC LIMIT 10
        """).fetchall()
        conn.close()

        if rows:
            logger.info("\nRecent news observations:")
            for r in rows:
                logger.info(f"  {r['event_type']} @ {r['event_time'][:16]} | "
                           f"spike={r['spike_direction']} ({r['spike_magnitude_points']:.0f}pts) | "
                           f"post={r['post_behavior']} | "
                           f"setup={'Y' if r['setup_appeared_15_60min'] else 'N'}")
        else:
            logger.info("\nNo news observations yet — bot is watching for events")
    except Exception as e:
        logger.info(f"  (could not load observations: {e})")


def run_diagnose(cfg: dict, days: int = 1):
    """Run signal diagnosis — find which gate is blocking setups on real data."""
    from core.signal_diagnosis import SignalDiagnoser

    broker = build_broker(cfg)
    if not broker.connect():
        logger.error("Failed to connect to broker")
        return

    diagnoser = SignalDiagnoser(cfg)
    funnels = []

    for sym_cfg in cfg["symbols"]:
        symbol = sym_cfg["name"]
        logger.info(f"\n[DIAGNOSE] Scanning {symbol}...")

        # Fetch data
        if hasattr(broker, 'get_history_by_date'):
            from datetime import timedelta, timezone
            date_to = datetime.now(timezone.utc).replace(tzinfo=None)
            date_from = date_to - timedelta(days=days)
            bias_df = broker.get_history_by_date(symbol, cfg["timeframes"]["bias_tf"], date_from, date_to)
            struct_df = broker.get_history_by_date(symbol, cfg["timeframes"]["structure_tf"], date_from, date_to)
            entry_df = broker.get_history_by_date(symbol, cfg["timeframes"]["entry_tf"], date_from, date_to)
        else:
            bias_df = broker.get_history(symbol, cfg["timeframes"]["bias_tf"], count=500)
            struct_df = broker.get_history(symbol, cfg["timeframes"]["structure_tf"], count=500)
            entry_df = broker.get_history(symbol, cfg["timeframes"]["entry_tf"], count=2000)

        if entry_df.empty or len(entry_df) < 50:
            logger.info(f"  Not enough data for {symbol} (got {len(entry_df)} bars)")
            continue

        funnel = diagnoser.diagnose_symbol(symbol, bias_df, struct_df, entry_df)
        funnels.append(funnel)

    diagnoser.print_report(funnels)
    broker.disconnect()


def run_detailed_backtest(cfg: dict, days: int = 30):
    """Run detailed backtest with CSV export and advanced metrics."""
    broker = build_broker(cfg)
    if not broker.connect():
        logger.error("Failed to connect to broker")
        return

    ledger = TradeLedger(db_path=os.path.join(os.path.dirname(__file__), "data", "trade_ledger.db"))
    backtester = IdleBacktester(ledger, broker, cfg)

    backtester.run_detailed_backtest(days_back=days, export_csv=True)
    broker.disconnect()


def main():
    parser = argparse.ArgumentParser(description="ICT Trading Bot with Memory, Learning & Strategy Evolution")
    parser.add_argument("--config", default=os.path.join(os.path.dirname(__file__), "config.yaml"))
    parser.add_argument("--steps", type=int, default=None, help="Run N steps then exit (for demo)")
    parser.add_argument("--fast", action="store_true", help="No sleep between steps (simulator only)")
    parser.add_argument("--analyze", action="store_true", help="Run pattern analysis on historical trades")
    parser.add_argument("--backtest", action="store_true", help="Run a full backtest of all symbols")
    parser.add_argument("--deep-backtest", action="store_true", help="Run deep backtest using 30 days of MT5 history")
    parser.add_argument("--evolve", action="store_true", help="Run strategy evolution lab (invents new strategy variants)")
    parser.add_argument("--discover", action="store_true", help="Run pattern discovery (finds new statistical edges)")
    parser.add_argument("--news-stats", action="store_true", help="Show news trading statistics and learned patterns")
    parser.add_argument("--diagnose", action="store_true", help="Diagnose which gate blocks setups (run --diagnose --days 7)")
    parser.add_argument("--detailed-backtest", action="store_true", help="Detailed backtest with CSV export and advanced metrics")
    parser.add_argument("--days", type=int, default=30, help="Number of days for backtest/evolution/diagnosis (default 30)")
    parser.add_argument("--generations", type=int, default=3, help="Number of evolution generations (default 3)")
    parser.add_argument("--log-level", default=None)
    args = parser.parse_args()

    cfg = load_config(args.config)

    log_level = args.log_level or cfg.get("notifications", {}).get("log_level", "INFO")
    log_file = os.path.join(os.path.dirname(__file__), "logs", "bot.log") if cfg.get("notifications", {}).get("log_to_file", True) else None
    setup_logging(level=log_level, log_file=log_file)

    if args.analyze:
        run_analyze(cfg)
    elif args.backtest:
        run_backtest(cfg, deep=False)
    elif args.deep_backtest:
        run_backtest(cfg, deep=True, days=args.days)
    elif args.detailed_backtest:
        run_detailed_backtest(cfg, days=args.days)
    elif args.evolve:
        run_evolve(cfg, days=args.days, generations=args.generations)
    elif args.discover:
        run_discover(cfg, days=args.days)
    elif args.news_stats:
        run_news_stats(cfg)
    elif args.diagnose:
        run_diagnose(cfg, days=args.days)
    else:
        run_bot(cfg, max_steps=args.steps, fast=args.fast)


if __name__ == "__main__":
    main()
