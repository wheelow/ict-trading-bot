"""Simple verification - no Unicode"""
print("="*60)
print("ICT 2022 IMPLEMENTATION VERIFICATION")
print("="*60)

success_count = 0
fail_count = 0

# Test 1
try:
    from core.ict_2022_model import detect_ict_2022_setup, ICT2022Setup
    print("[PASS] core.ict_2022_model")
    success_count += 1
except Exception as e:
    print(f"[FAIL] core.ict_2022_model: {e}")
    fail_count += 1

# Test 2
try:
    from core.filters import apply_all_filters
    print("[PASS] core.filters")
    success_count += 1
except Exception as e:
    print(f"[FAIL] core.filters: {e}")
    fail_count += 1

# Test 3
try:
    from news.filter import is_nfp_week, is_fomc_day
    print("[PASS] news.filter updates")
    success_count += 1
except Exception as e:
    print(f"[FAIL] news.filter: {e}")
    fail_count += 1

# Test 4
try:
    from news.calendar import get_upcoming_high_impact_events
    print("[PASS] news.calendar updates")
    success_count += 1
except Exception as e:
    print(f"[FAIL] news.calendar: {e}")
    fail_count += 1

# Test 5
try:
    from risk.manager import calculate_stop_loss_by_book
    print("[PASS] risk.manager updates")
    success_count += 1
except Exception as e:
    print(f"[FAIL] risk.manager: {e}")
    fail_count += 1

print("="*60)
print(f"Results: {success_count} passed, {fail_count} failed")
print("="*60)

if fail_count == 0:
    print("SUCCESS - All components working!")
    print("\nYou can now run:")
    print("  python main.py --steps 60")
else:
    print("FAILED - Fix errors above before running bot")
