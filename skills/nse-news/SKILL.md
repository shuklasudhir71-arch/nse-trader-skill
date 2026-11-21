---
name: nse-news
description: >-
  NSE News Monitor. Pulls the latest headlines for Indian stocks from Google News (India edition) and
  Yahoo Finance RSS, shows only what is new since the last check, newest first, in IST, and tags each
  item (results, order win, deal, dividend/bonus/split, broker target, SEBI/RBI/regulatory, management,
  macro). Use when the user says "RELIANCE ki news", "why is this stock moving", "kya news hai",
  "morning briefing", "any news on my watchlist", "results kab hain", or after nse-scanner finds setups.
argument-hint: "[--symbols RELIANCE,TCS]"
---

# nse-news

Reply in the user's language. `${CLAUDE_SKILL_DIR}` is this skill's folder.

```bash
python3 "${CLAUDE_SKILL_DIR}/nse.py" news --symbols RELIANCE,TCS --hours 24
python3 "${CLAUDE_SKILL_DIR}/nse.py" news                  # the plan's stock + the watchlist hits
python3 "${CLAUDE_SKILL_DIR}/nse.py" news --symbols INFY --all   # include items already shown
```

Company names come from `nse/universe.json` (better search results); without it the symbol is used.
Seen items are remembered in `nse/state/news-seen.json`, so a second run shows only new ones.

## Steps

1. Run it, then sum up in a few lines: what happened, and whether anything is a scheduled event today
   (results, record date, board meeting).
2. Flag results due today or tomorrow: an intraday trade on a results day can gap through a stop.
   For the official source, point the user to the company's announcements page on nseindia.com.
3. Never invent a headline. If the feeds are empty or blocked, say so.

## Rules

- Headlines are aggregator links, not official filings. Official corporate announcements are on
  nseindia.com and bseindia.com.
- Education and tooling, not investment advice.
