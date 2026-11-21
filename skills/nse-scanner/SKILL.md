---
name: nse-scanner
description: >-
  NSE Chart Scanner for Indian stocks. Builds the symbol list (Nifty 50 / 100 / 200 / 500, Midcap 150,
  Smallcap 250 or every NSE EQ-series stock), runs an end-of-day scan of daily bars (20-day closing-high
  breakout, bull flag, reclaimed the 20-day average, oversold RSI, oversold bounce) with price and
  turnover filters, and an intraday opening-range breakout scan on 5-minute bars, in IST. Writes
  nse/watchlist.json. Free delayed Yahoo data (RELIANCE.NS, TCS.BO). Use when the user says "scan NSE",
  "Nifty breakout stocks", "aaj kaunse stocks breakout de rahe hain", "intraday ke liye stocks batao",
  "find setups in Indian market", "opening range breakout", "oversold Nifty stocks", "build my watchlist".
argument-hint: "[daily|orb] [--index nifty500]"
---

# nse-scanner

Finds Indian stocks worth a closer look. It never says buy. Reply in the user's language (Hindi,
Hinglish or English, whichever they wrote in).

`${CLAUDE_SKILL_DIR}` is this skill's folder. Run commands from the user's project folder so files
land in `./nse/`.

```bash
python3 "${CLAUDE_SKILL_DIR}/nse.py" init                          # once: nse/config.json
python3 "${CLAUDE_SKILL_DIR}/nse.py" universe --index nifty500,niftybank   # or nifty50, nifty100, nifty200, midcap150, smallcap250, all; comma list merges
python3 "${CLAUDE_SKILL_DIR}/nse.py" scan daily                    # after 15:30 or before 9:15, on daily closes
python3 "${CLAUDE_SKILL_DIR}/nse.py" scan daily --setup breakout20,bull_flag
python3 "${CLAUDE_SKILL_DIR}/nse.py" scan orb --at 09:45           # opening-range breakouts so far today
python3 "${CLAUDE_SKILL_DIR}/nse.py" scan orb --symbols RELIANCE,TCS,INFY,HDFCBANK
```

- `universe` downloads from niftyindices.com or archives.nseindia.com. NSE sites sometimes refuse
  scripts: then ask the user to open the URL in a browser, save the CSV, and run
  `universe --file that.csv`.
- Filters from nse/config.json: `min_price` (default 50) and `min_avg_turnover_cr` (average daily
  traded value, default 5 crore) keep out illiquid stocks where a stop-loss can slip badly.
- Yahoo is free, unofficial and delayed. One request per stock, 0.4 s apart: Nifty 500 takes ~4
  minutes. Use `--limit` or `--symbols` for a quick look.
- The ORB scan uses the first 15 minutes (9:15 to 9:30, `range_min` in config) and the first
  5-minute bar that CLOSES above that range's high.

## Steps

1. If `nse/universe.json` is missing, build it (Nifty 500 is a good default).
2. Run the scan the user asked for. Show the top hits as a short table: symbol, setup, close, why.
3. Hand off: `nse-news` for the headlines on the hits, then `nse-entry` on the one they pick.

## Rules

- A setup is a pattern, not a recommendation. Say so once.
- F&O, currency, commodity and crypto are not scanned: cash equity only.
- Education and tooling, not SEBI-registered investment advice.
