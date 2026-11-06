# BESS Arbitrage Simulator: Scarlet / Sonrisa cohort

Browser-based CAISO energy-arbitrage simulator for the Scarlet I/II/III and Sonrisa BESS resources (cohort pnode `SCARLT_7_N010`, all cohort pnodes clear within about $0.15) versus the NP15 hub.

Open `index.html` (or the GitHub Pages site). A live price dashboard is at `live/` (GitHub Pages: `/live/`). Pick a project, adjust MW, duration, round-trip efficiency, cycles per day, degradation cost, date range, and trigger thresholds.

## Strategies
| Strategy | What it models |
|---|---|
| Perfect foresight (RT) | Exact hourly DP optimization on real-time hourly prices, full-day hindsight. The ceiling. |
| DA schedule | Same optimizer on day-ahead prices, settled at DA. |
| DA plan, RT settle | DA-chosen schedule paid real-time prices; the gap is forecast-error cost. |
| Threshold trigger | Charge at or below the low trigger, discharge at or above the high trigger, 15-min RT, SOC carried across days. |

Energy arbitrage only. No ancillary services, RA, or capacity revenue; no SOC/AS co-optimization, no outages.

## Data
`scripts/fetch_prices.py` pulls 365 days of CAISO OASIS `PRC_LMP` (DAM hourly) and `PRC_RTPD_LMP` (15-min) for the cohort pnode and `TH_NP15_GEN-APND` into `data/prices.json`. Times are Pacific; charts use hour-ending (HE1 = 00:00 to 01:00).

A GitHub Action refreshes the data daily at about 3 pm PT. Run it manually from the Actions tab if needed.

## Project sizes
Scarlet III 78 MW / 4 h and Sonrisa BESS 184 MW / 4 h are on file. Scarlet I-1A, I-1B and II use 78 MW / 4 h placeholders until updated in `index.html` (`PROJECTS`).

Prepared by SG3 Consultants, LLC. Not investment advice; illustrative energy-arbitrage estimates from public CAISO market data.

## Live dashboard (`live/`)
Real-time 5-min LMP and components (energy, congestion, losses, GHG) at SCARLT_7_N011 and SCARLT_7_ND001, NP15/ZP26/SP15 hubs, node-to-NP15 basis, day-ahead forecast vs real-time actual by hour ending (Pacific), 48-hour charts, and simulated BESS arbitrage (last day and month to date).

`.github/workflows/pages.yml` runs `scripts/fetch_live.py` about every 10 minutes and deploys the site to GitHub Pages. Repo Settings > Pages > Source must be set to **GitHub Actions**.
