#!/usr/bin/env python3
"""
Helius Quota Burner Dashboard - OBLITERATE EDITION
==================================================

Maximum credit destruction via 100-credit Wallet API, Enhanced Transactions,
and getTransactionsForAddress endpoints.

ONLY use API keys you own or have explicit permission to load-test.
"""

import asyncio
import aiohttp
import os
import random
import time
from collections import deque
from typing import List, Dict, Any, Callable, Awaitable, Tuple, Optional

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
import uvicorn

# ==================== CONFIG & STATE ====================

HELIUS_RPC = "https://mainnet.helius-rpc.com"
HELIUS_API = "https://api.helius.xyz"
MAX_CONCURRENCY = int(os.getenv("MAX_CONCURRENCY", "500"))
# Railway containers choke on hundreds of bare asyncio tasks — cap task count,
# use a semaphore for actual in-flight HTTP concurrency instead.
MAX_WORKER_TASKS = int(os.getenv("MAX_WORKER_TASKS", "48"))
DEFAULT_OBLITERATE_CONCURRENCY = int(os.getenv("OBLITERATE_CONCURRENCY", "200"))

KNOWN_PROGRAMS = [
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
    "metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s",
    "BGUMAp9Gq7iTEuizy4pqaxsTyUCBK68MDfK752saRPUY",
    "cndy3Z4yapfJBmL3ShUp5exZKqR3z33thTzeNMm2gRZ",
    "M2mx93ekt1fmXSVkTrUL9xVFHkmME8HTUi5Cyc5aF7K",
]

# High-activity wallets — better responses for history/GTF/wallet API
ACTIVE_WALLETS = [
    "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1",
    "86xCnPeV69n6t3DnyGvkKobf9FdN2H9oiVDdaMpo2MMY",
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4",
    "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM",
    "So11111111111111111111111111111111111111112",
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
]

KNOWN_WALLETS = ACTIVE_WALLETS + [
    "11111111111111111111111111111111",
]

KNOWN_MINTS = [
    "So11111111111111111111111111111111111111112",
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
]

# Fallback signatures for enhanced transaction parsing
FALLBACK_SIGNATURES = [
    "4jzQxVTaJ4Fe4Fct9y1aaT9hmVyEjpCqE2bL8JMnuLZbzHZwaL4kZZvNEZ6bEj6fGmiAdCPjmNQHCf8v994PAgDf",
    "5VERv8NMvzbJMEkVzxMoZrdUm9QRTebh5UKdkdvELVLZWr5jpFfGjkAPQouRXXjws3XkmZvwU2YBSaNxa6KJA3",
    "2nBhEBYYvfaAe16UMNqRHre4YNSskvuYgx3M6E4JP1oDYvZEJHvoPzyUidNgX5e8ERochnyq1wYWpa5f9KUF9",
]

sig_pool: deque = deque(maxlen=10000)
inflight_sem: Optional[asyncio.Semaphore] = None

state: Dict[str, Any] = {
    "keys": [],
    "concurrency": 150,
    "mode": "expensive",
    "running": False,
    "start_time": 0.0,
    "stats": {
        "total": 0,
        "success": 0,
        "rate_limited": 0,
        "errors": 0,
        "exceptions": 0,
        "current_rps": 0.0,
        "credits_burned": 0,
        "credit_rps": 0.0,
    },
    "recent_logs": deque(maxlen=80),
    "stop_event": None,
    "tasks": [],
    "session": None,
    "reporter_task": None,
    "harvester_task": None,
}

OPERATION_POOLS = {
    "basic": [
        ("get_slot", 3),
        ("get_recent_block", 1),
    ],
    "das-heavy": [
        ("get_asset", 2),
        ("get_assets_by_owner", 4),
        ("search_assets", 6),
    ],
    "expensive": [
        ("get_program_accounts", 7),
        ("get_signatures_for_address", 3),
        ("get_recent_block", 2),
    ],
    "mixed": [
        ("get_slot", 1),
        ("get_recent_block", 2),
        ("get_program_accounts", 5),
        ("get_assets_by_owner", 5),
        ("search_assets", 6),
        ("get_asset", 2),
        ("get_signatures_for_address", 3),
    ],
    "nuke": [
        ("get_program_accounts", 12),
        ("get_assets_by_owner", 8),
        ("search_assets", 9),
        ("get_signatures_for_address", 5),
        ("get_recent_block", 4),
        ("get_asset", 2),
        ("get_slot", 1),
    ],
    "obliterate": [
        # 100-credit endpoints — maximum destruction
        ("enhanced_transactions", 18),
        ("wallet_balances", 14),
        ("wallet_history", 14),
        ("wallet_transfers", 12),
        ("wallet_batch_identity", 10),
        ("wallet_identity", 8),
        ("get_transactions_for_address", 16),
        ("get_program_accounts", 4),
        ("search_assets", 2),
        ("get_assets_by_owner", 2),
    ],
}

# Helius credit cost per successful call
CREDIT_COSTS: Dict[str, int] = {
    "get_slot": 1,
    "get_recent_block": 2,
    "get_program_accounts": 10,
    "get_assets_by_owner": 10,
    "search_assets": 10,
    "get_asset": 10,
    "get_signatures_for_address": 1,
    "wallet_balances": 100,
    "wallet_history": 100,
    "wallet_transfers": 100,
    "wallet_identity": 100,
    "wallet_batch_identity": 100,
    "enhanced_transactions": 100,
    "get_transactions_for_address": 100,
}

AGGRESSIVE_MODES = frozenset({"nuke", "obliterate"})

# ==================== BURN OPERATIONS ====================

BurnResult = Tuple[int, str]  # (http_status, op_name)


async def do_get_slot(session: aiohttp.ClientSession, key: str) -> BurnResult:
    payload = {"jsonrpc": "2.0", "id": random.randint(1, 10**9), "method": "getSlot"}
    url = f"{HELIUS_RPC}/?api-key={key}"
    async with session.post(url, json=payload) as resp:
        return resp.status, "get_slot"


async def do_get_recent_block(session: aiohttp.ClientSession, key: str) -> BurnResult:
    url = f"{HELIUS_RPC}/?api-key={key}"
    slot_payload = {"jsonrpc": "2.0", "id": 1, "method": "getSlot"}
    async with session.post(url, json=slot_payload) as r:
        data = await r.json()
        slot = data.get("result", 0)

    block_payload = {
        "jsonrpc": "2.0",
        "id": random.randint(1, 10**9),
        "method": "getBlock",
        "params": [max(0, slot - random.randint(1, 50)), {
            "encoding": "json",
            "maxSupportedTransactionVersion": 0,
            "transactionDetails": "full",
            "rewards": True,
        }],
    }
    async with session.post(url, json=block_payload) as resp:
        return resp.status, "get_recent_block"


async def do_get_program_accounts(session: aiohttp.ClientSession, key: str) -> BurnResult:
    program = random.choice(KNOWN_PROGRAMS)
    payload = {
        "jsonrpc": "2.0",
        "id": random.randint(1, 10**9),
        "method": "getProgramAccounts",
        "params": [program, {"encoding": "base64", "commitment": "confirmed"}],
    }
    url = f"{HELIUS_RPC}/?api-key={key}"
    async with session.post(url, json=payload) as resp:
        return resp.status, "get_program_accounts"


async def do_get_assets_by_owner(session: aiohttp.ClientSession, key: str) -> BurnResult:
    owner = random.choice(KNOWN_WALLETS + KNOWN_PROGRAMS)
    payload = {
        "jsonrpc": "2.0",
        "id": random.randint(1, 10**9),
        "method": "getAssetsByOwner",
        "params": {
            "ownerAddress": owner,
            "page": random.randint(1, 3),
            "limit": random.randint(100, 1000),
        },
    }
    url = f"{HELIUS_RPC}/?api-key={key}"
    async with session.post(url, json=payload) as resp:
        return resp.status, "get_assets_by_owner"


async def do_search_assets(session: aiohttp.ClientSession, key: str) -> BurnResult:
    payload = {
        "jsonrpc": "2.0",
        "id": random.randint(1, 10**9),
        "method": "searchAssets",
        "params": {
            "page": 1,
            "limit": random.randint(100, 1000),
            "options": {"showCollectionMetadata": True},
            **random.choice([
                {"interface": "FungibleToken"},
                {"interface": "V1_NFT"},
                {"creatorAddress": random.choice(KNOWN_PROGRAMS)},
                {},
            ]),
        },
    }
    url = f"{HELIUS_RPC}/?api-key={key}"
    async with session.post(url, json=payload) as resp:
        return resp.status, "search_assets"


async def do_get_asset(session: aiohttp.ClientSession, key: str) -> BurnResult:
    mint = random.choice(KNOWN_MINTS)
    payload = {
        "jsonrpc": "2.0",
        "id": random.randint(1, 10**9),
        "method": "getAsset",
        "params": {"id": mint},
    }
    url = f"{HELIUS_RPC}/?api-key={key}"
    async with session.post(url, json=payload) as resp:
        return resp.status, "get_asset"


async def do_get_signatures_for_address(session: aiohttp.ClientSession, key: str) -> BurnResult:
    addr = random.choice(ACTIVE_WALLETS)
    payload = {
        "jsonrpc": "2.0",
        "id": random.randint(1, 10**9),
        "method": "getSignaturesForAddress",
        "params": [addr, {"limit": 1000}],
    }
    url = f"{HELIUS_RPC}/?api-key={key}"
    async with session.post(url, json=payload) as resp:
        if resp.status == 200:
            try:
                data = await resp.json()
                for entry in data.get("result") or []:
                    sig = entry.get("signature")
                    if sig:
                        sig_pool.append(sig)
            except Exception:
                pass
        return resp.status, "get_signatures_for_address"


async def do_wallet_balances(session: aiohttp.ClientSession, key: str) -> BurnResult:
    wallet = random.choice(ACTIVE_WALLETS)
    url = f"{HELIUS_API}/v1/wallet/{wallet}/balances?api-key={key}"
    async with session.get(url) as resp:
        return resp.status, "wallet_balances"


async def do_wallet_history(session: aiohttp.ClientSession, key: str) -> BurnResult:
    wallet = random.choice(ACTIVE_WALLETS)
    url = f"{HELIUS_API}/v1/wallet/{wallet}/history?api-key={key}&limit=100"
    async with session.get(url) as resp:
        return resp.status, "wallet_history"


async def do_wallet_transfers(session: aiohttp.ClientSession, key: str) -> BurnResult:
    wallet = random.choice(ACTIVE_WALLETS)
    url = f"{HELIUS_API}/v1/wallet/{wallet}/transfers?api-key={key}&limit=100"
    async with session.get(url) as resp:
        return resp.status, "wallet_transfers"


async def do_wallet_identity(session: aiohttp.ClientSession, key: str) -> BurnResult:
    wallet = random.choice(ACTIVE_WALLETS)
    url = f"{HELIUS_API}/v1/wallet/{wallet}/identity?api-key={key}"
    async with session.get(url) as resp:
        return resp.status, "wallet_identity"


async def do_wallet_batch_identity(session: aiohttp.ClientSession, key: str) -> BurnResult:
    batch = random.sample(ACTIVE_WALLETS, min(100, len(ACTIVE_WALLETS)))
    while len(batch) < 20:
        batch.extend(ACTIVE_WALLETS)
    batch = batch[:100]
    url = f"{HELIUS_API}/v1/wallet/batch-identity?api-key={key}"
    payload = {"addresses": batch}
    async with session.post(url, json=payload) as resp:
        return resp.status, "wallet_batch_identity"


async def do_enhanced_transactions(session: aiohttp.ClientSession, key: str) -> BurnResult:
    # Helius rejects duplicate signatures in a batch (HTTP 400).
    if len(sig_pool) < 10:
        return 503, "enhanced_transactions"

    seen: set = set()
    sigs: List[str] = []
    while sig_pool and len(sigs) < 100:
        sig = sig_pool.popleft()
        if sig not in seen:
            seen.add(sig)
            sigs.append(sig)

    if len(sigs) < 1:
        return 503, "enhanced_transactions"

    url = f"{HELIUS_API}/v0/transactions?api-key={key}"
    payload = {"transactions": sigs}
    async with session.post(url, json=payload) as resp:
        return resp.status, "enhanced_transactions"


async def do_get_transactions_for_address(session: aiohttp.ClientSession, key: str) -> BurnResult:
    wallet = random.choice(ACTIVE_WALLETS)
    payload = {
        "jsonrpc": "2.0",
        "id": random.randint(1, 10**9),
        "method": "getTransactionsForAddress",
        "params": [
            wallet,
            {
                "transactionDetails": "full",
                "limit": 1000,
                "sortOrder": "desc",
            },
        ],
    }
    url = f"{HELIUS_RPC}/?api-key={key}"
    async with session.post(url, json=payload) as resp:
        if resp.status == 200:
            try:
                data = await resp.json()
                for tx in data.get("result") or []:
                    sig = tx.get("signature") or tx.get("transaction", {}).get("signatures", [None])[0]
                    if sig:
                        sig_pool.append(sig)
            except Exception:
                pass
        return resp.status, "get_transactions_for_address"


OP_MAP: Dict[str, Callable[[aiohttp.ClientSession, str], Awaitable[BurnResult]]] = {
    "get_slot": do_get_slot,
    "get_recent_block": do_get_recent_block,
    "get_program_accounts": do_get_program_accounts,
    "get_assets_by_owner": do_get_assets_by_owner,
    "search_assets": do_search_assets,
    "get_asset": do_get_asset,
    "get_signatures_for_address": do_get_signatures_for_address,
    "wallet_balances": do_wallet_balances,
    "wallet_history": do_wallet_history,
    "wallet_transfers": do_wallet_transfers,
    "wallet_identity": do_wallet_identity,
    "wallet_batch_identity": do_wallet_batch_identity,
    "enhanced_transactions": do_enhanced_transactions,
    "get_transactions_for_address": do_get_transactions_for_address,
}


def build_weighted_pool(mode: str) -> List[str]:
    pool = OPERATION_POOLS.get(mode, OPERATION_POOLS["mixed"])
    weighted = []
    for op_name, weight in pool:
        weighted.extend([op_name] * weight)
    return weighted


def record_result(status: int, op_name: str) -> None:
    stats = state["stats"]
    stats["total"] += 1
    if status == 200:
        stats["success"] += 1
        stats["credits_burned"] += CREDIT_COSTS.get(op_name, 1)
    elif status == 429:
        stats["rate_limited"] += 1
        if stats["rate_limited"] % 500 == 1:
            state["recent_logs"].append(
                f"[{time.strftime('%H:%M:%S')}] 429 on {op_name} (total 429s: {stats['rate_limited']})"
            )
    else:
        stats["errors"] += 1
        if stats["errors"] % 100 == 1:
            state["recent_logs"].append(
                f"[{time.strftime('%H:%M:%S')}] HTTP {status} on {op_name}"
            )


async def execute_burn_op(
    session: aiohttp.ClientSession,
    keys: List[str],
    weighted_ops: List[str],
    key_index: int,
) -> int:
    """Run one burn operation under the global semaphore."""
    global inflight_sem
    op_name = random.choice(weighted_ops)
    key = keys[key_index % len(keys)]

    try:
        async with inflight_sem:
            func = OP_MAP[op_name]
            status, op_name = await func(session, key)
        record_result(status, op_name)
        return status
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        state["stats"]["exceptions"] += 1
        state["stats"]["total"] += 1
        if state["stats"]["exceptions"] % 50 == 1:
            state["recent_logs"].append(
                f"[{time.strftime('%H:%M:%S')}] exception on {op_name}: {type(exc).__name__}"
            )
        return 0


async def worker(
    worker_id: int,
    session: aiohttp.ClientSession,
    keys: List[str],
    weighted_ops: List[str],
    stop_event: asyncio.Event,
    mode: str,
):
    key_index = worker_id
    calls_per_loop = 3 if mode == "nuke" else (2 if mode == "obliterate" else 1)
    backoff = 0.0

    while not stop_event.is_set() and keys:
        if backoff > 0:
            await asyncio.sleep(backoff)

        for _ in range(calls_per_loop):
            if stop_event.is_set():
                break

            try:
                status = await execute_burn_op(session, keys, weighted_ops, key_index)
                key_index += 1
                if status == 429:
                    backoff = min(0.1, backoff + 0.005)
                elif status == 200:
                    backoff = max(0.0, backoff - 0.002)
            except asyncio.CancelledError:
                break

        # Yield so FastAPI can still serve /api/stats while burning
        await asyncio.sleep(0.002 if mode in AGGRESSIVE_MODES else 0.01)


async def sig_harvester(
    session: aiohttp.ClientSession,
    keys: List[str],
    stop_event: asyncio.Event,
):
    """Cheap 1-credit calls to fill sig pool for 100-credit enhanced tx batches."""
    idx = 0
    while not stop_event.is_set() and keys:
        if len(sig_pool) >= 2000:
            await asyncio.sleep(0.5)
            continue

        key = keys[idx % len(keys)]
        idx += 1
        addr = random.choice(ACTIVE_WALLETS)
        payload = {
            "jsonrpc": "2.0",
            "id": random.randint(1, 10**9),
            "method": "getSignaturesForAddress",
            "params": [addr, {"limit": 1000}],
        }
        url = f"{HELIUS_RPC}/?api-key={key}"
        try:
            async with session.post(url, json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for entry in data.get("result") or []:
                        sig = entry.get("signature")
                        if sig:
                            sig_pool.append(sig)
                    state["stats"]["credits_burned"] += 1
                    state["stats"]["success"] += 1
                    state["stats"]["total"] += 1
                elif resp.status == 429:
                    state["stats"]["rate_limited"] += 1
                    state["stats"]["total"] += 1
                    await asyncio.sleep(0.1)
                else:
                    state["stats"]["errors"] += 1
                    state["stats"]["total"] += 1
        except asyncio.CancelledError:
            break
        except Exception:
            state["stats"]["exceptions"] += 1

        await asyncio.sleep(0.02)


async def stats_reporter(stop_event: asyncio.Event, start_time: float):
    last_total = 0
    last_credits = 0
    while not stop_event.is_set():
        await asyncio.sleep(2.0)
        total = state["stats"]["total"]
        credits = state["stats"]["credits_burned"]
        rps = (total - last_total) / 2.0
        cps = (credits - last_credits) / 2.0
        last_total = total
        last_credits = credits
        state["stats"]["current_rps"] = round(rps, 1)
        state["stats"]["credit_rps"] = round(cps, 1)

        if rps > 0:
            state["recent_logs"].append(
                f"[{time.strftime('%H:%M:%S')}] {rps:.0f} req/s | "
                f"{cps:,.0f} cr/s | burned: {credits:,.0f} | "
                f"429s: {state['stats']['rate_limited']}"
            )


async def start_burner() -> Tuple[bool, str]:
    if state["running"]:
        return False, "Already running — stop first"
    if not state["keys"]:
        return False, "No keys loaded — paste key and click Save Keys first"

    global inflight_sem
    state["running"] = True
    state["start_time"] = time.time()
    state["stop_event"] = asyncio.Event()
    state["stats"] = {
        "total": 0,
        "success": 0,
        "rate_limited": 0,
        "errors": 0,
        "exceptions": 0,
        "current_rps": 0.0,
        "credits_burned": 0,
        "credit_rps": 0.0,
    }
    sig_pool.clear()
    state["recent_logs"].clear()
    state["recent_logs"].append("=== BURN STARTED ===")

    concurrency = max(1, min(MAX_CONCURRENCY, int(state["concurrency"])))
    inflight_sem = asyncio.Semaphore(concurrency)
    connector = aiohttp.TCPConnector(
        limit=concurrency + 10,
        limit_per_host=concurrency + 10,
        ttl_dns_cache=300,
        enable_cleanup_closed=True,
    )
    timeout = aiohttp.ClientTimeout(total=45, connect=8, sock_read=40)
    session = aiohttp.ClientSession(connector=connector, timeout=timeout)
    state["session"] = session

    weighted = build_weighted_pool(state["mode"])
    mode = state["mode"]
    worker_tasks = min(MAX_WORKER_TASKS, max(8, concurrency // 4))

    tasks = []
    for i in range(worker_tasks):
        t = asyncio.create_task(
            worker(i, session, state["keys"], weighted, state["stop_event"], mode)
        )
        tasks.append(t)

    reporter = asyncio.create_task(stats_reporter(state["stop_event"], state["start_time"]))
    state["tasks"] = tasks
    state["reporter_task"] = reporter

    if mode == "obliterate":
        harvester = asyncio.create_task(
            sig_harvester(session, state["keys"], state["stop_event"])
        )
        state["harvester_task"] = harvester
        tasks.append(harvester)

    state["recent_logs"].append(
        f"Burner started: {worker_tasks} tasks, {concurrency} in-flight, "
        f"mode={mode}, keys={len(state['keys'])}"
    )
    return True, f"Started ({concurrency} concurrent, {worker_tasks} tasks)"


async def stop_burner():
    if not state["running"]:
        return

    if state["stop_event"]:
        state["stop_event"].set()

    for task in state["tasks"]:
        task.cancel()

    if state["reporter_task"]:
        state["reporter_task"].cancel()

    if state["harvester_task"]:
        state["harvester_task"].cancel()

    if state["session"]:
        await state["session"].close()

    state["running"] = False
    state["tasks"] = []
    state["reporter_task"] = None
    state["harvester_task"] = None
    state["session"] = None
    state["recent_logs"].append("=== BURN STOPPED ===")


# ==================== FASTAPI APP ====================

app = FastAPI(title="Helius Quota Burner Dashboard")
templates = Jinja2Templates(directory=".")


@app.on_event("startup")
async def startup_event():
    env_keys = os.getenv("HELIUS_KEYS") or os.getenv("HELIUS_KEY", "")
    if env_keys:
        state["keys"] = [k.strip() for k in env_keys.split(",") if k.strip()]

    env_conc = os.getenv("CONCURRENCY")
    if env_conc:
        state["concurrency"] = int(env_conc)

    env_mode = os.getenv("MODE")
    if env_mode in OPERATION_POOLS:
        state["mode"] = env_mode

    state["recent_logs"].append("Dashboard ready. OBLITERATE mode available.")


@app.on_event("shutdown")
async def shutdown_event():
    await stop_burner()


@app.get("/api/stats")
async def get_stats():
    elapsed = time.time() - state["start_time"] if state["running"] else 0
    credits = state["stats"].get("credits_burned", 0)
    credits_per_hour = (credits / elapsed * 3600) if elapsed > 0 else 0
    return JSONResponse({
        "running": state["running"],
        "keys_count": len(state["keys"]),
        "concurrency": state["concurrency"],
        "mode": state["mode"],
        "elapsed": round(elapsed, 1),
        "sig_pool_size": len(sig_pool),
        "credits_per_hour": round(credits_per_hour),
        "stats": state["stats"],
        "recent_logs": list(state["recent_logs"])[-20:],
    })


@app.post("/api/keys")
async def set_keys(keys_text: str = Form(...)):
    new_keys = parse_keys_text(keys_text)
    state["keys"] = new_keys
    state["recent_logs"].append(f"Updated keys ({len(new_keys)} total)")
    return {"ok": True, "count": len(new_keys)}


@app.post("/api/config")
async def set_config(concurrency: int = Form(...), mode: str = Form(...)):
    if mode in OPERATION_POOLS:
        state["mode"] = mode
    state["concurrency"] = max(1, min(MAX_CONCURRENCY, concurrency))
    state["recent_logs"].append(
        f"Config: {state['concurrency']} workers, mode={state['mode']}"
    )
    return {"ok": True}


def parse_keys_text(keys_text: str) -> List[str]:
    new_keys = []
    for line in keys_text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        key = None
        if "api-key=" in line:
            try:
                key = line.split("api-key=")[1].split("&")[0].split(" ")[0].strip()
            except Exception:
                pass
        else:
            key = line
        if key and len(key) > 10:
            new_keys.append(key)
    seen = set()
    return [k for k in new_keys if not (k in seen or seen.add(k))]


@app.post("/api/start")
async def api_start(keys_text: str = Form(default="")):
    if keys_text.strip():
        parsed = parse_keys_text(keys_text)
        if parsed:
            state["keys"] = parsed
    ok, message = await start_burner()
    return {"ok": ok, "message": message}


@app.post("/api/stop")
async def api_stop():
    await stop_burner()
    return {"ok": True, "message": "Burner stopped"}


@app.post("/api/obliterate")
async def api_obliterate(keys_text: str = Form(default="")):
    """One-click max destruction: obliterate mode + high concurrency."""
    await stop_burner()
    if keys_text.strip():
        parsed = parse_keys_text(keys_text)
        if parsed:
            state["keys"] = parsed
    state["mode"] = "obliterate"
    state["concurrency"] = DEFAULT_OBLITERATE_CONCURRENCY
    ok, message = await start_burner()
    return {"ok": ok, "message": message}


@app.post("/api/nuke")
async def api_nuke(keys_text: str = Form(default="")):
    await stop_burner()
    if keys_text.strip():
        parsed = parse_keys_text(keys_text)
        if parsed:
            state["keys"] = parsed
    state["mode"] = "nuke"
    state["concurrency"] = 200
    ok, message = await start_burner()
    return {"ok": ok, "message": message}


# ==================== DASHBOARD UI ====================

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>HELIUS BURNER • OBLITERATE</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600&amp;family=Space+Grotesk:wght@500;600&amp;display=swap');
        body { font-family: 'Inter', system-ui, sans-serif; }
        .font-display { font-family: 'Space Grotesk', 'Inter', sans-serif; }
        .stat-card { transition: all 0.1s ease; }
        .log-line { font-family: ui-monospace, monospace; font-size: 0.75rem; }
    </style>
</head>
<body class="bg-zinc-950 text-zinc-200">
    <div class="max-w-6xl mx-auto p-6">
        <div class="flex items-center justify-between mb-8">
            <div class="flex items-center gap-3">
                <div class="w-10 h-10 bg-red-600 rounded-2xl flex items-center justify-center text-2xl">💀</div>
                <div>
                    <h1 class="text-4xl font-semibold tracking-tighter font-display">HELIUS BURNER</h1>
                    <p class="text-red-500 text-sm -mt-1">OBLITERATE — 100-credit endpoints</p>
                </div>
            </div>
            <div id="status-badge" class="px-4 py-1.5 rounded-3xl text-sm font-medium flex items-center gap-2 border border-zinc-800 bg-zinc-900">
                <div class="w-2 h-2 rounded-full bg-zinc-500" id="status-dot"></div>
                <span id="status-text">STOPPED</span>
            </div>
        </div>

        <div class="mb-6 p-4 bg-red-950 border border-red-900 rounded-3xl text-red-400 text-sm">
            <strong>OBLITERATE MODE:</strong> Wallet API + Enhanced Transactions + getTransactionsForAddress
            (100 credits each). Only use keys you own.
        </div>

        <div class="grid grid-cols-1 lg:grid-cols-12 gap-6">
            <div class="lg:col-span-5 bg-zinc-900 border border-zinc-800 rounded-3xl p-6">
                <h2 class="font-semibold text-lg mb-4">Controls</h2>

                <div class="mb-5">
                    <label class="block text-xs font-medium text-zinc-400 mb-1.5">HELIUS KEYS</label>
                    <textarea id="keys-text" rows="4"
                        class="w-full bg-zinc-950 border border-zinc-800 rounded-2xl p-3 text-sm font-mono resize-y"
                        placeholder="api-key per line or full URL"></textarea>
                    <button onclick="saveKeys()" class="mt-2 text-xs px-4 py-2 bg-zinc-800 hover:bg-zinc-700 rounded-2xl">Save Keys</button>
                </div>

                <div class="grid grid-cols-2 gap-4 mb-6">
                    <div>
                        <label class="block text-xs font-medium text-zinc-400 mb-1.5">CONCURRENCY</label>
                        <input type="number" id="concurrency" value="200"
                            class="w-full bg-zinc-950 border border-zinc-800 rounded-2xl p-3 text-lg font-semibold">
                        <input type="range" id="concurrency-slider" min="50" max="500" step="25" value="200"
                            class="w-full accent-red-600 mt-1"
                            oninput="document.getElementById('concurrency').value=this.value">
                    </div>
                    <div>
                        <label class="block text-xs font-medium text-zinc-400 mb-1.5">MODE</label>
                        <select id="mode" class="w-full bg-zinc-950 border border-zinc-800 rounded-2xl p-3 text-sm">
                            <option value="obliterate">💀 OBLITERATE (100cr/call)</option>
                            <option value="nuke">☢️ NUKE (10cr/call)</option>
                            <option value="expensive">EXPENSIVE</option>
                            <option value="mixed">MIXED</option>
                            <option value="das-heavy">DAS-HEAVY</option>
                            <option value="basic">BASIC</option>
                        </select>
                    </div>
                </div>

                <button onclick="applyConfig()" class="w-full mb-3 py-2.5 text-sm bg-zinc-800 hover:bg-zinc-700 rounded-2xl">Apply Config</button>

                <div class="flex gap-3 mb-3">
                    <button onclick="startBurn()" class="flex-1 py-3 font-semibold bg-red-600 hover:bg-red-500 rounded-3xl">START</button>
                    <button onclick="stopBurn()" class="flex-1 py-3 font-semibold bg-zinc-800 hover:bg-zinc-700 rounded-3xl">STOP</button>
                </div>

                <button onclick="obliterateMode()"
                    class="w-full py-6 text-xl font-bold bg-gradient-to-r from-purple-900 via-red-700 to-red-600 hover:from-purple-800 hover:to-red-500 rounded-3xl shadow-xl shadow-red-950 flex items-center justify-center gap-3">
                    <span class="text-3xl">💀</span>
                    <span>OBLITERATE — MAX CREDIT BURN</span>
                </button>
                <p class="text-[10px] text-center text-red-500/70 mt-1">200 concurrent + Wallet API + Enhanced TX + GTF (100 credits each)</p>

                <button onclick="nukeMode()" class="mt-2 w-full py-3 text-sm font-semibold bg-zinc-800 hover:bg-zinc-700 rounded-2xl">
                    ☢️ Legacy NUKE (10cr endpoints)
                </button>
            </div>

            <div class="lg:col-span-7 bg-zinc-900 border border-zinc-800 rounded-3xl p-6">
                <h2 class="font-semibold text-lg mb-4">LIVE STATS</h2>

                <div class="grid grid-cols-2 md:grid-cols-3 gap-4 mb-4">
                    <div class="stat-card bg-red-950/50 border border-red-900 rounded-2xl p-4 col-span-2 md:col-span-1">
                        <div class="text-xs text-red-400">CREDITS BURNED</div>
                        <div id="credits" class="text-3xl font-semibold tabular-nums text-red-400 mt-1">0</div>
                        <div class="text-[10px] text-zinc-500 mt-1"><span id="credit-rps">0</span> cr/s · <span id="credits-hour">0</span>/h</div>
                    </div>
                    <div class="stat-card bg-zinc-950 border border-zinc-800 rounded-2xl p-4">
                        <div class="text-xs text-zinc-500">REQUESTS/S</div>
                        <div id="rps" class="text-3xl font-semibold tabular-nums text-orange-400 mt-1">0</div>
                    </div>
                    <div class="stat-card bg-zinc-950 border border-zinc-800 rounded-2xl p-4">
                        <div class="text-xs text-zinc-500">SUCCESS (200)</div>
                        <div id="success" class="text-3xl font-semibold tabular-nums text-green-400 mt-1">0</div>
                    </div>
                    <div class="stat-card bg-zinc-950 border border-zinc-800 rounded-2xl p-4">
                        <div class="text-xs text-zinc-500">429 LIMITED</div>
                        <div id="rate-limited" class="text-3xl font-semibold tabular-nums text-orange-400 mt-1">0</div>
                    </div>
                    <div class="stat-card bg-zinc-950 border border-zinc-800 rounded-2xl p-4">
                        <div class="text-xs text-zinc-500">ERRORS</div>
                        <div id="errors" class="text-3xl font-semibold tabular-nums mt-1">0</div>
                    </div>
                    <div class="stat-card bg-zinc-950 border border-zinc-800 rounded-2xl p-4">
                        <div class="text-xs text-zinc-500">SIG POOL</div>
                        <div id="sig-pool" class="text-3xl font-semibold tabular-nums text-purple-400 mt-1">0</div>
                    </div>
                </div>

                <div class="flex flex-wrap items-center gap-4 text-sm">
                    <div><span class="text-zinc-500">Elapsed:</span> <span id="elapsed" class="font-mono font-semibold">0s</span></div>
                    <div><span class="text-zinc-500">Workers:</span> <span id="workers" class="font-semibold">0</span></div>
                    <div><span class="text-zinc-500">Keys:</span> <span id="keys-count" class="font-semibold">0</span></div>
                    <div id="mode-display" class="px-3 py-0.5 bg-red-900 rounded-2xl text-xs font-medium text-red-300"></div>
                </div>
            </div>

            <div class="lg:col-span-12 bg-zinc-900 border border-zinc-800 rounded-3xl p-6">
                <div class="flex justify-between items-center mb-3">
                    <h2 class="font-semibold">Burn Log</h2>
                    <button onclick="refreshStats()" class="text-xs px-3 py-1 bg-zinc-800 rounded-2xl hover:bg-zinc-700">Refresh</button>
                </div>
                <div id="log-container" class="bg-black border border-zinc-800 rounded-2xl p-3 h-64 overflow-auto font-mono text-xs text-zinc-400 space-y-0.5"></div>
            </div>
        </div>
    </div>

    <script>
        let pollInterval = null;

        function fmt(n) {
            if (n >= 1e6) return (n/1e6).toFixed(2) + 'M';
            if (n >= 1e3) return (n/1e3).toFixed(1) + 'K';
            return Math.round(n).toLocaleString();
        }

        async function refreshStats() {
            try {
                const res = await fetch('/api/stats');
                const data = await res.json();
                const s = data.stats;

                document.getElementById('credits').textContent = fmt(s.credits_burned || 0);
                document.getElementById('credit-rps').textContent = fmt(s.credit_rps || 0);
                document.getElementById('credits-hour').textContent = fmt(data.credits_per_hour || 0);
                document.getElementById('rps').textContent = (s.current_rps || 0).toFixed(1);
                document.getElementById('success').textContent = (s.success || 0).toLocaleString();
                document.getElementById('rate-limited').textContent = (s.rate_limited || 0).toLocaleString();
                document.getElementById('errors').textContent = ((s.errors || 0) + (s.exceptions || 0)).toLocaleString();
                document.getElementById('sig-pool').textContent = (data.sig_pool_size || 0).toLocaleString();
                document.getElementById('elapsed').textContent = data.elapsed + 's';
                document.getElementById('workers').textContent = data.concurrency;
                document.getElementById('keys-count').textContent = data.keys_count;
                document.getElementById('mode-display').textContent = data.mode.toUpperCase();

                const badge = document.getElementById('status-badge');
                const dot = document.getElementById('status-dot');
                const text = document.getElementById('status-text');

                if (data.running) {
                    badge.classList.add('!border-red-600', 'bg-red-950');
                    dot.classList.add('bg-red-500');
                    dot.classList.remove('bg-zinc-500');
                    text.textContent = data.mode === 'obliterate' ? 'OBLITERATING' : 'BURNING';
                    text.classList.add('text-red-400');
                } else {
                    badge.classList.remove('!border-red-600', 'bg-red-950');
                    dot.classList.remove('bg-red-500');
                    dot.classList.add('bg-zinc-500');
                    text.textContent = 'STOPPED';
                    text.classList.remove('text-red-400');
                }

                const logEl = document.getElementById('log-container');
                logEl.innerHTML = '';
                data.recent_logs.slice().reverse().forEach(line => {
                    const div = document.createElement('div');
                    div.className = 'log-line';
                    div.textContent = line;
                    logEl.appendChild(div);
                });
            } catch(e) { console.error(e); }
        }

        async function saveKeys() {
            const form = new FormData();
            form.append('keys_text', document.getElementById('keys-text').value);
            await fetch('/api/keys', { method: 'POST', body: form });
            await refreshStats();
            alert('Keys saved.');
        }

        async function applyConfig() {
            const form = new FormData();
            form.append('concurrency', parseInt(document.getElementById('concurrency').value));
            form.append('mode', document.getElementById('mode').value);
            await fetch('/api/config', { method: 'POST', body: form });
            await refreshStats();
        }

        function keysForm() {
            const form = new FormData();
            const keys = document.getElementById('keys-text').value.trim();
            if (keys) form.append('keys_text', keys);
            return form;
        }

        async function startBurn() {
            const res = await fetch('/api/start', { method: 'POST', body: keysForm() });
            const data = await res.json();
            if (!data.ok) alert(data.message || 'Failed to start');
            setTimeout(refreshStats, 300);
        }

        async function stopBurn() {
            await fetch('/api/stop', { method: 'POST' });
            setTimeout(refreshStats, 300);
        }

        async function obliterateMode() {
            const keys = document.getElementById('keys-text').value.trim();
            if (!keys) {
                alert('Paste your Helius API key first.');
                return;
            }
            document.getElementById('concurrency').value = 200;
            document.getElementById('concurrency-slider').value = 200;
            document.getElementById('mode').value = 'obliterate';
            await fetch('/api/keys', { method: 'POST', body: keysForm() });
            const res = await fetch('/api/obliterate', { method: 'POST', body: keysForm() });
            const data = await res.json();
            if (!data.ok) alert(data.message || 'Failed to start');
            setTimeout(refreshStats, 400);
        }

        async function nukeMode() {
            const keys = document.getElementById('keys-text').value.trim();
            if (!keys) { alert('Paste your Helius API key first.'); return; }
            await fetch('/api/keys', { method: 'POST', body: keysForm() });
            const res = await fetch('/api/nuke', { method: 'POST', body: keysForm() });
            const data = await res.json();
            if (!data.ok) alert(data.message || 'Failed to start');
            setTimeout(refreshStats, 400);
        }

        async function loadInitial() {
            const res = await fetch('/api/stats');
            const data = await res.json();
            document.getElementById('concurrency').value = data.concurrency;
            document.getElementById('concurrency-slider').value = Math.min(500, data.concurrency);
            document.getElementById('mode').value = data.mode;
            await refreshStats();
            if (pollInterval) clearInterval(pollInterval);
            pollInterval = setInterval(refreshStats, 2000);
        }

        window.onload = loadInitial;
    </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return HTMLResponse(DASHBOARD_HTML)


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)