import os

KALSHI_BASE_URL = os.getenv(
    "KALSHI_BASE_URL",
    "https://api.elections.kalshi.com/trade-api/v2",
)

ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "ALLOWED_ORIGINS",
        "https://kalshi-trading-platform-zeta.vercel.app,http://localhost:3000,http://127.0.0.1:5500",
    ).split(",")
    if origin.strip()
]

MLB_STRIKEOUT_PREFIX = os.getenv("MLB_STRIKEOUT_PREFIX", "KXMLBKS")
