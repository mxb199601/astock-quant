import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "cache", "kline_sina")
CACHE_LEGACY = os.path.join(ROOT, "cache", "kline")
OUTPUT = os.path.join(ROOT, "output")
FUND_PATH = os.path.join(CACHE, "_fundamentals.json")
META_PATH = os.path.join(CACHE, "_meta.json")

os.makedirs(CACHE, exist_ok=True)
os.makedirs(OUTPUT, exist_ok=True)
