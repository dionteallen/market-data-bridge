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


@asynccontextmanager
async def lifespan(app):
    async with mcp.session_manager.run():
        yield


app = Starlette(routes=[], lifespan=lifespan)
app.mount("/mcp-server", mcp.streamable_http_app())


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PORT)
