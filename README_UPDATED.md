# 🤖 ICT Trading Bot - PERPETUAL LEARNING TRADER

**The bot that NEVER stops learning and evolving.**

---

## ✅ WHAT'S INSIDE (FULLY INTEGRATED)

This bot has **EVERYTHING** in one system:

### **Core ICT 2022 Implementation** ✅
- 6-step ICT 2022 Mentorship Model (Ch.19)
- Killzones (London 02:00-05:00, NY AM 07:00-10:00)
- Fair Value Gaps, Order Blocks, Liquidity Sweeps
- Market Structure (BOS, CHoCH, MSS)
- News filtering & sentiment analysis

### **6 Enhancements** ✅
- Support/resistance detection
- Red flag detection (choppy markets, news proximity)
- Market regime detection (trending/ranging/volatile)
- News sentiment alignment
- Multi-timeframe confluence scoring

### **Learning Systems** ✅
- Adaptive risk management (0.5%-3.0%)
- Pattern recognition & quality threshold evolution
- Trade memory (SQLite database)

### **Evolution Lab** ✅
- Genetic algorithm strategy evolution
- Pattern discovery engine
- Best strategy auto-applied to live trading

### **Parallel Learning** ✅
- Micro-evolution during killzones
- Market regime tracking
- Setup observation & analysis

### **🆕 PERPETUAL TIME TRAVEL** ✅
- **Week-by-week historical backtest** (not 14-day repeating!)
- Goes **backward** through time (2026 → 2020)
- Then **forward** (2020 → 2026)
- **Repeats infinitely** - never stops learning
- **Checkpoint saved** after each week
- **Resumes exactly** where it stopped

---

## 🚀 HOW TO RUN

### **1. Make Sure MT5 Is Running**
```
1. Open MetaTrader 5
2. Login to FBS Demo
   - Login: supplied via `MT5_LOGIN`
   - Server: FBS-Demo
3. Check "Connected" in bottom-right
```

### **2. Run The Bot**
```bash
cd "c:\Users\HP\Downloads\ict_trading_bot"
python main.py
```

**That's it!** One command, everything runs.

---

## 📊 WHAT HAPPENS

### **During IDLE Time (No Killzone Active):**

Every hour, bot rotates between:

**Hour 0, 3, 6, 9, 12, 15, 18, 21:**
- 🕐 **Week-by-week time travel backtest**
- Analyzes one complete trading week (Mon-Fri)
- Finds all ICT 2022 setups that week
- Records results
- Moves to previous week
- Saves checkpoint
- **Never repeats same week twice** until full cycle complete

**Hour 1, 4, 7, 10, 13, 16, 19, 22:**
- 🧬 **Strategy evolution**
- Genetic algorithm creates variants
- Tests them on historical data
- Keeps best performers
- Auto-applies to live trading

**Hour 2, 5, 8, 11, 14, 17, 20, 23:**
- 🔍 **Pattern discovery**
- Analyzes what actually works
- Discovers winning combinations
- Updates quality thresholds

### **During KILLZONE Time (London/NY AM):**

- 💰 **LIVE TRADING**
- Scans for ICT 2022 setups
- Applies all 6 enhancements
- Executes high-quality trades (score > 0.70)
- Manages positions (breakeven, partial close)
- **Parallel learner observes** every setup (even rejected ones)

---

## 🛑 STOPPING THE BOT

**Press Ctrl+C anytime**

You'll see:
```
================================================================================
  🤖 PERPETUAL LEARNING TRADER - SESSION SUMMARY
================================================================================

⏱️  Runtime: 48.2 hours (2 days, 0 hours)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  📚 HISTORICAL BACKTEST PROGRESS (Week-by-Week Time Travel)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⏪ Current Position:
   Week: 2026-05-12 to 2026-05-16
   Direction: BACKWARD

📊 Analysis Progress:
   Weeks analyzed: 12
   Years covered: 0.2
   Cycles completed: 0

📈 Historical Trades:
   Total: 84
   Wins: 52 (61.9%)
   Losses: 32
   Patterns discovered: 8

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  💰 LIVE TRADING RESULTS (Real Killzone Trades)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   Total trades: 6
   Wins: 4 (66.7%)
   Losses: 2
   Total P&L: +$1,240.00

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  🧬 EVOLUTION TIMELINE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   Strategy Evolution: Generation 4 active
   Quality threshold: 0.70 → 0.72 (learned from patterns)
   Best symbol: US30 (70% WR)
   Best time: Monday London (4W/0L)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  📍 CURRENT STATE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   ✅ Checkpoint saved: data/time_travel_checkpoint.json
   ✅ On restart, bot resumes from week: 2026-05-12
   ✅ All learning progress preserved

================================================================================
  Session Complete - All progress saved ✅
================================================================================
```

---

## 🎯 KEY FEATURES

### **What Makes This Special:**

1. ✅ **Set It and Forget It**
   - Run once, let it work for days/weeks/months
   - No babysitting needed

2. ✅ **Never Stops Learning**
   - Backtests ALL history week-by-week
   - Goes backward, then forward, repeats forever
   - Never analyzes same week twice per cycle

3. ✅ **Learns While Trading**
   - Parallel learner observes every setup
   - Micro-evolution during killzones
   - Real-time pattern discovery

4. ✅ **Auto-Evolution**
   - Strategy Lab creates & tests variants
   - Best performers auto-applied
   - No manual tuning needed

5. ✅ **Checkpoint & Resume**
   - Saves progress after each week
   - Can stop/start anytime
   - Never loses learning progress

6. ✅ **Comprehensive Summary**
   - Press Ctrl+C for full report
   - Backtest + Live + Evolution stats
   - Clear next steps

---

## ⚙️ CONFIGURATION

Edit `config.yaml`:

```yaml
mode: "mt5_paper"  # or "mt5_live" when ready

risk:
  risk_per_trade_pct: 1.0  # Conservative
  max_daily_drawdown_pct: 3.0
  max_trades_per_day: 3

strategy:
  entry_model: "ict_2022"
  min_quality_score: 0.70

# All times in NY (DST-aware)
killzones_ny:
  London: ["02:00", "05:00"]
  NY AM: ["07:00", "10:00"]
```

---

## 📂 FILES STRUCTURE

```
ict_trading_bot/
├── main.py                          ← RUN THIS (everything integrated)
├── config.yaml                      ← Settings
├── core/
│   ├── time_travel.py              ← Week-by-week navigation
│   ├── week_backtester.py          ← Historical analysis
│   ├── summary_generator.py        ← Reports
│   ├── strategy.py                 ← ICT 2022 Model
│   ├── learning.py                 ← Adaptive learning
│   ├── strategy_lab.py             ← Evolution engine
│   ├── parallel_learner.py         ← Killzone learning
│   ├── enhancements.py             ← 6 intelligent layers
│   └── ...
├── data/
│   ├── time_travel_checkpoint.json ← Progress saved here
│   ├── trade_ledger.db             ← All trades
│   └── session_summary.txt         ← Last summary
└── logs/
    └── bot.log                     ← Runtime logs
```

---

## 🔧 TROUBLESHOOTING

### **"Failed to connect to MT5"**
- Make sure MT5 is running & logged in
- Check config.yaml has correct login/server

### **"No trades being executed"**
- Bot is selective (quality > 0.70)
- Check logs: `logs/bot.log`
- Lower `min_quality_score` to 0.65 if needed

### **"Bot stuck on one week"**
- Check if MT5 has historical data for that period
- Try different symbols in config

---

## 💯 EXPECTED RESULTS

**After 24 Hours:**
- ~6 weeks analyzed
- 20-40 historical trades
- 0-3 live trades
- Evolution generation 2-3

**After 1 Week:**
- ~40 weeks analyzed
- 150-250 historical trades
- 5-15 live trades
- Clear patterns emerging

**After 1 Month:**
- ~180 weeks analyzed (3.5 years)
- 800-1200 historical trades
- 20-60 live trades
- Bot knows what works

**After 3 Months:**
- ~540 weeks analyzed (10+ years - FULL CYCLE)
- 3000+ historical trades analyzed
- 60-180 live trades
- Bot mastered YOUR market conditions

---

## ✅ QUICK START CHECKLIST

- [ ] MT5 open and logged in (FBS Demo)
- [ ] config.yaml has correct login/server
- [ ] Virtual environment activated: `.venv`
- [ ] Run: `python main.py`
- [ ] Let it run for at least 24-48 hours
- [ ] Press Ctrl+C to see summary anytime
- [ ] Restart resumes automatically

---

## 🎉 THE COMPLETE SYSTEM

**You now have ONE bot that does EVERYTHING:**

✅ ICT 2022 Model  
✅ 6 Enhancements  
✅ Learning Engine  
✅ Evolution Lab  
✅ Parallel Learning  
✅ Week-by-week Time Travel  
✅ Checkpoint & Resume  
✅ Comprehensive Reporting  

**Just run `python main.py` and let it work!** 🚀

---

**Built:** July 19, 2026  
**Version:** Perpetual Learning Trader v2.0 (Fully Integrated)  
**Status:** Ready to run ✅
