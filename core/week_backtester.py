"""
Week-by-Week Backtester

Analyzes complete trading weeks (Monday-Friday) systematically.
Works with TimeTraveler to progressively learn from all history.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import pandas as pd

logger = logging.getLogger("ict.week_backtester")

class WeekBacktester:
    """
    Backtests a complete trading week (Mon-Fri).
    
    Finds all ICT 2022 setups during killzones that week,
    simulates outcomes, and returns results.
    """
    
    def __init__(self, broker, config: Dict, strategy):
        """
        Initialize week backtester.
        
        Args:
            broker: MT5Broker instance (for historical data)
            config: Bot configuration
            strategy: ICTStrategy instance (with ICT 2022 model)
        """
        self.broker = broker
        self.config = config
        self.strategy = strategy
        self.symbols = [s['name'] for s in config['symbols']]
    
    def analyze_week(self, monday: datetime, friday: datetime) -> Dict:
        """
        Analyze one complete trading week.
        
        Args:
            monday: Start of week (Monday 00:00)
            friday: End of week (Friday 23:59)
        
        Returns:
            {
                'week': 'YYYY-MM-DD to YYYY-MM-DD',
                'trades': [...],
                'total_trades': int,
                'wins': int,
                'losses': int,
                'win_rate': float,
                'total_pnl': float,
                'by_symbol': {...},
                'by_day': {...}
            }
        """
        logger.info(f"[WEEK BACKTEST] Analyzing {monday.date()} to {friday.date()}")
        
        all_trades = []
        
        # Analyze each symbol
        for symbol in self.symbols:
            try:
                symbol_trades = self._analyze_symbol_week(symbol, monday, friday)
                all_trades.extend(symbol_trades)
            except Exception as e:
                logger.warning(f"Failed to analyze {symbol} for week {monday.date()}: {e}")
        
        # Compile results
        results = self._compile_results(all_trades, monday, friday)
        
        # Log summary
        logger.info(f"[WEEK BACKTEST] {monday.date()}: "
                   f"{results['total_trades']} trades | "
                   f"{results['wins']}W/{results['losses']}L | "
                   f"WR={results['win_rate']:.1f}% | "
                   f"P&L=${results['total_pnl']:+,.2f}")
        
        return results
    
    def _analyze_symbol_week(self, symbol: str, monday: datetime, friday: datetime) -> List[Dict]:
        """Analyze one symbol for the week"""
        trades = []
        
        # Load data for the week (need extra bars for context)
        bars_needed = 500  # Enough for HTF bias + structure
        
        try:
            # Get H1 bias data
            bias_df = self._get_historical_bars(symbol, "H1", friday, bars_needed)
            if bias_df is None or len(bias_df) < 50:
                return trades
            
            # Get M15 structure data
            struct_df = self._get_historical_bars(symbol, "M15", friday, bars_needed)
            if struct_df is None or len(struct_df) < 50:
                return trades
            
            # Get M5 entry data
            entry_df = self._get_historical_bars(symbol, "M5", friday, bars_needed)
            if entry_df is None or len(entry_df) < 100:
                return trades
            
            # Get D1 for daily context
            daily_df = self._get_historical_bars(symbol, "D1", friday, 50)
            
        except Exception as e:
            logger.warning(f"Failed to load data for {symbol}: {e}")
            return trades
        
        # Scan each day of the week
        current_date = monday
        while current_date <= friday:
            # Skip weekends
            if current_date.weekday() >= 5:  # Saturday=5, Sunday=6
                current_date += timedelta(days=1)
                continue
            
            # Analyze each killzone that day
            day_trades = self._analyze_day(
                symbol, current_date,
                bias_df, struct_df, entry_df, daily_df
            )
            trades.extend(day_trades)
            
            current_date += timedelta(days=1)
        
        return trades
    
    def _get_historical_bars(self, symbol: str, timeframe: str, end_date: datetime, 
                            count: int) -> Optional[pd.DataFrame]:
        """Get historical bars from MT5"""
        try:
            # MT5 copy_rates_range expects start and end times
            start_date = end_date - timedelta(days=count)  # Rough estimate
            
            import MetaTrader5 as mt5
            
            # Map timeframe string to MT5 constant
            tf_map = {
                "M5": mt5.TIMEFRAME_M5,
                "M15": mt5.TIMEFRAME_M15,
                "H1": mt5.TIMEFRAME_H1,
                "H4": mt5.TIMEFRAME_H4,
                "D1": mt5.TIMEFRAME_D1,
            }
            
            mt5_tf = tf_map.get(timeframe, mt5.TIMEFRAME_M5)
            
            rates = mt5.copy_rates_range(symbol, mt5_tf, start_date, end_date)
            
            if rates is None or len(rates) == 0:
                return None
            
            df = pd.DataFrame(rates)
            df['time'] = pd.to_datetime(df['time'], unit='s')
            df.set_index('time', inplace=True)
            df.rename(columns={'open': 'open', 'high': 'high', 'low': 'low', 
                              'close': 'close', 'tick_volume': 'volume'}, inplace=True)
            
            return df[['open', 'high', 'low', 'close', 'volume']]
            
        except Exception as e:
            logger.error(f"Failed to get historical bars for {symbol} {timeframe}: {e}")
            return None
    
    def _analyze_day(self, symbol: str, date: datetime, 
                     bias_df: pd.DataFrame, struct_df: pd.DataFrame,
                     entry_df: pd.DataFrame, daily_df: pd.DataFrame) -> List[Dict]:
        """Analyze one trading day for setups"""
        from core.killzones import get_killzone_windows

        trades = []

        # News gate via the local archive (archive-as-you-go). The previous code
        # called non-existent methods (refresh_calendar / should_block_trade) on
        # a NewsFilter it had constructed with a raw dict — both threw, were
        # swallowed by a bare except, and so news never blocked anything. Use
        # the archive directly, consistent with core/backtester.py.
        news_keywords = self.config.get("news", {}).get(
            "high_impact_keywords", ["NFP", "CPI", "FOMC", "Fed Chair", "Interest Rate"]
        )
        news_before = self.config.get("news", {}).get("block_minutes_before", 15)
        news_after = self.config.get("news", {}).get("block_minutes_after", 15)

        # Get killzone windows for this day
        killzones = get_killzone_windows(date, self.config)

        # Check each killzone
        for kz_name, (kz_start, kz_end) in killzones.items():
            # Skip non-primary killzones for efficiency
            if kz_name not in ["London", "NY AM"]:
                continue

            # CHECK NEWS BLOCKING for this killzone start, using archived events
            try:
                from news import archive
                from news.calendar import is_within_news_window, filter_high_impact
                bar_date = kz_start.date() if hasattr(kz_start, 'date') else date.date()
                if archive.has_coverage(bar_date):
                    day_events = archive.get_events_for_day(bar_date)
                    hi_events = filter_high_impact(day_events, news_keywords)
                    news_blocked, ev = is_within_news_window(
                        kz_start, hi_events, news_before, news_after
                    )
                    if news_blocked:
                        logger.debug(
                            f"News blocked trade at {kz_start} on {date.date()} ({ev.title})"
                        )
                        continue  # Skip this killzone due to news
            except Exception as ne:
                # Never let the news gate break a backtest.
                logger.debug(f"News gate unavailable for {date.date()}: {ne}")
            
            # Get data slice for this killzone
            kz_entry_df = entry_df[
                (entry_df.index >= kz_start) & (entry_df.index <= kz_end)
            ]
            
            if len(kz_entry_df) < 10:
                continue
            
            # Run ICT 2022 model to find setups
            try:
                setup = self.strategy.find_ict_2022_setup(
                    symbol=symbol,
                    bias_df=bias_df,
                    struct_df=struct_df,
                    entry_df=kz_entry_df,
                    daily_df=daily_df
                )
                
                if setup:
                    # Simulate trade outcome
                    outcome = self._simulate_trade(setup, entry_df, kz_start)
                    
                    trade = {
                        'symbol': symbol,
                        'date': date.date().isoformat(),
                        'killzone': kz_name,
                        'time': kz_start.isoformat(),
                        'direction': setup.direction,
                        'entry': setup.entry_price,
                        'stop': setup.stop_loss,
                        'target': setup.take_profit,
                        'quality': setup.quality_score,
                        'outcome': outcome['result'],  # 'win', 'loss', 'breakeven'
                        'pnl': outcome['pnl'],
                        'bars_held': outcome['bars_held'],
                        'news_blocked': False,  # Made it past news check
                    }
                    
                    trades.append(trade)
                    
            except Exception as e:
                logger.debug(f"Error finding setup for {symbol} {kz_name} {date.date()}: {e}")
        
        return trades
    
    def _simulate_trade(self, setup, entry_df: pd.DataFrame, entry_time: datetime) -> Dict:
        """
        Simulate trade outcome.
        
        Walks forward from entry_time to see if SL or TP hit first.
        """
        # Get bars after entry
        future_df = entry_df[entry_df.index > entry_time].copy()
        
        if len(future_df) == 0:
            return {'result': 'no_data', 'pnl': 0, 'bars_held': 0}
        
        entry_price = setup.entry_price
        stop_loss = setup.stop_loss
        take_profit = setup.take_profit
        direction = setup.direction
        
        # Calculate pips per move
        pip_value = 1.0  # Simplified
        
        bars_held = 0
        
        for idx, row in future_df.iterrows():
            bars_held += 1
            
            if direction == "BUY":
                # Check if stop hit
                if row['low'] <= stop_loss:
                    pips_lost = (entry_price - stop_loss) * pip_value
                    return {
                        'result': 'loss',
                        'pnl': -pips_lost * 10,  # Rough $10/pip
                        'bars_held': bars_held,
                        'exit_price': stop_loss,
                    }
                
                # Check if target hit
                if row['high'] >= take_profit:
                    pips_won = (take_profit - entry_price) * pip_value
                    return {
                        'result': 'win',
                        'pnl': pips_won * 10,
                        'bars_held': bars_held,
                        'exit_price': take_profit,
                    }
            
            else:  # SELL
                # Check if stop hit
                if row['high'] >= stop_loss:
                    pips_lost = (stop_loss - entry_price) * pip_value
                    return {
                        'result': 'loss',
                        'pnl': -pips_lost * 10,
                        'bars_held': bars_held,
                        'exit_price': stop_loss,
                    }
                
                # Check if target hit
                if row['low'] <= take_profit:
                    pips_won = (entry_price - take_profit) * pip_value
                    return {
                        'result': 'win',
                        'pnl': pips_won * 10,
                        'bars_held': bars_held,
                        'exit_price': take_profit,
                    }
            
            # Max 100 bars (5 hours on M5)
            if bars_held >= 100:
                break
        
        # If neither hit, assume breakeven
        return {
            'result': 'timeout',
            'pnl': 0,
            'bars_held': bars_held,
        }
    
    def _compile_results(self, trades: List[Dict], monday: datetime, friday: datetime) -> Dict:
        """Compile week results"""
        total_trades = len(trades)
        
        if total_trades == 0:
            return {
                'week': f"{monday.date()} to {friday.date()}",
                'trades': [],
                'total_trades': 0,
                'wins': 0,
                'losses': 0,
                'win_rate': 0.0,
                'total_pnl': 0.0,
                'by_symbol': {},
                'by_day': {},
            }
        
        wins = sum(1 for t in trades if t['outcome'] == 'win')
        losses = sum(1 for t in trades if t['outcome'] == 'loss')
        total_pnl = sum(t['pnl'] for t in trades)
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
        
        # Group by symbol
        by_symbol = {}
        for trade in trades:
            sym = trade['symbol']
            if sym not in by_symbol:
                by_symbol[sym] = {'trades': 0, 'wins': 0, 'losses': 0, 'pnl': 0}
            
            by_symbol[sym]['trades'] += 1
            by_symbol[sym]['pnl'] += trade['pnl']
            if trade['outcome'] == 'win':
                by_symbol[sym]['wins'] += 1
            elif trade['outcome'] == 'loss':
                by_symbol[sym]['losses'] += 1
        
        # Group by day
        by_day = {}
        for trade in trades:
            day = trade['date']
            if day not in by_day:
                by_day[day] = {'trades': 0, 'wins': 0, 'losses': 0, 'pnl': 0}
            
            by_day[day]['trades'] += 1
            by_day[day]['pnl'] += trade['pnl']
            if trade['outcome'] == 'win':
                by_day[day]['wins'] += 1
            elif trade['outcome'] == 'loss':
                by_day[day]['losses'] += 1
        
        return {
            'week': f"{monday.date()} to {friday.date()}",
            'trades': trades,
            'total_trades': total_trades,
            'wins': wins,
            'losses': losses,
            'win_rate': win_rate,
            'total_pnl': total_pnl,
            'by_symbol': by_symbol,
            'by_day': by_day,
        }
