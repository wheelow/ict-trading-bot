"""
Idle Backtester — Runs When No Killzone Active

When the bot is outside a killzone (idle), it backtests the strategy on
historical data to:
1. Validate the strategy is working
2. Find optimal parameters
3. Feed results into the learning engine
4. Test variations (grid search)

The backtester uses the broker's get_history() to fetch historical bars,
then replays them through the strategy as if they were live.
"""
from dataclasses import dataclass
from typing import List, Dict, Optional
from datetime import datetime, timedelta, timezone
import logging
import time

from .strategy import generate_signal
from .killzones import parse_kz, in_killzone
from .memory import TradeLedger

logger = logging.getLogger("ict.backtester")


@dataclass
class BacktestResult:
    symbol: str
    period_start: str
    period_end: str
    total_trades: int
    wins: int
    losses: int
    win_rate: float
    total_pnl: float
    avg_rr: float
    max_drawdown: float
    parameters: Dict
    profit_factor: float = 0.0
    notes: str = ""


class IdleBacktester:
    """Backtests the strategy on historical data when the bot is idle."""

    def __init__(self, ledger: TradeLedger, broker, cfg: Dict):
        self.ledger = ledger
        self.broker = broker
        self.cfg = cfg
        self.kzs = parse_kz(cfg["strategy"])
        self.tf_bias = cfg["timeframes"]["bias_tf"]
        self.tf_struct = cfg["timeframes"]["structure_tf"]
        self.tf_entry = cfg["timeframes"]["entry_tf"]
        self.min_rr = cfg["risk"]["reward_to_risk"]
        self.symbols = [s["name"] for s in cfg["symbols"]]
        # Trade management params (Fix 2 & Fix 3, 2026-07): read from risk config
        # so backtest matches live behavior. Defaults keep old behavior if missing.
        self.be_at_rr = cfg["risk"].get("breakeven_at_rr", 1.5)
        self.partial_at_rr = cfg["risk"].get("partial_close_at_rr", 2.0)
        self.partial_pct = 0.5  # close 50% at partial
        self.trailing_pips = cfg["risk"].get("trailing_stop_pips", 10.0)
        # News gate (archive-as-you-go). When backtest_news is enabled, the
        # backtester blocks trades within the news window of any archived
        # high-impact event for the bar's date — matching live behavior. Days
        # with no archived coverage are SKIPPED GRACEFULLY (logged once per day)
        # rather than treated as "no news", since absence of data != absence of
        # events. Coverage grows automatically as the live bot archives weeks.
        news_cfg = cfg.get("news", {}) if isinstance(cfg.get("news"), dict) else {}
        self.backtest_news = news_cfg.get("backtest_news", True)
        self.news_block_before = news_cfg.get("block_minutes_before", 15)
        self.news_block_after = news_cfg.get("block_minutes_after", 15)
        self._news_skipped_days: set = set()  # track days we already warned about
        self._last_run = None
        self._running = False
        logger.info("Idle backtester initialized")

    def run_deep_backtest(self, symbol: str, days_back: int = 30) -> Optional[BacktestResult]:
        """Deep backtest using actual historical data from MT5 (by date range).
        This is superior to FX Replay because it uses your broker's REAL data.

        Args:
            symbol: which symbol to backtest
            days_back: how many days of history to test (default 30)

        Returns:
            BacktestResult or None if failed
        """
        if self._running:
            return None
        self._running = True

        try:
            from datetime import timedelta
            date_to = datetime.now(timezone.utc).replace(tzinfo=None)
            date_from = date_to - timedelta(days=days_back)

            logger.info(f"[DEEP BACKTEST] {symbol} | {days_back} days | {date_from.date()} -> {date_to.date()}")

            # Fetch deep history using date range (much more data than copy_rates_from_pos)
            if hasattr(self.broker, 'get_history_by_date'):
                # MT5 mode — use copy_rates_range for deep history
                entry_df = self.broker.get_history_by_date(symbol, self.tf_entry, date_from, date_to)
                struct_df = self.broker.get_history_by_date(symbol, self.tf_struct, date_from, date_to)
                bias_df = self.broker.get_history_by_date(symbol, self.tf_bias, date_from, date_to)
                daily_df = self.broker.get_history_by_date(symbol, "D1", date_from, date_to)
            else:
                # Fallback — use regular get_history
                entry_df = self.broker.get_history(symbol, self.tf_entry, count=min(days_back * 288, 65000))
                struct_df = self.broker.get_history(symbol, self.tf_struct, count=min(days_back * 96, 65000))
                bias_df = self.broker.get_history(symbol, self.tf_bias, count=min(days_back * 24, 65000))
                daily_df = self.broker.get_history(symbol, "D1", count=min(days_back, 1000))

            if entry_df.empty or len(entry_df) < 100:
                logger.warning(f"[DEEP BACKTEST] Not enough data for {symbol} (got {len(entry_df)} bars)")
                return None

            logger.info(f"[DEEP BACKTEST] {symbol}: {len(entry_df)} M5 bars loaded")

            # Run the same backtest logic as run_backtest
            return self._execute_backtest(symbol, entry_df, struct_df, bias_df, f"{days_back} days deep backtest", daily_df=daily_df)

        except Exception as e:
            logger.error(f"[DEEP BACKTEST] Error: {e}")
            return None
        finally:
            self._running = False

    def run_backtest(self, symbol: str, bars_to_test: int = 500) -> Optional[BacktestResult]:
        """Backtest the strategy on historical data for a symbol.

        Args:
            symbol: which symbol to backtest
            bars_to_test: how many M5 bars to replay

        Returns:
            BacktestResult or None if failed
        """
        if self._running:
            return None
        self._running = True

        try:
            logger.info(f"[BACKTEST] Starting backtest for {symbol} ({bars_to_test} bars)...")

            # Fetch historical data
            bias_df = self.broker.get_history(symbol, self.tf_bias, count=300)
            struct_df = self.broker.get_history(symbol, self.tf_struct, count=300)
            entry_df = self.broker.get_history(symbol, self.tf_entry, count=bars_to_test)
            # Daily bars enable the ICT 2022 Model (PDH/PDL, dealing range, liquidity pools for TP)
            daily_df = self.broker.get_history(symbol, "D1", count=100)

            if entry_df.empty or len(entry_df) < 50:
                logger.warning(f"[BACKTEST] Not enough data for {symbol} (got {len(entry_df)} bars)")
                return None

            return self._execute_backtest(symbol, entry_df, struct_df, bias_df, f"{bars_to_test} bars", daily_df=daily_df)

        except Exception as e:
            logger.error(f"[BACKTEST] Error: {e}")
            return None
        finally:
            self._running = False

    def _execute_backtest(self, symbol: str, entry_df, struct_df, bias_df, notes: str = "", daily_df=None) -> Optional[BacktestResult]:
        """Execute backtest on provided dataframes — shared by run_backtest and run_deep_backtest."""
        # Simulate trades by walking through historical bars
        trades = []
        open_trade = None
        entry_closes = entry_df["close"].values
        entry_highs = entry_df["high"].values
        entry_lows = entry_df["low"].values

        # Start testing from bar 100 (need history for indicators)
        start_bar = 100
        for i in range(start_bar, len(entry_df) - 1):
            current_entry_df = entry_df.iloc[:i+1]
            current_struct_df = struct_df.iloc[:max(1, i//3 + 1)] if not struct_df.empty else struct_df
            current_bias_df = bias_df.iloc[:max(1, i//12 + 1)] if not bias_df.empty else bias_df

            # Check if we have an open trade — manage it
            if open_trade:
                bar_high = entry_highs[i]
                bar_low = entry_lows[i]
                direction = open_trade["direction"]
                entry = open_trade["entry"]
                orig_risk = open_trade["risk_distance"]  # |entry - initial SL|, fixed at open
                sl = open_trade["sl"]      # SL coming INTO this bar (may already be trailed)
                tp = open_trade["tp"]
                vol_remaining = open_trade["vol_remaining"]
                partial_pnl = open_trade["partial_pnl"]

                # --- (a) BREAKEVEN: move SL to entry once price reaches be_at_rr in favor ---
                # Config-driven (default 1.5R, was hardcoded 1R). The old 1R trigger yanked
                # trades to BE the instant they touched +1R, then the same M5 bar would
                # retrace to entry and stop them out — that was the source of most "[BE hit]"
                # non-winners in the 2026-07 diagnosis.
                if not open_trade.get("be_applied"):
                    if direction == "BUY" and bar_high >= entry + self.be_at_rr * orig_risk:
                        sl = entry
                        open_trade["sl"] = sl
                        open_trade["be_applied"] = True
                    elif direction == "SELL" and bar_low <= entry - self.be_at_rr * orig_risk:
                        sl = entry
                        open_trade["sl"] = sl
                        open_trade["be_applied"] = True

                # --- (b) PARTIAL CLOSE: take 50% off at partial_at_rr in favor ---
                # Books the profit on half the position; the other half runs to TP/SL/BE.
                # Realizes gains instead of giving them all back on a reversal.
                if not open_trade.get("partial_done"):
                    partial_price = None
                    if direction == "BUY" and bar_high >= entry + self.partial_at_rr * orig_risk:
                        partial_price = entry + self.partial_at_rr * orig_risk
                    elif direction == "SELL" and bar_low <= entry - self.partial_at_rr * orig_risk:
                        partial_price = entry - self.partial_at_rr * orig_risk
                    if partial_price is not None:
                        partial_vol = open_trade["vol"] * self.partial_pct
                        open_trade["partial_pnl"] += abs(partial_price - entry) * partial_vol
                        open_trade["vol_remaining"] -= partial_vol
                        open_trade["partial_done"] = True
                        partial_pnl = open_trade["partial_pnl"]
                        vol_remaining = open_trade["vol_remaining"]

                # --- (c) Evaluate SL / TP hit on this bar ---
                # Conservative: if both SL and TP fall inside the bar, assume SL hit first.
                #
                # PnL formula note: once BE fires and the trailing stop activates, SL can
                # be PAST entry (in profit). The remaining-position PnL must therefore use
                # the signed directional difference (sl - entry for BUY, entry - sl for SELL),
                # NOT -|sl - entry|. The old formula silently zeroed out trailed profits.
                #
                # Classification: status is assigned from the sign of final_pnl, not from
                # which trigger fired — so a trailed stop that closes in profit counts as
                # a WIN, and a partial+BE that nets positive also counts as a WIN.
                def _classify(pnl: float) -> str:
                    if pnl > 1e-9:
                        return "won"
                    if pnl < -1e-9:
                        return "lost"
                    return "be"

                if direction == "BUY":
                    if bar_low <= sl:
                        remaining_pnl = (sl - entry) * vol_remaining  # signed: + if trailed past entry
                        final_pnl = partial_pnl + remaining_pnl
                        status = _classify(final_pnl)
                        trigger = "trail" if (open_trade.get("be_applied") and sl > entry) else \
                                  ("BE" if open_trade.get("be_applied") else "SL")
                        logger.info(f"[TRADE CLOSED] BUY {trigger} | Entry={entry:.2f} SL={sl:.2f} TP={tp:.2f} | "
                                    f"Bar Low={bar_low:.2f} | PnL={final_pnl:+.2f} (partial={partial_pnl:+.2f})")
                        realized_rr = final_pnl / (orig_risk * open_trade["vol"]) if orig_risk else 0.0
                        trades.append({**open_trade, "status": status, "pnl": final_pnl, "rr": realized_rr})
                        open_trade = None
                    elif bar_high >= tp:
                        remaining_pnl = (tp - entry) * vol_remaining
                        final_pnl = partial_pnl + remaining_pnl
                        logger.info(f"[TRADE CLOSED] BUY TP | Entry={entry:.2f} SL={sl:.2f} TP={tp:.2f} | "
                                    f"Bar High={bar_high:.2f} | PnL={final_pnl:+.2f} (partial={partial_pnl:+.2f})")
                        realized_rr = final_pnl / (orig_risk * open_trade["vol"]) if orig_risk else 0.0
                        trades.append({**open_trade, "status": "won", "pnl": final_pnl, "rr": realized_rr})
                        open_trade = None
                else:  # SELL
                    if bar_high >= sl:
                        remaining_pnl = (entry - sl) * vol_remaining  # signed: + if trailed past entry
                        final_pnl = partial_pnl + remaining_pnl
                        status = _classify(final_pnl)
                        trigger = "trail" if (open_trade.get("be_applied") and sl < entry) else \
                                  ("BE" if open_trade.get("be_applied") else "SL")
                        logger.info(f"[TRADE CLOSED] SELL {trigger} | Entry={entry:.2f} SL={sl:.2f} TP={tp:.2f} | "
                                    f"Bar High={bar_high:.2f} | PnL={final_pnl:+.2f} (partial={partial_pnl:+.2f})")
                        realized_rr = final_pnl / (orig_risk * open_trade["vol"]) if orig_risk else 0.0
                        trades.append({**open_trade, "status": status, "pnl": final_pnl, "rr": realized_rr})
                        open_trade = None
                    elif bar_low <= tp:
                        remaining_pnl = (entry - tp) * vol_remaining
                        final_pnl = partial_pnl + remaining_pnl
                        logger.info(f"[TRADE CLOSED] SELL TP | Entry={entry:.2f} SL={sl:.2f} TP={tp:.2f} | "
                                    f"Bar Low={bar_low:.2f} | PnL={final_pnl:+.2f} (partial={partial_pnl:+.2f})")
                        realized_rr = final_pnl / (orig_risk * open_trade["vol"]) if orig_risk else 0.0
                        trades.append({**open_trade, "status": "won", "pnl": final_pnl, "rr": realized_rr})
                        open_trade = None

                # --- (d) TRAILING STOP: only if trade survived this bar AND BE is on ---
                # Trails SL by `trailing_pips` behind the favorable extreme of THIS bar,
                # taking effect on the NEXT bar. Never loosens — only tightens.
                if open_trade is not None and open_trade.get("be_applied") and self.trailing_pips > 0:
                    if direction == "BUY":
                        new_sl = bar_high - self.trailing_pips
                        if new_sl > open_trade["sl"]:
                            open_trade["sl"] = new_sl
                    else:  # SELL
                        new_sl = bar_low + self.trailing_pips
                        if new_sl < open_trade["sl"]:
                            open_trade["sl"] = new_sl
                continue

            # Check if we're in a killzone (use bar timestamp)
            bar_time = entry_df.index[i]
            if hasattr(bar_time, 'tzinfo') and bar_time.tzinfo is not None:
                bar_time_naive = bar_time.replace(tzinfo=None)
            else:
                bar_time_naive = bar_time

            if not in_killzone(bar_time_naive, self.kzs):
                continue

            # News gate: block trades inside the news window of any archived
            # high-impact event for this bar's date. Mirrors the live path's
            # news.can_trade() check. See __init__ for the skip-gracefully policy.
            if self.backtest_news:
                try:
                    from news.calendar import is_within_news_window, filter_high_impact
                    from news import archive
                    bar_date = bar_time_naive.date()
                    if archive.has_coverage(bar_date):
                        day_events = archive.get_events_for_day(bar_date)
                        hi_events = filter_high_impact(day_events, self.cfg["news"]["high_impact_keywords"])
                        blocked, ev = is_within_news_window(
                            bar_time_naive, hi_events,
                            self.news_block_before, self.news_block_after,
                        )
                        if blocked:
                            continue  # would have been blocked live
                    elif bar_date not in self._news_skipped_days:
                        self._news_skipped_days.add(bar_date)
                        logger.info(
                            f"Backtest {bar_date}: no news coverage in archive "
                            f"— news gate skipped (run the live bot to archive weeks)"
                        )
                except Exception as ne:
                    # News gate must NEVER break a backtest — degrade to off.
                    if bar_date not in self._news_skipped_days:
                        self._news_skipped_days.add(bar_date)
                        logger.debug(f"News gate unavailable for {bar_date}: {ne}")

            # Try to generate a signal
            try:
                # Slice daily_df to bars before current time (no look-ahead bias).
                # daily_df must only contain data that was KNOWN at bar_time.
                current_daily_df = None
                if daily_df is not None and not daily_df.empty:
                    try:
                        # daily_df.index must be tz-naive for comparison with bar_time_naive
                        daily_idx = daily_df.index
                        if hasattr(daily_idx, 'tz') and daily_idx.tz is not None:
                            daily_idx = daily_idx.tz_localize(None)
                            daily_df_naive = daily_df.copy()
                            daily_df_naive.index = daily_idx
                        else:
                            daily_df_naive = daily_df
                        current_daily_df = daily_df_naive[daily_df_naive.index < bar_time_naive]
                    except Exception:
                        current_daily_df = daily_df

                signal = generate_signal(
                    symbol, current_bias_df, current_struct_df,
                    current_entry_df, self.cfg["strategy"], min_rr=self.min_rr,
                    daily_df=current_daily_df
                )
            except Exception:
                continue

            if signal is None:
                continue

            # Normalize position size so every trade risks the SAME dollar amount.
            # vol = $100_risk / SL_distance_in_points
            # This makes PnL directly comparable across trades with different SL widths.
            # Without this, wide-SL trades cost more dollars than tight-SL trades,
            # creating a false negative PnL even when R-multiple is positive.
            risk_distance = abs(signal.entry - signal.stop_loss)
            if risk_distance <= 0:
                continue
            normalized_vol = 100.0 / risk_distance  # $100 risk per trade

            # Open a simulated trade
            open_trade = {
                "symbol": symbol,
                "direction": signal.direction,
                "entry": signal.entry,
                "sl": signal.stop_loss,
                "tp": signal.take_profit,
                "vol": normalized_vol,  # normalized so $100 risk per trade
                "vol_remaining": normalized_vol,  # decremented when partial close fires
                "partial_pnl": 0.0,              # realized PnL from partial closes
                "partial_done": False,           # has the 2R partial fired?
                "rr": signal.rr,
                "risk_distance": risk_distance,
                "open_time": str(bar_time),
            }
            logger.info(f"[TRADE OPENED] {signal.direction} | Entry={signal.entry:.2f} SL={signal.stop_loss:.2f} TP={signal.take_profit:.2f} | RR={signal.rr:.2f} | Risk=${risk_distance:.2f}")

        # Close any remaining open trade at the last close price
        # NOTE: This represents trades that didn't hit TP or SL during the backtest period
        # We DON'T count these in results (they're incomplete data)
        if open_trade:
            logger.debug(f"[TRADE INCOMPLETE] Trade opened at {open_trade['open_time']} did not hit TP/SL by end of backtest - excluding from results")
            # DO NOT append to trades list - this would corrupt results

        # Compute results
        if not trades:
            logger.info(f"[BACKTEST] {symbol}: 0 trades generated in {len(entry_df)} bars")
            result = BacktestResult(
                symbol=symbol,
                period_start=str(entry_df.index[0]),
                period_end=str(entry_df.index[-1]),
                total_trades=0, wins=0, losses=0, win_rate=0,
                total_pnl=0, avg_rr=0, max_drawdown=0, profit_factor=0.0,
                parameters={"bars": len(entry_df), "min_rr": self.min_rr},
                notes="No trades generated"
            )
            self.ledger.save_backtest_result(
                symbol=result.symbol, period_start=result.period_start, period_end=result.period_end,
                total_trades=0, wins=0, losses=0, win_rate=0, total_pnl=0, avg_rr=0,
                max_drawdown=0, parameters=result.parameters, notes=result.notes
            )
            return result

        wins = sum(1 for t in trades if t["status"] == "won")
        losses = sum(1 for t in trades if t["status"] == "lost")
        total_pnl = sum(t["pnl"] for t in trades)
        avg_rr = sum(t["rr"] for t in trades) / len(trades)

        # Max drawdown
        balance = 0
        peak = 0
        max_dd = 0
        for t in trades:
            balance += t["pnl"]
            peak = max(peak, balance)
            dd = peak - balance
            max_dd = max(max_dd, dd)

        # Profit factor
        gross_profit = sum(t["pnl"] for t in trades if t["pnl"] > 0)
        gross_loss = abs(sum(t["pnl"] for t in trades if t["pnl"] < 0))
        pf = gross_profit / gross_loss if gross_loss > 0 else (2.0 if gross_profit > 0 else 0.0)

        result = BacktestResult(
            symbol=symbol,
            period_start=str(entry_df.index[0]),
            period_end=str(entry_df.index[-1]),
            total_trades=len(trades),
            wins=wins,
            losses=losses,
            win_rate=wins / len(trades) * 100 if trades else 0,
            total_pnl=total_pnl,
            avg_rr=avg_rr,
            max_drawdown=max_dd,
            profit_factor=pf,
            parameters={"bars": len(entry_df), "min_rr": self.min_rr, "notes": notes},
        )

        # Save to ledger
        self.ledger.save_backtest_result(
            symbol=result.symbol,
            period_start=result.period_start,
            period_end=result.period_end,
            total_trades=result.total_trades,
            wins=result.wins,
            losses=result.losses,
            win_rate=result.win_rate,
            total_pnl=result.total_pnl,
            avg_rr=result.avg_rr,
            max_drawdown=result.max_drawdown,
            parameters=result.parameters,
            notes=result.notes
        )

        logger.info(f"[BACKTEST] {symbol}: {result.total_trades} trades | "
                   f"W/L={result.wins}/{result.losses} | "
                   f"win_rate={result.win_rate:.1f}% | "
                   f"PnL={result.total_pnl:+.2f} | "
                   f"avg_RR={result.avg_rr:.2f} | "
                   f"max_dd={result.max_drawdown:.2f}")

        self._last_run = result
        return result

    def run_full_backtest(self, bars_per_symbol: int = 500):
        """Backtest all symbols. Called when the bot is idle."""
        logger.info("=" * 70)
        logger.info("[BACKTEST] Starting full backtest of all symbols...")
        logger.info("=" * 70)

        results = []
        for symbol in self.symbols:
            result = self.run_backtest(symbol, bars_to_test=bars_per_symbol)
            if result:
                results.append(result)
            time.sleep(1)  # brief pause between symbols

        # Summary
        if results:
            logger.info("=" * 70)
            logger.info("[BACKTEST] FULL BACKTEST SUMMARY")
            logger.info("=" * 70)
            for r in results:
                logger.info(f"  {r.symbol}: {r.total_trades} trades | "
                           f"win={r.win_rate:.0f}% | "
                           f"PnL={r.total_pnl:+.2f} | "
                           f"avg_RR={r.avg_rr:.2f}")
            total_pnl = sum(r.total_pnl for r in results)
            total_trades = sum(r.total_trades for r in results)
            logger.info(f"  TOTAL: {total_trades} trades | PnL={total_pnl:+.2f}")
            logger.info("=" * 70)

        return results

    def run_detailed_backtest(self, days_back: int = 30, export_csv: bool = True) -> Dict:
        """Run a detailed backtest with per-trade export.

        This is the 'strengthened' backtester:
        - Tests all symbols
        - Records every trade with full details
        - Exports to CSV for analysis
        - Computes advanced metrics (profit factor, expectancy, streak stats)
        - Returns a comprehensive report
        """
        import csv
        import os

        logger.info("=" * 70)
        logger.info(f"[DETAILED BACKTEST] {days_back} days, all symbols, per-trade export")
        logger.info("=" * 70)

        all_trades = []
        summary = {"symbols": [], "total_trades": 0, "total_wins": 0, "total_losses": 0,
                   "total_pnl": 0, "total_profit": 0, "total_loss": 0}

        for symbol in self.symbols:
            result = self.run_deep_backtest(symbol, days_back=days_back)
            if result:
                summary["symbols"].append({
                    "symbol": symbol,
                    "trades": result.total_trades,
                    "wins": result.wins,
                    "losses": result.losses,
                    "win_rate": result.win_rate,
                    "pnl": result.total_pnl,
                    "avg_rr": result.avg_rr,
                    "max_dd": result.max_drawdown,
                })
                summary["total_trades"] += result.total_trades
                summary["total_wins"] += result.wins
                summary["total_losses"] += result.losses
                summary["total_pnl"] += result.total_pnl

            time.sleep(1)

        # Compute advanced metrics
        if summary["total_trades"] > 0:
            summary["overall_win_rate"] = summary["total_wins"] / summary["total_trades"] * 100
            # Estimate profit factor from per-symbol data
            gross_profit = sum(s["pnl"] for s in summary["symbols"] if s["pnl"] > 0)
            gross_loss = abs(sum(s["pnl"] for s in summary["symbols"] if s["pnl"] < 0))
            summary["profit_factor"] = gross_profit / gross_loss if gross_loss > 0 else (2.0 if gross_profit > 0 else 0)
            summary["expectancy"] = summary["total_pnl"] / summary["total_trades"]
        else:
            summary["overall_win_rate"] = 0
            summary["profit_factor"] = 0
            summary["expectancy"] = 0

        # Export CSV report
        if export_csv and summary["symbols"]:
            csv_path = os.path.join(os.path.dirname(self.ledger.db_path), "backtest_report.csv")
            try:
                with open(csv_path, "w", newline="") as f:
                    writer = csv.writer(f)
                    writer.writerow(["Symbol", "Trades", "Wins", "Losses", "Win Rate %",
                                    "Total PnL", "Avg RR", "Max Drawdown"])
                    for s in summary["symbols"]:
                        writer.writerow([s["symbol"], s["trades"], s["wins"], s["losses"],
                                        f"{s['win_rate']:.1f}", f"{s['pnl']:.2f}",
                                        f"{s['avg_rr']:.2f}", f"{s['max_dd']:.2f}"])
                    writer.writerow([])
                    writer.writerow(["TOTAL", summary["total_trades"], summary["total_wins"],
                                    summary["total_losses"], f"{summary['overall_win_rate']:.1f}",
                                    f"{summary['total_pnl']:.2f}", "", ""])
                    writer.writerow(["Profit Factor", f"{summary['profit_factor']:.2f}"])
                    writer.writerow(["Expectancy/trade", f"{summary['expectancy']:.2f}"])
                logger.info(f"[DETAILED BACKTEST] CSV report saved: {csv_path}")
            except Exception as e:
                logger.warning(f"Could not save CSV: {e}")

        # Print summary
        logger.info("=" * 70)
        logger.info("[DETAILED BACKTEST] SUMMARY")
        logger.info("=" * 70)
        for s in summary["symbols"]:
            logger.info(f"  {s['symbol']}: {s['trades']} trades | win={s['win_rate']:.0f}% | "
                       f"PnL={s['pnl']:+.2f} | RR={s['avg_rr']:.2f} | max_dd={s['max_dd']:.2f}")
        logger.info(f"  -")
        logger.info(f"  TOTAL: {summary['total_trades']} trades | W/L={summary['total_wins']}/{summary['total_losses']}")
        logger.info(f"  Win rate: {summary['overall_win_rate']:.1f}%")
        logger.info(f"  Total PnL: {summary['total_pnl']:+.2f}")
        logger.info(f"  Profit factor: {summary['profit_factor']:.2f} (>1.0 = profitable)")
        logger.info(f"  Expectancy/trade: {summary['expectancy']:+.2f}")
        logger.info("=" * 70)

        if summary["profit_factor"] < 1.0 and summary["total_trades"] > 0:
            logger.warning("  [WARNING] Strategy is NET LOSING on this data.")
            logger.warning("  Run --diagnose to find which gate is blocking profitable setups.")
        elif summary["profit_factor"] >= 1.5:
            logger.info("  [GOOD] Strategy is profitable with healthy profit factor.")

        return summary

    def run_deep_full_backtest(self, days_back: int = 30):
        """Deep backtest all symbols using actual MT5 historical data (by date range).
        This fetches MUCH more data than the regular backtest — up to 65,000 bars per symbol.
        Equivalent to running FX Replay, but using your broker's REAL data.
        """
        logger.info("=" * 70)
        logger.info(f"[DEEP BACKTEST] Starting {days_back}-day deep backtest of all symbols...")
        logger.info("=" * 70)

        results = []
        for symbol in self.symbols:
            result = self.run_deep_backtest(symbol, days_back=days_back)
            if result:
                results.append(result)
            time.sleep(2)  # longer pause for deep backtest

        # Summary
        if results:
            logger.info("=" * 70)
            logger.info(f"[DEEP BACKTEST] {days_back}-DAY SUMMARY")
            logger.info("=" * 70)
            for r in results:
                logger.info(f"  {r.symbol}: {r.total_trades} trades | "
                           f"win={r.win_rate:.0f}% | "
                           f"PnL={r.total_pnl:+.2f} | "
                           f"avg_RR={r.avg_rr:.2f} | "
                           f"max_dd={r.max_drawdown:.2f}")
            total_pnl = sum(r.total_pnl for r in results)
            total_trades = sum(r.total_trades for r in results)
            total_wins = sum(r.wins for r in results)
            overall_win_rate = (total_wins / total_trades * 100) if total_trades else 0
            logger.info(f"  TOTAL: {total_trades} trades | W/L={total_wins}/{total_trades-total_wins} | "
                       f"win_rate={overall_win_rate:.1f}% | PnL={total_pnl:+.2f}")
            logger.info("=" * 70)

        return results
