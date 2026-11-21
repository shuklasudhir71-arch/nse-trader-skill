---
name: nse-exit
description: >-
  NSE Exit Planner. Sets the stop-loss and target BEFORE entering an Indian intraday trade. Reads
  nse/entry.json (or a price you give), puts the stop at the opening-range midpoint by default (or the
  range low, a percent, a price, or a multiple of the 5-minute ATR), rounds to the tick size (0.05 by
  default), and the target at 2R (or 1R, 1.5R, or a price). Refuses a stop at or above the entry, or one
  too tight. Sets the square-off time (15:10 IST default, before brokers auto square-off MIS ~15:20).
  Writes nse/plan.json. Use when the user says "stop loss kahan lagaun", "target kya rakhun",
  "plan the exit", "risk reward", or right after nse-entry triggers.
argument-hint: "[--stop or-mid] [--target 2R]"
---

# nse-exit

Reply in the user's language. `${CLAUDE_SKILL_DIR}` is this skill's folder.

```bash
python3 "${CLAUDE_SKILL_DIR}/nse.py" plan                                  # or-mid stop, 2R target
python3 "${CLAUDE_SKILL_DIR}/nse.py" plan --stop or-low --target 1.5R
python3 "${CLAUDE_SKILL_DIR}/nse.py" plan --stop pct:1 --target price:2520
python3 "${CLAUDE_SKILL_DIR}/nse.py" plan --stop atr:2
python3 "${CLAUDE_SKILL_DIR}/nse.py" plan --symbol INFY --entry 1850 --stop price:1832 --target 2R
python3 "${CLAUDE_SKILL_DIR}/nse.py" plan --tick 0.10 --flat-by 15:00
```

Tick size: most NSE stocks trade in 0.05 steps, but NSE ties the tick size to the price band, so a
high-priced stock may need `--tick 0.10` or more. A wrong tick is rejected by the broker, not filled.

## Steps

1. Run `plan`. Say the result in one line: entry, stop, rupees at risk per share, target, R, square-off.
2. Next: `nse-timemachine` for the odds, then `nse-budget` for the share count.

## Rules

- A stop is a plan, not a guarantee: on a gap or a circuit, an SL-M can fill well below the trigger,
  and in a stock locked at its lower circuit it may not fill at all.
- Long only. Education and tooling, not investment advice.
