import sys
sys.path.insert(0, "D:/量化平台V4/量化平台V3")
try:
    import main
    print("OK - main imported successfully")
except Exception as e:
    print(f"FAIL: {e}")
