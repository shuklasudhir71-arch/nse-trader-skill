---
name: nse-timemachine
description: >-
  NSE Time Machine. Before risking a rupee, replays the rest of the trading day 10,000 times from the
  stock's own 5-minute bars of the last 60 days (each bar drawn from the same time of day), and re-runs
  the same opening-range rule on every past session. Prints the odds of target first / stop first /
  still open at square-off, the average R, the history and an honest verdict. Use when the user says
  "odds kya hain", "simulate this trade", "backtest", "monte carlo", "is this a coin flip", or after
  nse-exit writes a plan.
argument-hint: "[--n 10000] [--seed 19]"
---

# nse-timemachine

Reply in the user's language. `${CLAUDE_SKILL_DIR}` is this skill's folder.

```bash
python3 "${CLAUDE_SKILL_DIR}/nse.py" sim --write            # -> nse/sim.json
python3 "${CLAUDE_SKILL_DIR}/nse.py" sim --n 20000 --seed 7
python3 "${CLAUDE_SKILL_DIR}/nse.py" sim --json
```

A bar that touches both the stop and the target is counted as a stop (the cautious choice). A gap
below the stop is counted at the worse price.

## Steps

1. Run `sim --write`. Give the three odds, the average R, the history line and the verdict.
2. If it is close to a coin flip or leans against them, say that plainly. Then `nse-budget`.

## Rules

- A simulation from 60 days of bars, not a prediction. Results, news, a block deal or a circuit
  limit can do what those days never did.
- Same seed, same answer: anyone can repeat it.
