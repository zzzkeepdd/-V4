import sys
print("python:", sys.executable)
print("paths:", sys.path[:3])
try:
    import ccxt
    print("ccxt:", ccxt.__version__)
except ImportError:
    print("ccxt NOT FOUND")
