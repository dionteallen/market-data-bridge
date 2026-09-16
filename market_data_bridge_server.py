"""
Market Data Bridge — real historical price bars for the Backtest &
Validation Lab (Dynamic Grok Bot Desk), crypto edition
"""

import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
from starlette.applications import Starlette
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

PORT = int(os.environ.get("PORT", "10000"))
COINBASE_BASE = "https://api.exchange.coinbase.com"
VALID_GRANULARITIES = {60, 300, 900, 3600, 21600, 86400}

mcp = FastMCP(
    "market-data-bridge-crypto",
    stateless_http=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    ),
)
mcp.settings.streamable_http_path = "/"


@mcp.tool()
def get_historical_candles(product_id: str, granularity: int, start: str, end: str) -> dict:
    """
    Real historical OHLCV candles for one crypto trading pair, from
    Coinbase's real public market data — never invented, never estimated.

    product_id: trading pair, e.g. "BTC-USD", "ETH-USD"
    granularity: seconds per candle — MUST be one of 60, 300, 900, 3600,
                 21600, 86400 (1min/5min/15min/1hour/6hour/1day)
    start: ISO 8601 timestamp, e.g. "2026-06-01T00:00:00Z"
    end: ISO 8601 timestamp, e.g. "2026-09-01T00:00:00Z"

    A single request returns at most 300 candles (a real Coinbase limit,
    not ours). If your requested range/granularity would exceed that,
    this returns an explicit error telling you to split the range into
    multiple calls — it never silently truncates or estimates.
    """
    if granularity not in VALID_GRANULARITIES:
        return {
            "status": "error",
            "detail": f"granularity must be one of {sorted(VALID_GRANULARITIES)} seconds; got {granularity}",
        }

    url = f"{COINBASE_BASE}/products/{product_id}/candles"
    params = {"granularity": granularity, "start": start, "end": end}
    with httpx.Client(timeout=30) as client:
        resp = client.get(url, params=params)

    if resp.status_code != 200:
        return {"status": "error", "http_status": resp.status_code, "detail": resp.text}

    raw = resp.json()
    candles = [
        {"time": c[0], "low": c[1], "high": c[2], "open": c[3], "close": c[4], "volume": c[5]}
        for c in raw
    ]
    return {
        "status": "ok",
        "product_id": product_id,
        "granularity": granularity,
        "candle_count": len(candles),
        "candles": candles,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "note": "Max 300 candles per Coinbase request. If you expected more, split your start/end range and call again.",
    }


@mcp.tool()
def get_ticker(product_id: str) -> dict:
    """
    Real-time snapshot for a crypto trading pair from Coinbase: last
    trade price, best bid/ask, and 24h volume.

    product_id: trading pair, e.g. "BTC-USD"
    """
    url = f"{COINBASE_BASE}/products/{product_id}/ticker"
    with httpx.Client(timeout=15) as client:
        resp = client.get(url)
    if resp.status_code != 200:
        return {"status": "error", "http_status": resp.status_code, "detail": resp.text}
    return {"status": "ok", "product_id": product_id, **resp.json()}

# Published Coinbase Advanced Trade fee schedule (public, static — not
# account-verified). Source: Coinbase's own published tier table, as
# compiled from public documentation current as of September 2026.
# This is a best-effort public schedule, not a live authenticated feed —
# Coinbase's actual account-tier endpoint requires authentication and
# is out of scope until a real trading connection exists (Phase 2).
_FEE_TIERS = [
    # (min_30d_volume_usd, maker_rate, taker_rate)
    (0, 0.0060, 0.0120),
    (10_000, 0.0025, 0.0040),
    (50_000, 0.0015, 0.0025),
]


@mcp.tool()
def get_fee_tier(assumed_30d_volume_usd: float = 0) -> dict:
    """
    Coinbase Advanced Trade's PUBLIC, STATIC fee schedule — not read from
    your actual account, since that requires authentication this bridge
    does not have yet (Phase 2, once a real trading connection exists).

    assumed_30d_volume_usd: the 30-day trading volume to look up a tier
    for. Defaults to 0 (the lowest tier), which is realistic for a very
    small account.

    This is intentionally conservative and clearly labeled — never treat
    this as your confirmed real account rate. Above $50,000 in volume,
    this tool does not have a verified tier and says so explicitly
    rather than guessing.
    """
    tier = None
    for min_vol, maker, taker in _FEE_TIERS:
        if assumed_30d_volume_usd >= min_vol:
            tier = (min_vol, maker, taker)
    if tier is None:
        return {"status": "error", "detail": "no tier found"}

    min_vol, maker, taker = tier
    above_verified_range = assumed_30d_volume_usd >= 50_000
    return {
        "status": "ok",
        "source": "public_static_schedule",
        "verified_against_account": False,
        "tier_min_30d_volume_usd": min_vol,
        "maker_rate": maker,
        "taker_rate": taker,
        "round_trip_worst_case_rate": round(taker * 2, 6),
        "warning": (
            "Above $50,000 in 30-day volume this schedule is not "
            "independently verified — confirm directly with Coinbase "
            "before relying on it."
            if above_verified_range
            else None
        ),
    }
@asynccontextmanager
async def lifespan(app):
    async with mcp.session_manager.run():
        yield


app = Starlette(routes=[], lifespan=lifespan)
app.mount("/mcp-server", mcp.streamable_http_app())


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PORT)
