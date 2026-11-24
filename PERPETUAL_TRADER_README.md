# 🤖 PERPETUAL LEARNING TRADER

The bot that **NEVER stops learning**.

---

## 🎯 What Is This?

This is a Gen Z-optimized trading bot that:

✅ Runs **continuously for days/weeks/months**  
✅ **Backtests during idle time** (week-by-week through history)  
✅ **Trades live during killzones** (London 02:00-05:00, NY AM 07:00-10:00)  
✅ **Learns from mistakes** in real-time  
✅ **Saves checkpoint** after each week analyzed  
✅ **Resumes from exact position** when you restart  
✅ **Shows complete summary** when you stop (Ctrl+C)  

**Set it and forget it** - just run once and let it work for days. 🚀

---

## 🚀 Quick Start

### 1. Make Sure MT5 Is Running

```
1. Open MetaTrader 5
2. Login to your FBS Demo account
   - Login: supplied via `MT5_LOGIN`
   - Server: FBS-Demo
3. Check "Connected" shows in bottom-right
```

### 2. Run The Bot

```bash
cd "c:\Users\HP\Downloads\ict_trading_bot"
python main_perpetual.py
```

### 3. Let It Run

The bot will:
- **During idle hours (no killzone):** Backtest week-by-week through history
- **During killzones (02:00-05:00, 07:00-10:00):** Trade live, find ICT 2022 setups

You can leave it running for days, weeks, or months!

### 4. Stop Anytime

Press **Ctrl+C** to stop.

You'll see a complete summary:
- How many weeks analyzed
- Backtest results (win rate, P&L)
- Live trading results
- Evolution timeline (what the bot learned)
- Current position saved

### 5. Resume Later

Just run `python main_perpetual.py` again!

The bot picks up **exactly where it left off** - same week, same direction.

---

## 📊 How It Works

### **Time Travel System**

The bot travels through time analyzing complete trading weeks (Monday-Friday):

```
Today: July 19, 2026

Week 1: July 14-18, 2026 (current week) ✅
Week 2: July 7-11, 2026 (last week) → analyze
Week 3: June 30 - July 4, 2026 → analyze
Week 4: June 23-27, 2026 → analyze
...
Goes backward to 2020 (or earliest data)
...
Reaches 2020 → REVERSES → Goes forward
...
Reaches 2026 → REVERSES → Goes backward
...
♾️ Repeats forever
```

### **Dual-Mode Operation**

```
TIME          | MODE      | ACTION
--------------|-----------|----------------------------------
00:00 - 02:00 | BACKTEST  | Analyzing historical weeks
02:00 - 05:00 | LIVE      | ⚡ Trading (London killzone)
05:00 - 07:00 | BACKTEST  | Back to analyzing history
07:00 - 10:00 | LIVE      | ⚡ Trading (NY AM killzone)
10:00 - 02:00 | BACKTEST  | Analyzing more weeks
(Repeats)
```

### **Learning & Evolution**

After each live trade:
1. Bot searches historical patterns similar to this setup
2. Compares outcome to historical win rate
3. Adjusts quality thresholds if needed
4. Updates what works and what doesn't

After each week analyzed:
1. Stores all setups found that week
2. Calculates win rate patterns
3. Discovers which symbols/times work best
4. Evolves strategy automatically

---

## 📂 Files Created

| File | Purpose |
|------|---------|
| `data/time_travel_checkpoint.json` | Saves current week position |
| `data/trade_ledger.db` | All trades (backtest + live) |
| `data/session_summary.txt` | Last summary report |
| `logs/bot.log` | Runtime logs |

---

## ⚙️ Configuration

Edit `config.yaml`:

```yaml
risk:
  risk_per_trade_pct: 1.0  # Conservative 1% per trade
  max_daily_drawdown_pct: 3.0  # Stop if -3% in one day
  max_trades_per_day: 3  # Limit trades

strategy:
  entry_model: "ict_2022"  # Using ICT 2022 Model
  min_quality_score: 0.70  # Only take high-quality setups

# Killzones (all in NY time, DST-aware)
killzones_ny:
  London: ["02:00", "05:00"]
  NY AM: ["07:00", "10:00"]
```

---

## 🛑 Stopping The Bot

**Press Ctrl+C anytime**

You'll see:

```
================================================================================
  🤖 PERPETUAL LEARNING TRADER - SESSION SUMMARY
================================================================================

⏱️  Runtime: 24.3 hours (1 days, 0 hours)
📅 Session: 2026-07-19 05:00 → 2026-07-20 05:18

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  📚 HISTORICAL BACKTEST PROGRESS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⏪ Current Position:
   Week: 2026-06-16 to 2026-06-20
   Direction: BACKWARD

📊 Analysis Stats:
   Weeks analyzed: 8
   Years covered: 0.2
   Time cycles: 0 complete loops through history

📈 Historical Trades:
   Total: 42
   Wins: 26 (61.9%)
   Losses: 16
   Patterns discovered: 0

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  💰 LIVE TRADING RESULTS (Real Killzone Trades)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   Total trades: 3
   Wins: 2 (66.7%)
   Losses: 1
   Total P&L: +$620.00

   Last 3 Trades:
      2026-07-20T08:15 | US30 | WIN | +$340.00
      2026-07-19T07:22 | US500 | LOSS | -$120.00
      2026-07-19T03:10 | US30 | WIN | +$400.00

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  🧬 EVOLUTION TIMELINE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   Quality threshold: Started 0.70 → Now 0.70

   Best Performing Symbols:
      US30: 67% WR (3 trades)

   Best Killzones:
      London: 100% WR (1 trades)
      NY AM: 50% WR (2 trades)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  📍 CURRENT STATE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   ✅ Checkpoint saved automatically
   ✅ When you restart, bot will resume from week: 2026-06-16
   ✅ All learning progress preserved in database

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  🚀 NEXT STEPS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   To resume the bot:
      python main_perpetual.py

   Bot will:
      1. Load checkpoint and resume from saved week
      2. Continue time travel backtest during idle
      3. Trade live during killzones
      4. Keep evolving strategy automatically

================================================================================
  Session Complete - All progress saved ✅
================================================================================
```

---

## 🎯 Expected Results

### **After 1 Day:**
- ~4 weeks analyzed (96 hours of market data)
- 10-20 setups found in history
- 0-3 live trades executed
- Initial patterns discovered

### **After 1 Week:**
- ~30 weeks analyzed (7 months of history)
- 100-200 setups analyzed
- 5-15 live trades executed
- Clear patterns emerging

### **After 1 Month:**
- ~120 weeks analyzed (2.3 years of history)
- 500-1000 setups analyzed
- 20-60 live trades executed
- Bot knows what works for YOU

### **After 3 Months:**
- ~360 weeks analyzed (6.9 years - FULL HISTORY)
- 2000+ setups analyzed
- 60-180 live trades
- Bot has seen EVERYTHING
- First full cycle complete → starts going forward

---

## 🔧 Troubleshooting

### **"Failed to connect to MT5"**
- Make sure MT5 is running and logged in
- Check login/server in config.yaml match MT5
- Try closing and reopening MT5

### **"No trades being executed"**
- Bot is very selective (quality > 0.70)
- Check logs to see if setups are being rejected
- Try lowering `min_quality_score` to 0.65 in config

### **"Bot is stuck analyzing one week"**
- Likely no data available for that symbol/week
- Check MT5 has historical data
- Try switching to different symbols

### **"How do I know if it's working?"**
- Check `logs/bot.log` file
- Look for "LIVE MODE" and "BACKTEST MODE" switches
- Watch for "Setup found" messages during killzones

---

## 💡 Pro Tips

1. **Let it run for at least 48 hours** before judging results
2. **Check summary every few days** - see what it's learning
3. **Don't change config mid-run** - let it adapt naturally
4. **Start on demo account** - validate for 1-3 months first
5. **Be patient** - full learning takes ~3 months to analyze all history

---

## 📞 Support

If the bot crashes or has issues:

1. Check `logs/bot.log` for errors
2. Verify MT5 is connected
3. Make sure historical data is available
4. Try restarting bot (it will resume automatically)

---

## ✅ Quick Checklist

Before running:
- [ ] MT5 is open and logged in
- [ ] FBS Demo account showing "Connected"
- [ ] config.yaml has correct login/server
- [ ] Virtual environment activated (`.venv`)

To run:
```bash
python main_perpetual.py
```

To stop:
```
Press Ctrl+C (wait for summary)
```

To resume:
```bash
python main_perpetual.py
```

---

**That's it! Set it and forget it. The bot will learn, evolve, and trade automatically.** 🚀

---

**Built:** July 19, 2026  
**Version:** Perpetual Learning Trader v1.0  
**Status:** Ready to run ✅
