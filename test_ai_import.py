import sys
sys.path.insert(0, "D:/量化平台V4")
try:
    from ai_engine import extract_features, analyze_market, match_strategy, decide_multiplier, run_ai_pipeline
    print("PASS - Import successful")
    print(f"  extract_features: {extract_features}")
    print(f"  analyze_market: {analyze_market}")
    print(f"  match_strategy: {match_strategy}")
    print(f"  decide_multiplier: {decide_multiplier}")
    print(f"  run_ai_pipeline: {run_ai_pipeline}")
except Exception as e:
    print(f"FAIL - {e}")
