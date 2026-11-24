# ICT Trading Bot

A Python trading bot implementing **ICT (Inner Circle Trader)** concepts on stock indices (US30, NAS100, SP500) via MetaTrader 5, with news coordination and a built-in simulator for safe demo runs.

## ⚠️ MAJOR UPDATE: ICT 2022 Mentorship Implementation (Jan 2025)

This bot has been **completely rewritten** to align with ICT's 2022 Mentorship course (407 pages). 

### What Changed?

**Primary Entry Model**: ICT 2022 Mentorship Model (Ch.19)
- Replaces OTE-first approach
- 6-step mechanical entry: Sweep → Displacement+FVG → MSS → 50% Fib → FVG at EQ → Entry
- OTE kept as fallback for A/B testing

**Killzone Times** (Ch.11):
- London: `02:00-05:00` EST/EDT (was `07:00-10:00 UTC` - **fixed!**)
- New York: `07:00-10:00` EST/EDT
- Automatic DST handling with `zoneinfo`

**Order Blocks** (Ch.29):
- Now **body-based** (open→close) NOT wicks
- Book quote: "Use BODY not wicks - wicks unreliable across brokers"

**Filters Added** (Ch.16, 18, 25, 32):
- Day-of-week: Prefer Mon/Tue/Wed
- NFP week: Block Thu/Fri
- FOMC day: Block all-day
- Dealing Range: Buy discount, sell premium
- Displacement quality: Require ≥1.5 ATR

**Stop Loss** (Ch.32):
- Uses book's table: 5-20 points depending on entry type
- Replaces generic ATR-based stops

**Take Profit** (Ch.32):
- Scales out per book's guidance
- 20% @ 10-15pts, 30% @ 2STD, 25% @ PDH/PDL, 25% @ ADR

### Quick Start (Updated)

1. **Fix config** (CRITICAL):
```yaml
killzones:
  london_kz_start: "02:00"  # NOT "07:00"!
  london_kz_end: "05:00"
  timezone: "America/New_York"
```

2. **Test**:
```bash
python main.py --steps 60
```

3. **Backtest** (compare before/after):
```bash
python -m core.backtester --start 2024-01-01 --end 2024-12-31
```

### Expected Performance (per book)
- Win rate: 50-60%
- Average RR: 2.0-3.0
- Max trades/day: 1-2
- Best days: Mon/Tue/Wed

See `CHANGELOG.md` for full details.

---

## ⚠️ Risk Disclaimer

This bot is for **educational and research purposes only**. Trading involves substantial risk of loss. **No bot can guarantee profits or "minimal losses."** Always test thoroughly on a demo account before risking real capital. The author is not responsible for any financial losses.

---

## Strategy Overview

The bot implements ICT's 2022 Mentorship Model strategy:

| Concept | Implementation |
|---|---|
| **Entry Model** | ICT 2022 Mentorship Model (6-step: Sweep → Displacement → MSS → Equilibrium → Entry) |
| **Killzones** | London (02:00-05:00 EST/EDT), NY AM (07:00-10:00), with automatic DST |
| **HTF Bias** | H1 market structure (BOS / CHoCH) determines direction |
| **Liquidity Sweep** | Prioritizes PDH/PDL, then Asian highs/lows, then equal levels |
| **Displacement** | Requires ≥1.5 ATR move with FVG (institutional sponsorship) |
| **MSS Confirmation** | Market Structure Shift validated with swing alignment |
| **Entry Zone** | FVG at 50% equilibrium of displacement range |
| **Order Blocks** | Body-based detection (open→close) per Ch.29 |
| **Take-Profit** | Scaled exits: 20% @ 10-15pts, 30% @ 2STD, 25% @ PDH/PDL, 25% @ ADR |
| **Stop Loss** | Book's table (Ch.32): 5-20 points depending on entry type |
| **News Filter** | NFP week blocking, FOMC all-day block, Mon-after-NFP block |
| **Day Filter** | Prefer Mon/Tue/Wed, caution Thu, avoid Fri (Ch.25) |
| **Dealing Range** | Buy in discount, sell in premium (Ch.16) |

## Risk Management (Aggressive Profile)

- **2% risk per trade** (configurable)
- **5% max daily drawdown** → halts trading for the day
- **Max 5 trades per day**
- **1:3 minimum reward-to-risk**
- **Breakeven** at 1R, **partial close (50%)** at 2R
- Stop-loss always set below/above swept liquidity level

---

## Project Structure

```
ict_trading_bot/
├── main.py                  # Entry point — run this
├── config.yaml              # All settings (broker, symbols, risk, news)
├── requirements.txt         # Python dependencies
├── core/                    # ICT strategy engine
│   ├── strategy.py          # Combines all concepts into trade signals
│   ├── market_structure.py  # BOS / CHoCH detection
│   ├── order_block.py       # Order Block detection & mitigation
│   ├── fair_value_gap.py    # FVG detection
│   ├── liquidity.py         # Equal highs/lows + sweep detection
│   └── killzones.py         # Session timing
├── risk/
│   └── manager.py           # Position sizing, drawdown limits, trade tracking
├── news/
│   ├── calendar.py          # ForexFactory economic calendar
│   ├── sentiment.py         # RSS headline sentiment (VADER)
│   └── filter.py            # Unified news gate
├── broker/
│   ├── base.py              # Abstract broker interface
│   ├── mt5_broker.py        # MT5 live/paper adapter
│   └── simulator.py         # In-memory simulator (works without MT5)
└── logs/                    # Runtime logs
```

---

## Quick Start (Simulator Mode)

The simulator runs **anywhere** (no MT5 needed) — perfect for testing the bot logic.

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run the bot in simulator mode (default)
cd ict_trading_bot
python main.py --steps 200

# `--steps 200` runs 200 simulated M5 bars then exits with stats.
# Remove `--steps` to run continuously.
```

You'll see logs like:
```
SIGNAL: BUY US30 @ 38450.12 | SL=38420.50 | TP=38540.00 | RR=3.05 | vol=0.45
   Reason: NY AM KZ | HTF bias=bullish | Sweep=bullish_sweep | CHoCH @ 38462.50 | OTE zone 38445-38455
[SIM] Opened BUY US30 vol=0.45 @ 38450.12 ...
[SIM] TP hit on US30 BUY
```

---

## Live Paper Trading (MT5 Demo Account)

### Requirements

- **Windows** (MetaTrader 5 terminal only runs on Windows; on Linux you need Wine — advanced)
- **MetaTrader 5 terminal** installed and logged into a **demo account**
  - Recommended brokers for ICT-style index trading: IC Markets, Pepperstone, FTMO
- **MT5 Python package**: `pip install MetaTrader5`

### Setup Steps

1. **Open a demo account** with your broker and note:
   - Login number
   - Password
   - Server name (e.g. `ICMarketsSC-Demo`)

2. **Install MT5 terminal** and log in once manually so credentials are cached.

3. **Edit `config.yaml`:**
   ```yaml
   mode: "mt5_paper"

   mt5:
     login: 12345678
     password: "your_password"
     server: "ICMarketsSC-Demo"
     path: ""   # leave blank for auto-detect, or set full path to terminal64.exe
   ```

4. **Run:**
   ```bash
   python main.py
   ```

5. The bot will:
   - Connect to MT5
   - Pull live H1/M15/M5 bars for US30, NAS100, SP500
   - Wait inside killzones for setups
   - Open positions on your demo account
   - Manage SL/TP/breakeven automatically

---

## Configuration Reference (`config.yaml`)

| Section | Key | Description |
|---|---|---|
| `mode` | `"simulator"` / `"mt5_paper"` | Broker mode |
| `mt5` | login/password/server | MT5 demo credentials |
| `symbols` | name / pip_value / contract_size | Instruments to trade |
| `timeframes` | bias_tf / structure_tf / entry_tf | Default: H1 / M15 / M5 |
| `strategy` | killzone times, OTE range, FVG params | ICT settings |
| `risk` | risk_per_trade_pct, max_daily_drawdown_pct | Risk profile |
| `news` | block_minutes, keywords, rss_feeds | News coordination |
| `notifications` | telegram / log_to_file | Alerts |
| `execution` | poll_interval_seconds | Main loop interval |

### Risk Profile Switching

Edit `risk` section to switch between profiles:

```yaml
# Conservative (smaller gains, fewer losses)
risk:
  risk_per_trade_pct: 0.5
  max_daily_drawdown_pct: 1.0
  max_trades_per_day: 3

# Moderate (balanced)
risk:
  risk_per_trade_pct: 1.0
  max_daily_drawdown_pct: 3.0
  max_trades_per_day: 5

# Aggressive (default — bigger swings)
risk:
  risk_per_trade_pct: 2.0
  max_daily_drawdown_pct: 5.0
  max_trades_per_day: 5
```

---

## How News Coordination Works

1. **Economic Calendar** — pulls the ForexFactory weekly JSON feed every 5 minutes (cached).
2. Filters to high-impact events (NFP, CPI, FOMC, Fed Chair, Interest Rate, Unemployment, GDP, PMI, Retail Sales).
3. Before opening any trade, checks if the current time is within ±15 minutes of any event → **blocks the trade**.
4. **Sentiment Filter** — pulls Yahoo Finance RSS headlines for ^DJI, ^IXIC, ^GSPC.
5. Computes VADER sentiment score; if `|score| >= 0.5` (extreme), blocks trades.

Upcoming events are logged for visibility:
```
Calendar refreshed: 12 high-impact events this week
Headlines refreshed: 8 | sentiment=+0.12
News block: Nonfarm Payrolls @ 13:30 UTC
```

---

## Adding Telegram Alerts (Optional)

1. Create a bot via [@BotFather](https://t.me/BotFather) and get the token.
2. Get your chat ID by messaging your bot and visiting `https://api.telegram.org/bot<TOKEN>/getUpdates`.
3. Edit `config.yaml`:
   ```yaml
   notifications:
     telegram_enabled: true
     telegram_bot_token: "123456:ABC-DEF..."
     telegram_chat_id: "987654321"
   ```

---

## Troubleshooting

| Issue | Solution |
|---|---|
| `MetaTrader5` import error | Only installable on Windows. Use `mode: simulator` on Mac/Linux. |
| MT5 `initialize() failed` | Check login/password/server. Make sure MT5 terminal is running and logged in. |
| No signals generated | Check that current time is inside a killzone (London 07-10 UTC, NY AM 12-15 UTC, NY PM 15:30-18 UTC). |
| `feedparser not installed` | Run `pip install feedparser` — sentiment is optional but recommended. |
| Calendar fetch fails | ForexFactory may rate-limit. Bot will continue without calendar (warns in log). |

---

## Roadmap / Next Steps

- [ ] Backtesting module — replay historical data and compute strategy metrics
- [ ] Walk-forward optimization of OTE Fib range and killzone times
- [ ] Multi-symbol portfolio correlation filter (don't open US30 + NAS100 simultaneously)
- [ ] Web dashboard for live monitoring
- [ ] Mobile push notifications (Telegram already supported)

---

## Final Reality Check

This bot implements ICT concepts mechanically. ICT himself emphasizes **discretion and judgment** — a bot cannot replicate that. Expect:

- Win rate typically **40-55%** (with 1:3 RR, this is profitable)
- Drawdown periods of **weeks** are normal
- No strategy works in all market conditions
- **Backtest thoroughly** before going live, even on demo
- Past performance does not guarantee future results

Trade safe. 📈
