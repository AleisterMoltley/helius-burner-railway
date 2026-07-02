"""
Solana JSON-RPC proxy — same shape as rush.trading's /api/solana-rpc.

Helius API key stays server-side (HELIUS_KEY / HELIUS_KEYS). Optional RPC_PROXY_SECRET
gates access so the endpoint is not fully public.
"""

from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from typing import Any, Optional

import aiohttp
from fastapi import Request
from fastapi.responses import JSONResponse, Response

HELIUS_RPC = os.getenv("HELIUS_RPC_URL", "https://mainnet.helius-rpc.com")

# Methods that are cheap / normal for wallet apps. Empty ALLOW_METHODS = allow all.
DEFAULT_ALLOW = frozenset({
    "getHealth",
    "getVersion",
    "getSlot",
    "getBalance",
    "getAccountInfo",
    "getLatestBlockhash",
    "getSignatureStatuses",
    "getTransaction",
    "simulateTransaction",
    "sendTransaction",
    "getTokenAccountsByOwner",
    "getParsedTokenAccountsByOwner",
    "getMinimumBalanceForRentExemption",
    "getEpochInfo",
    "getBlockHeight",
    "getRecentPrioritizationFees",
    "getFeeForMessage",
})

BLOCKED_ALWAYS = frozenset({
    "getProgramAccounts",  # expensive; use Helius burner dashboard if you need to burn quota
})

_rate: dict[str, list[float]] = defaultdict(list)
RATE_WINDOW_S = 60
RATE_MAX = int(os.getenv("RPC_RATE_LIMIT_PER_MIN", "120"))


def helius_key() -> Optional[str]:
    single = (os.getenv("HELIUS_KEY") or "").strip()
    if single:
        return single
    keys = (os.getenv("HELIUS_KEYS") or "").strip()
    if keys:
        return keys.split(",")[0].strip()
    return None


def proxy_enabled() -> bool:
    if os.getenv("RPC_PROXY_ENABLED", "1").strip().lower() in ("0", "false", "no"):
        return False
    return helius_key() is not None


def allowed_methods() -> Optional[frozenset[str]]:
    raw = (os.getenv("RPC_ALLOW_METHODS") or "").strip()
    if not raw or raw == "*":
        return None
    return frozenset(m.strip() for m in raw.split(",") if m.strip())


def check_auth(request: Request) -> Optional[JSONResponse]:
    secret = (os.getenv("RPC_PROXY_SECRET") or "").strip()
    if not secret:
        return None
    auth = request.headers.get("authorization", "")
    if auth == f"Bearer {secret}":
        return None
    if request.headers.get("x-rpc-key") == secret:
        return None
    return JSONResponse({"error": "unauthorized"}, status_code=401)


def check_rate(ip: str) -> Optional[JSONResponse]:
    now = time.time()
    window = _rate[ip]
    window[:] = [t for t in window if now - t < RATE_WINDOW_S]
    if len(window) >= RATE_MAX:
        return JSONResponse({"error": "rate limit exceeded"}, status_code=429)
    window.append(now)
    return None


async def forward_rpc(body: bytes) -> tuple[int, bytes, str]:
    key = helius_key()
    if not key:
        return 503, b'{"error":"rpc proxy not configured"}', "application/json"

    url = f"{HELIUS_RPC}/?api-key={key}"
    timeout = aiohttp.ClientTimeout(total=30, connect=8, sock_read=25)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, data=body, headers={"Content-Type": "application/json"}) as resp:
            data = await resp.read()
            ctype = resp.headers.get("Content-Type", "application/json")
            return resp.status, data, ctype.split(";")[0].strip()


def validate_payload(raw: bytes) -> Optional[JSONResponse]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return JSONResponse({"error": "invalid json"}, status_code=400)

    allow = allowed_methods()
    items = payload if isinstance(payload, list) else [payload]
    for item in items:
        if not isinstance(item, dict):
            continue
        method = item.get("method")
        if not isinstance(method, str):
            continue
        if method in BLOCKED_ALWAYS:
            return JSONResponse(
                {"jsonrpc": "2.0", "error": {"code": -32601, "message": f"method blocked: {method}"}, "id": item.get("id")},
                status_code=403,
            )
        if allow is not None and method not in allow:
            return JSONResponse(
                {"jsonrpc": "2.0", "error": {"code": -32601, "message": f"method not allowed: {method}"}, "id": item.get("id")},
                status_code=403,
            )
    return None


async def handle_solana_rpc(request: Request) -> Response:
    if not proxy_enabled():
        return JSONResponse({"error": "rpc proxy disabled — set HELIUS_KEY"}, status_code=503)

    denied = check_auth(request)
    if denied:
        return denied

    ip = request.client.host if request.client else "unknown"
    limited = check_rate(ip)
    if limited:
        return limited

    raw = await request.body()
    if len(raw) > 256_000:
        return JSONResponse({"error": "payload too large"}, status_code=413)

    bad = validate_payload(raw)
    if bad:
        return bad

    status, data, ctype = await forward_rpc(raw)
    return Response(content=data, status_code=status, media_type=ctype)