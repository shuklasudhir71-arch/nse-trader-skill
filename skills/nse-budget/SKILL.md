---
name: nse-budget
description: >-
  NSE Budget Manager. Sizes an Indian intraday position in whole shares to your capital and your max
  loss per trade (in rupees), within a position cap, a max order value and a cash buffer, and says which
  limit decided. Estimates Zerodha-style charges (brokerage, STT, exchange fees, SEBI fee, stamp duty,
  GST) at the stop and at the target so you see the net. Writes ONE order to nse/orders.json with the
  Kite place_order parameters. Places nothing. Use when the user says "kitne shares lun", "mere paas
  1 lakh hai", "500 rupaye se zyada risk nahi", "size this trade", "charges kitne lagenge", "set my rules".
argument-hint: ""
---

# nse-budget

Reply in the user's language. `${CLAUDE_SKILL_DIR}` is this skill's folder.

```bash
python3 "${CLAUDE_SKILL_DIR}/nse.py" init                  # once: nse/config.json
python3 "${CLAUDE_SKILL_DIR}/nse.py" quote RELIANCE --write  # a fresh price (optional)
python3 "${CLAUDE_SKILL_DIR}/nse.py" size
```

## nse/config.json (rupees)

| key | default | meaning |
| --- | --- | --- |
| `paper` | true | practise; flip to false only when the user decides to go live |
| `capital` | 100000 | trading capital |
| `max_loss_per_trade` | 1000 | loss at the stop, per trade |
| `daily_loss_limit` | 3000 | total at risk per day |
| `max_trades_per_day` | 3 | |
| `max_position_pct` | 50 | one position at most this % of capital |
| `max_order_value` | 100000 | |
| `cash_buffer_pct` | 10 | never use this part of capital |
| `mis_leverage` | 1 | 1 = no leverage. Brokers allow ~5x on MIS; a loss is on the full position |
| `product` | MIS | MIS intraday or CNC delivery |
| `min_reward_risk` | 1.5 | used by nse-risk |
| `no_entries_before` / `no_entries_after` | 09:30 / 14:45 | entry window, IST |
| `flat_by` | 15:10 | square-off time |
| `tick_size` | 0.05 | |
| `charges` | Zerodha-style | check against your broker's current rate card |

When the user gives numbers ("1 lakh capital, 500 max loss"), edit nse/config.json for them and show
the change.

## Steps

1. Run `size`. Say: shares, value, which limit decided, loss + charges at the stop, gain - charges at
   the target.
2. If it refuses ("No trade"), explain: the stop is too far for their max loss. Do not shrink their
   rules to make it fit.
3. Next: `nse-risk`, always.

## Rules

- Whole shares only. Long only. Education and tooling, not investment advice.
