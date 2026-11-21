---
name: nse-entry
description: >-
  NSE Entry Timer. Watches one Indian stock bar by bar on 5-minute bars and says the moment to get in:
  the first completed bar that CLOSES above the 15-minute opening range high (9:15-9:30 IST), or above a
  level you give, with an optional volume check. Says waiting (and how far), triggered (time and price),
  expired (after no_entries_after, default 14:45), or market closed (NSE holidays and weekends). Writes
  nse/entry.json. Long only, never places an order. Use when the user says "entry kab lun", "tell me when
  to buy RELIANCE", "has it broken out", "watch TCS", "ORB signal", or after nse-scanner and nse-news.
argument-hint: "[SYMBOL] [--level price]"
---

# nse-entry

Reply in the user's language. `${CLAUDE_SKILL_DIR}` is this skill's folder.

```bash
python3 "${CLAUDE_SKILL_DIR}/nse.py" clock                                  # is NSE open?
python3 "${CLAUDE_SKILL_DIR}/nse.py" entry check RELIANCE                   # opening-range breakout
python3 "${CLAUDE_SKILL_DIR}/nse.py" entry check TCS --level 4185           # close above your level
python3 "${CLAUDE_SKILL_DIR}/nse.py" entry check SBIN --volume-mult 1.5     # and 1.5x average volume
python3 "${CLAUDE_SKILL_DIR}/nse.py" entry watch RELIANCE --every 60 --until 11:00
python3 "${CLAUDE_SKILL_DIR}/nse.py" entry check 500325 --exchange BSE      # BSE code or symbol
```

Exit codes: 0 triggered, 1 market closed, 2 expired, 3 waiting.

## Steps

1. Check the clock first. Before 9:30 the opening range is still forming.
2. Run `check` (or `watch`, or a `/loop` every few minutes). Report in one line.
3. On TRIGGERED, go straight to `nse-exit` to set the stop and target before any buy.

## Rules

- Bars are free and delayed (Yahoo). By the time a 5-minute close shows up, the price may have moved.
  The broker's price is the one that fills.
- A trigger is a rule firing, not advice. Long only: no short selling.
