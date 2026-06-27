#!/usr/bin/env python3
"""
Local max-throughput burner — no web server overhead.
Saturates Professional dual rate-limit lanes from your machine.

Usage:
  python burn_max.py --key YOUR_KEY
  python burn_max.py --keys key1,key2,key3
  HELIUS_KEYS=key1,key2 python burn_max.py
"""

import argparse
import asyncio
import os
import signal
import sys
import time


async def run(keys: list[str]) -> None:
    from app import state, start_burner, stop_burner, stats_reporter

    state["keys"] = keys
    state["mode"] = "annihilate"

    ok, msg = await start_burner()
    if not ok:
        print(f"Failed: {msg}")
        sys.exit(1)

    print(msg)
    print("Ctrl+C to stop. Credits/s printed every 2s.\n")

    stop = asyncio.Event()

    def _sig(*_):
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _sig)
        except NotImplementedError:
            pass

    try:
        while not stop.is_set():
            await asyncio.sleep(2)
            s = state["stats"]
            elapsed = time.time() - state["start_time"]
            cph = s["credits_burned"] / elapsed * 3600 if elapsed > 0 else 0
            print(
                f"[{elapsed:6.0f}s] {s['credit_rps']:,.0f} cr/s | "
                f"burned {s['credits_burned']:,.0f} | "
                f"~{cph:,.0f}/h | 429s={s['rate_limited']} | "
                f"sig_pool={len(__import__('app').sig_pool)}"
            )
    finally:
        await stop_burner()
        s = state["stats"]
        print(f"\nDone. Total credits burned: {s['credits_burned']:,.0f}")


def main():
    parser = argparse.ArgumentParser(description="Helius max local burner")
    parser.add_argument("--key")
    parser.add_argument("--keys")
    args = parser.parse_args()

    keys_str = args.keys or args.key or os.getenv("HELIUS_KEYS") or os.getenv("HELIUS_KEY")
    if not keys_str:
        print("Provide --key or HELIUS_KEYS env var")
        sys.exit(1)

    keys = [k.strip() for k in keys_str.split(",") if k.strip()]
    asyncio.run(run(keys))


if __name__ == "__main__":
    main()