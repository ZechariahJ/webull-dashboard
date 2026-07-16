# Market Dashboard

Auto-generated static page showing public US market movers (S&P 500 + NASDAQ-100)
and market news headlines. **Informational only — not investment advice.** No
personal or account data ever appears here.

Live page is served by **GitHub Pages** from `index.html` on `main`.

## How it updates itself

A scheduled GitHub Action (`.github/workflows/build-dashboard.yml`) runs every
weekday morning **in the cloud** — no computer of yours needs to be on. It runs the
code in `generator/`, writes a fresh `index.html`, and commits it. Pages redeploys.

The `generator/` code only *reads* public market data + news. It contains no
secrets and cannot trade or access any account.

## Required secrets (Settings → Secrets and variables → Actions)

| Secret | What it is |
|---|---|
| `WEBULL_APP_KEY` | A **Market-Data-only** Webull API key (Trading unchecked, 2FA off) |
| `WEBULL_APP_SECRET` | That key's secret |
| `NEWS_API_KEY` | Free [Finnhub](https://finnhub.io) key (optional; blank = no news section) |

Use a dedicated read-only key here — even if it leaked, it could only look up market
data. Do **not** use a key that has Trading permission.

## Run it now / on demand

Actions tab → **Build dashboard** → **Run workflow**.
