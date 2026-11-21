---
name: nse-broker
description: >-
  NSE Order Executor. Shows each risk-passed order as a plain ticket (buy qty, LIMIT entry, SL-M
  stop-loss, target, square-off time, max loss and charges) with the exact Zerodha Kite place_order
  parameters, records the user's explicit yes word for word, records fills, and builds the square-off
  sell ticket. Paper by default. Never places an order on its own. Covers connecting Zerodha's Kite MCP
  server and setting its order tools to "Needs approval". Use when the user says "order lagao",
  "buy karo", "place the order", "square off", "position band karo", "connect Zerodha", or after nse-risk passes.
argument-hint: "[show|confirm|fill|flatten]"
---

# nse-broker

Reply in the user's language. `${CLAUDE_SKILL_DIR}` is this skill's folder.

```bash
python3 "${CLAUDE_SKILL_DIR}/nse.py" ticket show
python3 "${CLAUDE_SKILL_DIR}/nse.py" ticket confirm --id RELIANCE-2026-11-23-0940 --said "haan, place it"
python3 "${CLAUDE_SKILL_DIR}/nse.py" ticket fill --id RELIANCE-2026-11-23-0940 --price 2481.5
python3 "${CLAUDE_SKILL_DIR}/nse.py" ticket fill --id ... --status denied      # or failed / closed
python3 "${CLAUDE_SKILL_DIR}/nse.py" ticket flatten --symbol RELIANCE --qty 40
```

## Three checks before any order

An order can go out only when ALL hold:
1. `nse-risk` printed PASS for that exact order today (`ticket show` says READY).
2. The user typed an explicit yes for that one ticket in the chat, after seeing it. Record it with
   `ticket confirm --said "<their exact words>"`. "ok maybe", silence, or a yes from earlier is not a yes.
3. The broker tool is set to "Needs approval", so Claude's own Allow once / Deny card asks again.
   Never suggest "Always allow".

## Paper vs live

Zerodha has no paper account. With `"paper": true` nothing goes to any broker: show the ticket,
then record the pretend fill with `ticket fill --price <the price at that moment>`.

## Placing a live order

- With a Zerodha Kite MCP connector (Zerodha publishes one; the user adds it in Claude's connector
  settings and logs in with Kite): send the entry leg (BUY LIMIT) with its place-order tool. Only
  after the entry fills, send the SL-M stop leg. Never more than one ticket per yes.
- Without a connector: the user places it in the Kite app from the ticket. Then record it with `ticket fill`.
- Zerodha's old bracket orders are gone. The target is watched by `nse-alerts`; when it rings, the
  user sells with a LIMIT and cancels the SL-M. If both a stop order and a target order rest at the
  broker, both can fill and leave a short MIS position: never leave both.
- MIS positions still open are auto squared-off by the broker around 15:20 with a fee. Square off
  first at `flat_by`: `ticket flatten`, cancel the open SL-M, then the user's yes.

## Rules

- Never call cancel-all, exit-all, F&O, GTT changes or fund transfers on your own.
- API keys and Kite logins stay in the connector or environment variables, never in the chat.
- Education and tooling, not investment advice.
