---
name: nse-risk
description: >-
  NSE Risk Manager. Checks every order in nse/orders.json against your rules before it can reach the
  broker: stop-loss required, loss at the stop within max loss per trade (from the higher of the plan
  entry and a fresh price), reward:risk minimum, daily loss limit, trades per day, entry window in IST,
  NSE open (holidays, weekends), blocked list, order size, position cap, cash buffer, MIS/CNC only,
  long only, and cash-equity only (no F&O, no crypto). Prints PASS or BLOCK with reasons and records it
  so nse-broker only shows passed orders. Use when the user says "check my order", "risk check",
  "kya yeh trade le sakta hun", "is this safe", or before nse-broker, always.
argument-hint: ""
---

# nse-risk

Reply in the user's language. `${CLAUDE_SKILL_DIR}` is this skill's folder.

```bash
python3 "${CLAUDE_SKILL_DIR}/nse.py" risk
```

Exit code 0 = all passed, 2 = something blocked. Verdicts go to `nse/state/risk.json`, keyed by a hash
of the order, so a changed order needs a new check. Filled trades recorded with `ticket fill` count
toward the day's trades and daily loss.

## Steps

1. Run `risk`. Show PASS or each BLOCK reason in plain words.
2. Never edit the config or the order to turn a BLOCK into a PASS unless the user explicitly asks
   for that rule change. Getting out of a position is never blocked (`ticket flatten`).
3. On PASS: `nse-broker`.

## Rules

- A PASS is a rules check, not a recommendation.
