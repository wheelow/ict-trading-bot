"""
ICT 2022 Implementation Verification Script

This script tests all new components to verify the implementation is working correctly.
Run this BEFORE running the full bot to catch any issues early.
"""
import sys
import traceback
from datetime import datetime

print("=" * 80)
print("  ICT 2022 MENTORSHIP IMPLEMENTATION VERIFICATION")
print("=" * 80)
print()

# Track results
results = {
    "passed": [],
    "failed": [],
    "warnings": []
}

def test_step(name, test_func):
    """Run a test step and record results"""
    try:
        print(f"Testing: {name}...", end=" ")
        test_func()
        print("✅ PASS")
        results["passed"].append(name)
        return True
    except Exception as e:
        print(f"❌ FAIL")
        print(f"   Error: {str(e)}")
        results["failed"].append((name, str(e)))
        return False

# ============================================================================
# TEST 1: Import all new modules
# ============================================================================
def test_imports():
    """Test that all new modules can be imported"""
    print("\n" + "=" * 80)
    print("TEST 1: Module Imports")
    print("=" * 80)
    
    def test_ict_2022_model():
        from core.ict_2022_model import detect_ict_2022_setup, ICT2022Setup
    
    def test_filters():
        from core.filters import (
            check_day_of_week, check_nfp_week, check_fomc_day,
            check_monday_after_nfp, check_dealing_range_position,
            check_displacement_quality, apply_all_filters
        )
    
    def test_news_filter_updates():
        from news.filter import is_nfp_week, is_fomc_day, should_block_trade_by_news
    
    def test_news_calendar_updates():
        from news.calendar import get_upcoming_high_impact_events, is_high_impact_news_within_window
    
    def test_risk_manager_updates():
        from risk.manager import calculate_stop_loss_by_book, calculate_take_profit_levels_by_book
    
    test_step("core.ict_2022_model", test_ict_2022_model)
    test_step("core.filters", test_filters)
    test_step("news.filter updates", test_news_filter_updates)
    test_step("news.calendar updates", test_news_calendar_updates)
    test_step("risk.manager updates", test_risk_manager_updates)

# ============================================================================
# TEST 2: ICT 2022 Model functionality
# ============================================================================
def test_ict_2022_model_logic():
    """Test ICT 2022 model detection logic"""
    print("\n" + "=" * 80)
    print("TEST 2: ICT 2022 Model Logic")
    print("=" * 80)
    
    def test_setup_detection():
        import pandas as pd
        import numpy as np
        from core.ict_2022_model import detect_ict_2022_setup
        
        # Create sample data
        dates = pd.date_range('2024-01-01 08:00', periods=100, freq='5min')
        df = pd.DataFrame({
            'open': np.random.uniform(38000, 38100, 100),
            'high': np.random.uniform(38100, 38200, 100),
            'low': np.random.uniform(37900, 38000, 100),
            'close': np.random.uniform(38000, 38100, 100),
        }, index=dates)
        
        # Test setup detection (should return None for random data, but shouldn't crash)
        result = detect_ict_2022_setup(
            df=df,
            direction="BUY",
            pdh=38200.0,
            pdl=37900.0,
            asian_high=38150.0,
            asian_low=37950.0
        )
        
        print(f"      Setup detection ran successfully (result: {type(result).__name__})")
    
    test_step("ICT 2022 setup detection", test_setup_detection)

# ============================================================================
# TEST 3: Filters functionality
# ============================================================================
def test_filters_logic():
    """Test filter logic"""
    print("\n" + "=" * 80)
    print("TEST 3: Filter Logic")
    print("=" * 80)
    
    def test_day_of_week_filter():
        import pandas as pd
        from core.filters import check_day_of_week
        
        # Test Monday (should pass)
        monday = pd.Timestamp('2024-01-01 10:00')  # Monday
        result = check_day_of_week(monday)
        assert result.passed, "Monday should pass"
        assert "optimal" in result.reason.lower(), "Monday should be optimal"
        print(f"      Monday: {result.reason}")
        
        # Test Friday (should warn/block)
        friday = pd.Timestamp('2024-01-05 10:00')  # Friday
        result = check_day_of_week(friday)
        print(f"      Friday: {result.reason} (severity: {result.severity})")
    
    def test_dealing_range_filter():
        from core.filters import check_dealing_range_position
        
        # Test BUY in discount (should pass)
        result = check_dealing_range_position(
            current_price=38000,
            dealing_range_high=38200,
            dealing_range_low=37800,
            direction="BUY",
            strictness=0.5
        )
        print(f"      BUY at 38000 in range 37800-38200: {result.reason}")
        assert result.passed, "BUY in discount should pass"
        
        # Test SELL in premium (should pass)
        result = check_dealing_range_position(
            current_price=38150,
            dealing_range_high=38200,
            dealing_range_low=37800,
            direction="SELL",
            strictness=0.5
        )
        print(f"      SELL at 38150 in range 37800-38200: {result.reason}")
        assert result.passed, "SELL in premium should pass"
    
    def test_displacement_quality_filter():
        from core.filters import check_displacement_quality
        
        # Test strong displacement (should pass)
        result = check_displacement_quality(
            displacement_size=100.0,
            atr=50.0,
            min_multiplier=1.5
        )
        print(f"      100pt displacement with 50pt ATR: {result.reason}")
        assert result.passed, "Strong displacement should pass"
        
        # Test weak displacement (should fail)
        result = check_displacement_quality(
            displacement_size=50.0,
            atr=50.0,
            min_multiplier=1.5
        )
        print(f"      50pt displacement with 50pt ATR: {result.reason}")
        assert not result.passed, "Weak displacement should fail"
    
    test_step("Day-of-week filter", test_day_of_week_filter)
    test_step("Dealing range filter", test_dealing_range_filter)
    test_step("Displacement quality filter", test_displacement_quality_filter)

# ============================================================================
# TEST 4: News blocking functions
# ============================================================================
def test_news_blocking():
    """Test news blocking logic"""
    print("\n" + "=" * 80)
    print("TEST 4: News Blocking Functions")
    print("=" * 80)
    
    def test_nfp_week_detection():
        import pandas as pd
        from news.filter import is_nfp_week
        
        # Mock NFP event this week
        monday = pd.Timestamp('2024-01-01 10:00')
        events = [
            {"title": "Nonfarm Payrolls", "time": "2024-01-05 13:30"}
        ]
        
        result = is_nfp_week(monday, events)
        print(f"      NFP week detection: {result}")
    
    def test_fomc_day_detection():
        import pandas as pd
        from news.filter import is_fomc_day
        
        # Mock FOMC event today
        today = pd.Timestamp('2024-01-10 10:00')
        events = [
            {"title": "FOMC Meeting", "time": "2024-01-10 14:00"}
        ]
        
        result = is_fomc_day(today, events)
        print(f"      FOMC day detection: {result}")
    
    def test_comprehensive_blocking():
        import pandas as pd
        from news.filter import should_block_trade_by_news
        
        # Test normal day (should not block)
        normal_day = pd.Timestamp('2024-01-02 10:00')
        events = []
        
        should_block, reason = should_block_trade_by_news(normal_day, events)
        print(f"      Normal day: blocked={should_block}, reason='{reason}'")
    
    test_step("NFP week detection", test_nfp_week_detection)
    test_step("FOMC day detection", test_fomc_day_detection)
    test_step("Comprehensive news blocking", test_comprehensive_blocking)

# ============================================================================
# TEST 5: Risk management functions
# ============================================================================
def test_risk_management():
    """Test risk management functions"""
    print("\n" + "=" * 80)
    print("TEST 5: Risk Management Functions")
    print("=" * 80)
    
    def test_stop_loss_calculation():
        from risk.manager import calculate_stop_loss_by_book
        
        # Test CBDR overlap entry
        sl = calculate_stop_loss_by_book(
            entry_type="cbdr_overlap",
            entry_price=38100.0,
            direction="BUY",
            atr=50.0
        )
        print(f"      CBDR overlap BUY @ 38100: SL = {sl}")
        assert sl < 38100, "SL should be below entry for BUY"
        
        # Test asian range run entry
        sl = calculate_stop_loss_by_book(
            entry_type="asian_range_run",
            entry_price=38100.0,
            direction="BUY",
            atr=50.0,
            asian_range_low=38050.0
        )
        print(f"      Asian run BUY @ 38100: SL = {sl}")
        assert sl < 38050, "SL should be below Asian low"
    
    def test_take_profit_calculation():
        from risk.manager import calculate_take_profit_levels_by_book
        
        levels = calculate_take_profit_levels_by_book(
            entry_price=38100.0,
            direction="BUY",
            asian_range=(38000.0, 38200.0),
            pdh=38250.0,
            five_day_adr=200.0
        )
        
        print(f"      TP levels for BUY @ 38100:")
        for price, pct, reason in levels:
            print(f"        - {price:.2f} ({pct:.0%}): {reason}")
        
        assert len(levels) > 0, "Should return TP levels"
    
    test_step("Stop loss calculation", test_stop_loss_calculation)
    test_step("Take profit calculation", test_take_profit_calculation)

# ============================================================================
# TEST 6: Config validation
# ============================================================================
def test_config():
    """Test configuration is correct"""
    print("\n" + "=" * 80)
    print("TEST 6: Configuration Validation")
    print("=" * 80)
    
    def test_killzone_times():
        import yaml
        with open('config.yaml', 'r') as f:
            config = yaml.safe_load(f)
        
        # Check killzones
        kz = config.get('strategy', {})
        
        # Check entry model
        entry_model = kz.get('entry_model', 'ote')
        print(f"      Entry model: {entry_model}")
        if entry_model != 'ict_2022':
            results["warnings"].append("Entry model not set to 'ict_2022' - will use legacy OTE")
        
        # Check killzone times
        london_start = kz.get('london_kz_start', '')
        london_end = kz.get('london_kz_end', '')
        
        print(f"      London KZ: {london_start} - {london_end}")
        
        if london_start == "02:00" and london_end == "05:00":
            print(f"      ✅ Killzone times CORRECT (02:00-05:00)")
        else:
            print(f"      ⚠️  Killzone times may be wrong (expected 02:00-05:00)")
            results["warnings"].append(f"London KZ times: {london_start}-{london_end} (expected 02:00-05:00)")
    
    test_step("Config file validation", test_killzone_times)

# ============================================================================
# TEST 7: Integration check
# ============================================================================
def test_integration():
    """Test that strategy.py can use new components"""
    print("\n" + "=" * 80)
    print("TEST 7: Integration Check")
    print("=" * 80)
    
    def test_strategy_imports():
        # This will fail if strategy.py has import errors
        import core.strategy
        print("      ✅ strategy.py imports successfully")
        
        # Check if generate_signal function exists
        assert hasattr(core.strategy, 'generate_signal'), "generate_signal function not found"
        print("      ✅ generate_signal function exists")
    
    test_step("Strategy integration", test_strategy_imports)

# ============================================================================
# RUN ALL TESTS
# ============================================================================
print("\nStarting verification tests...")
print()

try:
    test_imports()
    test_ict_2022_model_logic()
    test_filters_logic()
    test_news_blocking()
    test_risk_management()
    test_config()
    test_integration()
except Exception as e:
    print(f"\n❌ CRITICAL ERROR: {e}")
    traceback.print_exc()

# ============================================================================
# RESULTS SUMMARY
# ============================================================================
print("\n" + "=" * 80)
print("  VERIFICATION RESULTS")
print("=" * 80)
print()

print(f"✅ PASSED: {len(results['passed'])} tests")
for test in results['passed']:
    print(f"   - {test}")

if results['failed']:
    print(f"\n❌ FAILED: {len(results['failed'])} tests")
    for test, error in results['failed']:
        print(f"   - {test}")
        print(f"     Error: {error}")

if results['warnings']:
    print(f"\n⚠️  WARNINGS: {len(results['warnings'])}")
    for warning in results['warnings']:
        print(f"   - {warning}")

print("\n" + "=" * 80)

# Final verdict
if results['failed']:
    print("❌ IMPLEMENTATION HAS ERRORS - Fix failed tests before running bot")
    print("=" * 80)
    sys.exit(1)
elif results['warnings']:
    print("⚠️  IMPLEMENTATION COMPLETE WITH WARNINGS - Review warnings")
    print("=" * 80)
    sys.exit(0)
else:
    print("✅ IMPLEMENTATION VERIFIED - All tests passed!")
    print("\nYou can now run the bot:")
    print("  python main.py --steps 60")
    print("=" * 80)
    sys.exit(0)
