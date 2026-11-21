---
name: nse-alerts
description: >-
  NSE Price Alerts. Turns the trade plan into alerts in one go (stop, target, square-off time), plus
  above / below levels, percent moves from the previous close, any IST time of day and the NSE opening
  bell, each ringing once. Knows NSE hours (9:15-15:30 IST, pre-open 9:00) and holidays. Fetches free
  delayed quotes and writes nse/prices.json. Use when the user says "alert lagao", "batana jab RELIANCE
  2500 cross kare", "watch my stop", "3 baje yaad dilana", "market khula hai?", "price batao", or right
  after nse-exit writes a plan.
argument-hint: "[SYMBOL] [price]"
---

# nse-alerts

Reply in the user's language. `${CLAUDE_SKILL_DIR}` is this skill's folder.

```bash
python3 "${CLAUDE_SKILL_DIR}/nse.py" clock
python3 "${CLAUDE_SKILL_DIR}/nse.py" alerts add --from-plan            # stop, target, square-off
python3 "${CLAUDE_SKILL_DIR}/nse.py" alerts add --symbol TCS --above 4200
python3 "${CLAUDE_SKILL_DIR}/nse.py" alerts add --symbol SBIN --pct 3
python3 "${CLAUDE_SKILL_DIR}/nse.py" alerts add --at 15:00 --label "check positions"
python3 "${CLAUDE_SKILL_DIR}/nse.py" alerts add --opening-bell
python3 "${CLAUDE_SKILL_DIR}/nse.py" alerts check                      # exit 0 = something rang
python3 "${CLAUDE_SKILL_DIR}/nse.py" alerts list
python3 "${CLAUDE_SKILL_DIR}/nse.py" alerts reset tcs-above-4200.0
python3 "${CLAUDE_SKILL_DIR}/nse.py" quote RELIANCE TCS --write
```

## Steps

1. After a plan, run `add --from-plan` and confirm the three alerts.
2. While a trade is open, run `check` every few minutes (a Claude scheduled task or `/loop 3m`).
3. Say what rang, one line each. Target rang: the user sells with a LIMIT and cancels the SL-M
   (`nse-broker`). Square-off rang: `ticket flatten`, then their yes. Never act for them.

## Rules

- An alert is information, never an order.
- Quotes are free, delayed and unofficial: never present them as live. A price can pass a level
  between two checks.
