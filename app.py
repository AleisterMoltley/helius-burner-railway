#!/usr/bin/env python3
"""
Helius Quota Burner Dashboard - NUKE EDITION
============================================

Web UI to control and monitor pointless high-volume Helius RPC consumption.

**Nuke Mode**: The fastest way to drive a key to zero.
Prioritizes the heaviest possible calls (getProgramAccounts with no filters = massive data transfer),
fires multiple calls per worker iteration with zero sleeps, and supports extreme concurrency (400+).

WARNING:
- This tool is designed to burn Helius API quota as fast as possible with
  completely useless but expensive calls (getProgramAccounts, searchAssets,
  getAssetsByOwner, getBlock full, etc.).
- ONLY use API keys that YOU own or have explicit permission to abuse.
- Using this on leaked or third-party keys can be illegal and against Helius ToS.
- You are fully responsible.

Optimized for Railway deployment as a web service.
"""

import asyncio
import aiohttp
import os
import random
import time
from collections import defaultdict, deque
from typing import List, Dict, Any

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import uvicorn

# ==================== CONFIG & STATE ====================

HELIUS_RPC = "https://mainnet.helius-rpc.com"

# Same heavy, pointless operations as before (max quota burn)
KNOWN_PROGRAMS = [
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
    "metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s",
    "BGUMAp9Gq7iTEuizy4pqaxsTyUCBK68MDfK752saRPUY",
    "cndy3Z4yapfJBmL3ShUp5exZKqR3z33thTzeNMm2gRZ",
    "M2mx93ekt1fmXSVkTrUL9xVFHkmME8HTUi5Cyc5aF7K",
]

KNOWN_WALLETS = [
    "11111111111111111111111111111111",
    "So11111111111111111111111111111111111111112",
]

KNOWN_MINTS = [
    "So11111111111111111111111111111111111111112",
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
]

# Global state (in-memory, fine for Railway lifetime)
state: Dict[str, Any] = {
    "keys": [],                    # list of api keys
    "concurrency": 150,
    "mode": "expensive",           # basic | das-heavy | expensive | mixed
    "running": False,
    "start_time": 0.0,
    "stats": {
        "total": 0,
        "success": 0,
        "rate_limited": 0,
        "errors": 0,
        "exceptions": 0,
        "current_rps": 0.0,
    },
    "recent_logs": deque(maxlen=50),  # simple log buffer
    "stop_event": None,
    "tasks": [],
    "session": None,
    "reporter_task": None,
}

# Operation weights for "pointless burn"
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
        ("get_program_accounts", 7),   # very quota hungry without filters
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
        # MAXIMUM DESTRUCTION MODE - prioritize the heaviest calls possible
        ("get_program_accounts", 12),      # king of quota burners (no filters = massive responses)
        ("get_assets_by_owner", 8),
        ("search_assets", 9),              # broad searches = expensive
        ("get_signatures_for_address", 5),
        ("get_recent_block", 4),
        ("get_asset", 2),
        ("get_slot", 1),
    ],
}

# ==================== BURNER LOGIC (same aggressive calls) ====================

async def do_get_slot(session: aiohttp.ClientSession, key: str) -> int:
    payload = {"jsonrpc": "2.0", "id": random.randint(1, 10**9), "method": "getSlot"}
    url = f"{HELIUS_RPC}/?api-key={key}"
    async with session.post(url, json=payload) as resp:
        return resp.status

async def do_get_recent_block(session: aiohttp.ClientSession, key: str) -> int:
    slot_payload = {"jsonrpc": "2.0", "id": 1, "method": "getSlot"}
    url = f"{HELIUS_RPC}/?api-key={key}"
    async with session.post(url, json=slot_payload) as r:
        data = await r.json()
        slot = data.get("result", 0)

    block_payload = {
        "jsonrpc": "2.0", "id": random.randint(1, 10**9),
        "method": "getBlock",
        "params": [slot - random.randint(1, 50), {"encoding": "json", "maxSupportedTransactionVersion": 0}]
    }
    async with session.post(url, json=block_payload) as resp:
        return resp.status

async def do_get_program_accounts(session: aiohttp.ClientSession, key: str) -> int:
    program = random.choice(KNOWN_PROGRAMS)
    payload = {
        "jsonrpc": "2.0", "id": random.randint(1, 10**9),
        "method": "getProgramAccounts",
        "params": [program, {"encoding": "base64", "commitment": "confirmed"}]
    }
    url = f"{HELIUS_RPC}/?api-key={key}"
    async with session.post(url, json=payload) as resp:
        return resp.status

async def do_get_assets_by_owner(session: aiohttp.ClientSession, key: str) -> int:
    owner = random.choice(KNOWN_WALLETS + KNOWN_PROGRAMS)
    payload = {
        "jsonrpc": "2.0", "id": random.randint(1, 10**9),
        "method": "getAssetsByOwner",
        "params": {"ownerAddress": owner, "page": random.randint(1, 3), "limit": random.randint(50, 200)}
    }
    url = f"{HELIUS_RPC}/?api-key={key}"
    async with session.post(url, json=payload) as resp:
        return resp.status

async def do_search_assets(session: aiohttp.ClientSession, key: str) -> int:
    payload = {
        "jsonrpc": "2.0", "id": random.randint(1, 10**9),
        "method": "searchAssets",
        "params": {
            "page": 1,
            "limit": random.randint(50, 100),
            "options": {"showCollectionMetadata": True},
            **random.choice([
                {"interface": "FungibleToken"},
                {"interface": "V1_NFT"},
                {"creatorAddress": random.choice(KNOWN_PROGRAMS)},
                {}
            ])
        }
    }
    url = f"{HELIUS_RPC}/?api-key={key}"
    async with session.post(url, json=payload) as resp:
        return resp.status

async def do_get_asset(session: aiohttp.ClientSession, key: str) -> int:
    mint = random.choice(KNOWN_MINTS)
    payload = {"jsonrpc": "2.0", "id": random.randint(1, 10**9), "method": "getAsset", "params": {"id": mint}}
    url = f"{HELIUS_RPC}/?api-key={key}"
    async with session.post(url, json=payload) as resp:
        return resp.status

async def do_get_signatures_for_address(session: aiohttp.ClientSession, key: str) -> int:
    addr = random.choice(KNOWN_WALLETS + KNOWN_PROGRAMS)
    payload = {
        "jsonrpc": "2.0", "id": random.randint(1, 10**9),
        "method": "getSignaturesForAddress",
        "params": [addr, {"limit": 1000}]
    }
    url = f"{HELIUS_RPC}/?api-key={key}"
    async with session.post(url, json=payload) as resp:
        return resp.status

OP_MAP = {
    "get_slot": do_get_slot,
    "get_recent_block": do_get_recent_block,
    "get_program_accounts": do_get_program_accounts,
    "get_assets_by_owner": do_get_assets_by_owner,
    "search_assets": do_search_assets,
    "get_asset": do_get_asset,
    "get_signatures_for_address": do_get_signatures_for_address,
}

def build_weighted_pool(mode: str) -> List[str]:
    pool = OPERATION_POOLS.get(mode, OPERATION_POOLS["mixed"])
    weighted = []
    for op_name, weight in pool:
        weighted.extend([op_name] * weight)
    return weighted

async def worker(worker_id: int, session: aiohttp.ClientSession, keys: List[str], weighted_ops: List[str], stop_event: asyncio.Event, is_nuke: bool = False):
    key_index = 0
    while not stop_event.is_set() and keys:
        # In nuke mode: fire multiple heavy calls per iteration with no delay
        calls_this_loop = 3 if is_nuke else 1

        for _ in range(calls_this_loop):
            if stop_event.is_set():
                break
            op_name = random.choice(weighted_ops)
            key = keys[key_index % len(keys)]
            key_index += 1

            try:
                func = OP_MAP[op_name]
                status = await func(session, key)

                state["stats"]["total"] += 1
                if status == 200:
                    state["stats"]["success"] += 1
                elif status == 429:
                    state["stats"]["rate_limited"] += 1
                    state["recent_logs"].append(f"[{time.strftime('%H:%M:%S')}] 429 on {op_name}")
                else:
                    state["stats"]["errors"] += 1
            except asyncio.CancelledError:
                break
            except Exception:
                state["stats"]["exceptions"] += 1

        # Only tiny sleep in non-nuke modes
        if not is_nuke and random.random() < 0.05:
            await asyncio.sleep(0.0005)

async def stats_reporter(stop_event: asyncio.Event, start_time: float):
    last_total = 0
    while not stop_event.is_set():
        await asyncio.sleep(2.0)
        now = time.time()
        elapsed = now - start_time
        total = state["stats"]["total"]
        rps = (total - last_total) / 2.0
        last_total = total
        state["stats"]["current_rps"] = round(rps, 1)

        # keep a simple log entry
        if rps > 0:
            state["recent_logs"].append(
                f"[{time.strftime('%H:%M:%S')}] RPS: {rps:.1f} | Total: {total} | 429s: {state['stats']['rate_limited']}"
            )

async def start_burner():
    if state["running"] or not state["keys"]:
        return False

    state["running"] = True
    state["start_time"] = time.time()
    state["stop_event"] = asyncio.Event()
    state["stats"] = {k: 0 for k in state["stats"]}
    state["stats"]["current_rps"] = 0.0
    state["recent_logs"].clear()
    state["recent_logs"].append("=== BURN STARTED ===")

    connector = aiohttp.TCPConnector(limit=0, limit_per_host=0)
    timeout = aiohttp.ClientTimeout(total=30)
    session = aiohttp.ClientSession(connector=connector, timeout=timeout)
    state["session"] = session

    weighted = build_weighted_pool(state["mode"])
    concurrency = max(1, int(state["concurrency"]))
    is_nuke = state["mode"] == "nuke"

    tasks = []
    for i in range(concurrency):
        t = asyncio.create_task(
            worker(i, session, state["keys"], weighted, state["stop_event"], is_nuke=is_nuke)
        )
        tasks.append(t)

    reporter = asyncio.create_task(stats_reporter(state["stop_event"], state["start_time"]))
    state["tasks"] = tasks
    state["reporter_task"] = reporter

    # Start health server if on Railway
    if os.getenv("PORT") or os.getenv("RAILWAY_ENVIRONMENT"):
        asyncio.create_task(start_health_server())

    state["recent_logs"].append(f"Burner started with {concurrency} workers in {state['mode']} mode")
    return True

async def stop_burner():
    if not state["running"]:
        return

    if state["stop_event"]:
        state["stop_event"].set()

    for task in state["tasks"]:
        task.cancel()

    if state["reporter_task"]:
        state["reporter_task"].cancel()

    if state["session"]:
        await state["session"].close()

    state["running"] = False
    state["tasks"] = []
    state["reporter_task"] = None
    state["session"] = None
    state["recent_logs"].append("=== BURN STOPPED ===")

async def start_health_server():
    """Minimal health endpoint so Railway doesn't think we're dead."""
    port = int(os.getenv("PORT", "8080"))
    app = web.Application()

    async def health(_):
        return web.Response(text="BURNER ALIVE")

    app.router.add_get("/", health)
    app.router.add_get("/health", health)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    state["recent_logs"].append(f"Health server on port {port}")

# ==================== FASTAPI APP ====================

app = FastAPI(title="Helius Quota Burner Dashboard")

# Templates (we'll use inline HTML for simplicity + one index route)
templates = Jinja2Templates(directory=".")

@app.on_event("startup")
async def startup_event():
    # Load defaults from env if present
    env_keys = os.getenv("HELIUS_KEYS") or os.getenv("HELIUS_KEY", "")
    if env_keys:
        state["keys"] = [k.strip() for k in env_keys.split(",") if k.strip()]

    env_conc = os.getenv("CONCURRENCY")
    if env_conc:
        state["concurrency"] = int(env_conc)

    env_mode = os.getenv("MODE")
    if env_mode in OPERATION_POOLS:
        state["mode"] = env_mode

    state["recent_logs"].append("Dashboard started. Ready to burn.")

@app.on_event("shutdown")
async def shutdown_event():
    await stop_burner()

# ==================== API ENDPOINTS ====================

@app.get("/api/stats")
async def get_stats():
    elapsed = time.time() - state["start_time"] if state["running"] else 0
    return JSONResponse({
        "running": state["running"],
        "keys_count": len(state["keys"]),
        "concurrency": state["concurrency"],
        "mode": state["mode"],
        "elapsed": round(elapsed, 1),
        "stats": state["stats"],
        "recent_logs": list(state["recent_logs"])[-15:],
    })

@app.post("/api/keys")
async def set_keys(keys_text: str = Form(...)):
    raw_lines = [k.strip() for k in keys_text.strip().splitlines() if k.strip()]
    new_keys = []
    for line in raw_lines:
        if "api-key=" in line:
            # User pasted full URL like mainnet.helius-rpc.com/?api-key=xxx
            try:
                key = line.split("api-key=")[1].split("&")[0].strip()
                if key:
                    new_keys.append(key)
            except:
                pass
        elif line.startswith("http"):
            # full URL without api-key param? skip or extract if possible
            continue
        else:
            # assume it's a clean key
            new_keys.append(line)
    # dedupe while preserving order
    seen = set()
    new_keys = [k for k in new_keys if not (k in seen or seen.add(k))]
    state["keys"] = new_keys
    state["recent_logs"].append(f"Updated keys ({len(new_keys)} total)")
    return {"ok": True, "count": len(new_keys)}

@app.post("/api/config")
async def set_config(concurrency: int = Form(...), mode: str = Form(...)):
    if mode in OPERATION_POOLS:
        state["mode"] = mode
    state["concurrency"] = max(1, min(500, concurrency))  # safety cap
    state["recent_logs"].append(f"Config updated: {state['concurrency']} workers, mode={state['mode']}")
    return {"ok": True}

@app.post("/api/start")
async def api_start():
    success = await start_burner()
    return {"ok": success, "message": "Burner started" if success else "Already running or no keys"}

@app.post("/api/stop")
async def api_stop():
    await stop_burner()
    return {"ok": True, "message": "Burner stopped"}

# ==================== DASHBOARD UI ====================

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>HELIUS BURNER • Dashboard</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600&amp;family=Space+Grotesk:wght@500;600&amp;display=swap');
        body { font-family: 'Inter', system_ui, sans-serif; }
        .font-display { font-family: 'Space Grotesk', 'Inter', sans-serif; }
        .burn-red { color: #ef4444; }
        .stat-card { transition: all 0.1s ease; }
        .log-line { font-family: ui-monospace, monospace; font-size: 0.75rem; }
    </style>
</head>
<body class="bg-zinc-950 text-zinc-200">
    <div class="max-w-6xl mx-auto p-6">
        <!-- Header -->
        <div class="flex items-center justify-between mb-8">
            <div>
                <div class="flex items-center gap-3">
                    <div class="w-10 h-10 bg-red-600 rounded-2xl flex items-center justify-center text-white font-bold text-2xl">🔥</div>
                    <div>
                        <h1 class="text-4xl font-semibold tracking-tighter font-display">HELIUS BURNER</h1>
                        <p class="text-red-500 text-sm -mt-1">Maximum pointless quota consumption</p>
                    </div>
                </div>
            </div>
            <div id="status-badge" 
                 class="px-4 py-1.5 rounded-3xl text-sm font-medium flex items-center gap-2 border border-zinc-800 bg-zinc-900">
                <div class="w-2 h-2 rounded-full bg-zinc-500" id="status-dot"></div>
                <span id="status-text">STOPPED</span>
            </div>
        </div>

        <!-- Big Warning -->
        <div class="mb-6 p-4 bg-red-950 border border-red-900 rounded-3xl text-red-400 text-sm">
            <strong>EXTREME WARNING:</strong> This tool exists to waste Helius quota as fast as humanly possible using expensive RPC calls.
            Only use keys you personally own. Misuse can get you banned or worse.
        </div>

        <div class="grid grid-cols-1 lg:grid-cols-12 gap-6">
            
            <!-- Controls -->
            <div class="lg:col-span-5 bg-zinc-900 border border-zinc-800 rounded-3xl p-6">
                <h2 class="font-semibold text-lg mb-4 flex items-center gap-2">
                    <span>⚙️</span> 
                    <span>Controls</span>
                </h2>

                <!-- Keys -->
                <div class="mb-5">
                    <label class="block text-xs font-medium text-zinc-400 mb-1.5">HELIUS KEYS (one per line)</label>
                    <textarea id="keys-text" rows="4" 
                              class="w-full bg-zinc-950 border border-zinc-800 rounded-2xl p-3 text-sm font-mono resize-y"
                              placeholder="Paste full URL or just the key&#10;mainnet.helius-rpc.com/?api-key=xxx&#10;or just xxx"></textarea>
                    <button onclick="saveKeys()" 
                            class="mt-2 text-xs px-4 py-2 bg-zinc-800 hover:bg-zinc-700 rounded-2xl transition">
                        Save Keys
                    </button>
                </div>

                <!-- Config -->
                <div class="grid grid-cols-2 gap-4 mb-6">
                    <div>
                        <label class="block text-xs font-medium text-zinc-400 mb-1.5">CONCURRENCY</label>
                        <input type="number" id="concurrency" value="150" 
                               class="w-full bg-zinc-950 border border-zinc-800 rounded-2xl p-3 text-lg font-semibold">
                        <input type="range" id="concurrency-slider" min="10" max="400" step="10" value="150"
                               class="w-full accent-red-600 mt-1" oninput="document.getElementById('concurrency').value=this.value">
                    </div>
                    <div>
                        <label class="block text-xs font-medium text-zinc-400 mb-1.5">MODE</label>
                        <select id="mode" class="w-full bg-zinc-950 border border-zinc-800 rounded-2xl p-3 text-sm">
                            <option value="nuke">☢️ NUKE (MAX DESTRUCTION)</option>
                            <option value="expensive">EXPENSIVE (max pain)</option>
                            <option value="mixed">MIXED</option>
                            <option value="das-heavy">DAS-HEAVY</option>
                            <option value="basic">BASIC (weaker)</option>
                        </select>
                    </div>
                </div>

                <button onclick="applyConfig()" 
                        class="w-full mb-3 py-2.5 text-sm bg-zinc-800 hover:bg-zinc-700 rounded-2xl transition">
                    Apply Config
                </button>

                <!-- Big Buttons -->
                <div class="flex gap-3">
                    <button onclick="startBurn()" 
                            class="flex-1 py-4 text-lg font-semibold bg-red-600 hover:bg-red-500 active:bg-red-700 rounded-3xl transition flex items-center justify-center gap-2">
                        <span>🔥 START BURN</span>
                    </button>
                    <button onclick="stopBurn()" 
                            class="flex-1 py-4 text-lg font-semibold bg-zinc-800 hover:bg-zinc-700 rounded-3xl transition">
                        STOP
                    </button>
                </div>

                <!-- Nuke Button - instant max destruction -->
                <button onclick="nukeMode()" 
                        class="mt-3 w-full py-5 text-xl font-bold bg-gradient-to-r from-red-700 via-red-600 to-orange-600 hover:from-red-600 hover:to-red-500 active:scale-[0.985] rounded-3xl transition flex items-center justify-center gap-3 shadow-xl shadow-red-950">
                    <span class="text-2xl">☢️</span>
                    <span>NUKE MODE - MAX SPEED TO ZERO</span>
                </button>
                <p class="text-[10px] text-center text-red-500/70 mt-1">Sets 400 workers + nuke mode + starts immediately</p>
            </div>

            <!-- Live Stats -->
            <div class="lg:col-span-7 bg-zinc-900 border border-zinc-800 rounded-3xl p-6">
                <h2 class="font-semibold text-lg mb-4">LIVE STATS</h2>
                
                <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
                    <div class="stat-card bg-zinc-950 border border-zinc-800 rounded-2xl p-4">
                        <div class="text-xs text-zinc-500">CURRENT RPS</div>
                        <div id="rps" class="text-4xl font-semibold tabular-nums text-red-500 mt-1">0.0</div>
                    </div>
                    <div class="stat-card bg-zinc-950 border border-zinc-800 rounded-2xl p-4">
                        <div class="text-xs text-zinc-500">TOTAL REQUESTS</div>
                        <div id="total" class="text-4xl font-semibold tabular-nums mt-1">0</div>
                    </div>
                    <div class="stat-card bg-zinc-950 border border-zinc-800 rounded-2xl p-4">
                        <div class="text-xs text-zinc-500">429 RATE LIMITED</div>
                        <div id="rate-limited" class="text-4xl font-semibold tabular-nums text-orange-400 mt-1">0</div>
                    </div>
                    <div class="stat-card bg-zinc-950 border border-zinc-800 rounded-2xl p-4">
                        <div class="text-xs text-zinc-500">ERRORS</div>
                        <div id="errors" class="text-4xl font-semibold tabular-nums mt-1">0</div>
                    </div>
                </div>

                <div class="flex items-center gap-6 text-sm">
                    <div>
                        <span class="text-zinc-500">Elapsed:</span> 
                        <span id="elapsed" class="font-mono font-semibold">0s</span>
                    </div>
                    <div>
                        <span class="text-zinc-500">Workers:</span> 
                        <span id="workers" class="font-semibold">0</span>
                    </div>
                    <div>
                        <span class="text-zinc-500">Keys loaded:</span> 
                        <span id="keys-count" class="font-semibold">0</span>
                    </div>
                    <div id="mode-display" class="px-3 py-0.5 bg-zinc-800 rounded-2xl text-xs font-medium"></div>
                </div>
            </div>

            <!-- Logs -->
            <div class="lg:col-span-12 bg-zinc-900 border border-zinc-800 rounded-3xl p-6">
                <div class="flex justify-between items-center mb-3">
                    <h2 class="font-semibold">Burn Log (last activity)</h2>
                    <button onclick="refreshStats()" class="text-xs px-3 py-1 bg-zinc-800 rounded-2xl hover:bg-zinc-700">Refresh</button>
                </div>
                <div id="log-container" 
                     class="bg-black border border-zinc-800 rounded-2xl p-3 h-64 overflow-auto font-mono text-xs text-zinc-400 space-y-0.5">
                    <!-- populated by JS -->
                </div>
            </div>
        </div>

        <div class="mt-6 text-[10px] text-center text-zinc-600">
            Goal: maximum pointless RPC calls. Use responsibly. • Railway edition
        </div>
    </div>

    <script>
        let pollInterval = null;

        async function refreshStats() {
            try {
                const res = await fetch('/api/stats');
                const data = await res.json();

                document.getElementById('rps').textContent = data.stats.current_rps.toFixed(1);
                document.getElementById('total').textContent = data.stats.total.toLocaleString();
                document.getElementById('rate-limited').textContent = data.stats.rate_limited.toLocaleString();
                document.getElementById('errors').textContent = (data.stats.errors + data.stats.exceptions).toLocaleString();
                document.getElementById('elapsed').textContent = data.elapsed + 's';
                document.getElementById('workers').textContent = data.concurrency;
                document.getElementById('keys-count').textContent = data.keys_count;
                document.getElementById('mode-display').textContent = data.mode.toUpperCase();

                // Status
                const badge = document.getElementById('status-badge');
                const dot = document.getElementById('status-dot');
                const text = document.getElementById('status-text');

                if (data.running) {
                    badge.classList.add('!border-red-600', 'bg-red-950');
                    dot.classList.add('bg-red-500');
                    dot.classList.remove('bg-zinc-500');
                    text.textContent = 'BURNING';
                    text.classList.add('text-red-400');
                } else {
                    badge.classList.remove('!border-red-600', 'bg-red-950');
                    dot.classList.remove('bg-red-500');
                    dot.classList.add('bg-zinc-500');
                    text.textContent = 'STOPPED';
                    text.classList.remove('text-red-400');
                }

                // Logs
                const logEl = document.getElementById('log-container');
                logEl.innerHTML = '';
                data.recent_logs.slice().reverse().forEach(line => {
                    const div = document.createElement('div');
                    div.className = 'log-line';
                    div.textContent = line;
                    logEl.appendChild(div);
                });

            } catch(e) {
                console.error(e);
            }
        }

        async function saveKeys() {
            const text = document.getElementById('keys-text').value;
            const form = new FormData();
            form.append('keys_text', text);
            
            await fetch('/api/keys', { method: 'POST', body: form });
            await refreshStats();
            alert('Keys saved. Start burner to use them.');
        }

        async function applyConfig() {
            const conc = parseInt(document.getElementById('concurrency').value);
            const mode = document.getElementById('mode').value;
            
            const form = new FormData();
            form.append('concurrency', conc);
            form.append('mode', mode);
            
            await fetch('/api/config', { method: 'POST', body: form });
            await refreshStats();
        }

        async function startBurn() {
            const res = await fetch('/api/start', { method: 'POST' });
            const data = await res.json();
            if (!data.ok) alert(data.message || 'Failed to start');
            setTimeout(refreshStats, 300);
        }

        async function stopBurn() {
            await fetch('/api/stop', { method: 'POST' });
            setTimeout(refreshStats, 300);
        }

        async function nukeMode() {
            // Instant nuke: max aggression
            document.getElementById('concurrency').value = 400;
            document.getElementById('concurrency-slider').value = 400;
            document.getElementById('mode').value = 'nuke';

            // Apply config
            const form = new FormData();
            form.append('concurrency', 400);
            form.append('mode', 'nuke');
            await fetch('/api/config', { method: 'POST', body: form });

            // Start immediately
            const res = await fetch('/api/start', { method: 'POST' });
            const data = await res.json();
            if (!data.ok) alert(data.message || 'Failed to nuke');
            setTimeout(refreshStats, 400);
        }

        // Load initial values
        async function loadInitial() {
            const res = await fetch('/api/stats');
            const data = await res.json();
            
            // Prefill keys if any (from env or previous)
            if (data.keys_count > 0) {
                // We don't expose keys for security, just count
            }
            
            document.getElementById('concurrency').value = data.concurrency;
            document.getElementById('concurrency-slider').value = data.concurrency;
            document.getElementById('mode').value = data.mode;

            await refreshStats();
            
            // Start polling
            if (pollInterval) clearInterval(pollInterval);
            pollInterval = setInterval(refreshStats, 2200);
        }

        // Keyboard shortcut
        document.addEventListener('keydown', function(e) {
            if (e.key === '/' && document.activeElement.tagName === 'BODY') {
                e.preventDefault();
                document.getElementById('keys-text').focus();
            }
            if (e.key.toLowerCase() === 's' && e.metaKey) {
                e.preventDefault();
                startBurn();
            }
        });

        window.onload = loadInitial;
    </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return HTMLResponse(DASHBOARD_HTML)

# ==================== RUN ====================

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)
