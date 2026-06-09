#!/usr/bin/env python3
"""
Helius Quota Stress Tester / "Burner" - Railway Edition
=======================================================

WAR N U N G / DISCLAIMER:
- Dieses Tool ist NUR für das Testen von API-Keys und Endpoints gedacht,
  die DU SELBST besitzt oder für die du ausdrückliche Erlaubnis zum
  Load-Testing hast.
- Die absichtliche Erschöpfung ("verbrennen") von fremden, geleakten oder
  bezahlten Helius-Keys ohne Autorisierung verstößt gegen die Nutzungs-
  bedingungen von Helius und kann rechtliche Konsequenzen haben.
- Der Entwickler dieses Tools distanziert sich von jeglichem Missbrauch.
  Du bist 100% selbst verantwortlich.

Railway-spezifisch:
- Konfiguration primär über Environment Variables (HELIUS_KEYS, CONCURRENCY, MODE, DURATION)
- DURATION=0 oder weglassen = unendlich laufen (perfekt für Railway)
- Läuft als persistenter Worker

Deployment auf Railway:
1. Diesen Ordner in ein Git-Repo pushen
2. Auf railway.app ein neues Project erstellen → Deploy from GitHub
3. Im Railway Dashboard unter Variables folgendes setzen:
   - HELIUS_KEYS=key1,key2,key3
   - CONCURRENCY=200
   - MODE=mixed   (oder expensive / das-heavy)
   - DURATION=0   (für unendlich)
"""

import asyncio
import aiohttp
import argparse
import os
import random
import sys
import time
from collections import defaultdict
from typing import List

from aiohttp import web  # for Railway health check endpoint

HELIUS_RPC = "https://mainnet.helius-rpc.com"

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

# Operationen (gleich wie vorher)
async def do_get_slot(session, key):
    payload = {"jsonrpc": "2.0", "id": random.randint(1, 10**9), "method": "getSlot"}
    url = f"{HELIUS_RPC}/?api-key={key}"
    async with session.post(url, json=payload) as resp:
        return resp.status

async def do_get_recent_block(session, key):
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

async def do_get_program_accounts(session, key):
    program = random.choice(KNOWN_PROGRAMS)
    payload = {
        "jsonrpc": "2.0", "id": random.randint(1, 10**9),
        "method": "getProgramAccounts",
        "params": [program, {"encoding": "base64", "commitment": "confirmed"}]
    }
    url = f"{HELIUS_RPC}/?api-key={key}"
    async with session.post(url, json=payload) as resp:
        return resp.status

async def do_get_assets_by_owner(session, key):
    owner = random.choice(KNOWN_WALLETS + KNOWN_PROGRAMS)
    payload = {
        "jsonrpc": "2.0", "id": random.randint(1, 10**9),
        "method": "getAssetsByOwner",
        "params": {"ownerAddress": owner, "page": random.randint(1, 3), "limit": random.randint(50, 200)}
    }
    url = f"{HELIUS_RPC}/?api-key={key}"
    async with session.post(url, json=payload) as resp:
        return resp.status

async def do_search_assets(session, key):
    payload = {
        "jsonrpc": "2.0", "id": random.randint(1, 10**9),
        "method": "searchAssets",
        "params": {
            "page": 1, "limit": random.randint(50, 100),
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

async def do_get_asset(session, key):
    mint = random.choice(KNOWN_MINTS)
    payload = {"jsonrpc": "2.0", "id": random.randint(1, 10**9), "method": "getAsset", "params": {"id": mint}}
    url = f"{HELIUS_RPC}/?api-key={key}"
    async with session.post(url, json=payload) as resp:
        return resp.status

async def do_get_signatures_for_address(session, key):
    addr = random.choice(KNOWN_WALLETS + KNOWN_PROGRAMS)
    payload = {
        "jsonrpc": "2.0", "id": random.randint(1, 10**9),
        "method": "getSignaturesForAddress",
        "params": [addr, {"limit": 1000}]
    }
    url = f"{HELIUS_RPC}/?api-key={key}"
    async with session.post(url, json=payload) as resp:
        return resp.status

OPERATION_POOLS = {
    "basic": [(do_get_slot, 5), (do_get_recent_block, 2)],
    "das-heavy": [(do_get_asset, 3), (do_get_assets_by_owner, 5), (do_search_assets, 8)],
    "expensive": [(do_get_program_accounts, 6), (do_get_signatures_for_address, 4), (do_get_recent_block, 3)],
    "mixed": [
        (do_get_slot, 2), (do_get_recent_block, 3),
        (do_get_program_accounts, 5), (do_get_assets_by_owner, 6),
        (do_search_assets, 7), (do_get_asset, 3), (do_get_signatures_for_address, 4),
    ],
}

def build_weighted_pool(mode: str):
    pool = OPERATION_POOLS.get(mode, OPERATION_POOLS["mixed"])
    weighted = []
    for func, weight in pool:
        weighted.extend([func] * weight)
    return weighted


async def start_health_server():
    """Minimal HTTP server so Railway sees the service as healthy (binds to $PORT)."""
    port = int(os.getenv("PORT", "8080"))
    app = web.Application()

    async def health(_request):
        return web.Response(text="OK - Helius Burner running")

    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    app.router.add_get("/_health", health)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"[RAILWAY] Health check server listening on 0.0.0.0:{port}", flush=True)

async def worker(worker_id, session, key, weighted_ops, stats, stop_event):
    while not stop_event.is_set():
        op = random.choice(weighted_ops)
        try:
            status = await op(session, key)
            stats["total"] += 1
            if status == 200:
                stats["success"] += 1
            elif status == 429:
                stats["rate_limited"] += 1
            else:
                stats["errors"] += 1
                stats["status_counts"][status] += 1
        except asyncio.CancelledError:
            break
        except Exception:
            stats["errors"] += 1
            stats["exceptions"] += 1

async def stats_reporter(stats, stop_event, start_time):
    last_total = 0
    while not stop_event.is_set():
        await asyncio.sleep(5)  # Alle 5s für Railway-Logs
        elapsed = time.time() - start_time
        total = stats["total"]
        rps = (total - last_total) / 5.0
        last_total = total
        success_rate = (stats["success"] / total * 100) if total > 0 else 0
        print(
            f"[{elapsed:.0f}s] RPS:{rps:6.1f} Total:{total:7d} "
            f"OK:{stats['success']} ({success_rate:5.1f}%) "
            f"429:{stats['rate_limited']} Err:{stats['errors']} Exc:{stats['exceptions']}",
            flush=True
        )

def get_config_from_env_and_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--key")
    parser.add_argument("--keys")
    parser.add_argument("--concurrency", type=int, default=100)
    parser.add_argument("--duration", type=int, default=0)
    parser.add_argument("--mode", default="mixed", choices=["basic", "das-heavy", "expensive", "mixed"])
    args = parser.parse_args()

    # Environment Variables haben Vorrang (wichtig für Railway!)
    keys_str = os.getenv("HELIUS_KEYS") or os.getenv("HELIUS_KEY") or args.keys or args.key
    concurrency = int(os.getenv("CONCURRENCY", args.concurrency))
    duration = int(os.getenv("DURATION", args.duration))
    mode = os.getenv("MODE", args.mode)

    if not keys_str:
        print("FEHLER: Kein Key gefunden! Setze HELIUS_KEYS=... als Environment Variable oder --key")
        sys.exit(1)

    keys = [k.strip() for k in keys_str.split(",") if k.strip()]
    return keys, concurrency, duration, mode

async def main():
    keys, concurrency, duration, mode = get_config_from_env_and_args()

    print("=" * 70, flush=True)
    print("  HELIUS QUOTA STRESS TESTER - RAILWAY MODE", flush=True)
    print("=" * 70, flush=True)
    print(f"Keys: {len(keys)} (round-robin)", flush=True)
    print(f"Concurrency: {concurrency}", flush=True)
    print(f"Mode: {mode}", flush=True)
    print(f"Duration: {'INFINITE (DURATION=0)' if duration <= 0 else f'{duration}s'}", flush=True)
    print("!!! NUR EIGENE KEYS !!!", flush=True)
    print("=" * 70, flush=True)

    weighted_ops = build_weighted_pool(mode)
    stats = defaultdict(int)
    stats["status_counts"] = defaultdict(int)
    stop_event = asyncio.Event()

    connector = aiohttp.TCPConnector(limit=0, limit_per_host=0)
    timeout = aiohttp.ClientTimeout(total=30)

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        # Start minimal health check server if we're on Railway (or PORT is set)
        # This prevents Railway from thinking the service is unhealthy / crashed
        if os.getenv("PORT") or os.getenv("RAILWAY_ENVIRONMENT"):
            asyncio.create_task(start_health_server())
            await asyncio.sleep(0.5)  # give it a moment to bind

        tasks = []
        start_time = time.time()

        for i in range(concurrency):
            key = keys[i % len(keys)]
            task = asyncio.create_task(worker(i, session, key, weighted_ops, stats, stop_event))
            tasks.append(task)

        reporter = asyncio.create_task(stats_reporter(stats, stop_event, start_time))

        try:
            if duration > 0:
                await asyncio.sleep(duration)
            else:
                # Infinite mode für Railway
                print("[RAILWAY] Running forever until container is stopped...", flush=True)
                while not stop_event.is_set():
                    await asyncio.sleep(3600)
        except KeyboardInterrupt:
            print("\n[INFO] Shutdown signal received...", flush=True)
        finally:
            stop_event.set()
            await asyncio.gather(*tasks, return_exceptions=True)
            reporter.cancel()

    elapsed = time.time() - start_time
    total = stats["total"]
    print("\n" + "=" * 70, flush=True)
    print("ERGEBNIS", flush=True)
    print(f"Laufzeit: {elapsed:.1f}s | Requests: {total} | RPS avg: {total/elapsed:.1f}", flush=True)
    print(f"429s: {stats['rate_limited']} | Errors: {stats['errors']}", flush=True)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
