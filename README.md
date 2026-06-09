# 🔥 Helius Quota Burner Dashboard (Railway)

**Goal: Burn Helius API keys as fast and pointlessly as possible.**

This is a web dashboard you can deploy on Railway. It lets you:
- Paste multiple Helius keys directly in the UI
- Control concurrency, mode (expensive/mixed/etc.)
- Start / stop the burner with one click
- Watch live RPS, 429 hits, total requests

**This exists purely to waste quota with expensive, useless RPC calls** (getProgramAccounts without filters, broad searchAssets, getAssetsByOwner, full getBlock, etc.).

---

## Deployment on Railway (Recommended)

1. Push this folder to a Git repo (the private one we created earlier is perfect).
2. On Railway: **New Project → Deploy from GitHub**.
3. After first deploy, go to **Variables** and optionally set defaults:
   - `HELIUS_KEYS=key1,key2,key3` (will be preloaded)
   - `CONCURRENCY=150`
   - `MODE=expensive`
4. The service will now be a proper web app on a URL.
5. Open the URL → you get the full dashboard.

**Important**: After changing variables, always hit **Redeploy**.

---

## Local Development

```bash
cd helius-burner-railway
pip install -r requirements.txt
python app.py
# or
uvicorn app:app --reload
```

Then open http://localhost:8080

---

## How to use the Dashboard

1. Paste your keys (one per line) in the textarea → **Save Keys**
2. Set **Concurrency** (higher = faster burn, 150-300 is good, Nuke goes to 400+)
3. Choose **Mode**:
   - **☢️ NUKE** → absolute maximum pointless destruction (recommended for "as fast to zero as possible")
   - `expensive` → very heavy
   - `mixed` → balanced
4. Click the big red **START BURN** or the nuclear **NUKE MODE** button (sets 400 workers + nuke + starts instantly)
5. Watch the live stats. The log shows recent activity and 429s.

You can change config while it's running (it will use new values on next start).

**Nuke Mode** is the fastest way to drain the key: highest weight on getProgramAccounts (no filters = huge responses), multiple calls per worker loop, zero sleeps, max concurrency. Goal: deplete quota as quickly as possible.

---

## Railway Tips for Maximum Burn

- Use a paid plan if you want high sustained RPS (free tier will throttle hard).
- Start with `CONCURRENCY=80-120`, then increase.
- `MODE=expensive` is the most effective for quota destruction.
- The dashboard now includes a health endpoint so Railway doesn't think the service died.

---

## Files

- `app.py` — Full FastAPI dashboard + burner logic
- `requirements.txt`
- `Dockerfile` + `Procfile` + `railway.toml` — ready for Railway

---

**Huge warning again**: Only keys you own. This thing is intentionally stupidly aggressive. Use at your own risk.

If you want extra features (multiple parallel burners, per-key stats, auto-ramp, etc.) just say the word.
